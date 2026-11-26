"""
MaroIFRS - conversion CGNC -> IAS/IFRS (version en ligne et hors ligne, sans IA externe)
Lancer :  streamlit run app.py
Normes : catalogue IAS/IFRS complet (voir CATALOGUE) + controle fiscal + rapport PDF
Pour ajouter une norme : écrire une fonction norme_xxx(df) -> liste de constats,
puis l'ajouter au dictionnaire NORMES en bas de la section 3.
ATTENTION : les numéros de comptes (CGNC) sont à vérifier avec votre plan comptable.
"""
import io
import json
import math
import re
from datetime import datetime
from pathlib import Path
import altair as alt
import pandas as pd
import os
import streamlit as st
import comparaison_officielle as cmp_off
import consolidation_groupe as conso_grp
import couverture_normes as cn
import grand_livre as gl
import i18n
import charte_pdf as cp
import interface_maroifrs as ui
import modeles_excel
import parametres_fiscaux as pf
import rapport_conformite as rapport_final
import synthese_conformite as sc
import transformation_comptes as tcomp

st.set_page_config(page_title="MaroIFRS - CGNC vers IAS/IFRS", layout="wide", initial_sidebar_state="collapsed")
i18n.installer(st)                     # traduction centralisée (affichage seulement) : voir i18n.py ; sans effet en français
i18n.initialiser_langue(st)            # langue de la session, sinon celle de l'adresse (?lang=en), sinon le français


# ============================ 0. OCR (PDF scannés, hors ligne) ============================
# Moteur : RapidOCR (pip install rapidocr-onnxruntime). Modèles inclus dans le paquet : aucun accès internet nécessaire.
OCR_MAX_PAGES = 40
_OCR = {}


def _moteur_ocr():
    """Retourne le moteur OCR, ou None s'il n'est pas installé."""
    if "m" not in _OCR:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _OCR["m"] = RapidOCR()
        except Exception:
            _OCR["m"] = None
    return _OCR["m"]


class _PageOCR:
    """Page issue de l'OCR, avec la même interface que les pages pdfplumber utilisées par le lecteur de PDF."""
    def __init__(self, mots, bbox):
        self._mots, self.bbox = mots, bbox
        self.width, self.height = bbox[2] - bbox[0], bbox[3] - bbox[1]

    def extract_words(self, **_):
        return [dict(w) for w in self._mots]

    def extract_text(self):
        lignes = []
        for w in sorted(self._mots, key=lambda w: ((w["top"] + w["bottom"]) / 2, w["x0"])):
            yc = (w["top"] + w["bottom"]) / 2
            if lignes and abs(yc - lignes[-1][0]) <= 0.6 * (w["bottom"] - w["top"]):
                lignes[-1][1].append(w["text"])
            else:
                lignes.append([yc, [w["text"]]])
        return "\n".join(" ".join(t) for _, t in lignes)

    def crop(self, box):
        x0, top, x1, bottom = box
        gardes = [w for w in self._mots if x0 <= (w["x0"] + w["x1"]) / 2 <= x1 and top <= (w["top"] + w["bottom"]) / 2 <= bottom]
        return _PageOCR(gardes, (x0, top, x1, bottom))

    def extract_tables(self, *_, **__):
        return []

    def flush_cache(self):
        pass


def _page_ocr(page, moteur, resolution=200):
    """OCR d'une page PDF scannée -> _PageOCR (mots avec positions en points PDF)."""
    import numpy as np
    img = page.to_image(resolution=resolution).original.convert("RGB")
    res, _ = moteur(np.array(img))
    e = 72.0 / resolution
    mots = []
    for boite, texte, *_ in (res or []):
        xs, ys = [p[0] for p in boite], [p[1] for p in boite]
        x0, x1, top, bottom = min(xs) * e, max(xs) * e, min(ys) * e, max(ys) * e
        morceaux = str(texte).split()
        if not morceaux:
            continue
        # écart serré entre les mots d'un même segment OCR (« 12 500,00 » doit rester une seule cellule / un seul montant)
        ecart = 0.3 * (bottom - top)
        pas = max((x1 - x0) - ecart * (len(morceaux) - 1), 0.1) / sum(len(w) for w in morceaux)
        pos = x0
        for w in morceaux:
            mots.append({"text": w, "x0": pos, "x1": pos + len(w) * pas, "top": top, "bottom": bottom, "upright": True})
            pos += len(w) * pas + ecart
    return _PageOCR(mots, (0.0, 0.0, float(page.width), float(page.height)))


def _pages_avec_ocr(pdf):
    """Pages du PDF ; celles sans texte (scans) sont lues par OCR si le moteur est installé. Retourne (pages, nb pages OCR)."""
    pages, n = [], 0
    for p in pdf.pages:
        if not (p.extract_text() or "").strip() and n < OCR_MAX_PAGES:
            moteur = _moteur_ocr()
            if moteur is not None:
                try:
                    p = _page_ocr(p, moteur)
                    n += 1
                except Exception:
                    pass
        pages.append(p)
    return pages, n


# ============================ 1. LECTURE DE LA BALANCE ============================
# Noms d'en-têtes acceptés pour une balance (sans tenir compte des majuscules, des accents ni de la ponctuation).
# Le modèle téléchargeable utilise « Code compte », « Intitulé du compte », « Débit », « Crédit » ; les noms historiques restent acceptés.
ALIAS_BALANCE = {
    "compte": ("compte", "code compte", "n compte", "no compte", "numero compte", "numero de compte", "code du compte"),
    "libelle": ("libelle", "intitule", "intitule du compte", "libelle du compte", "designation"),
    "debit": ("debit", "debits", "montant debit"),
    "credit": ("credit", "credits", "montant credit"),
}


def _nom_colonne(t):
    """Nom d'en-tête normalisé : minuscules, sans accents, ponctuation remplacée par des espaces."""
    import unicodedata
    t = unicodedata.normalize("NFD", str(t).lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _correspondance_colonnes(noms):
    """{nom canonique: position} si les quatre colonnes d'une balance sont reconnues dans la liste de noms, sinon dictionnaire incomplet."""
    index = {}
    for j, nom in enumerate(noms):
        index.setdefault(_nom_colonne(nom), j)
    res = {}
    for cle, alias in ALIAS_BALANCE.items():
        for a in alias:
            if a in index:
                res[cle] = index[a]
                break
    return res


def _balance_depuis_entete_decale(fichier, df):
    """Classeur Excel dont le tableau ne commence pas en première ligne (titre, informations de l'entreprise... au-dessus : modèle MaroIFRS).
    Cherche, dans les 60 premières lignes de chaque feuille (hors feuille « Instructions »), la ligne qui porte les quatre en-têtes
    (code compte, intitulé, débit, crédit) et ne retient que les lignes situées dessous. Retourne df inchangé si rien n'est trouvé."""
    if len(_correspondance_colonnes(df.columns)) == 4:
        return df                                           # en-têtes déjà en première ligne
    try:
        fichier.seek(0)
        classeur = pd.ExcelFile(fichier)
        feuilles = sorted([f_ for f_ in classeur.sheet_names if not _nom_colonne(f_).startswith("instruction")],
                          key=lambda f_: "balance" not in _nom_colonne(f_))
        for feuille in feuilles:
            tete = classeur.parse(feuille, header=None, nrows=60)
            for i in range(len(tete)):
                corr = _correspondance_colonnes([v_ for v_ in tete.iloc[i].tolist()])
                if len(corr) == 4:
                    brut = classeur.parse(feuille, header=None, skiprows=i + 1)
                    sel = brut.iloc[:, [corr["compte"], corr["libelle"], corr["debit"], corr["credit"]]].copy()
                    sel.columns = ["compte", "libelle", "debit", "credit"]
                    return sel.dropna(how="all")
    except Exception:
        pass
    return df


def preparer_balance(df):
    """Normalise une balance : colonnes compte, libelle, debit, credit + calcul du solde."""
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    corr = _correspondance_colonnes(df.columns)
    if len(corr) == 4 and not {"compte", "libelle", "debit", "credit"} <= set(df.columns):
        original = list(df.columns)
        df = df.rename(columns={original[j]: cle for cle, j in corr.items()})      # « Code compte », « Intitulé du compte », « Débit », « Crédit »...
    manque = {"compte", "libelle", "debit", "credit"} - set(df.columns)
    if manque:
        raise ValueError(f"Colonnes manquantes : {', '.join(sorted(manque))}")
    df = df.dropna(subset=["compte"])
    df["compte"] = df["compte"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    for c in ("debit", "credit"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    df["solde"] = df["debit"] - df["credit"]  # >0 = débiteur, <0 = créditeur
    return df


def _nombre(texte):
    """Convertit un montant lu dans un PDF (ex. '1 250 000,00') en nombre."""
    t = str(texte).strip().replace("\u00a0", " ").replace("\u202f", " ")
    if t in ("", "-", "\u2013", "\u2014"):
        return 0.0
    negatif = t.startswith("-") or (t.startswith("(") and t.endswith(")"))
    t = re.sub(r"[^\d,.]", "", t)
    if not t:
        return None
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".") if re.search(r",\d{1,2}$", t) else t.replace(",", "")
    elif re.search(r"\.\d{3}(\.\d{3})*$", t):
        t = t.replace(".", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if negatif else v


_ACCENTS = str.maketrans("éèêëàâîïôûùç", "eeeeaaiioouc")
_RE_MONTANT = re.compile(r"^\(?-?\s?\d{1,3}(?:[ .]\d{3})+(?:[,.]\d{1,3})?\)?$|^\(?-?\d+(?:[,.]\d{1,3})?\)?$")


def _sans_accent(t):
    return str(t).lower().translate(_ACCENTS)


def _num(texte):
    return _nombre(str(texte).replace("\u2212", "-"))


def _est_montant(texte):
    t = str(texte).replace("\u00a0", " ").replace("\u202f", " ").replace("\u2212", "-").strip()
    return t in ("-", "\u2013", "\u2014") or bool(_RE_MONTANT.match(t))


def _lignes_page(page, sans_lettres_seules=False):
    """Regroupe les mots d'une page en lignes (même hauteur), puis en cellules (selon l'écart horizontal)."""
    mots = page.extract_words(x_tolerance=1.5, y_tolerance=2, use_text_flow=False, extra_attrs=["upright"])
    if sans_lettres_seules:
        # états de synthèse : on écarte les intitulés verticaux (TRÉSORERIE, E X P L O I T A T I O N...), les lettres isolées
        # et les numéros de page imprimés dans les marges (petit nombre collé au bord de la page)
        x0, x1 = page.bbox[0], page.bbox[2]
        mots = [w for w in mots if w.get("upright", True)
                and not (len(w["text"]) == 1 and w["text"].isalpha() and w["text"].isupper())
                and not (re.fullmatch(r"\d{1,3}", w["text"]) and (w["x1"] < x0 + 45 or w["x0"] > x1 - 40))]
    lignes = []
    for w in sorted(mots, key=lambda w: ((w["top"] + w["bottom"]) / 2, w["x0"])):
        yc = (w["top"] + w["bottom"]) / 2
        if lignes and abs(yc - lignes[-1][0]) <= (4.6 if sans_lettres_seules else 3.5):
            lignes[-1][1].append(w)
        else:
            lignes.append([yc, [w]])
    resultat = []
    for yc, ws in lignes:
        cel = []
        for w in sorted(ws, key=lambda w: w["x0"]):
            seuil = max(3.5, 0.45 * (w["bottom"] - w["top"]))
            if cel and w["x0"] - cel[-1][2] <= seuil:
                cel[-1] = (cel[-1][0] + " " + w["text"], cel[-1][1], w["x1"])
            else:
                cel.append((w["text"], w["x0"], w["x1"]))
        resultat.append((yc, cel))
    return resultat


def _moities(page):
    """Les états de synthèse sont souvent imprimés en double page (Actif à gauche, Passif à droite ;
    CPC puis CPC suite) : on lit chaque moitié comme une page séparée pour ne pas mélanger les deux côtés."""
    if page.width > 1.3 * page.height:
        m = page.width / 2
        return [page.crop((0, 0, m, page.height)), page.crop((m, 0, page.width, page.height))]
    return [page]


def _analyser(cel):
    """Une ligne -> (n° de compte ou None, libellé, [(montant, position x)])."""
    cel = list(cel)
    compte, libelle = None, []
    if cel:
        m1 = re.match(r"^(\d{2,8})(?:\s+([^\d\s].*))?$", cel[0][0])
        if m1:
            compte = m1.group(1)
            if m1.group(2):
                libelle.append(m1.group(2))
            cel = cel[1:]
    montants = []
    while cel and _est_montant(cel[-1][0]):
        c = cel.pop()
        montants.insert(0, (_num(c[0]), (c[1] + c[2]) / 2))
    libelle += [c[0] for c in cel]
    return compte, " ".join(libelle).strip(), montants


def _affecter(montants, xd, xc):
    """Répartit les montants d'une ligne entre Débit et Crédit (d'après la position des colonnes)."""
    if xd is not None and xc is not None and xc > xd:
        ecart, d, c, trouve = xc - xd, 0.0, 0.0, False
        for v, x in montants:
            if abs(x - xd) > 0.75 * ecart and abs(x - xc) > 0.75 * ecart:
                continue  # colonne de mouvements, hors Débit / Crédit
            if abs(x - xd) <= abs(x - xc):
                d += v
            else:
                c += v
            trouve = True
        return (d, c) if trouve else None
    if len(montants) >= 2:
        return montants[-2][0], montants[-1][0]
    return None


def _lire_page_texte(lignes, xd=None, xc=None):
    """Lecture par position des mots. Retourne (lignes lues, lignes ignorées, totaux, xd, xc)."""
    from statistics import median
    for _, cel in lignes:  # en-tête « Débit ... Crédit » : on garde les deux dernières colonnes (soldes)
        d = [c for c in cel if "debit" in _sans_accent(c[0])]
        k = [c for c in cel if "credit" in _sans_accent(c[0])]
        if d and k and d[-1] is not k[-1]:
            xd, xc = (d[-1][1] + d[-1][2]) / 2, (k[-1][1] + k[-1][2]) / 2
    analyses = [(_analyser(cel), cel) for _, cel in lignes]
    complets = [mt for (cp, _, mt), _ in analyses if cp and len(mt) >= 2]
    if xd is None and len(complets) >= 2:
        xd, xc = median(mt[-2][1] for mt in complets), median(mt[-1][1] for mt in complets)
    brut, ignorees, totaux, attente = [], [], [], None
    for (compte, lib, montants), cel in analyses:
        texte = " ".join(c[0] for c in cel)
        if compte:
            if attente:
                ignorees.append(f"{attente[0]} {attente[1]}".strip())
                attente = None
            aff = _affecter(montants, xd, xc) if montants else None
            if aff is not None:
                brut.append((compte, lib, aff[0], aff[1]))
            elif not montants:
                attente = (compte, lib)  # montants sur la ligne suivante (libellé sur 2 lignes)
            else:
                ignorees.append(texte)
        elif "total" in _sans_accent(texte) and len(montants) >= 2:
            totaux.append((montants[-2][0], montants[-1][0]))
            attente = None
        elif attente and montants:
            aff = _affecter(montants, xd, xc)
            if aff is not None:
                brut.append((attente[0], f"{attente[1]} {lib}".strip(), aff[0], aff[1]))
            else:
                ignorees.append(f"{attente[0]} {attente[1]}".strip())
            attente = None
        elif attente and lib:
            attente = (attente[0], f"{attente[1]} {lib}".strip())
        else:
            attente = None
    if attente:
        ignorees.append(f"{attente[0]} {attente[1]}".strip())
    return brut, ignorees, totaux, xd, xc


def _lire_page_tableaux(page, jd=None, jc=None):
    """Lecture des PDF avec traits de tableau. Retourne (lignes lues, lignes ignorées, col. débit, col. crédit)."""
    brut, ignorees = [], []
    for tab in page.extract_tables():
        lignes = [[(c or "").replace("\n", " ").strip() for c in lg] for lg in tab if lg]
        for lg in lignes:
            norm = [_sans_accent(c) for c in lg]
            d = [i for i, c in enumerate(norm) if "debit" in c]
            k = [i for i, c in enumerate(norm) if "credit" in c]
            if d and k and d[-1] != k[-1]:
                jd, jc = d[-1], k[-1]
        for lg in lignes:
            idx = next((i for i in range(min(2, len(lg))) if re.fullmatch(r"\d{2,8}", lg[i])), None)
            if idx is None:
                continue
            if jd is not None and max(jd, jc) < len(lg):
                vd, vc = lg[jd], lg[jc]
            elif len(lg) >= idx + 4:
                vd, vc = lg[-2], lg[-1]
            else:
                ignorees.append(" ".join(c for c in lg if c))
                continue
            d, c = _num(vd), _num(vc)
            if d is None or c is None or (vd == "" and vc == ""):
                ignorees.append(" ".join(x for x in lg if x))
                continue
            lib = " ".join(x for x in lg[idx + 1:] if x and not _est_montant(x))
            brut.append((lg[idx], lib, d, c))
    return brut, ignorees, jd, jc


def _contient_etats(pages_lignes):
    """Vrai si au moins un titre d'état de synthèse (BILAN ACTIF, BILAN PASSIF, CPC) figure dans le document."""
    for lignes in pages_lignes:
        for _, cel in lignes:
            if _section_titre(_norm_lib(" ".join(c[0] for c in cel))):
                return True
    return False


def lire_pdf(fichier):
    """Lit un PDF texte, ou un PDF scanné grâce à l'OCR (RapidOCR, hors ligne) :
    - états de synthèse (bilan, CPC), même noyés dans un rapport annuel de plusieurs dizaines de pages
      ou imprimés en double page : ils sont repérés sur toutes les pages puis convertis en comptes CGNC ;
    - sinon balance générale : deux méthodes sont essayées sur chaque page (tableau, puis position des mots)."""
    import pdfplumber
    fichier.seek(0)
    lignes, ignorees, totaux, texte = [], [], [], ""
    pages_etats = []
    xd = xc = jd = jc = None
    with pdfplumber.open(fichier) as pdf:
        pages = len(pdf.pages)
        liste, n_ocr = _pages_avec_ocr(pdf)
        for page in liste:
            if len(texte) < 6000:
                texte += (page.extract_text() or "") + "\n"
            for moitie in _moities(page):
                try:
                    pages_etats.append(_lignes_page(moitie, True))
                except Exception:
                    pages_etats.append([])
            try:
                page.flush_cache()  # PDF volumineux (rapport annuel) : évite de saturer la mémoire
            except Exception:
                pass
        t = _sans_accent(texte)
        try:
            inventaire = inventorier(pages_etats)
        except Exception:
            inventaire = {"detectes": {}, "esg": {}, "tf": {}, "etic": {}}
        # 1) états de synthèse : cherchés sur TOUTES les pages (le bilan est rarement au début d'un rapport annuel)
        if _contient_etats(pages_etats):
            try:
                etats = lire_etats(pages_etats)
            except Exception:
                etats = None
            if etats is not None and len(etats) >= 6:
                etats.attrs.update({"consolide": bool(_CONSOLIDE.search(t)), "pages": pages, "ignorees": 0, "lignes_ignorees": [], "total_pdf": None,
                                    "scanne": False, "etats_financiers": True, "ocr": n_ocr > 0, "pages_ocr": n_ocr, "inventaire": inventaire})
                return etats
        # 2) balance générale
        for page in liste:
            lp = _lignes_page(page)
            lt, it, tt, xd, xc = _lire_page_texte(lp, xd, xc)
            try:
                lb, ib, jd, jc = _lire_page_tableaux(page, jd, jc)
            except Exception:
                lb, ib = [], []
            lus, ign = (lb, ib) if lb and len(lb) >= len(lt) else (lt, it)
            lignes += lus
            ignorees += ign
            totaux += tt
    df = pd.DataFrame(lignes, columns=["compte", "libelle", "debit", "credit"])
    df.attrs.update({
        "ignorees": len(ignorees), "lignes_ignorees": ignorees, "pages": pages,
        "total_pdf": totaux[-1] if totaux else None, "scanne": not t.strip(), "ocr": n_ocr > 0, "pages_ocr": n_ocr,
        "etats_financiers": ("bilan" in t or "compte de produits" in t or "etat de synthese" in t) and "balance" not in t,
        "consolide": bool(_CONSOLIDE.search(t)),
        "inventaire": inventaire,
    })
    if len(df) >= 3:  # des comptes lus avec leurs débits et crédits : c'est une balance
        inventaire["detectes"]["balance"] = max(inventaire["detectes"].get("balance", 0), 1)
    return df


# ============================ 1 bis. LECTURE DES ÉTATS DE SYNTHÈSE (bilan, CPC) ============================
# Les états de synthèse ne portent pas de numéros de compte : chaque poste est rattaché à un compte du CGNC,
# ce qui reconstitue une balance exploitable par les contrôles CGNC et les normes IAS/IFRS.
_NOTE = re.compile(r"^\(\w{1,2}\)$")
_AMORT = {"21": "281", "22": "282", "23": "283", "241": "2941", "248": "2948", "251": "2951", "258": "2958",
          "31": "391", "34": "394", "35": "395", "51": "590"}
_CONSOLIDE = re.compile(r"comptes consolides|bilan (actif |passif )?consolide|compte de resultat consolide|flux de tresorerie consolide")


def _compte_amort(code):
    """Compte d'amortissement / de provision du poste : 232 -> 2832, 342 -> 3942, 3411 -> 3941 (CGNC), sinon table _AMORT."""
    if code == "3411":
        return "3941"
    base = {"21": "281", "22": "282", "23": "283", "31": "391", "34": "394"}.get(code[:2])
    if base and len(code) >= 3:
        return base + code[2]
    return next((_AMORT[p] for p in sorted(_AMORT, key=len, reverse=True) if code.startswith(p)), None)


_STOP = re.compile(r"soldes de gestion|tableau de financement|informations complementaires|\betic\b|etat a\d|etat b\d|"
                   r"tableau des (immobilisations|amortissements|provisions|creances|dettes|surs)|liste des")
_SAUT = re.compile(r"^(total|resultat|chiffre d affaires|cumul|exercice|opérations|operations|n°|note)")
_REGLES = {
    "A": [(r"ecarts? de conversion.*financement permanent", None), (r"ecarts? de conversion.*circulant", "37"),
          (r"frais preliminaires", "211"), (r"charges a repartir", "212"), (r"primes? de remboursement", "213"),
          (r"recherche et developpement", "221"), (r"brevets", "222"), (r"fonds commercial", "223"),
          (r"autres immobilisations incorporelles", "228"), (r"immobilisations corporelles en cours", "239"),
          (r"autres immobilisations corporelles", "238"), (r"^terrains", "231"), (r"^constructions", "232"),
          (r"installations techniques", "233"), (r"materiel de transport", "234"), (r"^mobilier", "235"),
          (r"prets immobilises", "241"), (r"autres creances financieres", "248"), (r"titres de participation", "251"),
          (r"autres titres immobilises", "258"), (r"diminution des creances immobilisees", "271"),
          (r"augmentation des dettes de financement", "272"), (r"^marchandises", "311"),
          (r"matieres et fournitures", "312"), (r"produits en cours", "313"), (r"produits intermediaires", "314"),
          (r"produits finis", "315"), (r"fournis\w*\.? debiteurs|fournisseurs debiteurs", "3411"), (r"clients et comptes rattaches", "342"),
          (r"^personnel", "343"), (r"^etat", "345"), (r"comptes d associes", "346"), (r"autres debiteurs", "348"),
          (r"comptes de regularisation", "349"), (r"titres (et )?valeurs de placement", "35"),
          (r"cheques et valeurs a encaisser", "511"), (r"^banques", "514"), (r"^caisses?", "516")],
    "P": [(r"actionnaires|capital souscrit non appele", "1119"), (r"capital social", "1111"),
          (r"primes? d emission", "112"), (r"ecarts? de reevaluation", "113"), (r"reserve legale", "114"),
          (r"autres reserves", "115"), (r"report a nouveau", "116"), (r"resultats? nets? en instance", "118"),
          (r"resultat net de l exercice", "119"), (r"subventions d investissement", "131"),
          (r"provisions reglementees", "135"), (r"emprunts obligataires", "141"), (r"autres dettes de financement", "148"),
          (r"provisions durables", None), (r"autres provisions pour risques", "45"),
          (r"provisions pour risques", "151"), (r"provisions pour charges", "155"),
          (r"ecarts? de conversion.*financement permanent", "17"), (r"ecarts? de conversion.*circulant", "47"),
          (r"fournisseurs et comptes rattaches", "441"), (r"clients crediteurs", "442"), (r"^personnel", "443"),
          (r"organismes sociaux", "444"), (r"^etat", "445"), (r"comptes d associes", "446"),
          (r"autres creanciers", "448"), (r"comptes de regularisation", "449"), (r"credits? d escompte", "552"),
          (r"credits? de tresorerie", "553"), (r"^banques", "554")],
    "C": [(r"ventes de marchandises", "711"), (r"ventes de biens et services produits", "712"),
          (r"variations? de stocks? de produits", "713"), (r"immobilisations produites", "714"),
          (r"subventions d exploitation", "716"), (r"autres produits d exploitation", "718"),
          (r"reprises d exploitation", "719"), (r"achats revendus de marchandises", "611"),
          (r"achats consommes de matieres", "612"), (r"autres charges externes", "613"), (r"impots et taxes", "616"),
          (r"charges de personnel", "617"), (r"autres charges d exploitation", "618"),
          (r"dotations d exploitation", "619"), (r"produits des titres de participation", "732"),
          (r"gains de change", "733"), (r"interets et autres produits financiers", "738"),
          (r"reprises financieres", "739"), (r"charges d interets", "631"), (r"pertes de change", "633"),
          (r"autres charges financieres", "638"), (r"dotations financieres", "639"),
          (r"produits des? cessions", "751"), (r"subventions d equilibre", "754"),
          (r"reprises sur subventions d investissement", "757"), (r"autres produits non courants", "758"),
          (r"reprises non courantes", "759"), (r"valeurs? nettes? d amortissements", "651"),
          (r"subventions accordees", "654"), (r"autres charges non courantes", "658"),
          (r"dotations non courantes", "659"), (r"impots sur les resultats", "670")],
}


def _norm_lib(t):
    t = _sans_accent(t).replace("œ", "oe")
    t = re.sub(r"\(\s*\w{1,2}\s*\)", " ", t)            # repères (A), (1)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\b(moins|dont|\+/?-?)\b", " ", t) if t.strip().startswith("moins") else t
    return re.sub(r"\s+", " ", t).strip()


def _section_titre(t):
    if re.search(r"compte de produits", t) and "charges" in t:
        return "C"
    if "bilan" in t and "passif" in t:
        return "P"
    if "bilan" in t and "actif" in t:
        return "A"
    if t in ("actif", "passif"):
        return "A" if t == "actif" else "P"
    return None


def _analyser_etat(cel):
    cel = list(cel)
    montants = []
    while cel and _est_montant(cel[-1][0]) and not _NOTE.match(cel[-1][0].strip()):
        c = cel.pop()
        montants.insert(0, (_num(c[0]), (c[1] + c[2]) / 2))
    return " ".join(c[0] for c in cel).strip(), montants


def _centres(xs, ecart=22.0):
    """Regroupe des positions x en colonnes (centres triés)."""
    groupes = []
    for x in sorted(xs):
        if groupes and x - groupes[-1][-1] <= ecart:
            groupes[-1].append(x)
        else:
            groupes.append([x])
    return [sum(g) / len(g) for g in groupes]


def _est_sous_total(lib, ln):
    """Ligne de total / de sous-total (« Immobilisations incorporelles (B) », « IV Produits financiers », « Total... »)."""
    if re.search(r"\(\s*[A-Z](?:\s*[+\-]\s*[A-Z])*\s*\)", lib) or re.match(r"^[IVX]{1,5}\s", lib):
        return True
    return ln.startswith(("total", "capital appele", "dont", "capitaux propres", "tresorerie", "resultat",
                          "immobilisations en non valeur", "immobilisations incorporelles", "immobilisations corporelles",
                          "immobilisations financieres", "produits d exploitation", "charges d exploitation",
                          "produits financiers", "charges financieres", "produits non courants", "charges non courantes"))


def _choisir_tableaux(retenus):
    """Un rapport annuel contient souvent plusieurs bilans (comptes sociaux, comptes consolidés) et des pages de commentaires.
    On garde, pour chaque état, le tableau qui compte le plus de postes reconnus, en prenant l'actif comme point d'ancrage
    (le passif et le CPC viennent du même document, c'est-à-dire des pages voisines)."""
    blocs = {}
    for r in retenus:
        blocs.setdefault(r[5], []).append(r)
    infos = [(b, rs[0][0], min(r[1] for r in rs), len(rs)) for b, rs in blocs.items()]  # (bloc, section, page, score)
    actifs = [i for i in infos if i[1] == "A"]
    base = max(actifs or infos, key=lambda i: (i[3], -i[2]))
    choisis = {base[0]}
    for sec in "APC":
        if sec == base[1]:
            continue
        proches = [i for i in infos if i[1] == sec and abs(i[2] - base[2]) <= 6] or [i for i in infos if i[1] == sec]
        if proches:
            choisis.add(max(proches, key=lambda i: (i[3], -i[2]))[0])
    return [r for r in retenus if r[5] in choisis]


def lire_etats(pages):
    """pages : liste de pages, chaque page = liste de (y, cellules). Retourne un DataFrame compte/libelle/debit/credit
    reconstitué à partir du bilan (actif, passif) et du CPC, ou None si rien n'est reconnu."""
    section, retenus, ignorees, vus = None, [], [], set()
    x_tot, bloc = {}, 0
    for pi, lignes in enumerate(pages):
        attente = None  # libellé lu sans montant, dont les montants sont sur la ligne suivante (libellé sur 2 lignes)
        for yc, cel in lignes:
            texte = " ".join(c[0] for c in cel)
            t = _norm_lib(texte)
            if not t:
                continue
            if any("totaux" in _norm_lib(c[0]) for c in cel):
                x_tot[pi] = next((c[1] + c[2]) / 2 for c in cel if "totaux" in _norm_lib(c[0]))
            if _STOP.search(t):
                section = None
                continue
            nv = _section_titre(t)
            if nv:
                if nv != section:
                    bloc += 1  # nouveau tableau (actif, passif ou CPC) : le même titre répété (« suite ») prolonge le tableau
                section = nv
                continue
            if section is None:
                continue
            lib, montants = _analyser_etat(cel)
            ln = _norm_lib(lib)
            if not montants and len(ln) >= 3:
                attente = (lib, yc)
                continue
            if montants and len(ln) < 3 and attente and abs(yc - attente[1]) <= 10:
                lib = attente[0]
                ln = _norm_lib(lib)
            attente = None
            if not montants or len(ln) < 3:
                continue
            if _SAUT.match(ln) and not (section == "P" and ln.startswith("resultat net de l exercice")):
                continue
            code, trouve = None, False
            for motif, cd in _REGLES[section]:
                if re.search(motif, ln):
                    code, trouve = cd, True
                    break
            if not trouve or code is None:
                if not trouve and not _est_sous_total(lib, ln):
                    ignorees.append((bloc, f"{lib} : {', '.join(f'{v:,.0f}' for v, _ in montants if v is not None)}"))
                continue
            if (bloc, code) in vus:
                continue
            vus.add((bloc, code))
            retenus.append((section, pi, code, lib, montants, bloc))
    if not retenus:
        return None
    retenus = _choisir_tableaux(retenus)
    if not retenus:
        return None
    blocs_ok = {r[5] for r in retenus}
    ignorees = [t for b, t in ignorees if b in blocs_ok]  # on ne signale que les lignes du tableau retenu
    # colonnes par (section, page)
    xs = {}
    for sec, pi, _, _, mts, _ in retenus:
        xs.setdefault((sec, pi), []).extend(x for _, x in mts)
    centres = {k: _centres(v) for k, v in xs.items()}

    def colonne(sec, pi, x):
        c = centres[(sec, pi)]
        return min(range(len(c)), key=lambda i: abs(c[i] - x))

    lignes = []
    for sec, pi, code, lib, mts, _ in retenus:
        c = centres[(sec, pi)]
        par_col = {}
        for v, x in mts:
            if v is not None:
                par_col.setdefault(colonne(sec, pi, x), v)
        nom = re.sub(r"\s*\(\s*\w{1,2}\s*\)", "", lib).strip(" .:-")
        nom = re.sub(r"^(?:[IVX]{1,5}\s+)(?=\D)", "", nom)            # numéro de rubrique du CPC (« II Impôts et taxes »)
        nom = re.sub(r"[→|\[].*$", "", nom).strip(" .:-") or libelle_cgnc(code)  # débris de mise en page (« →  [I]| »)
        if re.search(r"\b(de|des|du|et|d|l|la|le|les|aux|au|a|en)$", _sans_accent(nom).replace("\u2019", " ").strip()):
            nom = libelle_cgnc(code) or nom  # libellé coupé en fin de ligne dans le PDF
        if sec == "A":
            brut, amort, net = par_col.get(0), par_col.get(1, 0.0), par_col.get(2)
            if len(c) <= 2:
                brut, amort, net = par_col.get(0), 0.0, par_col.get(0)
            if brut is None and net is not None:
                brut = net + amort
            if brut is None:
                continue
            lignes.append((code, nom, brut, 0.0) if brut >= 0 else (code, nom, 0.0, -brut))
            if amort:
                ca = _compte_amort(code)
                if ca:
                    lignes.append((ca, f"Amortissements et provisions : {nom}", 0.0, amort))
                else:
                    ignorees.append(f"{lib} : amortissement sans compte de rattachement")
        else:
            if sec == "C" and len(c) >= 2:
                xt = x_tot.get(pi)
                if xt is not None:
                    idx = min(range(len(c)), key=lambda i: abs(c[i] - xt))
                else:
                    idx = 2 if len(c) >= 4 else (1 if len(c) == 3 else 0)
            else:
                idx = 0
            v = par_col.get(idx)
            if v is None and len(c) < 2:
                v = next(iter(par_col.values()), None)
            if v is None:
                continue  # montant présent seulement dans la colonne « exercice précédent » : rien pour l'exercice lu
            cote_credit = code.startswith(("1", "4", "5", "7")) and code != "1119"
            if code == "1119":
                lignes.append((code, nom, abs(v), 0.0))
            elif cote_credit:
                lignes.append((code, nom, 0.0, v) if v >= 0 else (code, nom, -v, 0.0))
            else:
                lignes.append((code, nom, v, 0.0) if v >= 0 else (code, nom, 0.0, -v))
    df = pd.DataFrame(lignes, columns=["compte", "libelle", "debit", "credit"])
    if df.empty:
        return None
    df = df.groupby("compte", sort=False, as_index=False).agg(
        libelle=("libelle", lambda x: x.iloc[0] if len(set(x)) == 1 else x.iloc[0] + " (et autres postes)"),
        debit=("debit", "sum"), credit=("credit", "sum"))
    if df["compte"].str.startswith(("6", "7")).any() and (df["compte"] == "119").any():
        df = df[df["compte"] != "119"].reset_index(drop=True)  # résultat reconstitué par le CPC : pas de double comptage
        retire119 = True
    else:
        retire119 = False
    df.attrs.update({"etats_synthese": True, "ignorees_etats": ignorees, "resultat_via_cpc": retire119,
                     "sections": sorted({r[0] for r in retenus})})
    return df


def _pages_excel(fichier):
    """Chaque feuille du classeur devient une « page » de lignes (y, cellules), comme pour un PDF."""
    fichier.seek(0)
    pages = []
    for _, f in pd.read_excel(fichier, sheet_name=None, header=None).items():
        lignes = []
        for i, row in f.iterrows():
            cel = []
            for j, v in enumerate(row):
                if pd.isna(v):
                    continue
                cel.append((f"{v:.2f}" if isinstance(v, float) else str(v), j * 60.0, j * 60.0 + 50.0))
            if cel:
                lignes.append((float(i), cel))
        pages.append(lignes)
    return pages


def lire_etats_excel(fichier):
    """Bilan / CPC présentés dans un classeur Excel (une ou plusieurs feuilles)."""
    return lire_etats(_pages_excel(fichier))


def charger_balance(fichier):
    """Excel (.xlsx, .xls) ou CSV : colonnes compte, libelle, debit, credit ; ou états de synthèse (bilan, CPC, ESG, TF, ETIC)."""
    if fichier.name.lower().endswith(".csv"):
        df = pd.read_csv(fichier, sep=None, engine="python")
    else:
        df = pd.read_excel(fichier)
    nature = gl.est_liste_ecritures(df)
    if nature:  # grand livre / journal : les écritures sont contrôlées puis cumulées en balance
        ecr = gl.lire_ecritures(df)
        res = preparer_balance(gl.balance_depuis_ecritures(ecr)[["compte", "libelle", "debit", "credit"]])
        res.attrs["inventaire"] = {"detectes": {"balance": 1, nature: 1}, "esg": {}, "tf": {}, "etic": {}}
        res.attrs["ecritures_controles"] = gl.controles_ecritures(ecr, CGNC_COMPTES) + gl.controle_tva_ecritures(ecr)
        res.attrs["ecritures_nb"] = int(len(ecr))
        res.attrs["ecritures_nature"] = nature
        return res
    if not fichier.name.lower().endswith(".csv"):
        df = _balance_depuis_entete_decale(fichier, df)
    try:
        res = preparer_balance(df)
        res.attrs["inventaire"] = {"detectes": {"balance": 1}, "esg": {}, "tf": {}, "etic": {}}
        return res
    except ValueError:
        if fichier.name.lower().endswith(".csv"):
            raise
        pages = _pages_excel(fichier)
        inv = inventorier(pages)
        etats = lire_etats(pages)
        if etats is None or len(etats) < 6:
            if a_du_contenu(inv):
                raise DocumentSansComptes(inv)
            raise
        res = preparer_balance(etats)
        res.attrs.update(etats.attrs)
        res.attrs["inventaire"] = inv
        return res


# ============================ 1 ter. RECONNAISSANCE DES DOCUMENTS (ESG, tableau de financement, ETIC...) ============================
# Un dossier comptable contient plus que le bilan et le CPC : état des soldes de gestion (ESG), tableau de financement (TF),
# ETIC (annexes)... Chaque page est classée d'après ses titres ; les états chiffrés (ESG, TF) sont lus, puis rapprochés de la
# balance reconstituée (contrôle CGNC) et rattachés aux normes IAS/IFRS équivalentes.
class DocumentSansComptes(ValueError):
    """Document reconnu (ESG, tableau de financement, ETIC...) mais sans comptes : aucune balance n'en est déduite."""
    def __init__(self, inventaire):
        super().__init__("Document reconnu, mais sans comptes exploitables")
        self.inventaire = inventaire


LIBELLES_DOC = {
    "balance": "Balance générale", "bilan_actif": "Bilan : actif", "bilan_passif": "Bilan : passif",
    "cpc": "Compte de produits et charges (CPC)", "esg": "État des soldes de gestion (ESG)",
    "tf": "Tableau de financement (TF)", "etic": "ETIC (informations complémentaires)", "facture": "Factures",
    "grand_livre": "Grand livre", "journal": "Journal",
}
_TITRES_DOC = [  # (code, motif sur le titre normalisé) : un titre est une ligne courte
    ("esg", r"etat des soldes de gestion|soldes de gestion|tableau de formation des resultats|\besg\b"),
    ("tf", r"tableau de financement|synthese des masses|emplois et ressources"),
    ("etic", r"informations complementaires|\betic\b"),
    ("balance", r"balance generale|balance des comptes|balance auxiliaire"),
    ("grand_livre", r"grand livre"),
    ("journal", r"^journal\b|journal general|livre journal"),
]
_ESG_REGLES = [
    ("marge_brute", r"marge brute"), ("valeur_ajoutee", r"valeur ajoutee"),
    ("ebe", r"excedent brut d exploitation|insuffisance brute d exploitation|\bebe\b"),
    ("resultat_exploitation", r"resultat d exploitation"), ("resultat_financier", r"resultat financier"),
    ("resultat_courant", r"resultat courant"), ("resultat_non_courant", r"resultat non courant"),
    ("impots", r"impots sur les resultats"), ("resultat_net", r"resultat net"),
    ("caf", r"capacite d autofinancement|\bcaf\b"), ("autofinancement", r"^autofinancement"),
]
_TF_REGLES = [
    ("financement_permanent", r"financement permanent"), ("actif_immobilise", r"actif immobilise"),
    ("frng", r"fonds de roulement|\bfrng\b"), ("actif_circulant", r"actif circulant"),
    ("passif_circulant", r"passif circulant"), ("bfg", r"besoin de financement|\bbfg\b"),
    ("tresorerie_nette", r"tresorerie nette"),
]
LIBELLES_ESG = {
    "marge_brute": "Marge brute", "valeur_ajoutee": "Valeur ajoutée", "ebe": "Excédent brut d'exploitation (EBE)",
    "resultat_exploitation": "Résultat d'exploitation", "resultat_financier": "Résultat financier",
    "resultat_courant": "Résultat courant", "resultat_non_courant": "Résultat non courant",
    "impots": "Impôts sur les résultats", "resultat_net": "Résultat net", "caf": "Capacité d'autofinancement (CAF)",
    "autofinancement": "Autofinancement",
}
LIBELLES_TF = {
    "financement_permanent": "Financement permanent", "actif_immobilise": "Actif immobilisé",
    "frng": "Fonds de roulement fonctionnel (FRNG)", "actif_circulant": "Actif circulant",
    "passif_circulant": "Passif circulant", "bfg": "Besoin de financement global (BFG)",
    "tresorerie_nette": "Trésorerie nette",
}
ETIC_ANNEXES = [  # (motif, libellé de l'annexe, normes IAS/IFRS correspondantes)
    (r"methodes d evaluation", "Principales méthodes d'évaluation", "IAS 1 / IAS 8"),
    (r"derogations", "État des dérogations", "IFRS 1 / IAS 1"),
    (r"changements? de methodes", "Changements de méthodes", "IAS 8"),
    (r"immobilisations autres que financieres", "Tableau des immobilisations", "IAS 16 / IAS 38"),
    (r"tableau des amortissements", "Tableau des amortissements", "IAS 16 / IAS 38"),
    (r"plus ou moins values", "Plus ou moins-values sur cessions", "IAS 16 / IFRS 5"),
    (r"titres de participation", "Titres de participation", "IFRS 9 / IAS 27 / IAS 28"),
    (r"tableau des provisions", "Tableau des provisions", "IAS 37 / IAS 19"),
    (r"tableau des creances", "Tableau des créances", "IFRS 9 / IFRS 7"),
    (r"tableau des dettes", "Tableau des dettes", "IFRS 9 / IFRS 7"),
    (r"suretes reelles", "Sûretés réelles données ou reçues", "IFRS 7 / IAS 37"),
    (r"engagements financiers", "Engagements financiers", "IAS 37 / IFRS 7"),
    (r"credit bail", "Biens en crédit-bail", "IFRS 16"),
    (r"datation|evenements posterieurs", "Événements postérieurs à la clôture", "IAS 10"),
    (r"repartition du capital", "Répartition du capital", "IAS 1 / IAS 24"),
    (r"affectation des resultats", "Affectation des résultats", "IAS 1"),
    (r"chiffre d affaires", "Ventilation du chiffre d'affaires", "IFRS 15 / IFRS 8"),
]
_DEBUT_ANNEXE = ("tableau", "etat", "repartition", "datation", "principales", "ventilation", "liste")


def inventorier(pages):
    """pages : liste de pages, chaque page = liste de (y, cellules). Retourne l'inventaire des documents reconnus :
    {"detectes": {code: nb de titres}, "esg": {solde: montant}, "tf": {masse: montant}, "etic": {annexe: normes}}."""
    inv = {"detectes": {}, "esg": {}, "tf": {}, "etic": {}}
    det = inv["detectes"]
    noms = {"A": "bilan_actif", "P": "bilan_passif", "C": "cpc"}
    mode = None
    for lignes in pages:
        attente = None
        for yc, cel in lignes:
            t = _norm_lib(" ".join(c[0] for c in cel))
            if not t:
                continue
            nv = _section_titre(t)
            if nv:
                det[noms[nv]] = det.get(noms[nv], 0) + 1
                mode, attente = None, None
                continue
            lib, montants = _analyser_etat(cel)
            ln = _norm_lib(lib)
            mots = len(t.split())
            titre = None
            if mots <= 10 and len(montants) < 2:
                titre = next((code for code, motif in _TITRES_DOC if re.search(motif, t)), None)
            if titre:
                det[titre] = det.get(titre, 0) + 1
                mode = titre if titre in ("esg", "tf", "etic") else None
                attente = None
                continue
            if mode == "etic":
                if mots <= 16 and (t.startswith(_DEBUT_ANNEXE) or re.match(r"^etat [ab]\d", t)):
                    for motif, nom, normes in ETIC_ANNEXES:
                        if re.search(motif, t):
                            inv["etic"].setdefault(nom, normes)
                            break
                continue
            if mode not in ("esg", "tf"):
                continue
            if not montants and len(ln) >= 3:
                attente = (lib, yc)  # libellé sur deux lignes : les montants sont sur la suivante
                continue
            if montants and len(ln) < 3 and attente and abs(yc - attente[1]) <= 10:
                ln = _norm_lib(attente[0])
            attente = None
            valeur = next((v for v, _ in montants if v is not None), None)
            if valeur is None or len(ln) < 3:
                continue
            for cle, motif in (_ESG_REGLES if mode == "esg" else _TF_REGLES):
                if re.search(motif, ln):
                    inv[mode].setdefault(cle, valeur)
                    break
    return inv


def fusionner_inventaires(invs):
    res = {"detectes": {}, "esg": {}, "tf": {}, "etic": {}}
    for inv in invs:
        for k, v in ((inv or {}).get("detectes") or {}).items():
            res["detectes"][k] = res["detectes"].get(k, 0) + v
        for bloc in ("esg", "tf", "etic"):
            for k, v in ((inv or {}).get(bloc) or {}).items():
                res[bloc].setdefault(k, v)
    return res


def a_du_contenu(inv):
    """Vrai si l'inventaire contient au moins un état reconnu et lisible."""
    inv = inv or {}
    return bool(len(inv.get("esg") or {}) >= 3 or len(inv.get("tf") or {}) >= 3 or inv.get("etic"))


def _grandeurs_cpc(df):
    ex, fi, nc = -solde(df, "61", "71"), -solde(df, "63", "73"), -solde(df, "65", "75")
    return {"resultat_net": -solde(df, "6", "7"), "resultat_exploitation": ex, "resultat_financier": fi,
            "resultat_courant": ex + fi, "resultat_non_courant": nc, "marge_brute": -solde(df, "711") - solde(df, "611")}


def _grandeurs_bilan(df):
    fp = -solde(df, "11", "13", "14", "15", "17") - solde(df, "6", "7")
    ai, ac, pc, tn = solde(df, "2"), solde(df, "3"), -solde(df, "4"), solde(df, "5")
    return {"financement_permanent": fp, "actif_immobilise": ai, "frng": fp - ai, "actif_circulant": ac,
            "passif_circulant": pc, "bfg": ac - pc, "tresorerie_nette": tn}


def controles_documents(inv, df=None):
    """Contrôles propres aux états complémentaires : cohérence interne, rapprochement avec la balance reconstituée (CGNC)
    et correspondance avec les normes IAS/IFRS. Retourne une liste de constats."""
    r = []
    if not inv:
        return r
    esg, tf, etic, det = inv.get("esg") or {}, inv.get("tf") or {}, inv.get("etic") or {}, inv.get("detectes") or {}
    tol = lambda v: max(2.0, 0.005 * abs(v))
    a_cpc = df is not None and bool(df["compte"].str.startswith(("6", "7")).any())
    a_bilan = df is not None and bool(df["compte"].str.startswith(("1", "2")).any())

    def rapprocher(norme, lus, calcules, libelles, source):
        ecarts = [(libelles[k], v, calcules[k]) for k, v in lus.items() if k in calcules and abs(v - calcules[k]) > tol(v)]
        for lib, lu, calc in ecarts:
            r.append(constat(norme, "À vérifier", f"{lib} : {m(lu)} dans le document, {m(calc)} d'après {source}",
                             "Rechercher le poste mal lu ou mal classé, ou la différence de périmètre entre les deux états"))
        comparables = [k for k in lus if k in calcules]
        if comparables and not ecarts:
            r.append(constat(norme, "Info", f"Cohérent avec {source} ({len(comparables)} montant(s) rapprochés)", ""))

    # ---- ESG ----
    if det.get("esg") and len(esg) < 3:
        r.append(constat("ESG / CGNC", "À vérifier", "État des soldes de gestion détecté, mais moins de trois soldes lisibles",
                         "Vérifier la mise en page du document (ou importer un PDF texte / Excel)"))
    if len(esg) >= 3:
        ex, fi, co = (esg.get(k) for k in ("resultat_exploitation", "resultat_financier", "resultat_courant"))
        if None not in (ex, fi, co) and abs(co - (ex + fi)) > tol(co):
            r.append(constat("ESG / CGNC", "Erreur", f"Résultat courant ({m(co)}) différent de exploitation + financier ({m(ex + fi)})",
                             "Corriger l'état des soldes de gestion"))
        rc, rnc, imp, rn = (esg.get(k) for k in ("resultat_courant", "resultat_non_courant", "impots", "resultat_net"))
        if None not in (rc, rnc, imp, rn) and abs(rn - (rc + rnc - abs(imp))) > tol(rn):
            r.append(constat("ESG / CGNC", "Erreur", f"Résultat net ({m(rn)}) différent de courant + non courant - impôt ({m(rc + rnc - abs(imp))})",
                             "Corriger l'état des soldes de gestion"))
        if a_cpc:
            rapprocher("ESG / CGNC", esg, _grandeurs_cpc(df), LIBELLES_ESG, "le CPC")
        else:
            r.append(constat("ESG / CGNC", "Info", "Aucun CPC lu : l'ESG ne peut pas être rapproché des comptes de charges et de produits",
                             "Importer aussi le CPC ou la balance générale"))
        r.append(constat("ESG / IAS 1", "Info", "Les soldes de gestion (marge brute, valeur ajoutée, EBE) n'existent pas en IFRS",
                         "IAS 1 impose un état du résultat global par nature ou par fonction ; IFRS 18 (applicable dès 2027) crée les catégories "
                         "exploitation / investissement / financement et les mesures de performance définies par la direction"))
    # ---- Tableau de financement ----
    if det.get("tf") and len(tf) < 3:
        r.append(constat("TF / CGNC", "À vérifier", "Tableau de financement détecté, mais moins de trois masses lisibles",
                         "Vérifier la mise en page du document (ou importer un PDF texte / Excel)"))
    if len(tf) >= 3:
        fp, ai, fr, ac, pc, bf, tn = (tf.get(k) for k in ("financement_permanent", "actif_immobilise", "frng", "actif_circulant",
                                                           "passif_circulant", "bfg", "tresorerie_nette"))
        if None not in (fp, ai, fr) and abs(fr - (fp - ai)) > tol(fr):
            r.append(constat("TF / CGNC", "Erreur", f"FRNG ({m(fr)}) différent de financement permanent - actif immobilisé ({m(fp - ai)})",
                             "Corriger le tableau de financement"))
        if None not in (ac, pc, bf) and abs(bf - (ac - pc)) > tol(bf):
            r.append(constat("TF / CGNC", "Erreur", f"BFG ({m(bf)}) différent de actif circulant - passif circulant ({m(ac - pc)})",
                             "Corriger le tableau de financement"))
        if None not in (fr, bf, tn) and abs(tn - (fr - bf)) > tol(tn):
            r.append(constat("TF / CGNC", "Erreur", f"Trésorerie nette ({m(tn)}) différente de FRNG - BFG ({m(fr - bf)})",
                             "Corriger le tableau de financement"))
        if a_bilan:
            rapprocher("TF / CGNC", tf, _grandeurs_bilan(df), LIBELLES_TF, "le bilan reconstitué")
        else:
            r.append(constat("TF / CGNC", "Info", "Aucun bilan lu : le tableau de financement ne peut pas être rapproché des comptes",
                             "Importer aussi le bilan ou la balance générale"))
        r.append(constat("TF / IAS 7", "À vérifier", "Le tableau de financement analyse des masses du bilan, il ne remplace pas le tableau des flux de trésorerie",
                         "Établir le tableau des flux IAS 7 (exploitation, investissement, financement) ; rapprocher la trésorerie nette du TF "
                         "de la trésorerie et équivalents IAS 7 (découverts remboursables à vue inclus)"))
    # ---- ETIC ----
    if etic:
        liste = ", ".join(sorted(etic))
        normes = sorted({n.strip() for v in etic.values() for n in v.split("/")}, key=lambda x: (x[:3] != "IAS", int(re.search(r"\d+", x).group())))
        r.append(constat("ETIC / IFRS", "Info", f"{len(etic)} annexe(s) de l'ETIC reconnue(s) : {liste}",
                         "À convertir en notes IFRS : " + ", ".join(normes)))
        manquantes = [lib for lib in ("Tableau des provisions", "Biens en crédit-bail", "Événements postérieurs à la clôture") if lib not in etic]
        if manquantes:
            r.append(constat("ETIC / IFRS", "À vérifier", "Annexes utiles aux retraitements non trouvées : " + ", ".join(manquantes),
                             "Ces informations alimentent IAS 37, IFRS 16 et IAS 10 : les rechercher ou les demander à l'entreprise"))
    elif det and not det.get("balance") and any(det.get(k) for k in ("bilan_actif", "bilan_passif", "cpc", "esg", "tf")):
        r.append(constat("ETIC / IFRS", "Info", "Aucun ETIC détecté dans les documents importés",
                         "Les notes annexes IFRS (IAS 1.112) devront être préparées à partir des informations de l'entreprise"))
    return r


def documents_reconnus(inv, at=None):
    """Lignes du tableau « Documents reconnus » : document, état de la lecture, contrôles appliqués."""
    at = at or {}
    det, esg, tf, etic = (inv or {}).get("detectes") or {}, (inv or {}).get("esg") or {}, (inv or {}).get("tf") or {}, (inv or {}).get("etic") or {}
    secs = set(at.get("sections") or [])
    lignes = []

    def add(code, lecture, controles):
        lignes.append({"Document": LIBELLES_DOC[code], "Lecture": lecture, "Contrôles appliqués": controles})
    for code, sec in (("bilan_actif", "A"), ("bilan_passif", "P"), ("cpc", "C")):
        if det.get(code):
            add(code, "Converti en comptes CGNC" if sec in secs else "Détecté, non converti", "Conformité CGNC, puis normes IAS/IFRS")
    if det.get("balance") and not (det.get("grand_livre") or det.get("journal")):
        add("balance", "Lue : comptes, débit, crédit", "Conformité CGNC, puis normes IAS/IFRS")
    if det.get("esg"):
        add("esg", f"{len(esg)} solde(s) lu(s)" if len(esg) >= 3 else "Détecté, montants non lus",
            "Cohérence interne, rapprochement avec le CPC, IAS 1 / IFRS 18")
    if det.get("tf"):
        add("tf", f"{len(tf)} masse(s) lue(s)" if len(tf) >= 3 else "Détecté, montants non lus",
            "Cohérence interne, rapprochement avec le bilan, IAS 7")
    if det.get("etic") or etic:
        add("etic", f"{len(etic)} annexe(s) reconnue(s)" if etic else "Détecté, annexes non identifiées", "Correspondance avec les notes IFRS")
    if det.get("facture"):
        add("facture", f"{det['facture']} facture(s) lue(s)", "Écritures CGNC (achats, ventes, TVA), puis normes IAS/IFRS")
    for code in ("grand_livre", "journal"):
        if det.get(code):
            if det.get("balance"):
                add(code, "Écritures lues et cumulées en balance", "Équilibre par pièce, comptes, dates, TVA ; puis conformité CGNC et normes IAS/IFRS")
            else:
                add(code, "Reconnu, non converti", "Importer la balance générale correspondante")
    return lignes


def valeurs_lues(inv):
    """Lignes (état, poste, montant) des soldes ESG et des masses TF lus dans les documents."""
    lignes = []
    for cle, v in ((inv or {}).get("esg") or {}).items():
        lignes.append({"État": "ESG", "Poste": LIBELLES_ESG.get(cle, cle), "Montant (MAD)": f"{v:,.0f}".replace(",", " ")})
    for cle, v in ((inv or {}).get("tf") or {}).items():
        lignes.append({"État": "TF", "Poste": LIBELLES_TF.get(cle, cle), "Montant (MAD)": f"{v:,.0f}".replace(",", " ")})
    return lignes


def lire_lot(elements, est_facture):
    """Plusieurs documents de types différents (bilan, CPC, ESG, TF, ETIC...) : chaque fichier est lu, puis les comptes sont fusionnés.
    elements : [(nom, octets)]. Retourne (balance brute ou None, inventaire, attributs, messages)."""
    morceaux, invs, messages, sections, doublons = [], [], [], set(), []
    for (nom, octets), fact in zip(elements, est_facture):
        if fact:
            messages.append(f"{nom} : facture ignorée (importez les factures séparément des états de synthèse)")
            continue
        try:
            d_, a_ = lire_pdf_cache(octets) if nom.lower().endswith(".pdf") else charger_balance_cache(octets, nom)
        except DocumentSansComptes as e_:
            d_, a_ = None, {"inventaire": e_.inventaire}
        except Exception as e_:
            messages.append(f"{nom} : lecture impossible ({e_})")
            continue
        invs.append(a_.get("inventaire"))
        sections |= set(a_.get("sections") or [])
        if d_ is not None and not d_.empty:
            morceaux.append(d_[["compte", "libelle", "debit", "credit"]])
        elif not _inventaire_utile(a_):
            messages.append(f"{nom} : aucun compte reconnu")
    inv = fusionner_inventaires(invs)
    if not morceaux:
        return None, inv, {"sections": sorted(sections)}, messages
    brut = pd.concat(morceaux, ignore_index=True)
    brut["compte"] = brut["compte"].astype(str)
    doublons = sorted(brut.loc[brut["compte"].duplicated(), "compte"].unique())
    brut = brut.drop_duplicates("compte", keep="first").reset_index(drop=True)
    if brut["compte"].str.startswith(("6", "7")).any() and (brut["compte"] == "119").any():
        brut = brut[brut["compte"] != "119"].reset_index(drop=True)  # résultat reconstitué par le CPC : pas de double comptage
    if doublons:
        messages.append(f"{len(doublons)} compte(s) présent(s) dans plusieurs fichiers : la première lecture est conservée ({', '.join(doublons[:8])})")
    return brut, inv, {"sections": sorted(sections), "etats_synthese": bool(sections)}, messages


def _inventaire_utile(attrs):
    return a_du_contenu((attrs or {}).get("inventaire"))


# ============================ 1b. FACTURES -> BALANCE (n'importe quel document) ============================
class _Lot:
    """Plusieurs fichiers importés ensemble, présentés comme un seul fichier."""
    def __init__(self, fichiers):
        self.fichiers = fichiers
        self.name = f"{len(fichiers)} documents"
        self.size = sum(f.size for f in fichiers)


COLS_FACT = ["Fichier", "N° facture", "Date", "Tiers", "Sens", "Compte", "HT", "TVA", "TTC", "_tva_deduite"]
_NATURES = [  # (mots-clés, compte de charge CGNC)
    (("loyer", "location", "bail"), "6131"), (("reparation", "entretien", "maintenance"), "6133"),
    (("assurance",), "6134"), (("honoraires", "comptable", "avocat", "notaire", "commission"), "6136"),
    (("etude", "audit"), "6141"), (("transport", "livraison", "fret"), "6142"),
    (("deplacement", "hotel", "restaurant", "mission", "voyage"), "6143"),
    (("publicite", "communication", "annonce"), "6144"),
    (("telephone", "internet", "telecom", "poste", "inwi", "orange"), "6145"), (("frais bancaires", "banque"), "6147"),
    (("electricite", "eau", "lydec", "redal", "onee", "gaz", "carburant", "gasoil", "essence"), "6125"),
    (("fourniture", "papeterie", "bureau"), "6122"),
]
_SERVICES = ("prestation", "service", "honoraires", "formation", "conseil", "maintenance", "mission")


def _mots(t, mots):
    return any(re.search(r"\b" + re.escape(m), t) for m in mots)


def compte_pour(texte, sens):
    t = _sans_accent(texte)
    if sens == "Vente":
        return "7124" if _mots(t, _SERVICES) else "7111"
    for mots, compte in _NATURES:
        if _mots(t, mots):
            return compte
    return "6111"


@st.cache_data(show_spinner="Lecture du document (OCR si le PDF est scanné)...", max_entries=16)
def _texte_pdf(octets, max_pages=6):
    import pdfplumber
    textes = []
    with pdfplumber.open(io.BytesIO(octets)) as pdf:
        for p in pdf.pages[:max_pages]:
            t = p.extract_text() or ""
            if not t.strip():
                moteur = _moteur_ocr()
                if moteur is not None:
                    try:
                        t = _page_ocr(p, moteur).extract_text()
                    except Exception:
                        pass
            textes.append(t)
    return "\n".join(textes)


def est_facture(texte):
    t = _sans_accent(texte)
    if re.search(r"balance generale|compte de produits et charges|total debit|\bbilan\b", t[:3000]):
        return False
    return bool(re.search(r"\bfacture\b", t[:2500]))


def _montant_apres(t, motifs):
    for mo in motifs:
        r = re.search(mo + r"[^\d\n\-]{0,40}(\d[\d \u00a0\u202f.,]*\d|\d)", t)
        if r:
            v = _nombre(r.group(1))
            if v is not None:
                return v
    return None


def lire_facture_texte(texte, nom):
    """Extrait n°, date, tiers et montants HT / TVA / TTC d'une facture PDF (texte). None si rien n'est reconnu."""
    t = _sans_accent(texte).replace("\u2212", "-")
    ht = _montant_apres(t, [r"(?:total|montant|sous[- ]?total|net)\s*h\.?t\.?\b", r"\bbase\s*h\.?t\.?\b"])
    ttc = _montant_apres(t, [r"(?:total|montant|net a payer|a payer)\s*(?:general\s*)?t\.?t\.?c\.?\b", r"net a payer", r"total a payer"])
    tva = _montant_apres(t, [r"(?:total|montant)\s*t\.?v\.?a\.?\b(?:\s*\(?\s*\d{1,2}(?:[.,]\d+)?\s*%\s*\)?)?",
                             r"\bt\.?v\.?a\.?\s*\(?\s*\d{1,2}(?:[.,]\d+)?\s*%\s*\)?"])
    rt = re.search(r"t\.?v\.?a\.?\s*\(?\s*(\d{1,2}(?:[.,]\d+)?)\s*%", t) or re.search(r"\b(20|14|10|7)\s*%", t)
    taux = (_nombre(rt.group(1)) if rt else 20.0) or 20.0
    if ht is None and ttc is None:
        return None
    deduite = tva is None or ttc is None or ht is None   # TVA non lue telle quelle sur le document
    if ttc is None:
        tva = tva if tva is not None else round(ht * taux / 100, 2)
        ttc = ht + tva
    elif ht is None:
        ht = ttc - tva if tva is not None else round(ttc / (1 + taux / 100), 2)
        tva = ttc - ht
    elif tva is None:
        tva = ttc - ht
    rn = re.search(r"facture\s*(?:n[°o\u00ba]|numero|num|no)?\s*[:.#]?\s*([a-z0-9][a-z0-9/_\-.]{1,24})", t)
    num = rn.group(1).upper().strip(".") if rn and rn.group(1) not in ("date", "client", "du", "de", "d") else Path(nom).stem
    rd = re.search(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b", t)
    date = f"{int(rd.group(1)):02d}/{int(rd.group(2)):02d}/{rd.group(3)}" if rd else ""
    premiere = next((l.strip() for l in texte.splitlines() if len(l.strip()) > 2), "")[:60]
    return {"Fichier": nom, "N° facture": num, "Date": date, "Tiers": premiere, "Sens": None, "Compte": "",
            "HT": round(ht, 2), "TVA": round(tva, 2), "TTC": round(ttc, 2), "_texte": texte, "_tva_deduite": bool(deduite)}


def lire_factures_tableau(tab, nom):
    """Factures présentées dans un Excel / CSV (une facture par ligne : colonnes HT, TVA, TTC, date, tiers...)."""
    tab = tab.copy()
    tab.columns = [_sans_accent(c).strip() for c in tab.columns]

    def col(*mots):
        return next((c for c in tab.columns if any(m in c for m in mots)), None)
    c_ht, c_ttc, c_tva = col("ht", "hors taxe"), col("ttc"), col("tva")
    if c_ht is None and c_ttc is None:
        return []
    c_num, c_date, c_tiers = col("numero", "n°", "facture"), col("date"), col("fournisseur", "client", "tiers")
    c_sens, c_lib = col("sens", "type"), col("libelle", "designation", "description", "objet")
    r = []
    for _, ligne in tab.iterrows():
        ht = pd.to_numeric(ligne[c_ht], errors="coerce") if c_ht else float("nan")
        ttc = pd.to_numeric(ligne[c_ttc], errors="coerce") if c_ttc else float("nan")
        tva = pd.to_numeric(ligne[c_tva], errors="coerce") if c_tva else float("nan")
        if pd.isna(ht) and pd.isna(ttc):
            continue
        deduite = bool(pd.isna(ht) or pd.isna(tva))   # TVA absente du fichier : reconstituée, donc non contrôlable
        if pd.isna(ht):
            ht = ttc - tva if not pd.isna(tva) else round(ttc / 1.2, 2)
        if pd.isna(tva):
            tva = (ttc - ht) if not pd.isna(ttc) else round(ht * 0.2, 2)
        sens = None
        if c_sens and re.search(r"vente|client|emise", _sans_accent(ligne[c_sens])):
            sens = "Vente"
        elif c_sens and re.search(r"achat|fournisseur|recue", _sans_accent(ligne[c_sens])):
            sens = "Achat"
        d = ligne[c_date] if c_date else ""
        d = d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else ("" if pd.isna(d) else str(d))
        r.append({"Fichier": nom, "N° facture": "" if not c_num or pd.isna(ligne[c_num]) else str(ligne[c_num]), "Date": d,
                  "Tiers": "" if not c_tiers or pd.isna(ligne[c_tiers]) else str(ligne[c_tiers])[:60], "Sens": sens, "Compte": "",
                  "HT": round(float(ht), 2), "TVA": round(float(tva), 2), "TTC": round(float(ht + tva), 2),
                  "_texte": "" if not c_lib or pd.isna(ligne[c_lib]) else str(ligne[c_lib]), "_tva_deduite": deduite})
    return r


def extraire_factures(elements, sens_defaut):
    """elements : liste de (nom, octets). Retourne (tableau de factures, noms des documents non reconnus)."""
    lignes, ignores = [], []
    for nom, octets in elements:
        try:
            if nom.lower().endswith(".pdf"):
                f = lire_facture_texte(_texte_pdf(octets), nom)
                fs = [f] if f else []
            else:
                brut = io.BytesIO(octets)
                tab = pd.read_csv(brut, sep=None, engine="python") if nom.lower().endswith(".csv") else pd.read_excel(brut)
                fs = lire_factures_tableau(tab, nom)
        except Exception:
            fs = []
        if not fs:
            ignores.append(nom)
        for f in fs:
            f["Sens"] = f["Sens"] or sens_defaut
            f["Compte"] = compte_pour(f.pop("_texte", ""), f["Sens"])
            lignes.append(f)
    return pd.DataFrame(lignes, columns=COLS_FACT), ignores


def fichier_est_facture(nom, octets):
    try:
        if nom.lower().endswith(".pdf"):
            return est_facture(_texte_pdf(octets))
        brut = io.BytesIO(octets)
        tab = pd.read_csv(brut, sep=None, engine="python") if nom.lower().endswith(".csv") else pd.read_excel(brut)
        cols = {_sans_accent(c).strip() for c in tab.columns}
        return not {"compte", "debit", "credit"} <= cols and bool(lire_factures_tableau(tab, nom))
    except Exception:
        return False


def balance_depuis_factures(tab):
    """Écritures comptables CGNC déduites des factures : achats (charge, TVA récupérable 34552, fournisseurs 4411)
    et ventes (clients 3421, produit, TVA facturée 4455). Les avoirs (montants négatifs) s'inversent."""
    lignes = []

    def f(x):
        v = pd.to_numeric(x, errors="coerce")
        return 0.0 if pd.isna(v) else float(v)

    def ecr(code, lib, debit):
        lignes.append((code, lib, debit, 0.0) if debit >= 0 else (code, lib, 0.0, -debit))
    for _, r in tab.iterrows():
        ht, tva = f(r["HT"]), f(r["TVA"])
        sens = r["Sens"] if r["Sens"] in ("Achat", "Vente") else "Achat"
        code = str(r["Compte"]).strip() or compte_pour("", sens)
        lib = libelle_cgnc(code) or f"Compte {code}"
        if sens == "Vente":
            ecr("3421", "Clients", ht + tva)
            ecr(code, lib, -ht)
            ecr("4455", "État - TVA facturée", -tva)
        else:
            ecr(code, lib, ht)
            ecr("34552", "État - TVA récupérable sur les charges", tva)
            ecr("4411", "Fournisseurs", -(ht + tva))
    df = pd.DataFrame(lignes, columns=["compte", "libelle", "debit", "credit"])
    df = df.groupby("compte", sort=False, as_index=False).agg(libelle=("libelle", "first"), debit=("debit", "sum"), credit=("credit", "sum"))
    res = preparer_balance(df)
    res.attrs.update({"factures": True, "nb_factures": int(len(tab)), "tva_factures": gl.controle_tva_factures(tab)})
    return res


def solde(df, *prefixes):
    """Somme des soldes (débit - crédit) des comptes commençant par les préfixes."""
    masque = df["compte"].str.startswith(tuple(prefixes))
    return float(df.loc[masque, "solde"].sum())


def exemple_balance():
    lignes = [
        ("1111", "Capital social", 0, 1_000_000),
        ("1481", "Emprunts auprès des établissements de crédit", 0, 600_000),
        ("2111", "Frais préliminaires", 50_000, 0),
        ("2230", "Fonds commercial", 150_000, 0),
        ("2310", "Terrains", 300_000, 0),
        ("2321", "Constructions", 800_000, 0),
        ("2340", "Matériel de transport", 200_000, 0),
        ("2832", "Amortissements des immobilisations corporelles", 0, 950_000),
        ("3111", "Marchandises", 400_000, 0),
        ("3421", "Clients", 1_250_000, 0),
        ("4411", "Fournisseurs", 0, 700_000),
        ("5141", "Banque", 500_000, 0),
        ("6111", "Achats revendus de marchandises", 1_800_000, 0),
        ("6171", "Rémunérations du personnel", 600_000, 0),
        ("6513", "Charges non courantes", 30_000, 0),
        ("6701", "Impôts sur les résultats", 120_000, 0),
        ("7111", "Ventes de marchandises au Maroc", 0, 3_000_000),
        ("7119", "Rabais, remises et ristournes accordés", 100_000, 0),
        ("7585", "Produits non courants", 0, 50_000),
    ]
    return pd.DataFrame(lignes, columns=["compte", "libelle", "debit", "credit"])


def constat(norme, niveau, texte, retraitement=""):
    return {"Norme": norme, "Niveau": niveau, "Constat": texte, "Retraitement suggéré": retraitement}


def m(x):
    if x is None or x != x:   # NaN : valeur non calculée, jamais affichée comme zéro
        return "n/c"
    return f"{x:,.0f} MAD"


def _a_bilan(df):
    """Le document contient des comptes de bilan (classes 1 à 5)."""
    return bool(df["compte"].astype(str).str.startswith(("1", "2", "3", "4", "5")).any())


def _jeu_complet(df):
    """Bilan et CPC présents : seuls les ensembles d'états complets appellent les rappels d'annexe (IAS 8, IAS 10, IFRS 7...)."""
    return _a_bilan(df) and bool(df["compte"].astype(str).str.startswith(("6", "7")).any())


# ============================ 2. ÉTAT DE SITUATION (IAS 1) ============================
def etat_situation(df):
    """Présentation courant / non courant (mapping simplifié à adapter)."""
    resultat = -solde(df, "6", "7")
    postes = [
        ("Actif non courant", solde(df, "2")),
        ("Actif courant", solde(df, "3") + solde(df, "51") + solde(df, "59")),
        ("Capitaux propres (y compris résultat)", -solde(df, "11", "13") + resultat),
        ("Passif non courant", -solde(df, "14", "15", "16", "17")),
        ("Passif courant", -solde(df, "4") - solde(df, "55")),
    ]
    tab = pd.DataFrame(postes, columns=["Poste", "Montant"])
    actif = postes[0][1] + postes[1][1]
    passif = sum(p[1] for p in postes[2:])
    return tab, actif, passif


# ============================ 3. RÈGLES PAR NORME ============================
def norme_ias1(df):
    r = []
    ecart = df["debit"].sum() - df["credit"].sum()
    if abs(ecart) > 1:
        r.append(constat("IAS 1", "Erreur", f"Balance déséquilibrée (écart {m(ecart)})",
                         "Corriger la balance avant tout retraitement"))
    nv = solde(df, "21", "281")  # non-valeurs nettes de leurs amortissements
    if nv > 0:
        r.append(constat("IAS 1 / IAS 38", "Retraitement",
                         f"Immobilisations en non-valeurs : {m(nv)}",
                         "Pas d'actif en IFRS : passer en charges (contrepartie capitaux propres d'ouverture)"))
    nc = solde(df, "65")
    pn = -solde(df, "75")
    if nc or pn:
        r.append(constat("IAS 1", "Retraitement",
                         f"Éléments non courants : charges {m(nc)}, produits {m(pn)}",
                         "IAS 1 interdit les éléments « extraordinaires » : reclasser dans les charges/produits ordinaires ou les détailler en annexe"))
    _, actif, passif = etat_situation(df)
    if abs(actif - passif) > 1:
        r.append(constat("IAS 1", "Info",
                         f"Total actif ({m(actif)}) ≠ total passif ({m(passif)})",
                         "Adapter le mapping des comptes dans etat_situation()"))
    r.append(constat("IAS 1", "Info", "Rappel : présenter aussi l'état du résultat global, "
                     "le tableau des variations des capitaux propres, les flux de trésorerie et les notes.", ""))
    return r


def norme_ifrs15(df):
    r = []
    ca = -solde(df, "711", "712")
    r.append(constat("IFRS 15 / IAS 18", "Info", f"Chiffre d'affaires comptable : {m(ca)}", ""))
    rrr = solde(df, "7119", "7129")
    if rrr > 0:
        r.append(constat("IFRS 15", "À vérifier", f"Rabais, remises et ristournes accordés : {m(rrr)}",
                         "Contrepartie variable (IFRS 15.50) : estimer et déduire du CA"))
    pca = -solde(df, "4491")
    if pca:
        r.append(constat("IFRS 15", "À vérifier", f"Produits constatés d'avance : {m(pca)}",
                         "Vérifier le transfert de contrôle : passif sur contrat si obligation de prestation non satisfaite"))
    subv = -solde(df, "716")
    if subv > 0:
        r.append(constat("IFRS 15 / IAS 20", "À vérifier", f"Subventions d'exploitation : {m(subv)}",
                         "Hors champ IFRS 15 : traiter selon IAS 20"))
    r.append(constat("IFRS 15", "Info", "Modèle en 5 étapes : contrat, obligations de prestation, prix, "
                     "allocation du prix, reconnaissance à la satisfaction de l'obligation.", ""))
    return r


def norme_ias16(df):
    r = []
    brut = solde(df, "23")
    terrains = solde(df, "231")
    amort = -solde(df, "283")
    amortissable = brut - terrains
    if brut > 0 and amort == 0:
        r.append(constat("IAS 16", "Erreur", "Immobilisations corporelles sans aucun amortissement",
                         "Calculer l'amortissement selon la durée d'utilité (IAS 16.50)"))
    if amortissable > 0:
        taux = amort / amortissable
        niveau = "À vérifier" if taux > 0.9 else "Info"
        r.append(constat("IAS 16", niveau, f"Amortissements / base amortissable = {taux:.0%}",
                         "Taux > 90 % : réviser les durées d'utilité et valeurs résiduelles (IAS 16.51)" if taux > 0.9 else ""))
    if solde(df, "2321") > 0:
        r.append(constat("IAS 16", "À vérifier", "Constructions inscrites en bloc",
                         "Approche par composants (IAS 16.43) : gros-œuvre, étanchéité, installations..."))
    cb = solde(df, "6132")
    if cb > 0:
        r.append(constat("IAS 16 / IFRS 16", "Retraitement", f"Redevances de crédit-bail : {m(cb)}",
                         "Activer le droit d'utilisation et la dette de loyer (IFRS 16)"))
    return r


def norme_ias12(df):
    r = []
    impot = solde(df, "67")
    resultat_av = -(solde(df, "6", "7") - impot)
    if resultat_av > 0:
        r.append(constat("IAS 12", "Info", f"Taux d'impôt effectif : {impot / resultat_av:.1%}", ""))
    r.append(constat("IAS 12", "Retraitement",
                     "Le référentiel marocain ne constate pas systématiquement les impôts différés",
                     "Utiliser l'onglet IAS 12 : impôt différé sur chaque différence temporaire (y compris celles créées par les autres retraitements IFRS)"))
    return r


def norme_ias36(df):
    r = []
    fc = solde(df, "223")
    if fc > 0:
        r.append(constat("IAS 36", "À vérifier", f"Fonds commercial : {m(fc)}",
                         "Test de dépréciation annuel obligatoire (IAS 36.10) : utiliser l'onglet IAS 36"))
    r.append(constat("IAS 36", "Info", "Rechercher tout indice de perte de valeur sur les actifs corporels et incorporels.", ""))
    return r


NORMES = {  # <- ajouter ici les futures normes
    "IAS 1": norme_ias1,
    "IFRS 15 / IAS 18": norme_ifrs15,
    "IAS 16": norme_ias16,
    "IAS 12": norme_ias12,
    "IAS 36": norme_ias36,
}


# ============================ 3b. STATUT DES COMPTES, CONFORMITÉ, HISTORIQUE ============================
STATUTS = ["éliminé", "à revoir", "inchangé", "reclassé", "ajusté"]
COULEURS_STATUT = {"éliminé": "#9AA79F", "à revoir": "#E65100", "inchangé": "#388E3C",
                   "reclassé": "#4CAF50", "ajusté": "#B94A48"}
PUCES = {"éliminé": "⚫", "à revoir": "🟠", "inchangé": "🟢", "reclassé": "🟡", "ajusté": "🔴"}
DEFINITIONS_STATUT = {
    "éliminé": "compte sans équivalent en IFRS : il disparaît des états",
    "reclassé": "montant conservé, mais présenté ou classé autrement",
    "ajusté": "montant à recalculer par un retraitement (impôts différés, contrepartie variable)",
    "à revoir": "jugement de l'utilisateur nécessaire avant de conclure",
    "inchangé": "aucune règle ne s'applique : le compte est repris tel quel",
}
# L'historique est enregistré à côté de app.py ; la variable d'environnement MAROIFRS_HISTORIQUE permet de le rediriger (utilisée par les tests
# pour ne jamais écrire dans l'historique réel de l'utilisateur).
FICHIER_HISTO = (Path(os.environ["MAROIFRS_HISTORIQUE"]) if os.environ.get("MAROIFRS_HISTORIQUE")
                 else (Path(__file__).with_name("historique.json") if "__file__" in globals() else Path("historique.json")))


def statuts_comptes(df, normes):
    """Attribue un statut à chaque compte d'après les règles des normes sélectionnées."""
    ordre = {s: i for i, s in enumerate(["éliminé", "reclassé", "ajusté", "à revoir", "inchangé"])}
    res = df[["compte", "libelle", "solde"]].copy().reset_index(drop=True)
    res["statut"] = "inchangé"
    res["motif"] = "Aucune règle applicable : repris tel quel"
    amortissable = solde(df, "23") - solde(df, "231", "239")  # hors terrains et immobilisations en cours
    taux = (-solde(df, "283") / amortissable) if amortissable > 0 else 0.0
    regles = [  # (norme, préfixes de comptes, statut, motif, règle active ?)
        ("IAS 1", ("21", "281"), "éliminé", "Non-valeur (ou son amortissement) : pas d'actif en IFRS (IAS 38)", True),
        ("IAS 1", ("65",), "reclassé", "Charge non courante : à reclasser (IAS 1)", True),
        ("IAS 1", ("75",), "reclassé", "Produit non courant : à reclasser (IAS 1)", True),
        ("IFRS 15 / IAS 18", ("7119", "7129"), "ajusté", "Remises accordées : contrepartie variable (IFRS 15)", True),
        ("IFRS 15 / IAS 18", ("4491",), "à revoir", "Produit constaté d'avance : passif sur contrat ? (IFRS 15)", True),
        ("IFRS 15 / IAS 18", ("716",), "reclassé", "Subvention d'exploitation : à traiter selon IAS 20", True),
        ("IAS 16", ("2321",), "à revoir", "Constructions : approche par composants (IAS 16)", True),
        ("IAS 16", ("283",), "à revoir", "Amortissements élevés : durées d'utilité à réviser (IAS 16)", taux > 0.9),
        ("IAS 16", ("6132",), "reclassé", "Crédit-bail : droit d'utilisation et dette (IFRS 16)", True),
        ("IAS 12", ("67",), "ajusté", "Impôt : ajouter les impôts différés (IAS 12)", True),
        ("IAS 36", ("223",), "à revoir", "Fonds commercial : test de dépréciation annuel (IAS 36)", True),
        ("IFRS 16", ("6131",), "reclassé", "Loyers : droit d'utilisation et dette locative (IFRS 16)", True),
        ("IAS 37", ("15", "45"), "à revoir", "Provision pour risques et charges : critères IAS 37", True),
        ("IAS 21", ("271", "272", "171", "172"), "éliminé", "Écart de conversion : inexistant en IFRS (IAS 21)", True),
        ("IFRS 9", ("3942",), "à revoir", "Dépréciation clients : modèle de pertes attendues (IFRS 9)", True),
        ("IAS 2", ("391",), "à revoir", "Dépréciation de stocks : valeur nette de réalisation (IAS 2)", True),
        ("IFRS 10", ("251",), "à revoir", "Titres de participation : contrôle à qualifier (IFRS 10 / IAS 28 / IFRS 9)", True),
    ]

    def appliquer(masque, statut, motif):
        for i in res.index[masque]:
            if ordre[statut] < ordre[res.at[i, "statut"]]:
                res.at[i, "statut"] = statut
                res.at[i, "motif"] = motif

    for norme, prefixes, statut, motif, active in regles:
        if active and norme in normes:
            appliquer(res["compte"].str.startswith(prefixes) & (res["solde"] != 0), statut, motif)
    appliquer(res["compte"].str[:1] == "8", "à revoir", "Classe 8 (comptes de résultats) : solde de clôture, à ne pas cumuler avec les classes 6 et 7")
    appliquer(~res["compte"].str[:1].isin(list("12345678")), "à revoir", "Numéro hors des classes 1 à 8 du CGNC : à classer")
    return res


def compteurs_statuts(res):
    return {s: int((res["statut"] == s).sum()) for s in STATUTS}


def conformite(res):
    """Retourne (conformité en nombre de comptes, conformité en valeur), en %."""
    n = len(res)
    inchanges = res["statut"] == "inchangé"
    nombre = 100.0 * inchanges.sum() / n if n else 0.0
    total = res["solde"].abs().sum()
    valeur = 100.0 * res.loc[inchanges, "solde"].abs().sum() / total if total else 0.0
    return float(nombre), float(valeur)


def donut_html(compteurs):
    total = sum(compteurs.values())
    if not total:
        return '<div class="def">Aucun compte.</div>'
    r = 70
    c = 2 * math.pi * r
    arcs, decalage = "", 0.0
    for s in STATUTS:
        n = compteurs.get(s, 0)
        if n:
            lg = c * n / total
            arcs += (f'<circle cx="100" cy="100" r="{r}" fill="none" stroke="{COULEURS_STATUT[s]}" stroke-width="26" '
                     f'stroke-dasharray="{lg:.2f} {c - lg:.2f}" stroke-dashoffset="{-decalage:.2f}" transform="rotate(-90 100 100)"/>')
            decalage += lg
    legende = "".join(f'<div class="leg"><span class="dot" style="background:{COULEURS_STATUT[s]}"></span>{s}'
                      f'<b>{compteurs.get(s, 0)} <i style="font-style:normal;font-weight:500;color:#8A978F">· {100 * compteurs.get(s, 0) / total:.0f} %</i></b></div>' for s in STATUTS)
    return ('<div class="donut"><svg viewBox="0 0 200 200" width="190" height="190">'
            f'<circle cx="100" cy="100" r="{r}" fill="none" stroke="#D7EDD8" stroke-width="26"/>{arcs}'
            f'<text x="100" y="100" text-anchor="middle" font-size="28" font-weight="700" fill="#26322B">{total}</text>'
            '<text x="100" y="120" text-anchor="middle" font-size="11" fill="#8A978F">comptes</text></svg>'
            f'<div class="legs">{legende}</div></div>')


def badge_conformite(p):
    classe = "bv" if p >= 80 else ("bo" if p >= 50 else "br")
    return f'<span class="badge {classe}">{p:.0f} %</span>'


# ---------- graphiques (Altair) ----------
_FONT = "Plus Jakarta Sans, Segoe UI, system-ui, sans-serif"
NIV_COUL = {"Erreur": "#B94A48", "Retraitement": "#388E3C", "À vérifier": "#E65100", "Info": "#B7C4BA"}


def _style_alt(ch):
    return (ch.configure_view(strokeWidth=0)
            .configure_axis(labelColor="#5E6C63", titleColor="#5E6C63", gridColor="#D7EDD8", domainColor="#CFDCC6",
                            tickColor="#CFDCC6", labelFont=_FONT, titleFont=_FONT, labelFontSize=12,
                            titleFontSize=12, titleFontWeight="normal")
            .configure_legend(labelFont=_FONT, labelColor="#26322B", labelFontSize=12, orient="top", title=None,
                              symbolType="circle", symbolSize=110, padding=4)
            .configure(background="transparent"))


def _etiquettes(histo):
    out = []
    for i, e in enumerate(histo, 1):
        nom = Path(e["fichier"]).stem
        out.append(f"#{i} · " + (nom if len(nom) <= 22 else nom[:21] + "…"))
    return out


def gauge_html(p, couleur):
    p = max(0.0, min(100.0, float(p)))
    reperes = ""
    for v in (50, 80):
        a = math.pi * (1 - v / 100)
        x1, y1, x2, y2 = 100 + 66 * math.cos(a), 100 - 66 * math.sin(a), 100 + 94 * math.cos(a), 100 - 94 * math.sin(a)
        xt, yt = 100 + 106 * math.cos(a), 100 - 106 * math.sin(a)
        reperes += (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#BF360C" stroke-width="2"/>'
                    f'<text x="{xt:.1f}" y="{yt:.1f}" text-anchor="middle" font-size="9" fill="#8A978F">{v}</text>')
    arc = "M 20 100 A 80 80 0 0 1 180 100"
    return ('<div style="text-align:center"><svg viewBox="0 0 200 128" width="100%" style="max-width:340px">'
            f'<path d="{arc}" fill="none" stroke="#D7EDD8" stroke-width="16" stroke-linecap="round"/>'
            f'<path d="{arc}" fill="none" stroke="{couleur}" stroke-width="16" stroke-linecap="round" pathLength="100" '
            f'stroke-dasharray="{p:.1f} 100"/>{reperes}'
            f'<text x="100" y="92" text-anchor="middle" font-family="Plus Jakarta Sans,Segoe UI,Arial,sans-serif" font-size="36" font-weight="800" fill="#26322B">{p:.0f}<tspan font-size="18" fill="#8A978F"> %</tspan></text>'
            '<text x="100" y="114" text-anchor="middle" font-size="10" fill="#8A978F">des comptes sans retraitement</text></svg></div>')


def graph_evolution(histo):
    ordre = _etiquettes(histo)
    lignes = []
    for lab, e in zip(ordre, histo):
        lignes.append({"Fichier": lab, "Indicateur": "En nombre de comptes", "Conformité": e["conformite"]})
        lignes.append({"Fichier": lab, "Indicateur": "En valeur (MAD)", "Conformité": e["valeur"]})
    d = pd.DataFrame(lignes)
    x = alt.X("Fichier:N", sort=ordre, title=None, axis=alt.Axis(labelAngle=0, labelLimit=200, labelPadding=8))
    y = alt.Y("Conformité:Q", scale=alt.Scale(domain=[0, 108]), title="Comptes sans retraitement (%)", axis=alt.Axis(values=[0, 20, 40, 60, 80, 100]))
    off = alt.XOffset("Indicateur:N")
    coul = alt.Color("Indicateur:N", scale=alt.Scale(domain=["En nombre de comptes", "En valeur (MAD)"], range=["#66BB6A", "#E65100"]))
    base = alt.Chart(d)
    barres = base.mark_bar(cornerRadiusTopLeft=7, cornerRadiusTopRight=7).encode(
        x=x, y=y, xOffset=off, color=coul,
        tooltip=["Fichier", "Indicateur", alt.Tooltip("Conformité:Q", format=".1f")])
    texte = (base.transform_calculate(lab="format(datum['Conformité'], '.0f') + ' %'")
             .mark_text(dy=-9, fontWeight="bold", fontSize=12, color="#26322B")
             .encode(x=x, y=y, xOffset=off, text="lab:N"))
    seuil = alt.Chart(pd.DataFrame({"y": [80]})).mark_rule(strokeDash=[6, 5], color="#BF360C", strokeWidth=1.6).encode(y="y:Q")
    lseuil = (alt.Chart(pd.DataFrame({"y": [80]})).mark_text(align="left", dx=6, dy=-8, fontSize=11, color="#BF360C", fontWeight="bold")
              .encode(y="y:Q", x=alt.value(0), text=alt.value("Seuil de conformité 80 %")))
    return _style_alt(alt.layer(barres, texte, seuil, lseuil).properties(height=330))


def graph_statuts(histo):
    ordre = _etiquettes(histo)
    lignes = [{"Fichier": lab, "Statut": st_, "Comptes": int(e["statuts"].get(st_, 0)), "rang": STATUTS.index(st_)}
              for lab, e in zip(ordre, histo) for st_ in STATUTS]
    ch = alt.Chart(pd.DataFrame(lignes)).mark_bar(stroke="white", strokeWidth=2, cornerRadius=5).encode(
        y=alt.Y("Fichier:N", sort=ordre, title=None, axis=alt.Axis(labelLimit=240, labelPadding=8)),
        x=alt.X("Comptes:Q", stack="normalize", title="Part des comptes", axis=alt.Axis(format="%")),
        color=alt.Color("Statut:N", scale=alt.Scale(domain=STATUTS, range=[COULEURS_STATUT[k] for k in STATUTS]),
                        sort=STATUTS),
        order=alt.Order("rang:Q"),
        tooltip=["Fichier", "Statut", alt.Tooltip("Comptes:Q", title="Nombre de comptes")])
    return _style_alt(ch.properties(height=max(110, 62 * len(ordre) + 50)))


def graph_niveaux(cdf):
    d = pd.DataFrame({"Niveau": list(NIV_COUL), "Constats": [int((cdf["Niveau"] == k).sum()) for k in NIV_COUL]})
    base = alt.Chart(d).encode(
        y=alt.Y("Niveau:N", sort=list(NIV_COUL), title=None, axis=alt.Axis(labelFontSize=13, labelPadding=8)),
        x=alt.X("Constats:Q", title="Nombre de constats", axis=alt.Axis(tickMinStep=1)))
    barres = base.mark_bar(cornerRadiusTopRight=7, cornerRadiusBottomRight=7, size=26).encode(
        color=alt.Color("Niveau:N", legend=None, scale=alt.Scale(domain=list(NIV_COUL), range=list(NIV_COUL.values()))),
        tooltip=["Niveau", "Constats"])
    texte = base.mark_text(align="left", dx=7, fontWeight="bold", fontSize=13, color="#26322B").encode(text="Constats:Q")
    return _style_alt(alt.layer(barres, texte).properties(height=230))


def graph_normes(cdf):
    d = cdf[cdf["Niveau"] != "Info"]
    if d.empty:
        return None
    g = d.groupby(["Norme", "Niveau"]).size().reset_index(name="Constats")
    ordre = g.groupby("Norme")["Constats"].sum().sort_values(ascending=False).index.tolist()
    niv = [k for k in NIV_COUL if k != "Info"]
    ch = alt.Chart(g).mark_bar(stroke="white", strokeWidth=1.5, cornerRadius=4).encode(
        y=alt.Y("Norme:N", sort=ordre, title=None, axis=alt.Axis(labelLimit=200)),
        x=alt.X("Constats:Q", title="Constats à traiter (hors informations)", axis=alt.Axis(tickMinStep=1)),
        color=alt.Color("Niveau:N", sort=niv, scale=alt.Scale(domain=niv, range=[NIV_COUL[k] for k in niv])),
        tooltip=["Norme", "Niveau", "Constats"])
    return _style_alt(ch.properties(height=max(120, 34 * len(ordre) + 70)))


def graph_etat(etat):
    d = etat.copy()
    d["Côté"] = (["Actif", "Actif", "Passif et capitaux propres", "Passif et capitaux propres", "Passif et capitaux propres"] + [""] * len(d))[:len(d)]
    ordre = d["Poste"].tolist()
    base = alt.Chart(d).transform_calculate(lab="format(datum.Montant / 1000000, ',.1f') + ' M'").encode(
        y=alt.Y("Poste:N", sort=ordre, title=None, axis=alt.Axis(labelLimit=260, labelPadding=8)),
        x=alt.X("Montant:Q", title="Montant (MAD)", axis=alt.Axis(format="~s")))
    barres = base.mark_bar(cornerRadius=5, size=24).encode(
        color=alt.Color("Côté:N", scale=alt.Scale(domain=["Actif", "Passif et capitaux propres"], range=["#66BB6A", "#E65100"])),
        tooltip=["Poste", "Côté", alt.Tooltip("Montant:Q", format=",.0f")])
    pos = base.transform_filter("datum.Montant >= 0").mark_text(align="left", dx=6, fontSize=12, fontWeight="bold", color="#26322B").encode(text="lab:N")
    neg = base.transform_filter("datum.Montant < 0").mark_text(align="right", dx=-6, fontSize=12, fontWeight="bold", color="#26322B").encode(text="lab:N")
    return _style_alt(alt.layer(barres, pos, neg).properties(height=260))


# ============================ RÉFÉRENTIELS (CGNC, IAS/IFRS, IFRIC/SIC) ============================
def _charger_ref(nom, defaut):
    try:
        return json.loads(Path(__file__).with_name(nom).read_text(encoding="utf-8"))
    except Exception:
        return defaut


REF_CGNC = _charger_ref("referentiel_cgnc.json", {"comptes": {}})
REF_IFRS = _charger_ref("referentiel_ifrs.json", {"normes": [], "interpretations": []})
CGNC_COMPTES = REF_CGNC.get("comptes", {})
FICHES = {f["ref"]: f for f in REF_IFRS.get("normes", [])}
INTERPS = {i["ref"]: i for i in REF_IFRS.get("interpretations", [])}
LIEN_IFRS = REF_IFRS.get("lien_officiel", "https://www.ifrs.org/issued-standards/list-of-standards/")


def libelle_cgnc(code):
    """Libellé du compte au plan comptable CGNC, ou du poste parent le plus proche."""
    for k in range(len(code), 1, -1):
        if code[:k] in CGNC_COMPTES:
            return CGNC_COMPTES[code[:k]]
    return ""


# ============================ CONTRÔLE DE CONFORMITÉ AU CGNC (étape 1) ============================
SANS_CONTROLE_SENS = ("1119", "116", "118", "1199", "16", "6114", "6124", "6119", "6129", "6149", "7119", "7129", "713")
SENS_PARTICULIER = {
    "514": "Banque créditrice : à présenter en trésorerie passif (compte 554)",
    "516": "Caisse créditrice : anomalie de saisie ou d'encaissement non enregistré",
    "3421": "Client créditeur : à reclasser en « Clients créditeurs » (compte 4421)",
    "4411": "Fournisseur débiteur : à reclasser en « Fournisseurs débiteurs » (compte 3411)",
}


def sens_attendu(code):
    """D si le solde normal est débiteur, C s'il est créditeur, None si le sens peut varier."""
    if not code or code[0] not in "1234567" or code.startswith(SANS_CONTROLE_SENS):
        return None
    if code.startswith(("28", "29", "39", "59")) or code[0] in "14":
        return "C"
    if code[0] in "23":
        return "D"
    if code[0] == "5":
        return "D" if code.startswith("51") else "C"
    return "D" if code[0] == "6" else "C"


def controle_cgnc(df):
    """Audit de la balance au regard du CGNC : équilibre, existence des comptes, sens des soldes, cohérences."""
    r = []

    def add(controle, niveau, compte, constat, ref=""):
        r.append({"Contrôle": controle, "Niveau": niveau, "Compte": compte, "Constat": constat, "Référence CGNC": ref})

    ecart = df["debit"].sum() - df["credit"].sum()
    if abs(ecart) > 1:
        add("Équilibre", "Erreur", "—", f"Total débit ≠ total crédit (écart {m(ecart)})", "Principe de la partie double")
    comptes = [str(c) for c in df["compte"]]
    # 1. existence des comptes au plan
    if not CGNC_COMPTES:
        add("Plan comptable", "Info", "—", "Référentiel CGNC non chargé (fichier referentiel_cgnc.json absent) : existence des comptes non vérifiée")
    else:
        postes3 = {c[:3] for c in CGNC_COMPTES if len(c) >= 3}
        rub2 = {c[:2] for c in CGNC_COMPTES}
        subdivises = 0
        for code in dict.fromkeys(comptes):
            if not code.isdigit():
                add("Plan comptable", "Erreur", code, "Numéro de compte non numérique", "Classes 1 à 8 du plan comptable")
            elif code[0] in "09":
                continue
            elif code in CGNC_COMPTES:
                continue
            elif code[:3] in postes3 or (len(code) < 3 and code in rub2):
                subdivises += 1
            elif code[:2] in rub2:
                add("Plan comptable", "À vérifier", code, f"Poste {code[:3]} absent du plan comptable extrait : à confirmer", libelle_cgnc(code[:2]))
            else:
                add("Plan comptable", "Erreur", code, f"Rubrique {code[:2]} inexistante au CGNC", "Classes 1 à 8 du plan comptable")
        if subdivises:
            add("Plan comptable", "Info", "—", f"{subdivises} compte(s) subdivisé(s) au-delà du plan (autorisé : subdivisions propres à l'entreprise)")
    # 2. doublons et libellés
    for code in df.loc[df["compte"].duplicated(keep=False), "compte"].unique():
        add("Saisie", "À vérifier", code, "Compte présent plusieurs fois dans la balance")
    for code in df.loc[df["libelle"].fillna("").astype(str).str.strip() == "", "compte"]:
        add("Saisie", "Info", code, "Libellé manquant", libelle_cgnc(str(code)))
    # 3. sens normal des soldes
    for _, lg in df.iterrows():
        code, so = str(lg["compte"]), float(lg["solde"])
        sens = sens_attendu(code)
        if sens is None or abs(so) <= 1:
            continue
        if (sens == "D" and so < 0) or (sens == "C" and so > 0):
            special = next((t for p, t in SENS_PARTICULIER.items() if code.startswith(p)), None)
            motif = special or ("Solde créditeur inhabituel pour un compte d'actif ou de charge"
                                if sens == "D" else "Solde débiteur inhabituel pour un compte de passif ou de produit")
            add("Sens des soldes", "À vérifier", code, f"{motif} ({m(so)})", libelle_cgnc(code))
    # 4. cohérences entre comptes
    for brut, amort, nom in (("21", "281", "non-valeurs"), ("22", "282", "immobilisations incorporelles"),
                             ("23", "283", "immobilisations corporelles"), ("31", "391", "stocks")):
        if -solde(df, amort) > solde(df, brut) + 1 and (solde(df, brut) or solde(df, amort)):
            add("Cohérence", "Erreur", amort, f"Amortissements/provisions ({m(-solde(df, amort))}) supérieurs au brut des {nom} ({m(solde(df, brut))})",
                libelle_cgnc(amort))
    if (solde(df, "6") or solde(df, "7")) and (solde(df, "119") or solde(df, "8")):
        add("Cohérence", "À vérifier", "119 / 8", "Résultat présent à la fois dans les classes 6-7 et dans 119 ou la classe 8 : risque de double comptage",
            "Comptes de résultats (classe 8) et résultat net (119)")
    capital = -solde(df, "111")
    cp = -(solde(df, "11", "13") + solde(df, "6", "7"))
    if CGNC_COMPTES and not capital and any(c.startswith("11") for c in comptes):
        add("Cohérence", "À vérifier", "111", "Capital social ou personnel nul ou absent", libelle_cgnc("111"))
    if cp < 0:
        add("Cohérence", "Erreur", "11", f"Capitaux propres négatifs ({m(cp)})", libelle_cgnc("11"))
    elif capital > 0 and cp < capital / 4:
        add("Cohérence", "À vérifier", "11", f"Capitaux propres ({m(cp)}) inférieurs au quart du capital social ({m(capital)}) : règle des sociétés anonymes (loi 17-95) à vérifier",
            libelle_cgnc("11"))
    return pd.DataFrame(r, columns=["Contrôle", "Niveau", "Compte", "Constat", "Référence CGNC"])


def score_cgnc(df, cg):
    """Part des comptes sans anomalie (erreur ou à vérifier) au contrôle CGNC."""
    if df.empty:
        return 0.0
    graves = cg[cg["Niveau"].isin(["Erreur", "À vérifier"])]["Compte"].astype(str)
    comptes = {str(c) for c in df["compte"]}
    touches = {c for c in comptes if any(c == g or (g != "—" and "/" not in g and c.startswith(g)) for g in graves)}
    return 100.0 * (1 - len(touches) / len(comptes))


def cgnc_html(e):
    c = e.get("conformite_cgnc")
    return f'<div class="def">Comptes sans anomalie CGNC (étape 1) : {c:.0f} %</div>' if c is not None else ""


# ============================ FICHES NORMES ET INTERPRÉTATIONS ============================
def afficher_fiche(ref):
    f = FICHES.get(ref)
    if not f:
        st.info(f"Pas de fiche détaillée pour {ref}.")
        return
    st.markdown(f"**{f['ref']} — {f['titre']}**  \n*{f['statut']}*")
    st.markdown(f["resume"])
    st.markdown("**Points clés**  \n" + "  \n".join(f"• {p}" for p in f["points"]))
    st.markdown("**Écart avec le CGNC**  \n" + f["cgnc"])
    if f["comptes"]:
        st.markdown("**Comptes CGNC concernés**  \n" + "  \n".join(f"• {c} — {libelle_cgnc(c)}" for c in f["comptes"]))
    if f["interpretations"]:
        lignes = []
        for i in f["interpretations"]:
            it = INTERPS.get(i)
            lignes.append(f"• **{i}** — {it['titre']} : {it['objet']}" if it else f"• {i}")
        st.markdown("**Interprétations applicables (IFRIC / SIC)**  \n" + "  \n".join(lignes))
    st.markdown(f"[Texte officiel sur ifrs.org]({LIEN_IFRS})")



def charger_historique():
    try:
        return json.loads(FICHIER_HISTO.read_text(encoding="utf-8"))
    except Exception:
        return []


def sauver_historique(histo):
    try:
        FICHIER_HISTO.write_text(json.dumps(histo, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass  # dossier en lecture seule : l'historique reste en mémoire pour la session


# ============================ 4. CALCULATEURS IAS 12 et IAS 36 ============================
def calc_impots_differes(tab, taux):
    t = tab.dropna(subset=["Libellé"]).copy()
    for c in ("Valeur comptable IFRS", "Base fiscale"):
        t[c] = pd.to_numeric(t[c], errors="coerce").fillna(0.0)
    t["Différence temporaire"] = t.apply(
        lambda x: x["Valeur comptable IFRS"] - x["Base fiscale"] if x["Nature"] == "Actif"
        else x["Base fiscale"] - x["Valeur comptable IFRS"], axis=1)
    t["Impôt différé"] = t["Différence temporaire"] * taux
    t["Type"] = t["Impôt différé"].apply(
        lambda v: "Passif d'impôt différé" if v > 0 else ("Actif d'impôt différé" if v < 0 else "-"))
    return t


def base_ias12_depuis_balance(df):
    """Valeurs comptables lues dans la balance (nettes d'amortissements/dépréciations). La base fiscale est
    initialisée à la valeur comptable (aucune différence) : à corriger selon la comptabilité fiscale."""
    lignes = [
        ("Immobilisations corporelles (nettes)", "Actif", solde(df, "23", "283", "293")),
        ("Immobilisations incorporelles (nettes)", "Actif", solde(df, "22", "282", "292")),
        ("Stocks (nets de dépréciation)", "Actif", solde(df, "31", "391")),
        ("Clients et comptes rattachés (nets)", "Actif", solde(df, "342", "3942")),
        ("Provisions pour risques et charges", "Passif", -solde(df, "15", "45")),
    ]
    vals = [round(max(v, 0.0), 2) for _, _, v in lignes]
    return pd.DataFrame({"Libellé": [l for l, _, _ in lignes], "Nature": [n for _, n, _ in lignes],
                         "Valeur comptable IFRS": vals, "Base fiscale": list(vals)})


def valeurs_ias36_depuis_balance(df):
    """Valeur comptable = actif immobilisé net (classe 2) ; flux annuel indicatif = résultat net + dotations
    d'exploitation aux amortissements (6191 à 6193), à remplacer par vos prévisions."""
    vc = max(solde(df, "2"), 0.0)
    rnc = -solde(df, "6", "7")
    dotations = max(solde(df, "6191", "6192", "6193"), 0.0)
    return round(vc, 2), round(rnc + dotations, 2)


def valeur_utilite(flux, taux, g):
    vu = sum(f / (1 + taux) ** (i + 1) for i, f in enumerate(flux))
    if flux and taux > g:
        vu += flux[-1] * (1 + g) / (taux - g) / (1 + taux) ** len(flux)  # valeur terminale
    return vu





# ============================ 3c. NORMES SUPPLÉMENTAIRES ============================
def norme_ias2(df):
    r = []
    stocks = solde(df, "31")
    prov = -solde(df, "391")
    if stocks > 0:
        r.append(constat("IAS 2", "Info", f"Stocks inscrits au bilan : {m(stocks)}",
                         "Évaluer au plus bas du coût et de la valeur nette de réalisation (IAS 2.9)"))
        if prov == 0:
            r.append(constat("IAS 2", "À vérifier", "Aucune dépréciation de stocks (compte 391)",
                             "Comparer le coût à la valeur nette de réalisation, article par article"))
    return r


def norme_ias7(df):
    tres = solde(df, "51") + solde(df, "55")
    return [constat("IAS 7", "Info", f"Trésorerie à l'actif (comptes 51) : {m(solde(df, '51'))} ; trésorerie passif (comptes 55) : {m(-solde(df, '55'))}",
                    "Établir le tableau des flux (exploitation, investissement, financement) ; "
                    "rapprocher la trésorerie nette du TFT avec le bilan"),
            ] if tres else ([constat("IAS 7", "Info", "Tableau des flux de trésorerie obligatoire en IFRS", "")] if _a_bilan(df) else [])


def norme_ias8(df):
    if not _jeu_complet(df):
        return []
    return [constat("IAS 8", "Info", "Changements de méthodes et corrections d'erreurs",
                    "Appliquer rétrospectivement : retraiter les périodes comparatives et les capitaux propres d'ouverture")]


def norme_ias10(df):
    if not _jeu_complet(df):
        return []
    return [constat("IAS 10", "Info", "Événements postérieurs à la clôture",
                    "Distinguer événements donnant lieu à ajustement et événements sans ajustement (information en annexe)")]


def norme_ias19(df):
    r = []
    pers = solde(df, "617")
    prov = -solde(df, "1552", "4437")
    if pers > 0:
        r.append(constat("IAS 19", "À vérifier", f"Charges de personnel : {m(pers)}",
                         "Vérifier la provision pour congés payés, primes et avantages postérieurs à l'emploi (indemnités de départ)"))
    if prov == 0 and pers > 0:
        r.append(constat("IAS 19", "Retraitement", "Aucun engagement de retraite / indemnités de fin de carrière provisionné",
                         "Évaluer l'obligation par la méthode des unités de crédit projetées"))
    return r


def norme_ias20(df):
    sub = -solde(df, "716", "757", "131")
    if sub:
        return [constat("IAS 20", "À vérifier", f"Subventions publiques : {m(sub)}",
                        "Comptabiliser en résultat sur les périodes où les coûts correspondants sont constatés")]
    return []


def norme_ias21(df):
    r = []
    ec = solde(df, "271", "272", "171", "172")
    pg = -solde(df, "733", "633")
    if ec:
        r.append(constat("IAS 21", "Retraitement", f"Écarts de conversion (actif/passif) : {m(ec)}",
                         "Pas d'écart de conversion en IFRS : constater les gains/pertes de change en résultat (éléments monétaires)"))
    if pg:
        r.append(constat("IAS 21", "Info", f"Gains/pertes de change : {m(pg)}", ""))
    return r


def norme_ias23(df):
    ch = solde(df, "6311")
    if ch > 0:
        return [constat("IAS 23", "À vérifier", f"Intérêts des emprunts : {m(ch)}",
                        "Incorporer au coût de l'actif qualifié les intérêts liés à sa construction (IAS 23.8)")]
    return []


def norme_ias24(df):
    return [constat("IAS 24", "À vérifier", f"Comptes d'associés / apparentés : créances {m(solde(df, '346'))}, dettes {m(-solde(df, '446'))}",
                    "Identifier les parties liées, leurs transactions et soldes ; informations en annexe")] if solde(df, "346", "446") else \
        ([constat("IAS 24", "Info", "Informations sur les parties liées à fournir en annexe", "")] if _jeu_complet(df) else [])


def norme_ias32_ifrs9(df):
    r = []
    clients = solde(df, "3421")
    prov = -solde(df, "3942")
    if clients > 0:
        taux = prov / clients
        r.append(constat("IFRS 9", "Info", f"Dépréciation clients / créances = {taux:.1%}",
                         "Modèle de pertes de crédit attendues (ECL) : matrice de provisionnement dès la comptabilisation"))
        if prov == 0:
            r.append(constat("IFRS 9", "Retraitement", f"Aucune dépréciation sur {m(clients)} de créances clients",
                             "Calculer les pertes attendues à 12 mois ou à maturité selon les stades"))
    dette = -solde(df, "141", "1481")
    if dette:
        r.append(constat("IFRS 9", "À vérifier", f"Emprunts obligataires et bancaires : {m(dette)}",
                         "Évaluation au coût amorti (taux d'intérêt effectif), frais d'émission inclus"))
    return r


def norme_ias33(df):
    if not _jeu_complet(df) or solde(df, "111") == 0:
        return []
    return [constat("IAS 33", "Info", "Résultat par action (entités cotées ou en cours de cotation)",
                    "Résultat net part du groupe / nombre moyen pondéré d'actions ordinaires ; calculer aussi le dilué")]


def norme_ias37(df):
    r = []
    prov = -solde(df, "15", "45")
    if prov:
        r.append(constat("IAS 37", "À vérifier", f"Provisions pour risques et charges (comptes 15 et 45) : {m(prov)}",
                         "Conserver uniquement si obligation actuelle, sortie probable et estimation fiable (IAS 37.14)"))
    elif _a_bilan(df):
        r.append(constat("IAS 37", "Info", "Aucune provision pour risques et charges",
                         "Rechercher litiges, garanties, restructurations ; passifs éventuels en annexe"))
    return r


def norme_ias38(df):
    inc = solde(df, "22") - solde(df, "223")
    r = []
    if inc > 0:
        r.append(constat("IAS 38", "À vérifier", f"Immobilisations incorporelles : {m(inc)}",
                         "Critères de comptabilisation (identifiable, contrôlé, avantages futurs) ; frais de recherche en charges"))
    return r


def norme_ias40(df):
    r = []
    terrains_nus = solde(df, "2311")
    revenus = -solde(df, "7182")
    if terrains_nus > 0:
        r.append(constat("IAS 40", "À vérifier", f"Terrains nus (compte 2311) : {m(terrains_nus)}",
                         "Détenus pour valoriser le capital ? Alors immeubles de placement : choisir entre le coût et la juste valeur"))
    if revenus:
        r.append(constat("IAS 40", "À vérifier", f"Revenus d'immeubles non affectés à l'exploitation (compte 7182) : {m(revenus)}",
                         "Immeubles loués à des tiers : immeubles de placement (coût ou juste valeur)"))
    return r


def norme_ifrs10(df):
    tit, div = solde(df, "251"), -solde(df, "732")
    r = []
    if tit > 0:
        r.append(constat("IFRS 10", "À vérifier", f"Titres de participation (compte 251) : {m(tit)}",
                         "Qualifier chaque participation : contrôle (pouvoir, rendements variables, lien entre les deux) -> consolider par "
                         "intégration globale (onglet Consolidation) ; influence notable -> IAS 28 ; sinon IFRS 9"))
    if div > 0:
        r.append(constat("IFRS 10", "Info", f"Dividendes reçus (compte 732) : {m(div)}",
                         "Dividendes de filiales : à éliminer du résultat consolidé (IFRS 10.B86)"))
    return r


def norme_ifrs3(df):
    fc = solde(df, "223")
    if fc > 0:
        return [constat("IFRS 3", "À vérifier", f"Fonds commercial : {m(fc)}",
                        "Distinguer le goodwill d'acquisition (non amorti, test IAS 36) du fonds commercial acheté (actif incorporel)")]
    return []


def norme_ifrs5(df):
    ces = solde(df, "6513") - solde(df, "7513")  # valeur nette d'amortissement des immobilisations cédées / produits de cession
    if not (solde(df, "6513") or solde(df, "7513")):
        return []  # aucune cession dans les comptes : IFRS 5 ne concerne pas ce document
    return [constat("IFRS 5", "À vérifier", f"Cessions d'immobilisations constatées (comptes 6513 / 7513, solde {m(ces)})",
                    "Vérifier les actifs dont la vente est décidée à la clôture : présentation séparée au plus bas de la valeur comptable et de la juste valeur nette, arrêt de l'amortissement")]


def norme_ifrs7(df):
    if not _jeu_complet(df):
        return []
    return [constat("IFRS 7", "Info", "Informations sur les instruments financiers",
                    "Risques de crédit, de liquidité et de marché : informations qualitatives et quantitatives en annexe")]


def norme_ifrs8(df):
    if not _jeu_complet(df):
        return []
    return [constat("IFRS 8", "Info", "Information sectorielle", "Entités cotées : résultat par secteur opérationnel")]


def norme_ifrs16(df):
    r = []
    cb = solde(df, "6132", "6131")
    if cb > 0:
        r.append(constat("IFRS 16", "Retraitement", f"Loyers et redevances de location : {m(cb)}",
                         "Comptabiliser un droit d'utilisation et une dette de loyer (valeur actualisée des loyers) ; "
                         "remplacer la charge de loyer par amortissement + intérêts"))
    return r


# Catalogue complet : (titre, description). Les normes avec contrôle automatique figurent dans NORMES.
CATALOGUE = {
    "IAS 1": ("Présentation des états financiers", "Structure et contenu minimal des états financiers."),
    "IAS 2": ("Stocks", "Évaluation au plus bas du coût et de la valeur nette de réalisation."),
    "IAS 7": ("Tableau des flux de trésorerie", "Flux d'exploitation, d'investissement et de financement."),
    "IAS 8": ("Méthodes comptables, estimations et erreurs", "Changements de méthodes, d'estimations et corrections d'erreurs."),
    "IAS 10": ("Événements postérieurs à la clôture", "Événements avec ou sans ajustement des états financiers."),
    "IAS 12": ("Impôts sur le résultat", "Impôts exigibles et différés."),
    "IAS 16": ("Immobilisations corporelles", "Comptabilisation, amortissement par composants."),
    "IAS 19": ("Avantages du personnel", "Avantages à court terme, postérieurs à l'emploi, indemnités de fin de contrat."),
    "IAS 20": ("Subventions publiques", "Comptabilisation et information sur les aides publiques."),
    "IAS 21": ("Effets des variations des cours des monnaies étrangères", "Conversion des transactions et des états financiers."),
    "IAS 23": ("Coûts d'emprunt", "Incorporation des intérêts au coût des actifs qualifiés."),
    "IAS 24": ("Information sur les parties liées", "Transactions et soldes avec les parties liées."),
    "IAS 26": ("Régimes de retraite", "États financiers des régimes de retraite (hors champ des entreprises usuelles)."),
    "IAS 27": ("États financiers individuels", "Comptabilisation des participations dans les comptes individuels."),
    "IAS 28": ("Participations dans des entreprises associées et coentreprises", "Mise en équivalence."),
    "IAS 29": ("Économies hyperinflationnistes", "Retraitement des états financiers en cas d'hyperinflation."),
    "IAS 32": ("Instruments financiers : présentation", "Distinction dette / capitaux propres, compensation."),
    "IAS 33": ("Résultat par action", "Résultat de base et dilué par action."),
    "IAS 34": ("Information financière intermédiaire", "Contenu des comptes semestriels et trimestriels."),
    "IAS 36": ("Dépréciation d'actifs", "Test de perte de valeur : valeur recouvrable."),
    "IAS 37": ("Provisions, passifs et actifs éventuels", "Obligation actuelle, sortie probable, estimation fiable."),
    "IAS 38": ("Immobilisations incorporelles", "Critères de comptabilisation, frais de développement."),
    "IAS 40": ("Immeubles de placement", "Modèle du coût ou de la juste valeur."),
    "IAS 41": ("Agriculture", "Actifs biologiques évalués à la juste valeur."),
    "IFRS 1": ("Première adoption des IFRS", "Bilan d'ouverture IFRS, exemptions et exceptions."),
    "IFRS 2": ("Paiement fondé sur des actions", "Charges liées aux options et actions attribuées."),
    "IFRS 3": ("Regroupements d'entreprises", "Méthode de l'acquisition, goodwill."),
    "IFRS 4 / IFRS 17": ("Contrats d'assurance", "Comptabilisation des contrats d'assurance."),
    "IFRS 5": ("Actifs détenus en vue de la vente", "Présentation séparée, activités abandonnées."),
    "IFRS 6": ("Prospection et évaluation de ressources minérales", "Dépenses d'exploration."),
    "IFRS 7": ("Instruments financiers : informations à fournir", "Risques financiers et annexe."),
    "IFRS 8": ("Secteurs opérationnels", "Information sectorielle."),
    "IFRS 9": ("Instruments financiers", "Classement, dépréciation (ECL) et couverture."),
    "IFRS 10": ("États financiers consolidés", "Contrôle et périmètre de consolidation."),
    "IFRS 11": ("Partenariats", "Activités conjointes et coentreprises."),
    "IFRS 12": ("Informations sur les intérêts détenus dans d'autres entités", "Annexe de consolidation."),
    "IFRS 13": ("Évaluation de la juste valeur", "Hiérarchie des niveaux de juste valeur."),
    "IFRS 14": ("Comptes de report réglementaires", "Activités à tarifs réglementés."),
    "IFRS 15": ("Produits des contrats avec les clients", "Modèle en cinq étapes."),
    "IFRS 16": ("Contrats de location", "Droit d'utilisation et dette locative."),
    "IFRS 18": ("Présentation et informations dans les états financiers", "Remplace IAS 1 : catégories du résultat et mesures de performance."),
    "IFRS 19": ("Filiales sans obligation d'information publique", "Informations réduites pour certaines filiales."),
}

NORMES.update({
    "IAS 2": norme_ias2, "IAS 7": norme_ias7, "IAS 8": norme_ias8, "IAS 10": norme_ias10,
    "IAS 19": norme_ias19, "IAS 20": norme_ias20, "IAS 21": norme_ias21, "IAS 23": norme_ias23,
    "IAS 24": norme_ias24, "IFRS 9": norme_ias32_ifrs9, "IAS 33": norme_ias33, "IAS 37": norme_ias37,
    "IAS 38": norme_ias38, "IAS 40": norme_ias40, "IFRS 3": norme_ifrs3, "IFRS 10": norme_ifrs10, "IFRS 5": norme_ifrs5,
    "IFRS 7": norme_ifrs7, "IFRS 8": norme_ifrs8, "IFRS 16": norme_ifrs16,
})
NORMES = dict(sorted(NORMES.items(), key=lambda kv: (kv[0][:3] != "IAS", int(re.search(r"\d+", kv[0]).group()))))
N_RAPPEL = sum(1 for k_ in NORMES if cn.META.get(k_, (None,))[0] == cn.RAPPEL)   # rappels d'annexe : aucun calcul
N_CTRL = len(NORMES) - N_RAPPEL                                                    # normes dont une règle examine réellement les comptes
_deja = {t.strip() for k in CATALOGUE for t in k.split("/")}
for _r, _f in FICHES.items():
    if _r not in _deja:
        CATALOGUE[_r] = (_f["titre"], _f["resume"].split(". ")[0].rstrip(".") + ".")



# ============================ 4b. CONTRÔLE FISCAL (résultat comptable -> résultat fiscal -> IS) ============================
LIGNES_REINT = ["Amendes et pénalités", "Dons et cadeaux non déductibles", "Amortissements excédentaires",
                "Provisions non déductibles"]
# NB : l'IS comptabilisé (compte 67) est déjà réintégré automatiquement dans « Résultat comptable avant impôt ».
LIGNES_DEDUC = ["Produits de participation (dividendes)", "Plus-values exonérées", "Reprises de provisions non déductibles",
                "Amortissements déductibles non comptabilisés"]


def tableau_fiscal_vide(lignes):
    return pd.DataFrame({"Libellé": lignes, "Montant": [0.0] * len(lignes)})


# Comptes CGNC utilisés pour PRÉ-REMPLIR certaines lignes depuis la balance importée (modifiables à la main ensuite).
# Adaptez ces numéros à votre plan comptable si besoin ; les autres lignes restent à saisir manuellement.
COMPTES_AUTO_REINT = {"Amendes et pénalités": ("6583",),
                      "Dons et cadeaux non déductibles": ("6586", "61462", "61447"),
                      "Provisions non déductibles": ("6195", "6393", "6595")}
COMPTES_AUTO_DEDUC = {"Produits de participation (dividendes)": ("7321", "732"),
                      "Reprises de provisions non déductibles": ("7195", "7393", "7595")}


def tableau_fiscal_prerempli(lignes, df, comptes_auto, produits=False):
    """Tableau Libellé / Montant dont certaines lignes sont lues dans la balance (charges : solde débiteur ;
    produits : solde créditeur). Pour chaque ligne, on essaie les comptes du plus détaillé au plus agrégé
    (les états de synthèse PDF ne contiennent que des comptes à 3 chiffres) et on s'arrête au premier trouvé,
    pour éviter tout double comptage."""
    montants = []
    for lib in lignes:
        prefixes = comptes_auto.get(lib)
        total = 0.0
        if prefixes:
            for p in sorted(prefixes, key=len, reverse=True):
                masque = df["compte"].str.startswith(p)
                if masque.any():
                    s_ = float(df.loc[masque, "solde"].sum())
                    total = max(-s_ if produits else s_, 0.0)
                    break
        montants.append(round(total, 2))
    return pd.DataFrame({"Libellé": lignes, "Montant": montants})


def pistes_fiscales(df):
    """Comptes à examiner pour la liasse fiscale (montants lus dans le fichier importé, à analyser, non confirmés)."""
    pistes = [("Dotations d'exploitation (619)", ("619",), False), ("Dotations financières (639)", ("639",), False),
              ("Dotations non courantes (659)", ("659",), False), ("Autres charges non courantes (658)", ("658",), False),
              ("Reprises d'exploitation (719)", ("719",), True), ("Reprises financières (739)", ("739",), True),
              ("Reprises non courantes (759)", ("759",), True), ("Produits des titres de participation (732)", ("732",), True)]
    lignes = []
    for lib, pre, produit in pistes:
        s_ = solde(df, *pre)
        v = max(-s_ if produit else s_, 0.0)
        if v:
            lignes.append({"Compte à examiner": lib, "Montant (MAD)": round(v, 2)})
    return pd.DataFrame(lignes, columns=["Compte à examiner", "Montant (MAD)"])


# Paramètres fiscaux : définis, référencés et datés dans parametres_fiscaux.py (art. 19-I, 144 et 12 du CGI), avec leur niveau de vérification.
TAUX_IS_NORMAL, TAUX_IS_GRANDE, TAUX_IS_FINANCE = pf.valeur("IS_NORMAL"), pf.valeur("IS_GRANDE"), pf.valeur("IS_FINANCE")
SEUIL_TAUX_35 = pf.valeur("SEUIL_35")
CM_TAUX, CM_TAUX_REDUIT, CM_MINIMUM = pf.valeur("CM_TAUX"), pf.valeur("CM_TAUX_REDUIT"), pf.valeur("CM_MINIMUM")
CATEGORIES_IS = {
    "ordinaire": "Société ordinaire : 20 % (35 % si bénéfice net ≥ 100 M MAD)",
    "credit": "Établissement de crédit, assurance, Bank Al-Maghrib, CDG : 40 %",
    "libre": "Autre taux (exercice avant 2026, régime particulier)",
}
# Base de la cotisation minimale (art. 144-I-B), comptes CGNC à vérifier avec votre plan comptable :
# CA (711, 712), subventions d'exploitation (716), autres produits d'exploitation (718),
# produits financiers hors reprises (731, 732, 733), subventions d'équilibre et autres produits non courants (756, 758).
COMPTES_BASE_CM = ("711", "712", "716", "718", "732", "733", "738", "756", "758")


def calc_fiscal(df, reint, deduc, deficits, categorie="ordinaire", exclu_35=False, taux_libre=None,
                exonere_cm=False, ventes_taux_reduit=0.0, exercice=None):
    """Passage du résultat comptable au résultat fiscal, puis IS (art. 19) et cotisation minimale (art. 144)."""
    impot_compta = solde(df, "67")
    rnc = -solde(df, "6", "7")  # résultat net comptable
    rcav = rnc + impot_compta  # résultat comptable avant impôt
    tr = pd.to_numeric(reint["Montant"], errors="coerce").fillna(0.0).sum()
    td = pd.to_numeric(deduc["Montant"], errors="coerce").fillna(0.0).sum()
    rf_brut = rcav + tr - td
    deficit_imputable = min(max(rf_brut, 0.0), max(deficits, 0.0))
    rf_net = rf_brut - deficit_imputable if rf_brut > 0 else rf_brut
    NAN = float("nan")
    taux_is, motif_taux = pf.taux_is(exercice, categorie, rf_net, exclu_35, taux_libre)   # None : taux non déterminé, rien n'est supposé
    is_calcule = taux_is is not None
    is_theorique = max(rf_net, 0.0) * taux_is if is_calcule else NAN
    ca = -solde(df, "711", "712")
    base_cm = max(-solde(df, *COMPTES_BASE_CM), 0.0)
    reduit = min(max(ventes_taux_reduit, 0.0), base_cm)
    cm_ok, motif_cm = pf.cotisation_minimale_applicable(exercice)
    cm_calcule = (base_cm - reduit) * CM_TAUX + reduit * CM_TAUX_REDUIT
    if exonere_cm:
        cm = 0.0
    elif cm_ok:
        cm = max(cm_calcule, CM_MINIMUM)
    else:
        cm = NAN
    cm_connue = exonere_cm or cm_ok
    is_du = max(is_theorique, cm) if (is_calcule and cm_connue) else NAN
    return {"rnc": rnc, "impot_compta": impot_compta, "rcav": rcav, "reintegrations": tr, "deductions": td,
            "rf_brut": rf_brut, "deficit_impute": deficit_imputable, "rf_net": rf_net, "is_theorique": is_theorique,
            "base_cm": base_cm, "cm": cm, "is_du": is_du, "ecart_is": (is_du - impot_compta) if is_du == is_du else NAN,
            "taux_is": taux_is if is_calcule else NAN, "ca": ca, "categorie": categorie, "exonere_cm": exonere_cm,
            "exercice": exercice, "is_calcule": bool(is_calcule and cm_connue), "motif_taux": motif_taux, "motif_cm": motif_cm}


def tableau_passage(f):
    return pd.DataFrame([
        ("Résultat net comptable", f["rnc"]),
        ("+ Impôt sur les résultats comptabilisé (compte 67)", f["impot_compta"]),
        ("= Résultat comptable avant impôt", f["rcav"]),
        ("+ Réintégrations fiscales", f["reintegrations"]),
        ("- Déductions fiscales", f["deductions"]),
        ("= Résultat fiscal brut", f["rf_brut"]),
        ("- Déficits antérieurs imputés", f["deficit_impute"]),
        ("= Résultat fiscal net (base imposable)", f["rf_net"]),
        ((f"IS théorique au taux de {f['taux_is'] * 100:g} %" if f["taux_is"] == f["taux_is"] else "IS théorique (taux non déterminé)"), f["is_theorique"]),
        ("Base de la cotisation minimale (art. 144-I-B)", f["base_cm"]),
        ("Cotisation minimale" + (" (exonérée : 36 premiers mois)" if f.get("exonere_cm") else ""), f["cm"]),
        ("IS dû (le plus élevé des deux)", f["is_du"]),
        ("Écart avec l'IS comptabilisé", f["ecart_is"]),
    ], columns=["Ligne", "Montant (MAD)"])


def constats_fiscaux(f):
    r = []
    if not f.get("is_calcule", True):
        r.append(constat("Contrôle fiscal", "À vérifier", "IS dû non calculé : " + f.get("motif_taux", "") + ((" " + f.get("motif_cm", "")) if f["cm"] != f["cm"] else ""),
                         "Renseigner l'exercice et, pour un exercice antérieur à 2026, le taux applicable (paramètres dans le tableau « Règles appliquées »)"))
        if f["rf_brut"] < 0:
            r.append(constat("Contrôle fiscal", "Info", f"Résultat fiscal déficitaire : {m(f['rf_brut'])}", "Déficit reportable (CGI art. 12) : à suivre dans le tableau des déficits"))
        return r
    if abs(f["ecart_is"]) > 1:
        sens = "insuffisant" if f["ecart_is"] > 0 else "excédentaire"
        r.append(constat("Contrôle fiscal", "À vérifier",
                         f"IS comptabilisé {m(f['impot_compta'])} ≠ IS dû {m(f['is_du'])} (provision {sens}, écart {m(f['ecart_is'])})",
                         "Ajuster la charge d'impôt exigible ou justifier l'écart (réintégrations, déficits, cotisation minimale)"))
    else:
        r.append(constat("Contrôle fiscal", "Info", "IS comptabilisé cohérent avec l'IS calculé", ""))
    r.append(constat("Contrôle fiscal", "Info", f"Taux d'IS appliqué : {f['taux_is'] * 100:g} % — {f['motif_taux']}", ""))
    if f["taux_is"] == TAUX_IS_GRANDE and f.get("categorie") == "ordinaire":
        r.append(constat("Contrôle fiscal", "À vérifier", "Bénéfice net ≥ 100 M MAD : taux de 35 %",
                         "Vérifier les exclusions de l'art. 19-I-B (CFC, zones d'accélération industrielle, convention d'investissement)"))
    if f.get("exonere_cm"):
        r.append(constat("Contrôle fiscal", "À vérifier", "Cotisation minimale écartée (36 premiers mois d'exploitation)",
                         "L'exonération cesse au plus tard 60 mois après la constitution (art. 144-I-C-1°)"))
    elif f["cm"] > f["is_theorique"]:
        r.append(constat("Contrôle fiscal", "Info", "La cotisation minimale est supérieure à l'IS théorique : elle s'applique", ""))
    if f["rf_brut"] < 0:
        r.append(constat("Contrôle fiscal", "Info", f"Résultat fiscal déficitaire : {m(f['rf_brut'])}",
                         "Déficit reportable (règles de la loi de finances) : à suivre dans le tableau des déficits"))
    return r


# ============================ 4e. COMPTES CONSOLIDÉS (présentation du 2e PDF + retraitements) ============================
# Le PDF « comptes consolidés » reprend les comptes sociaux dans une présentation groupe (en milliers de dirhams) après
# retraitements de consolidation. Ici : même présentation, calculée depuis les comptes importés.
def conso_disponible(inv, df=None):
    det = (inv or {}).get("detectes") or {}
    d = set()
    if not det or "balance" in det:
        if df is not None and not df.empty:
            c = df["compte"].astype(str)
            a = c.str.startswith(("2", "3", "5")).any()
            p = c.str.startswith(("1", "4", "55")).any()
            r = c.str.startswith(("6", "7")).any()
            d = {k for k, ok in (("actif", a), ("passif", p), ("cpc", r)) if ok}
            if a and p:
                d.add("retr")
            return d
        return {"actif", "passif", "cpc", "retr"}
    # états de synthèse détectés (sans balance) : seuls les états présents dans les documents sont restitués
    cor = {"bilan_actif": "actif", "bilan_passif": "passif", "cpc": "cpc"}
    d = {cor[k] for k, v in det.items() if k in cor and v}
    if {"actif", "passif"} <= d:
        d.add("retr")  # les retraitements (non-valeurs, provisions réglementées) exigent l'actif et le passif
    return d

def etats_consolides(df, taux_id=0.20, nb_actions=0, etats=None):
    """Retourne un dict : actif, passif, cpc (DataFrames Poste / Montant en milliers de MAD / Type), retraitements, ecart, notes, dispo."""
    etats = set(etats) if etats is not None else {"actif", "passif", "cpc", "retr"}
    s = lambda *p: solde(df, *p)
    # ---- retraitements de consolidation (les mêmes familles que la note « Principaux retraitements » du PDF consolidé) ----
    nonval = max(s("21", "281"), 0.0)                 # frais préliminaires et charges à répartir (actifs en non-valeur)
    preg = max(-s("135"), 0.0)                        # provisions réglementées (écritures purement fiscales)
    id_nonval, id_preg = nonval * taux_id, -preg * taux_id
    cp_nonval, cp_preg = -nonval * (1 - taux_id), preg * (1 - taux_id)
    id_net = id_nonval + id_preg                      # >0 : impôt différé actif ; <0 : impôt différé passif
    retr = pd.DataFrame([
        {"Retraitement": "Actifs en non-valeur (comptes 21 / 281) : éliminés", "Base (MAD)": nonval,
         "Effet sur les réserves (MAD)": cp_nonval, "Impôt différé (MAD)": id_nonval},
        {"Retraitement": "Provisions réglementées (compte 135) : éliminées et reclassées en réserves", "Base (MAD)": preg,
         "Effet sur les réserves (MAD)": cp_preg, "Impôt différé (MAD)": id_preg}])
    # ---- bilan actif (les classes 2, 3 et 5 sont toutes reprises : le solde de chaque classe est le total de ses lignes) ----
    incorp = s("22", "282", "292")
    corp = s("23", "283", "293")
    financ = s("2") - nonval - incorp - corp
    stocks = s("31", "391")
    clients = s("342", "3942")
    vmp = s("35", "395")
    tres_passif = max(-s("55"), 0.0)
    dispo = s("5") + tres_passif                      # classe 5 hors trésorerie passif (compte 55)
    autres_creances = s("3") - stocks - clients - vmp
    dta = max(id_net, 0.0)
    immo = incorp + corp + financ + dta
    circ = stocks + clients + autres_creances + vmp + dispo
    total_actif = immo + circ
    # ---- compte de résultat ----
    ca = -s("711", "712")
    autres_prod = -s("71") - ca
    achats = -s("611", "612", "613", "614", "615")
    perso, autres_ch, impt_tx, dot = -s("617"), -s("618"), -s("616"), -s("619")
    r_expl = ca + autres_prod + achats + perso + autres_ch + impt_tx + dot
    fin = -s("63", "73")
    r_courant = r_expl + fin
    non_cour = -s("65", "75")
    impots = -s("67")
    r_net_int = r_courant + non_cour + impots
    r_net = -s("6", "7") if df["compte"].str.startswith(("6", "7")).any() else -s("119")
    ajust = r_net - r_net_int                         # comptes de résultat hors rubriques ci-dessus (rare) : intégré au non courant
    non_cour += ajust
    r_courant_f = r_courant
    r_net_int = r_net
    minor = 0.0
    r_groupe = r_net - minor
    # ---- bilan passif ----
    capital, primes = -s("111"), -s("112")
    reserves = -s("113", "114", "115", "116", "118") + cp_nonval + cp_preg
    autres_cp = -s("131")
    cp = capital + primes + reserves + r_groupe + autres_cp
    prov = -s("15")
    emprunts = -s("14")
    fourn = -s("441")
    autres_dettes = -s("4") - fourn + (-s("16", "17")) + (-id_net if id_net < 0 else 0.0)
    dettes = emprunts + fourn + autres_dettes + tres_passif
    total_passif = cp + minor + prov + dettes
    k = lambda x: round(x / 1000.0)
    L, T, G = "ligne", "sous-total", "total"
    actif = pd.DataFrame([
        ("Actif immobilisé", k(immo), T), ("Goodwill (écarts d'acquisition)", 0, L), ("Immobilisations incorporelles", k(incorp), L),
        ("Immobilisations corporelles", k(corp), L), ("Immobilisations financières", k(financ), L),
        ("Impôt différé actif", k(dta), L), ("Titres mis en équivalence", 0, L),
        ("Actif circulant", k(circ), T), ("Stocks et en-cours", k(stocks), L), ("Clients et comptes rattachés", k(clients), L),
        ("Autres créances et comptes de régularisation", k(autres_creances), L), ("Valeurs mobilières de placement", k(vmp), L),
        ("Disponibilités", k(dispo), L), ("TOTAL ACTIF", k(total_actif), G)], columns=["Poste", "Montant", "Type"])
    passif = pd.DataFrame([
        ("Capitaux propres (part du groupe)", k(cp), T), ("Capital", k(capital), L), ("Primes d'émission et de fusion", k(primes), L),
        ("Réserves", k(reserves), L), ("Résultat net part du groupe", k(r_groupe), L), ("Autres", k(autres_cp), L),
        ("Intérêts minoritaires", k(minor), T), ("Provisions pour risques et charges", k(prov), T), ("Dettes", k(dettes), T),
        ("Emprunts et dettes financières", k(emprunts), L), ("Fournisseurs et comptes rattachés", k(fourn), L),
        ("Autres dettes et comptes de régularisation", k(autres_dettes), L), ("Trésorerie - Passif", k(tres_passif), L),
        ("TOTAL PASSIF", k(total_passif), G)], columns=["Poste", "Montant", "Type"])
    lignes_cpc = [
        ("Chiffre d'affaires", k(ca), L), ("Autres produits d'exploitation", k(autres_prod), L), ("Achats consommés", k(achats), L),
        ("Charges de personnel", k(perso), L), ("Autres charges d'exploitation", k(autres_ch), L), ("Impôts et taxes", k(impt_tx), L),
        ("Dotations d'exploitation", k(dot), L), ("Résultat d'exploitation", k(r_expl), T),
        ("Charges et produits financiers", k(fin), L), ("Résultat courant", k(r_courant_f), T),
        ("Charges et produits non-courants", k(non_cour), L), ("Impôts sur les résultats", k(impots), L),
        ("Résultat net des entreprises intégrées", k(r_net_int), T),
        ("Quote-part dans les résultats des entreprises mises en équivalence", 0, L),
        ("Dotations aux amortissements des écarts d'acquisition", 0, L),
        ("Résultat net de l'ensemble consolidé", k(r_net), T), ("Résultat minoritaire", k(-minor), L),
        ("Résultat net part Groupe", k(r_groupe), G)]
    if nb_actions and nb_actions > 0:
        lignes_cpc += [("Résultat net consolidé par action (MAD)", round(r_groupe / nb_actions), L), ("Nombre d'actions", int(nb_actions), L)]
    cpc = pd.DataFrame(lignes_cpc, columns=["Poste", "Montant", "Type"])
    notes = ["Montants en milliers de dirhams (MAD), après retraitements de consolidation."]
    if {"actif", "passif"} & etats:
        notes.append("Comptes sociaux retraités, et non comptes consolidés de groupe : périmètre limité à la société importée, sans filiale, "
                     "donc goodwill, titres mis en équivalence et intérêts ne donnant pas le contrôle à zéro. Pour un groupe, utilisez l'onglet "
                     "« Consolidation » avec les comptes de la mère et de chaque filiale.")
    if "retr" in etats:
        notes.append("Les retraitements des actifs en non-valeur et des provisions réglementées sont portés en réserves nettes d'impôt "
                     f"différé au taux de {taux_id:.0%} ; la part de l'exercice (dotations et reprises) ne peut être isolée sans la "
                     "balance de l'exercice précédent.")
    notes.append("Seul l'exercice importé est restitué : les données de l'exercice précédent ne figurent pas dans le fichier.")
    ecart = k(total_actif) - k(total_passif) if {"actif", "passif"} <= etats else 0
    return {"actif": actif, "passif": passif, "cpc": cpc, "retraitements": retr,
            "ecart": ecart, "notes": notes, "dispo": etats}


def conso_vers_texte(tab):
    """Montants formatés pour l'affichage (espace entre les milliers, '-' pour zéro)."""
    t = tab.copy()
    t["Montant"] = t["Montant"].map(lambda v: "-" if v == 0 else f"{v:,.0f}".replace(",", " "))
    return t


# ============================ 4c. RAPPORT PDF ============================
def rapport_pdf(df, constats_df, etat, res_statuts, conf_n, conf_v, fiscal=None, dt=None, ugt=None, fichier="", conso=None, statut=None):
    """Rapport synthétique (PDF, A4 portrait) : page de garde, statut des comptes, constats, état de situation, consolidation, fiscalité, IAS 12 / IAS 36."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.platypus import PageBreak, Paragraph, Spacer

    st_ = cp.styles(9)
    nor, pet = st_["p"], st_["petit"]
    W = A4[0] - 3.2 * cm

    def mad(x):
        if x is None or x != x:
            return "n/c"
        return f"{x:,.0f}".replace(",", " ")

    def tableau(entetes, lignes, largeurs, droite=(), couleurs=None, gras=()):
        return cp.tableau_pdf(entetes, lignes, largeurs, 8, droite=droite, couleurs_colonnes=couleurs, lignes_gras=gras)

    buf = io.BytesIO()
    doc = cp.DocumentMaro(buf, pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=2.1 * cm, bottomMargin=1.7 * cm,
                          title="Rapport synthétique MaroIFRS", author="MaroIFRS")
    nom_fichier = fichier or "balance importée"
    el = [Spacer(1, 1), PageBreak()]
    el += [Paragraph(f"Fichier : <b>{cp.net(nom_fichier)}</b> &nbsp;·&nbsp; Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}", nor), Spacer(1, 8)]
    if statut:   # même statut que l'écran et que le rapport détaillé
        el += [Paragraph(f'<font color="{cp.STATUTS_COULEUR[statut[0]]}" size="14"><b>Statut final : {cp.net(statut[0])}</b></font>', nor),
               Paragraph("Motifs : " + cp.net("; ".join(statut[1])) + ".", nor), Spacer(1, 6)]
    el.append(cp.bloc_indicateurs([(str(len(df)), "comptes analysés"), (f"{conf_n:.0f} %", sc.ETIQUETTE_COMPTES.lower()),
                                   (f"{conf_v:.0f} %", sc.ETIQUETTE_VALEUR.lower()), (str(len(constats_df)), "constats")], W))
    el.append(Paragraph(cp.net(sc.DEFINITION_COMPTES), pet))
    el += cp.titre_section("Statut des comptes", st_)
    comp = compteurs_statuts(res_statuts)
    el.append(tableau(["Statut", "Nombre de comptes", "Signification"], [[s, comp[s], DEFINITIONS_STATUT[s]] for s in STATUTS],
                      [3 * cm, 3.4 * cm, W - 6.4 * cm], droite=(1,)))
    el += cp.titre_section("Constats et retraitements suggérés", st_)
    el.append(tableau(["Norme", "Niveau", "Constat", "Retraitement suggéré"],
                      constats_df[["Norme", "Niveau", "Constat", "Retraitement suggéré"]].values.tolist(),
                      [2.4 * cm, 2.3 * cm, (W - 4.7 * cm) / 2, (W - 4.7 * cm) / 2], couleurs={1: cp.NIVEAUX_COULEUR}))
    el += cp.titre_section("État de situation financière (IAS 1)", st_)
    el.append(tableau(["Poste", "Montant (MAD)"], [[p, mad(v)] for p, v in etat.values.tolist()], [W - 5.4 * cm, 5.4 * cm], droite=(1,)))
    if conso:
        el += [PageBreak()] + cp.titre_section("Comptes consolidés (en milliers de dirhams)", st_)
        for cle, titre in (("actif", "Bilan actif consolidé"), ("passif", "Bilan passif consolidé"), ("cpc", "Compte de résultat consolidé")):
            if cle not in conso["dispo"]:
                continue
            el.append(Paragraph(titre, st_["h2"]))
            tab_c = conso[cle].values.tolist()
            lg = [[a, ("-" if v == 0 else mad(v))] for a, v, ty in tab_c]
            el.append(tableau(["Poste", "Au 31 décembre (milliers MAD)"], lg, [W - 5.4 * cm, 5.4 * cm], droite=(1,),
                              gras={i for i, (_a, _v, ty) in enumerate(tab_c) if ty != "ligne"}))
        if "retr" in conso["dispo"]:
            el.append(Paragraph("Retraitements de consolidation", st_["h2"]))
            el.append(tableau(["Retraitement", "Base (MAD)", "Effet réserves (MAD)", "Impôt différé (MAD)"],
                              [[r[0], mad(r[1]), mad(r[2]), mad(r[3])] for r in conso["retraitements"].values.tolist()],
                              [W - 8.6 * cm, 2.8 * cm, 2.9 * cm, 2.9 * cm], droite=(1, 2, 3)))
        for n_ in conso["notes"]:
            el.append(Paragraph("• " + cp.net(n_), pet))
    if fiscal:
        el += [PageBreak()] + cp.titre_section("Contrôle fiscal : du résultat comptable au résultat fiscal", st_)
        el.append(tableau(["Ligne", "Montant (MAD)"], [[a, mad(b)] for a, b in tableau_passage(fiscal).values.tolist()], [W - 5.4 * cm, 5.4 * cm], droite=(1,)))
    if dt is not None and not dt.empty:
        el += cp.titre_section("IAS 12 : impôts différés", st_)
        el.append(tableau(["Libellé", "Différence temporaire", "Impôt différé", "Type"],
                          [[r["Libellé"], mad(r["Différence temporaire"]), mad(r["Impôt différé"]), r["Type"]] for _, r in dt.iterrows()],
                          [5 * cm, 4 * cm, 3.6 * cm, W - 12.6 * cm], droite=(1, 2)))
    if ugt is not None and not ugt.empty:
        el += cp.titre_section("IAS 36 : test de dépréciation", st_)
        el.append(tableau(["Indicateur", "Montant (MAD)"], [[a, mad(b)] for a, b in ugt.values.tolist()], [W - 5.4 * cm, 5.4 * cm], droite=(1,)))
    el += [Spacer(1, 14), Paragraph("<i>Outil d'aide à la décision : mapping conforme au CGNC, répartition courant / non courant simplifiée, résultats à valider par un "
                                     "expert-comptable.</i>", pet)]

    def garde(c, _d):
        cp.page_de_garde(c, A4[0], A4[1], ["Rapport", "synthétique"], "Conversion du CGNC vers les normes IAS / IFRS",
                         [("Fichier analysé", nom_fichier), ("Date d'édition", cp.date_edition()), ("Comptes analysés", str(len(df))),
                          (sc.ETIQUETTE_COMPTES, f"{conf_n:.0f} % des comptes · {conf_v:.0f} % en valeur")],
                         ["Document généré par MaroIFRS. Outil d'aide à la décision : résultats à valider par un expert-comptable."],
                         statut=((statut[0], cp.STATUTS_COULEUR[statut[0]]) if statut else None))
    doc.build(el, canvasmaker=cp.fabrique_canvas("Rapport synthétique", nom_fichier, avec_garde=True), onFirstPage=garde, onLaterPages=lambda c, _d: None)
    return buf.getvalue()


# ============================ 4d. EXPORT EXCEL MIS EN FORME ============================
def excel_formate(df, constats_df, etat, res_statuts, conf_n, conf_v, fiscal=None, dt=None, ugt=None, fichier="", conso=None, statut=None):
    """Classeur Excel professionnel : synthèse, constats, statut des comptes, situation, fiscalité, IAS 12 / IAS 36."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    BLEU, BLEU_CL, GRIS, PISTACHE = "2E7D32", "E8F5E9", "F1F8F2", "4CAF50"
    f_titre = Font(name="Calibri", size=20, bold=True, color="26322B")
    f_sous = Font(name="Calibri", size=10, italic=True, color="5E6C63")
    f_tete = Font(name="Calibri", size=11, bold=True, color="26322B")
    f_norm = Font(name="Calibri", size=10, color="26322B")
    f_gras = Font(name="Calibri", size=10, bold=True, color="26322B")
    fond_tete = PatternFill("solid", fgColor=PISTACHE)
    fond_cl = PatternFill("solid", fgColor=BLEU_CL)
    fond_zebra = PatternFill("solid", fgColor=GRIS)
    fin = Side(style="thin", color="DCE3D5")
    cadre = Border(left=fin, right=fin, top=fin, bottom=fin)
    NUM = '#,##0;[Red]-#,##0;"-"'
    COULEURS_NIV = {"Erreur": ("F3DAD9", "8C2F2D"), "Retraitement": ("DCEBCF", "1B5E20"),
                    "À vérifier": ("FBE6CF", "8A3000"), "Info": ("EEF1EA", "55705C")}
    COULEURS_ST = {"éliminé": ("E3E7E0", "26322B"), "à revoir": ("FBE6CF", "8A3000"), "inchangé": ("DCEBCF", "1B5E20"),
                   "reclassé": ("E8F5E9", "1B5E20"), "ajusté": ("F3DAD9", "8C2F2D")}

    wb = Workbook()

    def feuille(nom, titre, sous_titre, premiere=False):
        ws = wb.active if premiere else wb.create_sheet()
        ws.title = nom
        ws.sheet_view.showGridLines = False
        ws.sheet_properties.tabColor = PISTACHE
        ws["A1"], ws["A1"].font = titre, f_titre
        for col_ in "ABCDEFGH":
            ws[f"{col_}2"].border = Border(bottom=Side(style="medium", color="E65100"))
        ws["A2"], ws["A2"].font = sous_titre, f_sous
        ws.row_dimensions[1].height = 30
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.oddFooter.center.text = "MaroIFRS · Page &P sur &N"
        return ws

    def tableau(ws, ligne, entetes, lignes, largeurs, formats=None, couleurs=None, centrer=()):
        """Écrit un tableau à partir de `ligne` ; retourne la ligne suivante."""
        for j, e in enumerate(entetes, 1):
            c = ws.cell(row=ligne, column=j, value=e)
            c.font, c.fill, c.border = f_tete, fond_tete, cadre
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[ligne].height = 24
        for i, lg in enumerate(lignes):
            r = ligne + 1 + i
            for j, v in enumerate(lg, 1):
                c = ws.cell(row=r, column=j, value=v)
                c.font, c.border = f_norm, cadre
                if i % 2:
                    c.fill = fond_zebra
                if formats and formats.get(j):
                    c.number_format = formats[j]
                    c.alignment = Alignment(horizontal="right", vertical="top")
                else:
                    c.alignment = Alignment(horizontal="center" if j in centrer else "left", vertical="top", wrap_text=True)
                if couleurs and j in couleurs and v in couleurs[j]:
                    bg, fg = couleurs[j][v]
                    c.fill, c.font = PatternFill("solid", fgColor=bg), Font(name="Calibri", size=10, bold=True, color=fg)
        for j, w in enumerate(largeurs, 1):
            ws.column_dimensions[get_column_letter(j)].width = w
        ws.print_title_rows = f"{ligne}:{ligne}"
        return ligne + len(lignes) + 2

    maintenant = datetime.now().strftime("%d/%m/%Y à %H:%M")
    sous = f"Fichier : {fichier or 'balance importée'}  ·  Généré le {maintenant}"
    if statut:   # même statut que l'écran et que le rapport détaillé
        sous += f"  ·  Statut final : {statut[0]} ({'; '.join(statut[1])})"

    # ---- Synthèse ----
    ws = feuille("Synthèse", "MaroIFRS · Résultats de l'analyse : CGNC vers IAS / IFRS", sous, premiere=True)
    kpis = [("Comptes analysés", len(df), "0"), (sc.ETIQUETTE_COMPTES, conf_n / 100, "0%"),
            (sc.ETIQUETTE_VALEUR, conf_v / 100, "0%"), ("Constats", len(constats_df), "0")]
    for j, (lib, val, fmt) in enumerate(kpis):
        col = 1 + j * 2
        ws.merge_cells(start_row=4, start_column=col, end_row=4, end_column=col + 1)
        ws.merge_cells(start_row=5, start_column=col, end_row=5, end_column=col + 1)
        a, b = ws.cell(row=4, column=col, value=lib), ws.cell(row=5, column=col, value=val)
        a.font, a.alignment = Font(name="Calibri", size=9, bold=True, color="5E6C63"), Alignment(horizontal="center")
        b.font, b.number_format, b.alignment = Font(name="Calibri", size=22, bold=True, color="26322B"), fmt, Alignment(horizontal="center")
        for rr in (4, 5):
            for cc in (col, col + 1):
                ws.cell(row=rr, column=cc).fill = fond_cl
                ws.cell(row=rr, column=cc).border = Border(top=Side(style="medium", color="E65100") if rr == 4 else None)
    ws.row_dimensions[5].height = 38
    comp = compteurs_statuts(res_statuts)
    ligne = tableau(ws, 8, ["Statut", "Comptes", "Signification"],
                    [[s, comp[s], DEFINITIONS_STATUT[s]] for s in STATUTS], [26, 16, 70, 16, 16, 16, 16, 16],
                    couleurs={1: COULEURS_ST}, centrer=(2,))
    niv = constats_df["Niveau"].value_counts()
    tableau(ws, ligne + 1, ["Niveau des constats", "Nombre"], [[n, int(niv.get(n, 0))] for n in COULEURS_NIV],
            [26, 16, 70, 16, 16, 16, 16, 16], couleurs={1: COULEURS_NIV}, centrer=(2,))
    ws.cell(row=ligne + 8, column=1,
            value="Outil d'aide à la décision : mapping conforme au CGNC, répartition courant / non courant simplifiée, résultats à valider par un expert-comptable.").font = f_sous

    # ---- Constats ----
    ws = feuille("Constats", "Constats et retraitements suggérés", sous)
    lignes = constats_df[["Norme", "Niveau", "Constat", "Retraitement suggéré"]].values.tolist()
    tableau(ws, 4, ["Norme", "Niveau", "Constat", "Retraitement suggéré"], lignes, [20, 16, 60, 70],
            couleurs={2: COULEURS_NIV}, centrer=(1, 2))
    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:D{4 + max(len(lignes), 1)}"

    # ---- Statut des comptes ----
    ws = feuille("Statut des comptes", "Statut de chaque compte", sous)
    lignes = [[r.compte, r.libelle, float(r.solde), r.statut, r.motif] for r in res_statuts.itertuples()]
    tableau(ws, 4, ["Compte", "Libellé", "Solde (MAD)", "Statut", "Motif"], lignes, [12, 48, 18, 14, 62],
            formats={3: NUM}, couleurs={4: COULEURS_ST}, centrer=(1, 4))
    ws.freeze_panes = "A5"
    ws.auto_filter.ref = f"A4:E{4 + max(len(lignes), 1)}"

    # ---- Situation financière ----
    ws = feuille("Situation financière", "État de situation financière (IAS 1)", sous)
    postes = etat.values.tolist()
    fin_t = tableau(ws, 4, ["Poste", "Montant (MAD)"], postes, [52, 22], formats={2: NUM})
    actif_t = sum(v for p, v in postes[:2])
    passif_t = sum(v for p, v in postes[2:])
    for k, (lib, val) in enumerate([("Total actif", actif_t), ("Total passif et capitaux propres", passif_t),
                                    ("Écart", actif_t - passif_t)]):
        a, b = ws.cell(row=fin_t + k, column=1, value=lib), ws.cell(row=fin_t + k, column=2, value=val)
        for c in (a, b):
            c.font, c.fill, c.border = f_gras, fond_cl, cadre
        b.number_format = NUM

    # ---- Comptes consolidés (seulement les états correspondant aux documents importés) ----
    if conso:
        ws_n, r_n = None, 0
        for cle, nom_f, titre_f in (("actif", "Bilan actif consolidé", "Bilan actif consolidé (milliers de MAD)"),
                                    ("passif", "Bilan passif consolidé", "Bilan passif consolidé (milliers de MAD)"),
                                    ("cpc", "CPC consolidé", "Compte de résultat consolidé (milliers de MAD)")):
            if cle not in conso["dispo"]:
                continue
            ws = feuille(nom_f, titre_f, sous)
            tab_c = conso[cle]
            ws_n, r_n = ws, tableau(ws, 4, ["Poste", "Milliers de MAD"], tab_c[["Poste", "Montant"]].values.tolist(), [62, 20], formats={2: NUM})
            for i_, ty_ in enumerate(tab_c["Type"].tolist()):
                if ty_ != "ligne":
                    for c_ in (1, 2):
                        ws.cell(row=5 + i_, column=c_).font, ws.cell(row=5 + i_, column=c_).fill = f_gras, fond_cl
        if "retr" in conso["dispo"]:
            ws = feuille("Retraitements", "Retraitements de consolidation", sous)
            ws_n, r_n = ws, tableau(ws, 4, ["Retraitement", "Base (MAD)", "Effet réserves (MAD)", "Impôt différé (MAD)"],
                                    conso["retraitements"].values.tolist(), [70, 20, 22, 22], formats={2: NUM, 3: NUM, 4: NUM})
        if ws_n is not None:
            for k_, n_ in enumerate(conso["notes"]):
                c_ = ws_n.cell(row=r_n + 1 + k_, column=1, value="• " + n_)
                c_.font, c_.alignment = f_norm, Alignment(wrap_text=True, vertical="top")
                ws_n.merge_cells(start_row=r_n + 1 + k_, start_column=1, end_row=r_n + 1 + k_, end_column=2)
                ws_n.row_dimensions[r_n + 1 + k_].height = 42

    # ---- Contrôle fiscal ----
    if fiscal:
        ws = feuille("Contrôle fiscal", "Du résultat comptable au résultat fiscal", sous)
        passage = [[a, ("n/c" if (b is None or b != b) else b)] for a, b in tableau_passage(fiscal).values.tolist()]
        tableau(ws, 4, ["Ligne", "Montant (MAD)"], passage, [60, 22], formats={2: NUM})
        for r in range(5, 5 + len(passage)):
            if str(ws.cell(row=r, column=1).value).startswith(("=", "IS dû")):
                for c in (1, 2):
                    ws.cell(row=r, column=c).font, ws.cell(row=r, column=c).fill = f_gras, fond_cl

    # ---- IAS 12 ----
    if dt is not None and not dt.empty:
        ws = feuille("IAS 12", "Impôts différés", sous)
        cols = ["Libellé", "Nature", "Valeur comptable IFRS", "Base fiscale", "Différence temporaire", "Impôt différé", "Type"]
        tableau(ws, 4, cols, dt[cols].values.tolist(), [30, 12, 22, 20, 22, 18, 26], formats={3: NUM, 4: NUM, 5: NUM, 6: NUM}, centrer=(2,))

    # ---- IAS 36 ----
    if ugt is not None and not ugt.empty:
        ws = feuille("IAS 36", "Test de dépréciation", sous)
        tableau(ws, 4, ["Indicateur", "Montant (MAD)"], ugt.values.tolist(), [40, 22], formats={2: NUM})

    sortie = io.BytesIO()
    wb.save(sortie)
    return sortie.getvalue()


# ---- Icônes (SVG intégrés, aucune connexion nécessaire) ----
ICONES = {
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
    "scan": '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="3"/><path d="m16 16-1.9-1.9"/>',
    "scale": '<path d="m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z"/><path d="M7 21h10"/><path d="M12 3v18"/><path d="M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2"/>',
    "filecheck": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="m9 15 2 2 4-4"/>',
    "filetext": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
    "chart": '<path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/>',
    "coins": '<circle cx="8" cy="8" r="6"/><path d="M18.09 10.37A6 6 0 1 1 10.34 18"/><path d="M7 6h1v4"/><path d="m16.71 13.88.7.71-2.82 2.82"/>',
    "factory": '<path d="M2 20a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8l-7 5V8l-7 5V4a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z"/><path d="M17 18h1"/><path d="M12 18h1"/><path d="M7 18h1"/>',
    "receipt": '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"/><path d="M12 17.5v-11"/>',
    "down": '<polyline points="22 17 13.5 8.5 8.5 13.5 2 7"/><polyline points="16 17 22 17 22 11"/>',
    "shield": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
    "layers": '<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "mail": '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>',
    "lock": '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "target": '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
    "bank": '<line x1="3" x2="21" y1="22" y2="22"/><line x1="6" x2="6" y1="18" y2="11"/><line x1="10" x2="10" y1="18" y2="11"/><line x1="14" x2="14" y1="18" y2="11"/><line x1="18" x2="18" y1="18" y2="11"/><polygon points="12 2 20 7 4 7"/>',
    "flag": '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" x2="4" y1="22" y2="15"/>',
    "puzzle": '<path d="M19.439 7.85c-.049.322.059.648.289.878l1.568 1.568c.47.47.706 1.087.706 1.704s-.235 1.233-.706 1.704l-1.611 1.611a.98.98 0 0 1-.837.276c-.47-.07-.802-.48-.968-.925a2.501 2.501 0 1 0-3.214 3.214c.446.166.855.497.925.968a.979.979 0 0 1-.276.837l-1.61 1.61a2.404 2.404 0 0 1-1.705.707 2.402 2.402 0 0 1-1.704-.706l-1.568-1.568a1.026 1.026 0 0 0-.877-.29c-.493.074-.84.504-1.02.968a2.5 2.5 0 1 1-3.237-3.237c.464-.18.894-.527.967-1.02a1.026 1.026 0 0 0-.289-.877l-1.568-1.568A2.402 2.402 0 0 1 1.998 12c0-.617.236-1.234.706-1.704L4.23 8.77c.24-.24.581-.353.917-.303.515.077.877.528 1.073 1.01a2.5 2.5 0 1 0 3.259-3.259c-.482-.196-.933-.558-1.01-1.073-.05-.336.062-.676.303-.917l1.525-1.525A2.402 2.402 0 0 1 12 1.998c.617 0 1.234.236 1.704.706l1.568 1.568c.23.23.556.338.877.29.493-.074.84-.504 1.02-.968a2.5 2.5 0 1 1 3.237 3.237c-.464.18-.894.527-.967 1.02Z"/>',
}


def ico(nom, taille=22):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{taille}" height="{taille}" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{ICONES[nom]}</svg>')





# ============================ 5. INTERFACE ============================
NIVEAUX = {"Erreur": "🔴 Erreur", "Retraitement": "🟠 Retraitement", "À vérifier": "🟡 À vérifier", "Info": "⚪ Info"}
FONDS = ["#E8F5E9", "#FFF3E4", "#E8F5E9", "#C8E6C9", "#FFE3D1"]
ICONES_NORMES = ["filetext", "coins", "factory", "receipt", "down", "shield", "bank", "layers", "scale", "chart"]

st.markdown(ui.CSS, unsafe_allow_html=True)


def _motif(nom, c1, c2):
    f1, f2 = f"fill:{c1}", f"fill:{c2}"
    m = {
        "liste": "".join(f'<rect x="20" y="{22 + i * 38}" width="{170 - i * 24}" height="22" rx="11" style="{f1 if i % 2 == 0 else f2}" opacity="{1 - i * .15:.2f}"/>' for i in range(4)),
        "flux": f'<circle cx="40" cy="100" r="26" style="{f1}"/><circle cx="105" cy="100" r="26" style="{f2}"/><circle cx="170" cy="100" r="26" style="{f1}"/>'
                f'<path d="M68 100H77M133 100H142" style="stroke:{c1};stroke-width:5;fill:none"/>',
        "bouclier": f'<path d="M105 14 L178 44 V100 C178 142 142 172 105 186 C68 172 32 142 32 100V44Z" style="{f1}" opacity=".9"/>'
                    f'<path d="M70 102 L97 130 L143 74" style="stroke:{c2};stroke-width:11;fill:none;stroke-linecap:round;stroke-linejoin:round"/>',
        "grille": "".join(f'<rect x="{22 + (i % 3) * 58}" y="{22 + (i // 3) * 58}" width="48" height="48" rx="11" style="{f1 if i % 2 == 0 else f2}" opacity="{.95 if i % 2 == 0 else .85}"/>' for i in range(9)),
        "jauge": f'<circle cx="105" cy="100" r="70" style="stroke:{c1};stroke-width:18;fill:none" opacity=".25"/>'
                 f'<circle cx="105" cy="100" r="70" style="stroke:{c2};stroke-width:18;fill:none;stroke-linecap:round" stroke-dasharray="418" stroke-dashoffset="34" transform="rotate(-90 105 100)"/>'
                 f'<path d="M78 102 L98 122 L134 80" style="stroke:{c1};stroke-width:10;fill:none;stroke-linecap:round;stroke-linejoin:round"/>',
        "barres": "".join(f'<rect x="{22 + i * 34}" y="{176 - h}" width="24" height="{h}" rx="7" style="{f1 if i % 2 else f2}"/>' for i, h in enumerate([42, 74, 58, 108, 146]))
                  + f'<path d="M22 70 L68 52 L104 60 L140 30 L186 12" style="stroke:{c2};stroke-width:4;fill:none;stroke-linecap:round"/>',
        "feuille": f'<path d="M20 176 C20 84 90 30 190 30 C190 124 130 176 20 176Z" style="{f1}" opacity=".92"/>'
                   f'<path d="M20 176 C70 126 112 94 172 52" style="stroke:{c2};stroke-width:5;fill:none;stroke-linecap:round"/>',
        "onde": f'<path d="M0 108 C40 48 80 168 120 100 C150 52 180 108 210 80 V190 H0Z" style="{f1}" opacity=".9"/>'
                f'<path d="M0 144 C50 104 90 184 130 134 C160 104 190 144 210 124 V190 H0Z" style="{f2}" opacity=".85"/>',
    }
    return f'<svg viewBox="0 0 210 190" xmlns="http://www.w3.org/2000/svg">{m[nom]}</svg>'


# clé : (style du bandeau, motif, motif à gauche ?, phrase d'appui)
BANDEAUX = {
    "norm": ("clair", "liste", False, f"{len(CATALOGUE)} normes IAS/IFRS, interprétations IFRIC/SIC et plan comptable CGNC, à consulter librement."),
    "trans": ("orange", "flux", True, "Importez, identifiez le document, contrôlez-le au regard du CGNC puis convertissez-le en IAS/IFRS."),
    "fisc": ("sombre", "bouclier", False, "Du résultat comptable au résultat fiscal, avec l'impôt sur les sociétés et la cotisation minimale."),
    "calc": ("clair", "grille", True, "Calculateurs d'impôts différés (IAS 12) et de dépréciation (IAS 36)."),
    "res": ("clair", "jauge", False, "Score de conformité, constats par norme et états IFRS."),
    "conso": ("sombre", "flux", True, "Des comptes de chaque société aux comptes du groupe, puis comparaison avec les comptes publiés."),
    "dash": ("sombre", "barres", True, "L'évolution de la conformité d'un fichier à l'autre."),
    "hist": ("clair", "barres", True, "Les résultats de chaque transformation enregistrée, sans les balances importées."),
    "apropos": ("clair", "feuille", False, "Le projet, ses fonctionnalités et ses limites."),
    "contact": ("orange", "onde", True, "Une question sur la plateforme ? Écrivez-nous."),
}


def bandeau(cle, surtitre, titre):
    """Bandeau d'en-tête de page : titre, phrase d'appui et petit motif illustratif."""
    style, motif, gauche, texte = BANDEAUX[cle]
    return (f'<div class="bd"><div class="bd-t"><div class="eyebrow">{surtitre}</div>'
            f'<div class="bd-h">{titre}</div><div class="bd-s">{texte}</div></div>'
            + (ui.photo_bandeau(cle, None, lambda t: i18n.tr(t, st.session_state.get("lang", "fr")))
               or f'<div class="bd-m">{_motif(motif, "#66BB6A", "#E65100")}</div>') + '</div>')





_INDICES_MONTANT = ("montant", "solde", "débit", "crédit", "(mad)", "mad)", "écart", "valeur", "base", "total", "impôt", "nombre", "%", "k mad", "milliers")
_RE_MONTANT = re.compile(r"^[+\-\u2212]?\d[\d\s\u202f\u00a0.,]*(\s?%|\s?MAD)?$")


def _colonne_montants(lignes, c):
    """Une colonne est alignée à droite si son titre évoque un montant ET si toutes ses valeurs non vides sont des nombres (jamais un code de compte)."""
    if not any(k in str(c).lower() for k in _INDICES_MONTANT):
        return False
    valeurs = [str(lg.get(c, "")).strip() for lg in lignes]
    valeurs = [v for v in valeurs if v and v not in ("-", "—", "n/c")]
    return bool(valeurs) and all(_RE_MONTANT.match(v) for v in valeurs)


def tableau_html(lignes, colonnes, premiere_en_gras=True, pastille=None):
    """Tableau HTML : le texte long passe à la ligne, on lit tout sans cliquer (fonctionne hors ligne). Montants alignés à droite."""
    from html import escape
    nums = {c: _colonne_montants(lignes, c) for c in colonnes}
    tete = "".join(f'<th class="num">{escape(str(c))}</th>' if nums[c] else f"<th>{escape(str(c))}</th>" for c in colonnes)
    corps = ""
    for lg in lignes:
        cel = ""
        for j, c in enumerate(colonnes):
            v = escape(str(lg.get(c, "")))
            if j == 0 and premiere_en_gras:
                cel += f'<td class="c1">{v}</td>'
            elif pastille and c == pastille:
                cel += f'<td class="nw"><span class="pill2{"" if v == "En vigueur" else " x"}">{v}</span></td>'
            elif nums[c]:
                cel += f'<td class="num">{v}</td>'
            else:
                cel += f"<td>{v}</td>"
        corps += f"<tr>{cel}</tr>"
    return f'<div class="tw"><div class="tw-scroll"><table><thead><tr>{tete}</tr></thead><tbody>{corps}</tbody></table></div></div>'



# ---------- Mise en cache : on ne relit pas le fichier et on ne régénère pas les exports à chaque clic ----------
@st.cache_data(show_spinner=False, max_entries=8)
def lire_pdf_cache(octets):
    df_ = lire_pdf(io.BytesIO(octets))
    return df_, dict(df_.attrs)


@st.cache_data(show_spinner=False, max_entries=8)
def factures_cache(elements, sens_defaut):
    return extraire_factures(elements, sens_defaut)


@st.cache_data(show_spinner=False, max_entries=8)
def charger_balance_cache(octets, nom):
    f = io.BytesIO(octets)
    f.name = nom
    df_ = charger_balance(f)
    return df_, dict(df_.attrs)


@st.cache_data(show_spinner=False, max_entries=1)
def modele_balance_cache():
    return modeles_excel.modele_balance()


@st.cache_data(show_spinner=False, max_entries=4)
def traitements_cache(df_, constats_, choix_, taux_):
    """Tableau « traitement par norme » : même fonction que cn.tableau_traitements, mémorisée tant que les comptes, les constats et le choix de normes ne changent pas."""
    return cn.tableau_traitements(df_, constats_, choix_, taux_)


@st.cache_data(show_spinner=False, max_entries=4)
def rapport_detaille_pdf_cache(rap_):
    return rapport_final.construire_pdf(rap_)


@st.cache_data(show_spinner=False, max_entries=4)
def rapport_detaille_excel_cache(rap_):
    return rapport_final.construire_excel(rap_)


def _en_cache(fonction_cache, fonction_directe, rap_):
    """Utilise la version mémorisée ; si le contenu ne peut pas être mémorisé, construit le fichier directement (résultat identique)."""
    try:
        return fonction_cache(rap_)
    except Exception:
        return fonction_directe(rap_)


@st.cache_data(show_spinner=False, max_entries=8)
def excel_cache(*args):
    return excel_formate(*args)


@st.cache_data(show_spinner=False, max_entries=8)
def pdf_cache(*args):
    return rapport_pdf(*args)


if "historique" not in st.session_state:
    st.session_state["historique"] = charger_historique()


def kpi(icone, label, valeur):
    return f'<div class="kpi"><div class="kico">{ico(icone)}</div><div><span>{label}</span><b>{valeur}</b></div></div>'


def etape(num, icone, titre, texte):
    return (f'<div class="step"><div class="n">{num}</div><div class="tile">{ico(icone)}</div>'
            f'<h3>{titre}</h3><p>{texte}</p></div>')


def carte_norme(nom, i=0):
    titre, desc = CATALOGUE.get(nom, (nom, ""))
    auto = nom in NORMES
    icone, fond = ICONES_NORMES[i % len(ICONES_NORMES)], FONDS[i % len(FONDS)]
    if auto and cn.META.get(nom, (None,))[0] == cn.RAPPEL:
        badge = '<span class="a off">Rappel</span>'
    elif auto:
        badge = '<span class="a on">Contrôle auto</span>'
    else:
        badge = '<span class="a off">Référence</span>'
    return (f'<div class="nc"><div class="top" style="background:{fond}">'
            f'<div class="b">{ico(icone, 16)} {nom}</div>{badge}{ico(icone, 56)}</div>'
            f'<div class="body"><h3>{titre}</h3><p>{desc}</p></div></div>')


def grille(cartes, n=4):
    for i in range(0, len(cartes), n):
        for col, html in zip(st.columns(n), cartes[i:i + n]):
            col.markdown(html, unsafe_allow_html=True)
        st.write("")


def entete(surtitre, titre):
    st.markdown(f'<div class="eyebrow">{surtitre}</div><div class="h2">{titre}</div>', unsafe_allow_html=True)


def liste(items, classe, icone):
    return "".join(f'<div class="li {classe}">{ico(icone, 18)}<span>{t}</span></div>' for t in items)


def afficher_conso(c):
    """Affiche uniquement les états correspondant aux documents importés ; totaux et sous-totaux en gras."""
    dispo = c.get("dispo") or {"actif", "passif", "cpc", "retr"}
    noms = [(k_, n_) for k_, n_ in (("actif", "Bilan actif retraité"), ("passif", "Bilan passif retraité"),
                                    ("cpc", "Compte de résultat retraité"), ("retr", "Retraitements")) if k_ in dispo]

    def un(cle):
        if cle == "retr":
            r_ = c["retraitements"].copy()
            for col in r_.columns[1:]:
                r_[col] = r_[col].map(lambda v: f"{v:,.0f}".replace(",", " "))
            st.markdown(tableau_html(r_.to_dict("records"), list(r_.columns), premiere_en_gras=False), unsafe_allow_html=True)
            return
        t = conso_vers_texte(c[cle])
        types = t["Type"].tolist()
        aff_c = t[["Poste", "Montant"]].rename(columns={"Montant": "Milliers de MAD"})
        sty = (aff_c.style.apply(lambda col: ["font-weight:700;background-color:#D7EDD8" if ty != "ligne" else "" for ty in types], axis=0)
               .set_properties(subset=["Milliers de MAD"], **{"text-align": "right"}))
        st.dataframe(sty, hide_index=True, use_container_width=True, height=min(38 * (len(aff_c) + 1) + 4, 760))

    if len(noms) == 1:
        st.markdown(f"**{noms[0][1]}**")
        un(noms[0][0])
    else:
        for onglet, (k_, _n) in zip(st.tabs([n_ for _, n_ in noms]), noms):
            with onglet:
                un(k_)
    if c["ecart"]:
        st.warning(f"Écart actif / passif de {c['ecart']:,.0f} milliers de MAD : vérifiez les comptes importés.")
    with st.expander("Notes et limites"):
        for n_ in c["notes"]:
            st.markdown("- " + n_)


def indicateurs_bandeau():
    """Indicateurs du bandeau défilant de l'accueil. Données RÉELLES de MaroIFRS (référentiel, historique des transformations) ; les exemples
    marqués demo=True sont FICTIFS. Aucun calcul comptable : lecture seule de valeurs déjà calculées."""
    lg = st.session_state.get("lang", "fr")
    ind = [{"libelle": "Normes IAS/IFRS au référentiel", "valeur": str(len(CATALOGUE)), "variation": None, "serie": None},
           {"libelle": "Normes contrôlées automatiquement", "valeur": str(N_CTRL), "variation": None, "serie": None},
           {"libelle": "Rappels d'annexe", "valeur": str(N_RAPPEL), "variation": None, "serie": None}]
    histo_b = st.session_state.get("historique") or []
    try:
        if histo_b:
            for lib_, cle_ in (("Comptes sans retraitement (dernier fichier)", "conformite"), ("Valeur sans retraitement (dernier fichier)", "valeur")):
                ser_ = [float(e[cle_]) for e in histo_b if e.get(cle_) is not None][-12:]
                if ser_:
                    ind.append({"libelle": lib_, "valeur": ui._nombre(ser_[-1], 1, lg) + " %", "variation": (ser_[-1] - ser_[-2]) if len(ser_) > 1 else None,
                                "unite": "pt", "serie": ser_ if len(ser_) > 1 else None})
            ind.append({"libelle": "Transformations enregistrées", "valeur": str(len(histo_b)), "variation": None, "serie": None})
    except Exception:
        pass            # un historique ancien ou incomplet n'empêche jamais l'affichage de l'accueil
    demo = ui.indicateurs_demo(lg)
    mixte = []
    for k in range(max(len(ind), len(demo))):
        if k < len(ind):
            mixte.append(ind[k])
        if k < len(demo):
            mixte.append(demo[k])
    return mixte


# ---------------- NAVIGATION ----------------
# Deux parties reliées aux vraies pages : (1) rangée horizontale toujours visible, (2) menu ☰ (tiroir) pour les autres pages.
# Tous les boutons sont de vrais widgets Streamlit : ils modifient st.session_state["page"].
# Les pages sont des conteneurs calculés à chaque exécution (comme les anciens onglets) : seul l'affichage change, rien n'est perdu en navigant.
HERO_SVG = ui.HERO_SVG


def _streamlit_assez_recent():
    v = getattr(st, "__version__", None)
    if not isinstance(v, str):
        return True
    m = re.match(r"(\d+)\.(\d+)", v)
    return not m or (int(m.group(1)), int(m.group(2))) >= ui.STREAMLIT_MIN


if not _streamlit_assez_recent():
    st.error("Cette version de MaroIFRS demande Streamlit 1.40 ou plus récent. Double-cliquez sur lancer.bat (il met à jour les composants) "
             "ou exécutez : python -m pip install -U streamlit")
    st.stop()

if st.session_state.get("page") not in ui.PAGES:
    st.session_state["page"] = ui.PAGE_PAR_DEFAUT
if "menu_ouvert" not in st.session_state:
    st.session_state["menu_ouvert"] = False


def aller(page):
    """Affiche la page demandée et referme le menu ☰ (appelé par les boutons de navigation et les boutons d'action de l'accueil)."""
    st.session_state["page"] = page
    st.session_state["menu_ouvert"] = False


def aller_depuis_barre(page):
    aller(page)
    if not ui.NAV_FRAGMENT_SEUL:                 # mode de repli : exécution complète de l'application à chaque changement de page
        st.session_state["_nav_complet"] = True


def basculer_menu():
    st.session_state["menu_ouvert"] = not st.session_state.get("menu_ouvert", False)


def fermer_menu():
    st.session_state["menu_ouvert"] = False


def changer_langue_ui():
    """Sélecteur de langue de l'en-tête : enregistre la langue puis relance toute l'application pour retraduire chaque page (aucune donnée n'est modifiée)."""
    i18n.changer_langue(st, st.session_state.get("langue_ui"))
    st.session_state["_nav_complet"] = True


@st.fragment
def barre_navigation():
    """Barre du haut + menu ☰ + feuille de style qui masque les pages inactives.
    Fragment : ouvrir ou fermer le menu, ou changer de page, ne relance que ce fragment (aucun recalcul des pages : elles restent dans la page web,
    seules les règles d'affichage changent). Les fichiers importés, les résultats et les sélections ne sont donc jamais touchés."""
    if st.session_state.pop("_nav_complet", False):
        st.rerun()
    actif = st.session_state["page"]
    ouvert = bool(st.session_state.get("menu_ouvert"))
    st.markdown(ui.css_pages(actif), unsafe_allow_html=True)
    with st.container(key="barre"):
        cols = st.columns(len(ui.PAGES_BARRE) + 4, vertical_alignment="center")
        cols[0].button("Fermer" if ouvert else "Menu", icon=":material/close:" if ouvert else ":material/menu:", key="nav_burger", on_click=basculer_menu)
        cols[1].markdown(f'<div class="brand">{ui.logo_barre()}</div>', unsafe_allow_html=True)
        for col, p in zip(cols[2:2 + len(ui.PAGES_BARRE)], ui.PAGES_BARRE):
            lib, icone = ui.PAGES[p]
            col.button(lib, icon=icone, key="nav_" + p, on_click=aller_depuis_barre, args=(p,), type="primary" if p == actif else "secondary")
        cols[-2].markdown(f'<span class="chip">{N_CTRL} normes contrôlées · {len(CATALOGUE)} au référentiel</span>', unsafe_allow_html=True)
        with cols[-1]:
            _codes = list(i18n.LANGUES)
            st.selectbox("Langue", _codes, index=_codes.index(st.session_state.get("lang", "fr")), format_func=lambda c: i18n.LANGUES[c],
                         key="langue_ui", label_visibility="collapsed", on_change=changer_langue_ui)
    if ouvert:
        st.button("Fermer le menu", key="nav_scrim", on_click=fermer_menu)          # fond assombri : un clic à côté du menu le referme
        with st.container(key="tiroir"):
            tete_menu = st.columns([3, 2], vertical_alignment="center")
            tete_menu[0].markdown(f'<div class="tiroir-t">{ui.logo_complet_svg(36, False)}</div>', unsafe_allow_html=True)
            tete_menu[1].button("Fermer", icon=":material/close:", key="nav_fermer", on_click=fermer_menu)
            for titre_groupe, pages_groupe in ui.GROUPES_MENU:
                st.markdown(f'<div class="tiroir-g">{titre_groupe}</div>', unsafe_allow_html=True)
                for p in pages_groupe:
                    lib, icone = ui.PAGES[p]
                    st.button(lib, icon=icone, key="menu_" + p, on_click=aller_depuis_barre, args=(p,), type="primary" if p == actif else "secondary")


barre_navigation()          # contient aussi la feuille de style qui n'affiche que la page active
try:
    import streamlit.components.v1 as _cv_rv
    _cv_rv.html(ui.reveal_html(), height=0)          # apparitions au défilement (visuel seulement)
except Exception:
    pass

_ORDRE_PAGES = ["accueil", "normes", "transformation", "fiscal", "calc", "resultats", "conso", "dashboard", "historique", "apropos", "contact"]
(t_acc, t_norm, t_trans, t_fisc, t_calc, t_res, t_conso, t_dash, t_hist, t_apropos, t_contact) = [st.container(key="page_" + p) for p in _ORDRE_PAGES]

# ---------------- ACCUEIL ----------------
with t_acc:
    _lg = st.session_state.get("lang", "fr")
    with st.container(key="carrousel"):
        try:
            import streamlit.components.v1 as _cv1
            _cv1.html(ui.carrousel_html(lambda t: i18n.tr(t, _lg), lang=_lg), height=ui.CARROUSEL_HAUTEUR)
        except Exception:
            pass        # sans composant HTML : l'accueil s'affiche sans carrousel (les boutons de l'accueil restent disponibles)
    st.markdown(ui.services_html(lambda t: i18n.tr(t, _lg), _lg), unsafe_allow_html=True)
    with st.container(key="hero"):
        hero_g, hero_d = st.columns([1.1, 1], gap="large", vertical_alignment="center")
        with hero_g:
            st.markdown(f'<span class="pill">{ico("shield", 16)} CGNC → IAS / IFRS</span>'
                        '<div class="hero-t" role="heading" aria-level="1">Vos états de synthèse marocains, aux normes internationales.</div>'
                        '<p class="hero-p">Importez vos documents comptables (balance, bilan, CPC, ESG, tableau de financement, ETIC, factures). '
                        'MaroIFRS reconnaît chaque document, le lit, le contrôle au regard du CGNC, applique les règles IAS/IFRS, '
                        'contrôle le passage au résultat fiscal et produit un rapport PDF prêt à présenter.</p>', unsafe_allow_html=True)
            act1, act2, _vide = st.columns([1.35, 1.2, 1])
            act1.button("Importer mes documents", icon=":material/upload_file:", key="hero_importer", type="primary", on_click=aller,
                        args=("transformation",))
            act2.button("Explorer les normes", icon=":material/menu_book:", key="hero_normes", on_click=aller, args=("normes",))
            st.markdown('<div class="tags"><span>Accessible hors ligne</span><span>Gratuit et illimité</span>'
                        f'<span>{N_CTRL} normes contrôlées</span><span>Rapport PDF + Excel</span></div>', unsafe_allow_html=True)
        hero_d.markdown(f'<div class="hero-r">{HERO_SVG}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="stats"><div class="stat"><b>{len(CATALOGUE)}</b><span>normes IAS / IFRS au référentiel</span></div>'
                f'<div class="stat"><b>{N_CTRL}</b><span>normes contrôlées (+ {N_RAPPEL} rappels d\'annexe)</span></div>'
                '<div class="stat"><b>3</b><span>formats : Excel, CSV, PDF texte</span></div>'
                '<div class="stat"><b>PDF</b><span>rapport complet téléchargeable</span></div></div>', unsafe_allow_html=True)
    entete("Méthode", "Quatre étapes, de vos documents aux états IFRS et consolidés")
    grille([etape("01", "upload", "Importer", "Déposez vos documents, n'importe lesquels (états de synthèse, factures…) : Excel, CSV ou PDF, un ou plusieurs à la fois."),
            etape("02", "scan", "Identifier", "Reconnaissance du type de document (état de synthèse, facture…), puis contrôle au regard du CGNC."),
            etape("03", "scale", "Appliquer", "Contrôles IAS/IFRS et passage du résultat comptable au résultat fiscal."),
            etape("04", "filecheck", "Restituer", "Constats, états consolidés (bilan, CPC), score de conformité, rapport PDF et Excel.")])
    entete("Fonctionnalités", "De l'import au rapport, tout au même endroit")
    FONCTIONS_ACCUEIL = [
        ("transformation", "upload", "Transformation", "Importez une balance, des états de synthèse ou des factures : reconnaissance du document, contrôle CGNC et conversion IAS/IFRS."),
        ("resultats", "filecheck", "Résultats", "Constats par norme, états IFRS retraités, score de conformité, rapport de conformité en PDF et Excel."),
        ("normes", "scale", "Normes IAS/IFRS", f"{len(CATALOGUE)} normes au référentiel, interprétations IFRIC / SIC et plan comptable CGNC, à consulter librement."),
        ("fiscal", "receipt", "Contrôle fiscal", "Du résultat comptable au résultat fiscal, avec l'impôt sur les sociétés et la cotisation minimale."),
        ("conso", "layers", "Consolidation", "Périmètre, éliminations intragroupe, intérêts ne donnant pas le contrôle, comparaison avec les comptes publiés."),
        ("calc", "coins", "Calculateurs", "Impôts différés (IAS 12) et dépréciation d'actifs (IAS 36)."),
    ]
    for debut in range(0, len(FONCTIONS_ACCUEIL), 3):
        for colonne, (page_f, icone_f, titre_f, texte_f) in zip(st.columns(3), FONCTIONS_ACCUEIL[debut:debut + 3]):
            with colonne.container(key="fc_" + page_f):
                st.markdown(f'<div class="fc"><div class="tile">{ico(icone_f)}</div><h3>{titre_f}</h3><p>{texte_f}</p></div>', unsafe_allow_html=True)
                st.button("Ouvrir", icon=":material/arrow_forward:", key="ouvrir_" + page_f, on_click=aller, args=(page_f,))
    st.write("")
    with st.expander("Voir la liste complète des fonctionnalités"):
        st.markdown(liste(["Reconnaissance automatique du type de document : balance, bilan, CPC, ESG, tableau de financement, ETIC, factures (plusieurs fichiers à la fois)",
                           "Lecture des fichiers Excel (.xlsx, .xls), CSV et PDF (texte ou scanné) : états de synthèse même dans un rapport annuel",
                           "Détection des comptes par numéro CGNC et statut de chaque compte",
                           f"{N_CTRL} normes avec règles de contrôle sur les comptes, {N_RAPPEL} rappels d'annexe, {len(CATALOGUE)} normes au référentiel",
                           "Contrôle fiscal : résultat comptable → résultat fiscal → IS et cotisation minimale",
                           "Score de conformité IFRS en nombre de comptes et en valeur",
                           "Calculateurs d'impôts différés (IAS 12) et de dépréciation (IAS 36)",
                           "Comptes retraités IFRS d'une société (bilan, compte de résultat) en milliers de dirhams",
                           "Consolidation de groupe (mère, filiales, périmètre, éliminations intragroupe, intérêts ne donnant pas le contrôle)",
                           "Comparaison avec les comptes consolidés publiés par une société cotée (écarts expliqués, sans ajustement)",
                           "Lecture des grands livres et journaux, contrôle de TVA",
                           "Export Excel et rapport PDF, rapport de conformité complet (PDF et Excel)"], "ok", "check"), unsafe_allow_html=True)

# ---------------- NORMES ----------------
with t_norm:
    st.markdown(bandeau("norm", "Référentiel", "Normes IAS / IFRS, interprétations et plan comptable CGNC"), unsafe_allow_html=True)
    r1, r2, r3 = st.tabs(["Normes IAS / IFRS", "Interprétations IFRIC / SIC", "Plan comptable CGNC"])
    with r1:
        recherche = st.text_input("Rechercher une norme (numéro ou mot-clé)", placeholder="ex. IAS 16, location, impôts…", key="rech_norme")
        noms = [n for n in CATALOGUE if not recherche or recherche.lower() in (n + " " + CATALOGUE[n][0] + " " + CATALOGUE[n][1]).lower()]
        if not noms:
            st.info("Aucune norme ne correspond à cette recherche.")
        grille([carte_norme(n, i) for i, n in enumerate(noms)], n=4)
        st.caption("« Contrôle auto » : une règle de la plateforme examine les comptes concernés (le retraitement chiffré peut exiger des informations "
                   "complémentaires, listées dans la page Résultats). « Rappel » : simple rappel d'information d'annexe, aucun calcul. « Référence » : norme "
                   "au catalogue, sans contrôle automatique (à traiter dans l'annexe ou par un retraitement manuel).")
        if FICHES:
            st.markdown('<div class="titre2">Fiche détaillée d\'une norme</div>', unsafe_allow_html=True)
            ordre_f = sorted(FICHES, key=lambda x: (x[:3] != "IAS", int(re.search(r"\d+", x).group())))
            ref_f = st.selectbox("Choisir une norme", ordre_f, format_func=lambda x: f"{x} — {FICHES[x]['titre']}", key="fiche_norme")
            afficher_fiche(ref_f)
            st.caption(REF_IFRS.get("avertissement", "") + f" Dernière mise à jour du référentiel : {REF_IFRS.get('mise_a_jour', '—')}.")
        else:
            st.warning("Référentiel IAS/IFRS non chargé : ajoutez le fichier referentiel_ifrs.json à côté de app.py.")
    with r2:
        if INTERPS:
            q = st.text_input("Rechercher une interprétation", placeholder="ex. IFRIC 21, taxes, concession…", key="rech_interp")
            seulement = st.checkbox("Afficher seulement les interprétations en vigueur", value=True, key="interp_en_vigueur")
            lignes = [{"Référence": i["ref"], "Intitulé": i["titre"], "Objet": i["objet"], "Normes liées": ", ".join(i["normes"]), "Statut": i["statut"]}
                      for i in INTERPS.values()
                      if (not seulement or i["statut"] == "En vigueur") and (not q or q.lower() in (i["ref"] + i["titre"] + i["objet"]).lower())]
            if lignes:
                st.markdown(tableau_html(lignes, ["Référence", "Intitulé", "Objet", "Normes liées", "Statut"], pastille="Statut"),
                            unsafe_allow_html=True)
            else:
                st.info("Aucune interprétation ne correspond à cette recherche.")
            st.caption(f"{len(lignes)} interprétation(s). Les IFRIC et SIC complètent les normes ; certaines ont été remplacées par IFRS 15 ou IFRS 16. "
                       f"Liste officielle : {LIEN_IFRS}")
        else:
            st.warning("Référentiel non chargé : ajoutez le fichier referentiel_ifrs.json à côté de app.py.")
    with r3:
        if CGNC_COMPTES:
            q3 = st.text_input("Rechercher un compte (numéro ou libellé)", placeholder="ex. 3421, clients, amortissements…", key="rech_cgnc")
            lignes3 = [{"Compte": c, "Libellé": lib} for c, lib in CGNC_COMPTES.items() if not q3 or q3.lower() in (c + " " + lib).lower()]
            if lignes3:
                st.markdown(tableau_html(lignes3[:400], ["Compte", "Libellé"]), unsafe_allow_html=True)
            else:
                st.info("Aucun compte ne correspond à cette recherche.")
            st.caption(f"{len(lignes3)} compte(s) trouvé(s) sur {len(CGNC_COMPTES)} au plan comptable général des entreprises (CGNC).")
        else:
            st.warning("Plan comptable non chargé : ajoutez le fichier referentiel_cgnc.json à côté de app.py.")


# ---------------- TRANSFORMATION ----------------
df = None
conso = None
res_statuts = None
fichier = None
cgnc_df = None
conf_cgnc = None
inventaire = None
at_aff = {}
constats_docs = []
lignes_doc = []
nature = {"complets": None, "raisons": []}
qualite = None
traitements = None
manquantes_info = []
non_executes = []
detail_tva = False
ecr_controles = []
ph_statut = None     # emplacement du statut de conformité (page Transformation) : rempli plus bas avec le MÊME statut que le rapport PDF
valeurs_doc = []
with t_trans:
    st.markdown(bandeau("trans", "Transformation", "Importez vos documents"), unsafe_allow_html=True)
    c1, c2 = st.columns([2, 1])
    fichiers = c1.file_uploader("Balance, états de synthèse (bilan, CPC, ESG, tableau de financement, ETIC) ou factures : Excel / CSV / PDF", type=["xlsx", "xls", "csv", "pdf"],
                                accept_multiple_files=True, key="main",
                                help="Importez n'importe quel document comptable ou financier : il est reconnu automatiquement. Vous pouvez sélectionner plusieurs fichiers à la fois (par exemple bilan, CPC et ESG séparés).")
    fichier = None if not fichiers else (fichiers[0] if len(fichiers) == 1 else _Lot(fichiers))
    c2.write("")
    c2.write("")
    c2.download_button("Télécharger un modèle de balance", modele_balance_cache(), "modele_balance_MaroIFRS.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", icon=":material/download:", use_container_width=True,
                       help="Classeur Excel prêt à remplir (feuilles « Balance comptable » et « Instructions »), compatible avec l'importation ci-dessus.")
    choix = st.multiselect("Normes à appliquer", list(NORMES), default=list(NORMES), key="normes_a_appliquer")
    st.caption("Les normes affichées sont celles que vous demandez d'analyser. L'état réel de chaque contrôle (exécuté, sans objet, informations manquantes…) "
               "est indiqué dans la page Résultats, rubrique « Traitement par norme ».")
    if fichier is None:
        st.info("Importez une balance, des états de synthèse (bilan, CPC, ESG, tableau de financement, ETIC) ou des factures (PDF, Excel, CSV) : "
                "le type de chaque document est reconnu, puis le contrôle CGNC, la conversion IFRS, le contrôle fiscal et les résultats se font automatiquement.")
    else:
        try:
            elements = tuple((f_.name, f_.getvalue()) for f_ in fichiers)
            est_fact = [fichier_est_facture(n_, o_) for n_, o_ in elements]
            if all(est_fact):
                sens_txt = st.radio("Les factures importées sont…", ["Reçues (achats)", "Émises (ventes)", "Un mélange : je précise ligne par ligne"],
                                    horizontal=True, key="sens_fact")
                sens_def = "Vente" if sens_txt.startswith("Émises") else "Achat"
                tab_f, ign_f = factures_cache(elements, sens_def)
                if tab_f.empty:
                    st.error("Aucune facture reconnue dans ces documents. Pour des scans, vérifiez que le moteur OCR est installé "
                             "(pip install rapidocr-onnxruntime) ; sinon utilisez un PDF texte ou un tableau avec des colonnes HT / TVA / TTC.")
                else:
                    if ign_f:
                        st.warning(f"{len(ign_f)} document(s) non reconnu(s) comme facture : " + ", ".join(ign_f))
                    if _moteur_ocr() is not None:
                        st.caption("Les PDF scannés sont lus par reconnaissance de texte (OCR) : vérifiez soigneusement leurs montants.")
                    st.info(f"{len(tab_f)} facture(s) lue(s). Vérifiez ou corrigez le tableau (sens, compte CGNC, montants) : "
                            "la balance est reconstituée à partir de ces lignes. Montants en MAD.")
                    cfg_f = {"Sens": st.column_config.SelectboxColumn("Sens", options=["Achat", "Vente"], required=True),
                             "HT": st.column_config.NumberColumn("HT", format="%.2f"), "TVA": st.column_config.NumberColumn("TVA", format="%.2f"),
                             "TTC": st.column_config.NumberColumn("TTC", format="%.2f", disabled=True), "_tva_deduite": None}
                    edite_f = st.data_editor(tab_f, num_rows="dynamic", hide_index=True, use_container_width=True, column_config=cfg_f,
                                             key=f"fact_{fichier.name}_{fichier.size}_{sens_def}")
                    edite_f = edite_f.copy()
                    edite_f["TTC"] = pd.to_numeric(edite_f["HT"], errors="coerce").fillna(0) + pd.to_numeric(edite_f["TVA"], errors="coerce").fillna(0)
                    df = balance_depuis_factures(edite_f)
                    inventaire = {"detectes": {"facture": int(len(edite_f))}, "esg": {}, "tf": {}, "etic": {}}
                    st.warning(f"**Résultats partiels.** {len(edite_f)} facture(s) lue(s) : les écritures ci-dessous sont des PROPOSITIONS à valider. "
                               f"La balance reconstituée ({len(df)} comptes, débit = crédit = {m(df['debit'].sum())}) ne contient que ces factures : "
                               "ni bilan d'ouverture, ni paiements, ni paie, ni amortissements. Elle n'est pas une balance générale et ne permet pas "
                               "d'établir des états financiers fiables : les contrôles qui suivent sont partiels et le statut final ne pourra pas être « conforme ».")
                    ecr_prop = gl.ecritures_depuis_factures(edite_f, libelle_cgnc)
                    with st.expander(f"Écritures comptables proposées ({len(ecr_prop)} lignes) — à valider par le comptable", expanded=True):
                        st.dataframe(ecr_prop.style.format({"Débit": "{:,.2f}", "Crédit": "{:,.2f}"}), hide_index=True, use_container_width=True)
                        st.caption("Achat : charge ou immobilisation (HT), TVA récupérable 34552 / 34551, fournisseurs 4411 (TTC). Vente : clients 3421 (TTC), produit (HT), "
                                   "TVA facturée 4455. Le compte d'imputation est proposé d'après le libellé : corrigez-le dans le tableau des factures si nécessaire.")
                        b_ = io.BytesIO()
                        ecr_prop.to_excel(b_, index=False)
                        st.download_button("Télécharger les écritures proposées (Excel)", b_.getvalue(), "ecritures_proposees.xlsx", key="dl_ecr_prop")
            elif len(elements) > 1:
                with st.spinner("Lecture des documents en cours..."):
                    brut_l, inventaire, at_aff, msgs_l = lire_lot(elements, est_fact)
                for t_ in msgs_l:
                    st.warning(t_)
                if brut_l is None:
                    if not a_du_contenu(inventaire):
                        st.error("Aucun compte reconnu dans ces documents. Importez un bilan, un CPC, une balance générale ou des factures.")
                else:
                    st.info(f"{len(elements)} documents lus, {len(brut_l)} compte(s) fusionné(s). Vérifiez les montants : le tableau est modifiable.")
                    edite_l = st.data_editor(brut_l, num_rows="dynamic", use_container_width=True, key=f"lot_{fichier.name}_{fichier.size}")
                    df = preparer_balance(edite_l)
                    if abs(df["debit"].sum() - df["credit"].sum()) > 1:
                        st.error(f"Total débit ({m(df['debit'].sum())}) différent du total crédit ({m(df['credit'].sum())}) : "
                                 "un document manque (bilan ou CPC), ou un montant a été mal lu.")
                    else:
                        st.success(f"Balance reconstituée : {len(df)} comptes, total débit = total crédit ({m(df['debit'].sum())}).")
            elif fichier.name.lower().endswith(".pdf"):
                with st.spinner("Lecture du PDF en cours..."):
                    brut, at = lire_pdf_cache(fichier.getvalue())
                    brut.attrs.update(at)
                inventaire, at_aff = at.get("inventaire"), at
                if at.get("consolide"):
                    st.error("Ce document est déjà un état consolidé (montants en milliers de dirhams, après retraitements) : il ne peut pas être "
                             "consolidé une seconde fois. Importez les comptes sociaux (bilan, CPC) pour obtenir les comptes consolidés.")
                    df = None
                elif brut.empty and a_du_contenu(inventaire):
                    st.info("Ce PDF contient des états sans numéros de compte (ESG, tableau de financement, ETIC) : ils sont lus et contrôlés "
                            "ci-dessous. Ajoutez le bilan, le CPC ou la balance générale pour lancer la conversion complète.")
                elif brut.empty:
                    st.error("Aucun compte reconnu dans ce PDF. "
                             + ("Le PDF semble scanné (image) et le moteur OCR n'est pas installé : exécutez « pip install rapidocr-onnxruntime » "
                                "puis relancez l'application, ou utilisez un PDF texte, Excel ou CSV."
                                if at.get("scanne") else
                                "Vérifiez qu'il s'agit bien d'une balance (colonnes : compte, libellé, débit, crédit), "
                                "ou utilisez Excel / CSV."))
                else:
                    if at.get("ocr"):
                        st.warning(f"Document scanné lu par reconnaissance de texte (OCR) sur {at.get('pages_ocr', 0)} page(s) : "
                                   "vérifiez les montants dans le tableau (un chiffre peut être mal lu).")
                    if at.get("etats_synthese"):
                        st.success("États de synthèse reconnus (" + " + ".join({"A": "bilan actif", "P": "bilan passif", "C": "CPC"}[x] for x in at["sections"])
                                   + f") : {len(brut)} lignes converties en comptes CGNC pour reconstituer la balance."
                                   + (" Le résultat net du bilan est remplacé par celui du CPC (classes 6 et 7)." if at.get("resultat_via_cpc") else ""))
                        if at.get("ignorees_etats"):
                            with st.expander(f"{len(at['ignorees_etats'])} ligne(s) avec montants non rattachées à un compte (sous-totaux ou postes inconnus)"):
                                for t_ in at["ignorees_etats"][:60]:
                                    st.write("• " + t_)
                    elif at.get("etats_financiers"):
                        st.warning("Ce PDF ressemble à des états de synthèse (bilan, CPC) et non à une balance générale : "
                                   "seules les lignes avec un numéro de compte sont lues. Pour un résultat fiable, "
                                   "importez la balance générale.")
                    st.info(f"{len(brut)} compte(s) lu(s) sur {at.get('pages', '?')} page(s). Vérifiez les montants "
                            "ci-dessous : le tableau est modifiable.")
                    if at.get("ignorees"):
                        with st.expander(f"⚠ {at['ignorees']} ligne(s) non lue(s) : à ajouter à la main si besoin", expanded=True):
                            for t in at.get("lignes_ignorees", [])[:50]:
                                st.write("• " + t)
                    edite = st.data_editor(brut, num_rows="dynamic", use_container_width=True,
                                           key=f"pdf_{fichier.name}_{fichier.size}")
                    df = preparer_balance(edite)
                    tp = at.get("total_pdf")
                    if tp and (abs(df["debit"].sum() - tp[0]) > 1 or abs(df["credit"].sum() - tp[1]) > 1):
                        st.error(f"Écart avec la ligne « Total » du PDF : lu {m(df['debit'].sum())} / {m(df['credit'].sum())}, "
                                 f"PDF {m(tp[0])} / {m(tp[1])}. Une ligne manque ou un montant est mal lu.")
                    if abs(df["debit"].sum() - df["credit"].sum()) > 1:
                        st.error(f"Total débit ({m(df['debit'].sum())}) différent du total crédit ({m(df['credit'].sum())}) : "
                                 "une ligne manque ou un montant a été mal lu.")
                    else:
                        st.success(f"Balance lue : {len(df)} comptes, total débit = total crédit ({m(df['debit'].sum())}). "
                                   "Ouvrez la page « Résultats ».")
            else:
                df, _at = charger_balance_cache(fichier.getvalue(), fichier.name)
                df.attrs.update(_at)
                inventaire, at_aff = _at.get("inventaire"), _at
                if df.attrs.get("ecritures_controles") is not None:
                    st.success(f"{'Journal' if df.attrs.get('ecritures_nature') == 'journal' else 'Grand livre'} reconnu : {df.attrs.get('ecritures_nb', 0)} écriture(s) "
                               f"cumulées en {len(df)} comptes (balance reconstituée). Les contrôles des écritures figurent plus bas.")
                elif df.attrs.get("etats_synthese"):
                    st.success(f"États de synthèse (bilan / CPC) reconnus dans le classeur : {len(df)} comptes CGNC reconstitués.")
                else:
                    st.success(f"Balance chargée : {len(df)} comptes.")
        except DocumentSansComptes as e:
            inventaire = e.inventaire
            st.info("Ce classeur contient des états sans numéros de compte (ESG, tableau de financement, ETIC) : ils sont lus et contrôlés "
                    "ci-dessous. Ajoutez le bilan, le CPC ou la balance générale pour lancer la conversion complète.")
        except Exception as e:
            st.error(f"Erreur de lecture : {e}")

    if inventaire and fichier is not None:
        constats_docs = controles_documents(inventaire, df)
        lignes_doc = documents_reconnus(inventaire, at_aff)
        valeurs_doc = valeurs_lues(inventaire)
        if lignes_doc:
            st.markdown('<div class="eyebrow">Détection automatique</div><div class="h2" style="font-size:1.8rem">Documents reconnus</div>',
                        unsafe_allow_html=True)
            st.markdown(tableau_html(lignes_doc, ["Document", "Lecture", "Contrôles appliqués"]), unsafe_allow_html=True)
            vl = valeurs_lues(inventaire)
            if vl:
                with st.expander("Valeurs lues dans l'ESG et le tableau de financement"):
                    st.markdown(tableau_html(vl, ["État", "Poste", "Montant (MAD)"], premiere_en_gras=False), unsafe_allow_html=True)
            if df is None and constats_docs:
                aff_d = pd.DataFrame(constats_docs)
                aff_d["Niveau"] = aff_d["Niveau"].map(NIVEAUX).fillna(aff_d["Niveau"])
                st.markdown(tableau_html(aff_d.to_dict("records"), list(aff_d.columns), premiere_en_gras=False), unsafe_allow_html=True)
        st.write("")

    if df is not None:
        cgnc_df = controle_cgnc(df)
        detail_tva = df.attrs.get("ecritures_controles") is not None or df.attrs.get("tva_factures") is not None
        tva_l = gl.controle_tva(df, detail_disponible=detail_tva)
        if tva_l:
            cgnc_df = pd.concat([cgnc_df, pd.DataFrame([{"Contrôle": "TVA", "Niveau": x["Niveau"], "Compte": "4455 / 3455", "Constat": x["Constat"],
                                                         "Référence CGNC": x["Référence"]} for x in tva_l])], ignore_index=True)
        nature = cn.nature_resultats(df, factures=bool(df.attrs.get("factures")))
        if not nature["complets"] and not cgnc_df.empty:
            # document partiel : un constat de cohérence peut venir de l'absence des autres comptes (ex. capitaux propres d'une balance de factures)
            cgnc_df = cgnc_df.copy()
            m_ = (cgnc_df["Contrôle"] == "Cohérence") & (cgnc_df["Niveau"] == "Erreur")
            cgnc_df.loc[m_, "Constat"] = cgnc_df.loc[m_, "Constat"] + " : document partiel, à interpréter avec prudence (comptes manquants)"
            cgnc_df.loc[m_, "Niveau"] = "À vérifier"
        conf_cgnc = score_cgnc(df, cgnc_df)
        qualite = cn.qualite_donnees(df, cgnc_df, nature, CGNC_COMPTES)
        if not nature["complets"]:
            st.warning("**Résultats partiels** : " + " ".join(nature["raisons"]))
        qa_, qb_ = st.columns([1, 3])
        qa_.markdown(kpi("shield", "Qualité des données importées", f"{qualite['score']}/100 · {qualite['niveau']}"), unsafe_allow_html=True)
        with qb_.expander("Comment ce score est calculé"):
            st.dataframe(qualite["composantes"], hide_index=True, use_container_width=True)
            st.caption(qualite["avertissement"])
        ecr_controles = (df.attrs.get("ecritures_controles") or []) + (df.attrs.get("tva_factures") or [])
        if ecr_controles:
            st.markdown('<div class="eyebrow">Écritures comptables</div><div class="h2" style="font-size:1.8rem">Contrôle des écritures et des factures</div>',
                        unsafe_allow_html=True)
            aff_e = pd.DataFrame(ecr_controles)
            aff_e["Niveau"] = aff_e["Niveau"].map(NIVEAUX).fillna(aff_e["Niveau"])
            st.markdown(tableau_html(aff_e.to_dict("records"), list(aff_e.columns), premiere_en_gras=False), unsafe_allow_html=True)
        n_err = int((cgnc_df["Niveau"] == "Erreur").sum())
        n_ver = int((cgnc_df["Niveau"] == "À vérifier").sum())
        st.markdown('<div class="eyebrow">Étape 1 sur 2</div><div class="h2" style="font-size:1.8rem">Contrôle de conformité au CGNC</div>',
                    unsafe_allow_html=True)
        st.caption("Avant toute conversion, la balance est auditée au regard du plan comptable CGNC : équilibre, existence des comptes, "
                   "sens des soldes et cohérences entre comptes.")
        k0 = st.columns(4)
        k0[0].markdown(kpi("filetext", "Comptes contrôlés", str(len(df))), unsafe_allow_html=True)
        k0[1].markdown(kpi("shield", "Comptes sans anomalie CGNC", f"{conf_cgnc:.0f} %"), unsafe_allow_html=True)
        k0[2].markdown(kpi("flag", "Erreurs", str(n_err)), unsafe_allow_html=True)
        k0[3].markdown(kpi("scale", "À vérifier", str(n_ver)), unsafe_allow_html=True)
        st.write("")
        if cgnc_df.empty:
            st.success("Aucune anomalie : la balance respecte les contrôles CGNC appliqués.")
        else:
            aff0 = cgnc_df.copy()
            aff0["Niveau"] = aff0["Niveau"].map(NIVEAUX).fillna(aff0["Niveau"])
            st.markdown(tableau_html(aff0.to_dict("records"), list(aff0.columns), premiere_en_gras=False), unsafe_allow_html=True)
        if n_err:
            st.warning("Des erreurs de conformité au CGNC subsistent : la conversion reste possible, "
                       "mais ses résultats ne sont fiables qu'après correction de la balance.")
        st.markdown('<div class="eyebrow">Étape 2 sur 2</div><div class="h2" style="font-size:1.8rem">Conversion vers les normes IAS / IFRS</div>',
                    unsafe_allow_html=True)
    if df is not None and not choix:
        st.warning("Sélectionnez au moins une norme à appliquer.")
    elif df is not None:
        res_statuts = statuts_comptes(df, choix)
        par_statut = compteurs_statuts(res_statuts)
        conf_n, conf_v = conformite(res_statuts)
        st.markdown('<div class="pipe"><span>✓ Importer</span><span>✓ Contrôler (CGNC)</span>'
                    '<span>✓ Convertir (IFRS)</span><span>✓ Restituer</span></div>', unsafe_allow_html=True)
        ph_statut = st.empty()   # statut de conformité : même fonction que le rapport PDF (rempli en fin d'exécution)
        k = st.columns(4)
        k[0].markdown(kpi("filetext", "Comptes analysés", str(len(df))), unsafe_allow_html=True)
        k[1].markdown(kpi("shield", sc.ETIQUETTE_COMPTES, f"{conf_n:.0f} %"), unsafe_allow_html=True)
        k[2].markdown(kpi("scale", sc.ETIQUETTE_VALEUR, f"{conf_v:.0f} %"), unsafe_allow_html=True)
        k[3].markdown(kpi("flag", "Comptes à traiter", str(len(df) - par_statut["inchangé"])), unsafe_allow_html=True)
        st.write("")
        g1, g2 = st.columns(2)
        g1.markdown(f'<div class="panel"><h3>Statut des comptes</h3>{donut_html(par_statut)}</div>', unsafe_allow_html=True)
        couleur = "#388E3C" if conf_n >= 80 else ("#E65100" if conf_n >= 50 else "#B94A48")
        definitions = "".join(f'<div class="def"><span class="dot" style="background:{COULEURS_STATUT[s]}"></span> '
                              f'<b>{s}</b> : {DEFINITIONS_STATUT[s]}</div>' for s in STATUTS)
        g2.markdown(f'<div class="panel"><h3>{sc.ETIQUETTE_COMPTES} (IFRS)</h3>{gauge_html(conf_n, couleur)}'
                    f'<div class="def" style="margin-bottom:.8rem">{sc.DEFINITION_COMPTES}</div>'
                    f'{definitions}</div>', unsafe_allow_html=True)
        with st.expander("Comment sont calculés ces indicateurs ?"):
            st.markdown(f"**{sc.ETIQUETTE_COMPTES}** = comptes au statut « inchangé » ÷ nombre total de comptes.  \n"
                        f"**{sc.ETIQUETTE_VALEUR}** = somme des soldes (en valeur absolue) des comptes « inchangés » ÷ somme "
                        "des soldes de tous les comptes.  \nLes statuts viennent des règles des normes sélectionnées ; "
                        "le motif de chaque statut est indiqué dans le tableau ci-dessous.  \n"
                        "**Ces pourcentages ne sont pas un taux de conformité** : le verdict de conformité est le *statut final* affiché ci-dessus, "
                        "qui tient compte des contrôles CGNC, de la TVA, des écritures, des informations manquantes et des contrôles non exécutés.")
        st.markdown('<div class="titre2">Détail des comptes</div>', unsafe_allow_html=True)
        filtre = st.multiselect("Filtrer par statut", STATUTS, default=STATUTS, key="filtre_statut")
        aff = res_statuts[res_statuts["statut"].isin(filtre)].copy()
        aff["statut"] = aff["statut"].map(lambda s: f"{PUCES[s]} {s}")
        aff = aff.rename(columns={"compte": "Compte", "libelle": "Libellé", "solde": "Solde (MAD)",
                                  "statut": "Statut", "motif": "Motif"})
        st.dataframe(aff[["Compte", "Libellé", "Solde (MAD)", "Statut", "Motif"]].style.format({"Solde (MAD)": "{:,.0f}"}),
                     hide_index=True, use_container_width=True)
        st.caption("Les états retraités (bilan, compte de résultat) sont présentés dans la page « Résultats », uniquement pour les documents "
                   "que vous avez importés ; la consolidation d'un groupe se fait dans la page « Consolidation ».")
        st.caption("Le détail des constats et l'état de situation se trouvent dans la page « Résultats ».")

constats_df = pd.DataFrame(columns=["Norme", "Niveau", "Constat", "Retraitement suggéré"])
etat = pd.DataFrame(columns=["Poste", "Montant"])
actif = passif = 0.0
conf_n = conf_v = 0.0
if df is not None:
    constats_df = pd.DataFrame([c for n in choix for c in NORMES[n](df)] + constats_docs,
                               columns=["Norme", "Niveau", "Constat", "Retraitement suggéré"])
    etat, actif, passif = etat_situation(df)
    traitements = traitements_cache(df, constats_df, choix, 0.20)
    manquantes_info, non_executes = cn.informations_et_controles(df, nature, traitements, detail_tva)
    if res_statuts is not None:  # enregistrement dans l'historique des transformations
        par_statut_h = compteurs_statuts(res_statuts)
        conf_n, conf_v = conformite(res_statuts)
        entree = {"cle": f"{fichier.name}|{fichier.size}", "fichier": fichier.name,
                  "date": datetime.now().strftime("%d/%m/%Y %H:%M:%S"), "comptes": int(len(df)),
                  "conformite": round(conf_n, 1), "valeur": round(conf_v, 1), "statuts": par_statut_h,
                  "constats": constats_df.to_dict("records"),
                  "conformite_cgnc": round(conf_cgnc, 1) if conf_cgnc is not None else None}
        signature = json.dumps([entree["cle"], entree["comptes"], entree["conformite"], par_statut_h,
                                entree["constats"]], sort_keys=True, ensure_ascii=False)
        if st.session_state.get("derniere_sig") != signature:
            histo = st.session_state["historique"]
            if histo and histo[-1].get("cle") == entree["cle"]:
                histo[-1] = entree
            else:
                histo.append(entree)
            del histo[:-50]
            sauver_historique(histo)
            st.session_state["derniere_sig"] = signature
if fichier is None:
    st.session_state["derniere_sig"] = None

# ---------------- CONTRÔLE FISCAL ----------------
fiscal = None
constats_fisc = pd.DataFrame(columns=["Norme", "Niveau", "Constat", "Retraitement suggéré"])
with t_fisc:
    st.markdown(bandeau("fisc", "Fiscalité", "Contrôle fiscal : résultat comptable → résultat fiscal"), unsafe_allow_html=True)
    if df is None:
        st.info("Importez d'abord une balance dans la page « Transformation » : le résultat comptable en est déduit.")
    else:
        st.markdown('<div class="note">Les règles appliquées (taux, cotisation minimale, report des déficits) sont listées ci-dessous avec leur référence, '
                    'leur date d\'application et leur niveau de vérification. <b>Aucun taux n\'est supposé :</b> l\'exercice doit être indiqué, et pour un '
                    'exercice antérieur à 2026 le taux est à saisir.</div>', unsafe_allow_html=True)
        with st.expander("Règles et paramètres fiscaux appliqués (références, dates, vérification)"):
            st.dataframe(pf.tableau_parametres(), hide_index=True, use_container_width=True)
            st.caption("Ces paramètres ont été recoupés avec des sources secondaires et non avec le texte officiel du CGI : validez-les auprès de la DGI "
                       "avant usage dans un dossier. Pour un exercice antérieur à 2026, les taux transitoires de la loi de finances 2023 ne sont pas intégrés.")
        x1, x2, x3 = st.columns([1, 2, 2])
        ex_choix = x1.selectbox("Exercice fiscal", ["(à choisir)"] + [str(a_) for a_ in range(2030, 2018, -1)], key="exercice_fiscal",
                                help="Année d'ouverture de l'exercice : détermine les taux applicables.")
        exercice_fisc = None if ex_choix == "(à choisir)" else int(ex_choix)
        cat_label = x2.selectbox("Catégorie de société (art. 19-I)", list(CATEGORIES_IS.values()), key="cat_societe_is")
        categorie = next(k for k, v in CATEGORIES_IS.items() if v == cat_label)
        ventes_reduit = x3.number_input("Ventes au taux de 0,15 % (MAD)", 0.0, 1e13, 0.0, step=10000.0, key="ventes_reduit",
                                        help="Ventes de produits pétroliers, gaz, beurre, huile, sucre, farine, eau, électricité "
                                             "et médicaments par des entreprises commerciales (art. 144-I-D, à confirmer sur le texte officiel).")
        q1, q2, q3 = st.columns([1, 2, 2])
        taux_pct = q1.number_input("Taux d'IS (%)", 0.0, 60.0, 0.0, step=0.25, disabled=(categorie != "libre"), key="taux_is_libre",
                                   help="Obligatoire pour « Autre taux » (0 = non renseigné : l'IS n'est pas calculé).")
        taux_libre = (taux_pct / 100) if taux_pct > 0 else None
        exclu_35 = q2.checkbox("Exclue du taux de 35 % (CFC, zone d'accélération industrielle, convention d'investissement ≥ 1,5 Md MAD)",
                               disabled=(categorie != "ordinaire"), key="exclu_35")
        exonere_cm = q3.checkbox("Société dans ses 36 premiers mois d'exploitation (exonérée de cotisation minimale)", key="exonere_cm")
        st.markdown("**Déficits fiscaux antérieurs** (CGI art. 12 : fraction ordinaire reportable sur 4 exercices ; fraction « amortissements » sans limite)")
        tab_def = st.data_editor(pf.deficits_vide(), num_rows="dynamic", hide_index=True, use_container_width=True, key="deficits_tab",
                                 column_config={pf.COLONNES_DEFICITS[0]: st.column_config.NumberColumn("Exercice du déficit", format="%d", min_value=2000, max_value=2100, step=1),
                                                pf.COLONNES_DEFICITS[1]: st.column_config.NumberColumn("Déficit total (MAD)", format="%.2f"),
                                                pf.COLONNES_DEFICITS[2]: st.column_config.NumberColumn("dont amortissements régulièrement comptabilisés (MAD)", format="%.2f")})
        deficits, det_def, avert_def = pf.deficits_disponibles(tab_def, exercice_fisc)
        for a_ in avert_def:
            st.warning(a_)
        if len(det_def):
            st.dataframe(det_def.style.format({"Ordinaire imputable": "{:,.0f}", "Amortissements imputables": "{:,.0f}"}), hide_index=True, use_container_width=True)
        pistes = pistes_fiscales(df)
        n_cp = int(df["compte"].str.startswith(("6", "7")).sum())
        st.markdown("**Ce que contient le fichier importé**")
        if n_cp == 0:
            st.warning("Aucun compte de charges ou de produits (classes 6 et 7) n'a été lu : le CPC est absent du fichier. "
                       "Le résultat comptable vaut donc 0 et aucune ligne fiscale ne peut être pré-remplie.")
        elif pistes.empty:
            st.info(f"{n_cp} compte(s) de charges/produits lus, mais aucun ne correspond à une dotation, une reprise, "
                    "une charge non courante ou des dividendes (comptes 619, 639, 659, 658, 719, 739, 759, 732). "
                    "Les tableaux ci-dessous sont donc à zéro : saisissez les montants à la main.")
        else:
            st.success(f"{n_cp} compte(s) de charges/produits lus. Comptes à examiner pour la liasse fiscale :")
            st.caption("Une dotation ou une reprise n'est pas automatiquement non déductible : à vous de décider de la part à retraiter.")
            st.dataframe(pistes.style.format({"Montant (MAD)": "{:,.0f}"}), hide_index=True, use_container_width=True)
        e1, e2 = st.columns(2)
        base_reint = tableau_fiscal_prerempli(LIGNES_REINT, df, COMPTES_AUTO_REINT)
        base_deduc = tableau_fiscal_prerempli(LIGNES_DEDUC, df, COMPTES_AUTO_DEDUC, produits=True)
        # La clé change quand la balance change (nouveaux montants pré-remplis) ; sinon les saisies sont conservées.
        cle_fisc = f"{fichier.name}_{fichier.size}_" + "_".join(f"{x:.2f}" for x in list(base_reint["Montant"]) + list(base_deduc["Montant"]))
        cfg_montant = {"Montant": st.column_config.NumberColumn("Montant (MAD)", format="%.2f", step=100.0)}
        e1.markdown("**Réintégrations fiscales**")
        reint = e1.data_editor(base_reint, num_rows="dynamic", hide_index=True, key=f"reint_{cle_fisc}",
                               use_container_width=True, column_config=cfg_montant)
        e2.markdown("**Déductions fiscales**")
        deduc = e2.data_editor(base_deduc, num_rows="dynamic", hide_index=True, key=f"deduc_{cle_fisc}",
                               use_container_width=True, column_config=cfg_montant)
        st.caption("Pré-remplissage depuis le fichier : amendes (6583), dons et cadeaux (6586, 61462, 61447), dotations aux provisions (6195, 6393, 6595), dividendes (7321 ou 732), reprises de provisions (7195, 7393, 7595). Un état de synthèse PDF ne contient que des comptes agrégés : saisissez le détail à la main. "
                   "Les autres lignes (amortissements, provisions, plus-values exonérées…) sont à saisir d'après votre analyse : "
                   "le résultat fiscal, l'IS et le tableau de passage se recalculent dès que vous modifiez un montant. "
                   "L'IS comptabilisé (compte 67) est déjà réintégré automatiquement : ne le ressaisissez pas.")
        fiscal = calc_fiscal(df, reint, deduc, deficits, categorie, exclu_35, taux_libre, exonere_cm, ventes_reduit, exercice_fisc)
        if not fiscal["is_calcule"]:
            st.warning("IS non calculé : " + fiscal["motif_taux"] + ((" " + fiscal["motif_cm"]) if fiscal["cm"] != fiscal["cm"] else ""))
        k = st.columns(4)
        k[0].markdown(kpi("chart", "Résultat comptable avant impôt", m(fiscal["rcav"])), unsafe_allow_html=True)
        k[1].markdown(kpi("scale", "Résultat fiscal net", m(fiscal["rf_net"])), unsafe_allow_html=True)
        k[2].markdown(kpi("receipt", "IS dû", m(fiscal["is_du"])), unsafe_allow_html=True)
        k[3].markdown(kpi("flag", "Écart avec l'IS comptabilisé", m(fiscal["ecart_is"])), unsafe_allow_html=True)
        st.markdown('<div class="titre2">Tableau de passage</div>', unsafe_allow_html=True)
        st.dataframe(tableau_passage(fiscal).style.format({"Montant (MAD)": "{:,.0f}"}), hide_index=True,
                     use_container_width=True)
        constats_fisc = pd.DataFrame(constats_fiscaux(fiscal), columns=["Norme", "Niveau", "Constat", "Retraitement suggéré"])
        for _, c in constats_fisc.iterrows():
            st.markdown(f"{NIVEAUX[c['Niveau']]} — {c['Constat']}" + (f"  \n*{c['Retraitement suggéré']}*" if c["Retraitement suggéré"] else ""))

# ---------------- CALCULATEURS ----------------
dt = pd.DataFrame()
ugt = pd.DataFrame()
with t_calc:
    st.markdown(bandeau("calc", "Outils", "Calculateurs IAS 12 et IAS 36"), unsafe_allow_html=True)
    s12, s36 = st.tabs(["IAS 12 – Impôts différés", "IAS 36 – Dépréciation"])
    with s12:
        taux_id = st.number_input("Taux d'impôt (%)", 0.0, 100.0, 20.0, key="taux_id") / 100
        if df is not None:
            base = base_ias12_depuis_balance(df)
            cle12 = "_".join(f"{x:.0f}" for x in base["Valeur comptable IFRS"])
            st.caption("Valeurs comptables lues dans la balance importée (nettes d'amortissements et de dépréciations). "
                       "La base fiscale est initialisée à la même valeur : saisissez la base fiscale réelle de chaque ligne "
                       "pour obtenir l'impôt différé (elle ne figure pas dans la balance). Le tableau se met à jour "
                       "quand vous changez de balance.")
        else:
            base = pd.DataFrame({"Libellé": ["Constructions"], "Nature": ["Actif"],
                                 "Valeur comptable IFRS": [500_000.0], "Base fiscale": [400_000.0]})
            cle12 = "exemple"
            st.caption("Exemple chiffré : importez une balance dans « Transformation » pour pré-remplir les valeurs comptables.")
        saisie = st.data_editor(base, num_rows="dynamic", use_container_width=True, key=f"ias12_{cle12}",
                                column_config={"Nature": st.column_config.SelectboxColumn(options=["Actif", "Passif"])})
        if not saisie.dropna(subset=["Libellé"]).empty:
            dt = calc_impots_differes(saisie, taux_id)
            st.dataframe(dt, hide_index=True, use_container_width=True)
            st.markdown(kpi("receipt", "Impôt différé net (positif = passif)", m(dt["Impôt différé"].sum())),
                        unsafe_allow_html=True)
    with s36:
        if df is not None:
            vc0, flux0 = valeurs_ias36_depuis_balance(df)
            jv0 = 0.0
            cle36 = f"{vc0:.0f}_{flux0:.0f}"
            st.caption("Valeur comptable de l'UGT = actif immobilisé net de la balance ; flux annuel indicatif = résultat net + "
                       "dotations aux amortissements d'exploitation (à remplacer par vos prévisions). La juste valeur nette "
                       "n'est pas dans la balance : saisissez-la (à 0, seule la valeur d'utilité est retenue).")
        else:
            vc0, jv0, flux0 = 1_000_000.0, 800_000.0, 150_000.0
            cle36 = "exemple"
            st.caption("Exemple chiffré : importez une balance dans « Transformation » pour pré-remplir ces valeurs.")
        a1, a2 = st.columns(2)
        vc = a1.number_input("Valeur comptable de l'UGT", value=vc0, step=10_000.0, key=f"vc36_{cle36}")
        jv = a1.number_input("Juste valeur - coûts de cession", value=jv0, step=10_000.0, key=f"jv36_{cle36}")
        taux = a2.number_input("Taux d'actualisation (%)", 0.0, 100.0, 0.0, step=0.25, key="taux36",
                               help="À saisir : aucun taux n'est supposé. IAS 36.55 : taux avant impôt reflétant les risques propres à l'actif.") / 100
        g = a2.number_input("Croissance à l'infini (%)", 0.0, 20.0, 0.0, step=0.25, key="g36",
                            help="0 = pas de valeur terminale croissante. IAS 36.33 : ne pas dépasser la croissance moyenne à long terme du secteur.") / 100
        st.caption("IAS 36.50 : les flux doivent exclure les flux de financement et d'impôt. Le flux pré-rempli (résultat net + dotations) n'est qu'un ordre de "
                   "grandeur issu de la balance : remplacez-le par vos prévisions avant toute conclusion.")
        flux_df = st.data_editor(pd.DataFrame({"Année": [1, 2, 3, 4, 5], "Flux de trésorerie": [flux0] * 5}),
                                 use_container_width=True, key=f"flux36_{cle36}")
        flux = flux_df["Flux de trésorerie"].fillna(0).tolist()
        if taux <= 0:
            st.info("Saisissez le taux d'actualisation : sans lui, ni la valeur d'utilité ni la perte de valeur ne sont calculées.")
            vu = recouvrable = perte = None
        elif taux <= g:
            st.error("La croissance à l'infini doit rester strictement inférieure au taux d'actualisation : la valeur terminale n'est pas calculable. "
                     "Aucune valeur d'utilité n'est affichée.")
            vu = recouvrable = perte = None
        else:
            vu = valeur_utilite(flux, taux, g)
            recouvrable = max(vu, jv)
            perte = max(0.0, vc - recouvrable)
        k1, k2, k3 = st.columns(3)
        k1.markdown(kpi("chart", "Valeur d'utilité", m(vu)), unsafe_allow_html=True)
        k2.markdown(kpi("target", "Valeur recouvrable", m(recouvrable)), unsafe_allow_html=True)
        k3.markdown(kpi("down", "Perte de valeur", m(perte)), unsafe_allow_html=True)
        if vu is not None:
            ugt = pd.DataFrame({"Indicateur": ["Valeur comptable", "Juste valeur nette", "Valeur d'utilité",
                                               "Valeur recouvrable", "Perte de valeur"],
                                "Montant": [vc, jv, vu, recouvrable, perte]})


# ---------------- CONSOLIDATION ET COMPARAISON ----------------
conso_groupe = None
comparaison = None


def _nombre_saisi(texte):
    """Nombre saisi à la main (espaces, virgule décimale). Vide ou illisible : None."""
    s_ = str(texte or "").replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        return float(s_) if s_ else None
    except ValueError:
        return None


def lire_balance_entite(nom, octets):
    """Balance d'une société du groupe : Excel, CSV (balance ou liste d'écritures) ou PDF."""
    if nom.lower().endswith(".pdf"):
        brut_, _at = lire_pdf_cache(octets)
        if brut_ is None or brut_.empty:
            raise ValueError("aucun compte reconnu dans ce PDF")
        return preparer_balance(brut_)
    d_, _at = charger_balance_cache(octets, nom)
    return d_


def _tableau_conso(tab):
    t_ = conso_vers_texte(tab)
    types_ = t_["Type"].tolist()
    aff_ = t_[["Poste", "Montant"]].rename(columns={"Montant": "Milliers de MAD"})
    sty_ = (aff_.style.apply(lambda col: ["font-weight:700;background-color:#D7EDD8" if ty != "ligne" else "" for ty in types_], axis=0)
            .set_properties(subset=["Milliers de MAD"], **{"text-align": "right"}))
    st.dataframe(sty_, hide_index=True, use_container_width=True, height=min(38 * (len(aff_) + 1) + 4, 760))


def afficher_conso_groupe(c):
    k_ = st.columns(4)
    k_[0].markdown(kpi("bank", "Total actif consolidé (k MAD)", f"{c['total_actif'] / 1000:,.0f}".replace(",", " ")), unsafe_allow_html=True)
    k_[1].markdown(kpi("chart", "Chiffre d'affaires (k MAD)", f"{c['ca'] / 1000:,.0f}".replace(",", " ")), unsafe_allow_html=True)
    k_[2].markdown(kpi("scale", "Résultat net part du groupe (k MAD)", f"{c['resultat_groupe'] / 1000:,.0f}".replace(",", " ")), unsafe_allow_html=True)
    k_[3].markdown(kpi("layers", "Intérêts ne donnant pas le contrôle (k MAD)", f"{c['nci'] / 1000:,.0f}".replace(",", " ")), unsafe_allow_html=True)
    st.write("")
    for v_ in c["verifications"]:
        st.warning("À vérifier : " + v_)
    if c.get("donnees_manquantes"):
        st.markdown("**Données manquantes ou non saisies** (le résultat en dépend) :")
        for dm_ in c["donnees_manquantes"]:
            st.markdown("- " + dm_)
    onglets_ = st.tabs(["Bilan actif", "Bilan passif", "Compte de résultat", "Périmètre", "Éliminations"])
    with onglets_[0]:
        _tableau_conso(c["actif"])
    with onglets_[1]:
        _tableau_conso(c["passif"])
    with onglets_[2]:
        _tableau_conso(c["cpc"])
    with onglets_[3]:
        if c["perimetre"].empty:
            st.info("Aucune filiale dans le périmètre.")
        else:
            st.dataframe(c["perimetre"].style.format({col: "{:,.0f}" for col in c["perimetre"].columns if col not in ("Société", "Méthode")}),
                         hide_index=True, use_container_width=True)
    with onglets_[4]:
        if c["eliminations"].empty:
            st.info("Aucune élimination intragroupe déclarée : les opérations entre sociétés du groupe restent dans les comptes. "
                    "Renseignez le tableau « Opérations intragroupe » ci-dessus.")
        else:
            st.dataframe(c["eliminations"].style.format({"Montant (MAD)": "{:,.0f}"}), hide_index=True, use_container_width=True)
    with st.expander("Hypothèses et limites"):
        for n_ in c["notes"]:
            st.markdown("- " + n_)


with t_conso:
    st.markdown(bandeau("conso", "Groupe", "Consolidation et comparaison avec les comptes publiés"), unsafe_allow_html=True)
    sc1, sc2 = st.tabs(["Consolidation du groupe", "Comparaison avec les comptes officiels"])
    with sc1:
        st.markdown('<div class="note">Les comptes consolidés exigent les comptes <b>de la société mère et de chaque filiale</b>, le périmètre '
                    '(pourcentages de contrôle et d\'intérêt, méthode), le coût des titres, les capitaux propres à la date d\'acquisition et les '
                    'opérations intragroupe. Sans ces données, aucun compte consolidé n\'est produit.</div>', unsafe_allow_html=True)
        fichiers_g = st.file_uploader("Balances de la société mère et de chaque filiale (une par société : Excel, CSV ou PDF)",
                                      type=["xlsx", "xls", "csv", "pdf"], accept_multiple_files=True, key="fich_groupe")
        if len(fichiers_g or []) < 2:
            st.info("Importez au moins deux balances (la mère et une filiale). Un document isolé ne permet pas de consolider : "
                    "utilisez la page « Résultats » pour les comptes retraités d'une seule société.")
        else:
            lus_g, err_g = [], []
            for f_ in fichiers_g:
                try:
                    lus_g.append((f_.name, f_.size, lire_balance_entite(f_.name, f_.getvalue())))
                except Exception as e_:
                    err_g.append(f"{f_.name} : {e_}")
            for t_err in err_g:
                st.error("Lecture impossible — " + t_err)
            if len(lus_g) < 2:
                st.warning("Moins de deux balances lisibles : la consolidation n'est pas possible.")
            else:
                n_g = len(lus_g)
                cle_g = "_".join(f"{n_}{s_}" for n_, s_, _ in lus_g)
                c_ref, c_tx = st.columns([2, 1])
                ref_conso = c_ref.radio("Référentiel de consolidation", ["IFRS", "CGNC"], horizontal=True, key="ref_conso",
                                        help="IFRS : IFRS 10 / IFRS 3 / IAS 28 / IFRS 11 (l'intégration proportionnelle n'est pas admise pour les co-entreprises). "
                                             "CGNC : intégration proportionnelle possible, frais préliminaires conservés.")
                taux_g = c_tx.number_input("Taux d'impôt différé (%)", 0.0, 100.0, 20.0, step=0.5, key="taux_conso_g") / 100
                base_g = pd.DataFrame({
                    "Société": [Path(n_).stem for n_, _, _ in lus_g],
                    "Rôle": ["Mère"] + ["Filiale"] * (n_g - 1),
                    "Méthode": ["IG"] * n_g,
                    "% contrôle": [float("nan")] * n_g, "% intérêt": [float("nan")] * n_g,   # colonnes numériques vides (NaN) : à saisir, rien n'est supposé
                    "Coût des titres (MAD)": [float("nan")] * n_g, "Capitaux propres à l'acquisition (MAD)": [float("nan")] * n_g,
                    "Écart d'évaluation (MAD)": [0.0] * n_g, "Dividendes versés à la mère (MAD)": [0.0] * n_g,
                    "Contrôle de fait": [False] * n_g})
                tit_mere = float(lus_g[0][2].loc[lus_g[0][2]["compte"].astype(str).str.startswith("251"), "solde"].sum())
                st.markdown("**Périmètre de consolidation** (à compléter : la première balance est supposée être celle de la mère)")
                st.caption(f"Titres de participation inscrits à l'actif de la première société (compte 251) : {tit_mere:,.0f} MAD, à répartir entre les filiales. ".replace(",", " ")
                           + "Coût des titres et capitaux propres à l'acquisition se lisent dans les comptes de la mère et de la filiale à la date d'entrée dans le groupe.")
                par_g = st.data_editor(
                    base_g, hide_index=True, use_container_width=True, key=f"perim_{cle_g}",
                    column_config={
                        "Rôle": st.column_config.SelectboxColumn("Rôle", options=["Mère", "Filiale", "Hors périmètre"], required=True),
                        "Méthode": st.column_config.SelectboxColumn("Méthode", options=["IG", "IP", "MEE"], required=True,
                                                                    help="IG : intégration globale ; IP : proportionnelle (CGNC) ; MEE : mise en équivalence"),
                        "% contrôle": st.column_config.NumberColumn("% contrôle", min_value=0.0, max_value=100.0, format="%.2f"),
                        "% intérêt": st.column_config.NumberColumn("% intérêt", min_value=0.0, max_value=100.0, format="%.2f"),
                        "Coût des titres (MAD)": st.column_config.NumberColumn(format="%.0f"),
                        "Capitaux propres à l'acquisition (MAD)": st.column_config.NumberColumn(format="%.0f"),
                        "Écart d'évaluation (MAD)": st.column_config.NumberColumn(format="%.0f"),
                        "Dividendes versés à la mère (MAD)": st.column_config.NumberColumn(format="%.0f"),
                        "Contrôle de fait": st.column_config.CheckboxColumn("Contrôle de fait"),
                    })
                ent_g, dividendes_g, noms_vus = [], {}, set()
                for (nom_f, _s, bal_), (_, r_) in zip(lus_g, par_g.iterrows()):
                    if r_["Rôle"] == "Hors périmètre":
                        continue
                    nom_s = str(r_["Société"]).strip() or Path(nom_f).stem
                    num_ = lambda v_: None if pd.isna(v_) else float(v_)
                    ent_g.append({"nom": nom_s, "role": "mere" if r_["Rôle"] == "Mère" else "filiale", "methode": r_["Méthode"],
                                  "pct_controle": num_(r_["% contrôle"]) or 0.0, "pct_interet": num_(r_["% intérêt"]) or 0.0,
                                  "cout_titres": num_(r_["Coût des titres (MAD)"]), "fp_acquisition": num_(r_["Capitaux propres à l'acquisition (MAD)"]),
                                  "ecart_evaluation": num_(r_["Écart d'évaluation (MAD)"]) or 0.0, "controle_de_fait": bool(r_["Contrôle de fait"]), "df": bal_})
                    if r_["Rôle"] != "Mère" and (num_(r_["Dividendes versés à la mère (MAD)"]) or 0) > 0:
                        dividendes_g[nom_s] = num_(r_["Dividendes versés à la mère (MAD)"])
                    noms_vus.add(nom_s)
                noms_ent = [e_["nom"] for e_ in ent_g]
                if len(set(noms_ent)) != len(noms_ent):
                    st.error("Deux sociétés portent le même nom : renommez-les dans la colonne « Société ».")
                else:
                    st.markdown("**Opérations intragroupe à éliminer** (créances et dettes, ventes et achats, marges en stock ou en immobilisation, prêts)")
                    st.caption("Seules les éliminations saisies ici sont effectuées. Une opération non déclarée reste dans les comptes consolidés.")
                    intra_g = st.data_editor(
                        pd.DataFrame({"Type": pd.Series(dtype="object"), "Entité A": pd.Series(dtype="object"),
                                      "Entité B": pd.Series(dtype="object"), "Montant": pd.Series(dtype="float")}),
                        num_rows="dynamic", hide_index=True, use_container_width=True, key=f"intra_{cle_g}_{'_'.join(noms_ent)}",
                        column_config={
                            "Type": st.column_config.SelectboxColumn("Type", options=conso_grp.TYPES_ELIMINATION, required=True),
                            "Entité A": st.column_config.SelectboxColumn("Entité A (vendeur / prêteur / créancier)", options=noms_ent, required=True),
                            "Entité B": st.column_config.SelectboxColumn("Entité B (acheteur / emprunteur / débiteur)", options=noms_ent, required=True),
                            "Montant": st.column_config.NumberColumn("Montant (MAD)", format="%.0f")})
                    intra_ok = intra_g.dropna(subset=["Type", "Entité A", "Entité B", "Montant"])
                    try:
                        conso_groupe = conso_grp.consolider(ent_g, intra_ok, ref_conso, taux_g, dividendes_g)
                    except ValueError as e_:
                        st.error("**" + str(e_).split(" : ", 1)[0] + "**")
                        for ligne_ in str(e_).split(" : ", 1)[-1].split(" | "):
                            st.markdown("- " + ligne_)
                        conso_groupe = None
                    if conso_groupe is not None:
                        st.success("Comptes consolidés calculés à partir des données du périmètre déclaré. Vérifiez chaque paramètre : le résultat n'est "
                                   "aussi fiable que les coûts de titres, capitaux propres à l'acquisition et éliminations saisis.")
                        afficher_conso_groupe(conso_groupe)
    with sc2:
        st.markdown('<div class="note">Les chiffres officiels sont à saisir (ou à importer) d\'après la publication de l\'émetteur : rapport financier, '
                    'site de la société, Bourse de Casablanca, AMMC. L\'application n\'en contient aucun. La comparaison n\'est valable que pour le '
                    '<b>même exercice, le même référentiel, la même devise et le même périmètre</b> ; les résultats calculés ne sont jamais modifiés '
                    'pour se rapprocher des chiffres officiels.</div>', unsafe_allow_html=True)

        def _poste(tab_, nom_):
            ligne_ = tab_.loc[tab_["Poste"] == nom_, "Montant"]
            return None if ligne_.empty else float(ligne_.iloc[0])
        sources_c = {}
        if conso_groupe is not None:
            sources_c["Comptes consolidés du groupe (page Consolidation)"] = (
                {"total_actif": conso_groupe["total_actif"], "total_passif": conso_groupe["total_passif"], "ca": conso_groupe["ca"],
                 "resultat_net": conso_groupe["resultat_groupe"]},
                {"Périmètre": "Consolidé (groupe)", "Unité": "MAD"}, {"nci_resultat": float(conso_groupe["cpc"].loc[
                    conso_groupe["cpc"]["Poste"] == "Résultat des intérêts ne donnant pas le contrôle", "Montant"].sum()) * 1000})
        if df is not None:
            dispo_s = conso_disponible(inventaire, df)
            if dispo_s:
                c_s = etats_consolides(df, 0.20, 0, dispo_s)
                sources_c["Comptes retraités de la société importée (société seule)"] = (
                    {"total_actif": _poste(c_s["actif"], "TOTAL ACTIF") if "actif" in dispo_s else None,
                     "total_passif": _poste(c_s["passif"], "TOTAL PASSIF") if "passif" in dispo_s else None,
                     "ca": _poste(c_s["cpc"], "Chiffre d'affaires") if "cpc" in dispo_s else None,
                     "resultat_net": _poste(c_s["cpc"], "Résultat net part Groupe") if "cpc" in dispo_s else None},
                    {"Périmètre": "Social (société seule)", "Unité": "Milliers de MAD"}, {})
        if not sources_c:
            st.info("Aucun résultat calculé à comparer : importez d'abord une balance (page « Transformation ») ou consolidez un groupe.")
        else:
            nom_src = st.selectbox("Résultats calculés à comparer", list(sources_c), key="src_comp")
            vals_c, meta_src, ctx_c = sources_c[nom_src]
            e1, e2 = st.columns(2)
            ex_c = e1.text_input("Exercice des comptes calculés", placeholder="ex. 2025", key="ex_calc").strip()
            ref_c = e2.selectbox("Référentiel appliqué aux comptes calculés", [cmp_off.A_CHOISIR] + cmp_off.REFERENTIELS[:2], key="ref_calc",
                                 help="À indiquer : l'application ne le suppose pas.")
            st.markdown("**Comptes officiels publiés**")
            mode_o = st.radio("Source des chiffres officiels", ["Saisie manuelle", "Import d'un fichier (modèle Excel)"], horizontal=True, key="mode_off")
            modele_x = io.BytesIO()
            cmp_off.modele_officiel().to_excel(modele_x, index=False)
            st.download_button("Télécharger le modèle de fichier (comptes officiels)", modele_x.getvalue(), "modele_comptes_officiels.xlsx", key="dl_modele_off")
            off_vals, off_meta, societe_off = None, None, ""
            if mode_o == "Saisie manuelle":
                societe_off = st.text_input("Société cotée", placeholder="nom de la société", key="soc_off")
                o1, o2, o3, o4 = st.columns(4)
                ex_o = o1.text_input("Exercice publié", placeholder="ex. 2025", key="ex_off").strip()
                ref_o = o2.selectbox("Référentiel publié", [cmp_off.A_CHOISIR] + cmp_off.REFERENTIELS, key="ref_off")
                per_o = o3.selectbox("Périmètre publié", [cmp_off.A_CHOISIR] + cmp_off.PERIMETRES, key="per_off")
                uni_o = o4.selectbox("Unité des montants publiés", [cmp_off.A_CHOISIR] + list(cmp_off.UNITES), key="uni_off")
                dev_o = st.selectbox("Devise des montants publiés", [cmp_off.A_CHOISIR] + cmp_off.DEVISES, key="dev_off")
                v1, v2, v3, v4 = st.columns(4)
                off_vals = {"total_actif": _nombre_saisi(v1.text_input("Total actif", key="v_ta")),
                            "total_passif": _nombre_saisi(v2.text_input("Total passif", key="v_tp")),
                            "ca": _nombre_saisi(v3.text_input("Chiffre d'affaires", key="v_ca")),
                            "resultat_net": _nombre_saisi(v4.text_input("Résultat net part du groupe", key="v_rn"))}
                off_meta = {"Exercice": ex_o, "Référentiel": ref_o, "Périmètre": per_o, "Unité": uni_o, "Devise": dev_o}
            else:
                f_off = st.file_uploader("Fichier des comptes officiels (modèle Excel ou CSV)", type=["xlsx", "xls", "csv"], key="fich_off")
                if f_off is not None:
                    try:
                        tab_off = pd.read_csv(f_off, sep=None, engine="python") if f_off.name.lower().endswith(".csv") else pd.read_excel(f_off)
                        lignes_off = cmp_off.lire_officiel(tab_off)
                        choix_off = st.selectbox("Société et exercice", range(len(lignes_off)),
                                                 format_func=lambda i_: f"{lignes_off[i_]['Société']} — {lignes_off[i_]['Exercice']}", key="choix_off")
                        l_ = lignes_off[choix_off]
                        societe_off = str(l_["Société"])
                        off_vals = {k_: l_.get(k_) for k_, _ in cmp_off.INDICATEURS}
                        off_meta = {"Exercice": l_["Exercice"], "Référentiel": l_["Référentiel"], "Périmètre": l_["Périmètre"],
                                    "Unité": l_["Unité"], "Devise": l_["Devise"]}
                        listes_ = {"Référentiel": cmp_off.REFERENTIELS, "Périmètre": cmp_off.PERIMETRES, "Unité": list(cmp_off.UNITES), "Devise": cmp_off.DEVISES}
                        for champ_, opts_ in listes_.items():   # champ absent du fichier : à compléter, jamais supposé
                            if not off_meta[champ_]:
                                off_meta[champ_] = st.selectbox(f"{champ_} (absent du fichier : à renseigner)", [cmp_off.A_CHOISIR] + opts_, key=f"compl_{champ_}")
                        if not off_meta["Exercice"]:
                            off_meta["Exercice"] = st.text_input("Exercice (absent du fichier : à renseigner)", key="compl_exercice").strip()
                        if l_.get("Source"):
                            st.caption("Source indiquée : " + l_["Source"])
                    except Exception as e_:
                        st.error(f"Fichier illisible : {e_}")
            if off_vals is not None and any(v_ is not None for v_ in off_vals.values()):
                meta_calc = {"Exercice": ex_c, "Référentiel": ref_c, "Périmètre": meta_src["Périmètre"], "Unité": meta_src["Unité"], "Devise": "MAD"}
                comparaison = cmp_off.comparer(vals_c, off_vals, meta_calc, off_meta, ctx_c)
                comparaison["societe"] = societe_off
                st.markdown(f"**Résultat : {comparaison['statut']}**")
                for pb_ in comparaison["problemes"]:
                    (st.warning if comparaison.get("manquants") else st.error)(pb_)
                tab_c = comparaison["tableau"]
                st.dataframe(tab_c[["Indicateur", "Calculé (MAD)", "Officiel (MAD)", "Écart (MAD)", "Écart (%)", "Statut"]].style.format(
                    {"Calculé (MAD)": "{:,.0f}", "Officiel (MAD)": "{:,.0f}", "Écart (MAD)": "{:,.0f}", "Écart (%)": "{:.2f}"}, na_rep="—"),
                    hide_index=True, use_container_width=True)
                for _, l_ in tab_c.iterrows():
                    if l_["Statut"] not in ("Conforme (arrondis)",):
                        orig_ = l_["Origines possibles de l'écart"]
                        st.markdown(f"**{l_['Indicateur']}** : {orig_}")
                st.caption("Aucun résultat calculé n'a été ajusté. Les origines possibles sont des pistes à vérifier dans le rapport de l'émetteur, "
                           "pas des causes établies.")
            else:
                st.info("Saisissez au moins un montant officiel pour lancer la comparaison.")


# ---------------- RÉSULTATS ----------------
with t_res:
    st.markdown(bandeau("res", "Résultats", "Constats et états IFRS"), unsafe_allow_html=True)
    if df is None:
        st.info("Importez d'abord une balance dans la page « Transformation ».")
    else:
        tous = pd.concat([constats_df, constats_fisc], ignore_index=True)
        affiche = tous.copy()
        affiche["Niveau"] = affiche["Niveau"].map(NIVEAUX)
        st.markdown("#### Constats et retraitements suggérés")
        st.markdown(tableau_html(affiche.to_dict("records"), list(affiche.columns), premiere_en_gras=False), unsafe_allow_html=True)
        refs_liees = sorted({r for t in tous["Norme"].astype(str) for r in re.findall(r"(?:IAS|IFRS) \d+", t) if r in FICHES},
                            key=lambda x: (x[:3] != "IAS", int(x.split()[1])))
        if refs_liees:
            st.markdown("#### Normes et interprétations liées à ces constats")
            for rf in refs_liees:
                with st.expander(f"{rf} — {FICHES[rf]['titre']}"):
                    afficher_fiche(rf)
        if nature.get("complets") is False:
            st.warning("**Résultats partiels** : " + " ".join(nature["raisons"]))
        if qualite:
            st.markdown(f"**Qualité des données importées : {qualite['score']}/100 ({qualite['niveau']})** — {qualite['avertissement']}")
        st.markdown("#### Traitement par norme : état réel de chaque contrôle")
        n_auto_ = int(traitements["Statut du contrôle"].str.startswith("Exécuté").sum())
        n_info_ = int(traitements["Statut du contrôle"].str.contains("informations complémentaires").sum())
        n_so_ = int(traitements["Statut du contrôle"].str.startswith(("Sans objet", "Non applicable")).sum())
        n_ns_ = int(traitements["Statut du contrôle"].str.startswith("Non sélectionnée").sum())
        st.caption(f"{n_auto_} norme(s) réellement exécutée(s) sur ce document, dont {n_info_} dont le chiffrage exige des informations complémentaires ; "
                   f"{n_so_} sans objet ou non applicable ; {n_ns_} non sélectionnée(s). Seules les normes marquées « Exécuté » ont été contrôlées.")
        st.dataframe(traitements, hide_index=True, use_container_width=True)
        consult_ = cn.normes_non_automatisees(list(FICHES), list(NORMES))
        with st.expander(f"{len(consult_)} norme(s) consultable(s) au catalogue mais non automatisée(s) : aucun contrôle n'est exécuté pour elles"):
            st.write(", ".join(consult_))
            st.caption("Ces normes se consultent dans la page « Normes IAS/IFRS » ; leur application relève de l'annexe ou d'un retraitement manuel.")
        if manquantes_info or non_executes:
            with st.expander(f"Informations manquantes ({len(manquantes_info)}) et contrôles non exécutés ou non applicables ({len(non_executes)})"):
                for x_ in manquantes_info:
                    st.markdown("- **Information manquante** — " + x_)
                for x_ in non_executes:
                    st.markdown("- *Contrôle non exécuté ou non applicable* — " + x_)
        st.markdown("#### État de situation financière (IAS 1)")
        st.dataframe(etat.style.format({"Montant": "{:,.0f}"}), hide_index=True, use_container_width=True)
        st.write(f"Total actif : **{m(actif)}** | Total passif : **{m(passif)}**")
        dispo_c = conso_disponible(inventaire)
        conso = None
        if dispo_c:
            st.markdown("#### Comptes sociaux retraités selon les normes IFRS, société seule (milliers de dirhams)")
            noms_c = {"actif": "bilan actif", "passif": "bilan passif", "cpc": "compte de résultat", "retr": "retraitements"}
            st.caption("États présentés d'après les documents importés : " + ", ".join(noms_c[k_] for k_ in ("actif", "passif", "cpc", "retr") if k_ in dispo_c) + ".")
            q1_, q2_ = st.columns(2)
            taux_conso = (q1_.number_input("Taux d'impôt différé des retraitements (%)", 0.0, 100.0, 20.0, step=0.5, key="taux_conso") / 100
                          if "retr" in dispo_c else 0.20)
            nb_act = (q2_.number_input("Nombre d'actions (pour le résultat par action, facultatif)", 0, 10**10, 0, step=1000, key="nb_actions")
                      if "cpc" in dispo_c else 0)
            conso = etats_consolides(df, taux_conso, nb_act, dispo_c)
            afficher_conso(conso)
        else:
            st.info("Les comptes consolidés ne sont pas produits pour ce type de document (factures, ESG, tableau de financement, ETIC). "
                    "Importez une balance, un bilan ou un CPC pour les obtenir.")
        comparatif = cn.comparatif_cgnc_ifrs(df, (taux_conso if (dispo_c and "retr" in dispo_c) else 0.20))
        st.markdown("#### Comptes avant (CGNC) et après (IFRS) retraitement")
        st.dataframe(comparatif.style.format({"CGNC (MAD)": "{:,.0f}", "IFRS (MAD)": "{:,.0f}", "Écart (MAD)": "{:+,.0f}"}), hide_index=True, use_container_width=True)
        st.caption("Seuls les retraitements chiffrés par l'application figurent ici (non-valeurs, provisions réglementées, impôts différés associés). "
                   "Les autres sujets signalés dans le tableau par norme exigent des informations complémentaires et ne sont donc pas chiffrés.")
        # ---- rapport de conformité : construit UNE fois ; son statut est repris partout (page Transformation, tableau de bord, PDF) ----
        rap = None
        try:
            donnees_rap = df[["compte", "libelle", "debit", "credit", "solde"]].rename(
                columns={"compte": "Compte", "libelle": "Libellé", "debit": "Débit (MAD)", "credit": "Crédit (MAD)", "solde": "Solde (MAD)"})
            sans_objet = [n_ for n_ in choix if not tous["Norme"].astype(str).apply(
                lambda x_, n2_=n_: any(p_.strip() in x_ for p_ in n2_.split("/"))).any()]
            ecr_ko = any(x_.get("Niveau") == "Erreur" and "pièce" in x_.get("Contrôle", "").lower() + x_.get("Détail", "").lower()
                         for x_ in (ecr_controles or []))
            ecr_df = pd.DataFrame(ecr_controles) if ecr_controles else None
            rap = rapport_final.rapport_dict(
                fichier.name if fichier else "", lignes_doc, donnees_rap, cgnc_df, tous, comparaison, conso_groupe, ecr_df,
                texte_donnees=f"{len(df)} compte(s) lu(s) ; total débit {m(df['debit'].sum())}, total crédit {m(df['credit'].sum())}.",
                normes_non_appliquees=", ".join(sans_objet), pieces_ko=ecr_ko, traitements=traitements, comparatif=comparatif,
                qualite=qualite, nature=nature, manquantes=manquantes_info, non_executes=non_executes,
                fiscal_parametres=(pf.tableau_parametres() if fiscal is not None else None),
                texte_fiscal=((f"Exercice {fiscal.get('exercice') or 'non indiqué'} ; " + fiscal.get("motif_taux", "") + ". " + fiscal.get("motif_cm", ""))
                              if fiscal is not None else None),
                comptes_retraites=tcomp.comptes_retraites(res_statuts) if res_statuts is not None else None,
                indicateurs=sc.indicateurs(conf_n, conf_v, len(df)))
            if ph_statut is not None:   # même statut que le rapport PDF, affiché dans la page Transformation
                ph_statut.markdown(sc.bandeau_statut_html(*rap["statut"], source="Statut calculé par les mêmes règles que le rapport PDF (voir la page Résultats)."),
                                   unsafe_allow_html=True)
        except Exception as e:
            st.error(f"Rapport de conformité indisponible : {e}")
        try:
            sortie_xlsx = excel_cache(df, tous, etat, res_statuts, conf_n, conf_v, fiscal, dt, ugt,
                                      fichier.name if fichier else "", conso, (rap["statut"] if rap else None))
        except Exception as e:
            sortie_xlsx = b""
            st.error(f"Export Excel indisponible : {e}")
        st.markdown("#### Rapport synthétique et résultats de l'analyse")
        st.caption("Restitution courte à lire ou à présenter : statut des comptes, constats et retraitements, état de situation financière, comptes consolidés, "
                   "contrôle fiscal, IAS 12 et IAS 36 lorsqu'ils ont été calculés. Le fichier Excel reprend les mêmes résultats sous forme de tableaux exploitables.")
        b1, b2 = st.columns(2)
        if res_statuts is not None:
            try:
                pdf_octets = pdf_cache(df, tous, etat, res_statuts, conf_n, conf_v, fiscal, dt, ugt,
                                       fichier.name if fichier else "", conso, (rap["statut"] if rap else None))
                b1.download_button("Rapport synthétique – PDF", pdf_octets, "rapport_synthetique_maroifrs.pdf", "application/pdf",
                                   icon=":material/picture_as_pdf:", use_container_width=True, key="dl_syn_pdf")
            except Exception as e:
                b1.error(f"Rapport synthétique indisponible : {e}")
        b2.download_button("Résultats de l'analyse – Excel", sortie_xlsx, "resultats_analyse_maroifrs.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", icon=":material/table_view:",
                           use_container_width=True, key="dl_res_xlsx")

        # ---- rapport de conformité complet (document identifié, données, contrôles, normes, consolidation, comparaison) ----
        st.markdown("#### Rapport détaillé de conformité")
        st.caption("Dossier complet, sans abrègement : documents identifiés, données extraites, contrôles CGNC (dont TVA), anomalies, normes IAS/IFRS applicables, "
                   "retraitements, consolidation et comparaison avec les comptes officiels si elles ont été réalisées, et le statut final de conformité. "
                   "Le PDF (avec sommaire) se lit et s'archive ; l'Excel permet de filtrer et de retrouver chaque ligne.")
        if rap is not None:
            try:
                st.markdown(sc.bandeau_statut_html(*rap["statut"], source="Même statut que celui du rapport PDF et de la page Transformation."), unsafe_allow_html=True)
                r1, r2 = st.columns(2)
                r1.download_button("Rapport détaillé de conformité – PDF", _en_cache(rapport_detaille_pdf_cache, rapport_final.construire_pdf, rap),
                                   "rapport_detaille_conformite_maroifrs.pdf", "application/pdf", icon=":material/picture_as_pdf:",
                                   use_container_width=True, key="dl_rap_pdf")
                r2.download_button("Rapport détaillé de conformité – Excel", _en_cache(rapport_detaille_excel_cache, rapport_final.construire_excel, rap),
                                   "rapport_detaille_conformite_maroifrs.xlsx",
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", icon=":material/table_view:",
                                   use_container_width=True, key="dl_rap_xlsx")
            except Exception as e:
                st.error(f"Rapport de conformité indisponible : {e}")

# ---------------- TABLEAU DE BORD ----------------
with t_dash:
    st.markdown(bandeau("dash", "Synthèse", "Tableau de bord"), unsafe_allow_html=True)
    histo = st.session_state["historique"]
    if df is not None:
        a_cpc_d = bool(df["compte"].astype(str).str.startswith(("6", "7")).any())
        a_bil_d = bool(df["compte"].astype(str).str.startswith(("1", "2", "3", "4", "5")).any())
        part_ = " (partiel : factures seules)" if df.attrs.get("factures") else ""
        k = st.columns(5)
        k[0].markdown(kpi("coins", "Chiffre d'affaires" + part_, m(-solde(df, "711", "712")) if a_cpc_d else "n/c : CPC absent"), unsafe_allow_html=True)
        k[1].markdown(kpi("chart", "Résultat net" + part_, m(-solde(df, "6", "7")) if a_cpc_d else "n/c : CPC absent"), unsafe_allow_html=True)
        k[2].markdown(kpi("bank", "Total actif" + part_, m(actif) if a_bil_d else "n/c : bilan absent"), unsafe_allow_html=True)
        k[3].markdown(kpi("flag", "Constats", str(len(constats_df) + len(constats_fisc))), unsafe_allow_html=True)
        k[4].markdown(kpi("shield", "Qualité des données", f"{qualite['score']}/100" if qualite else "n/c"), unsafe_allow_html=True)
        if nature.get("complets") is False:
            st.warning("Indicateurs partiels : " + " ".join(nature["raisons"]))
        st.write("")
    if not histo:
        st.info("Aucune transformation enregistrée : importez une balance dans la page « Transformation ».")
    else:
        g1, g2 = st.columns([3, 2])
        with g1:
            st.markdown('<div class="titre2" style="margin-top:0">Évolution de la conformité par transformation</div>',
                        unsafe_allow_html=True)
            st.altair_chart(graph_evolution(histo), use_container_width=True)
        with g2:
            st.markdown(f'<div class="panel"><h3>Statut des comptes (dernier fichier)</h3>{donut_html(histo[-1]["statuts"])}</div>',
                        unsafe_allow_html=True)
        st.markdown('<div class="titre2">Répartition des statuts par transformation</div>', unsafe_allow_html=True)
        st.altair_chart(graph_statuts(histo), use_container_width=True)
    if df is not None and not constats_df.empty:
        st.write("")
        g1, g2 = st.columns(2)
        g1.markdown('<div class="titre2" style="margin-top:0">Répartition des constats par niveau</div>', unsafe_allow_html=True)
        g1.altair_chart(graph_niveaux(constats_df), use_container_width=True)
        g2.markdown('<div class="titre2" style="margin-top:0">Structure de l\'état de situation</div>', unsafe_allow_html=True)
        g2.altair_chart(graph_etat(etat), use_container_width=True)
        gn = graph_normes(constats_df)
        if gn is not None:
            st.markdown('<div class="titre2">Normes concentrant le plus de constats</div>', unsafe_allow_html=True)
            st.altair_chart(gn, use_container_width=True)

# ---------------- HISTORIQUE ----------------
with t_hist:
    st.markdown(bandeau("hist", "Suivi", "Historique des transformations"), unsafe_allow_html=True)
    histo = st.session_state["historique"]
    if not histo:
        st.info("Aucune transformation enregistrée : importez une balance dans la page « Transformation ».")
    else:
        h1, h2 = st.columns([6, 1.4])
        h1.markdown(f'<div class="titre2" style="margin-top:0">{len(histo)} transformation(s) enregistrée(s)</div>', unsafe_allow_html=True)
        h2.write("")
        if h2.button("🗑 Effacer", key="effacer_histo"):
            st.session_state["historique"] = []
            st.session_state["voir"] = None
            sauver_historique([])
            st.rerun()
        tete = st.columns([4, 3, 1.6, 2, 1.2])
        for col, t in zip(tete, ["Fichier", "Date", "Comptes", "Sans retraitement", ""]):
            col.markdown(f'<div class="hh">{t}</div>', unsafe_allow_html=True)
        for i in reversed(range(len(histo))):
            e = histo[i]
            cols = st.columns([4, 3, 1.6, 2, 1.2])
            cols[0].markdown(f"**{e['fichier']}**")
            cols[1].write(e["date"])
            cols[2].write(str(e["comptes"]))
            cols[3].markdown(badge_conformite(e["conformite"]), unsafe_allow_html=True)
            if cols[4].button("Voir", key=f"voir_{i}"):
                st.session_state["voir"] = i
        voir = st.session_state.get("voir")
        if voir is not None and voir < len(histo):
            e = histo[voir]
            st.markdown(f'<div class="titre2">Détail : {e["fichier"]} ({e["date"]})</div>', unsafe_allow_html=True)
            d1, d2 = st.columns(2)
            d1.markdown(f'<div class="panel"><h3>Statut des comptes</h3>{donut_html(e["statuts"])}</div>', unsafe_allow_html=True)
            d2.markdown(f'<div class="panel"><h3>{sc.ETIQUETTE_COMPTES}</h3><div class="score">{e["conformite"]:.0f}<span>%</span></div>'
                        f'<div class="def">en valeur : {e["valeur"]:.0f} %</div>'
                        f'<div class="def">{len(e["constats"])} constats enregistrés</div>{cgnc_html(e)}</div>', unsafe_allow_html=True)
            detail = pd.DataFrame(e["constats"], columns=["Norme", "Niveau", "Constat", "Retraitement suggéré"])
            detail["Niveau"] = detail["Niveau"].map(NIVEAUX).fillna(detail["Niveau"])
            st.markdown(tableau_html(detail.to_dict("records"), list(detail.columns), premiere_en_gras=False), unsafe_allow_html=True)
            if st.button("Fermer le détail", key="fermer_detail"):
                st.session_state["voir"] = None
                st.rerun()
        st.caption("L'historique est enregistré dans le fichier historique.json de l'application ; il ne contient que les résultats, pas la balance. "
                   "Sur le site en ligne, il peut être remis à zéro lorsque l'application redémarre.")

# ---------------- À PROPOS ----------------
with t_apropos:
    st.markdown(bandeau("apropos", "Projet", "À propos de MaroIFRS"), unsafe_allow_html=True)
    st.markdown('<div class="panel g"><h3>Un outil d\'aide à la conversion CGNC → IAS/IFRS</h3>'
                '<p>Projet réalisé dans le module <b>Consolidation et normes IFRS</b>. L\'objectif est de faciliter le travail des comptables, '
                'auditeurs et financiers en automatisant une partie de la conversion du référentiel marocain (CGNC) vers les normes IAS/IFRS.</p></div>',
                unsafe_allow_html=True)
    entete("Objectifs", "Ce que MaroIFRS cherche à faire")
    grille([etape("", "target", "Aider à décider", "Un outil d'aide à la décision, qui ne remplace pas l'expert-comptable."),
            etape("", "lock", "Protéger les données", "Les balances ne sont pas conservées : seuls les résultats figurent dans l'historique."),
            etape("", "puzzle", "S'étendre facilement", "Chaque norme est une fonction que l'on peut ajouter facilement.")], n=3)
    entete("Fonctionnalités", "Les fonctionnalités principales")
    grille([etape("", "scan", "Reconnaissance des documents", "Balance, bilan, CPC, ESG, tableau de financement, ETIC, factures, grands livres et journaux : Excel, CSV ou PDF."),
            etape("", "shield", "Contrôle CGNC", "Détection des comptes par numéro CGNC, statut de chaque compte, contrôle de TVA."),
            etape("", "scale", "Contrôles IAS/IFRS", f"{N_CTRL} normes contrôlées et {N_RAPPEL} rappels d'annexe ; {len(CATALOGUE)} normes consultables au référentiel."),
            etape("", "receipt", "Contrôle fiscal", "Du résultat comptable au résultat fiscal, impôt sur les sociétés et cotisation minimale."),
            etape("", "layers", "Consolidation", "Périmètre, éliminations intragroupe, intérêts ne donnant pas le contrôle, comparaison avec les comptes publiés."),
            etape("", "filecheck", "Rapports", "Rapport de conformité complet en PDF et en Excel, états retraités et historique des transformations.")], n=3)
    entete("Limites", "À savoir avant d'utiliser les résultats")
    st.markdown('<div class="panel">' + liste([
        "Les résultats sont une aide : ils doivent être validés par un expert-comptable.",
        "Les retraitements IFRS chiffrés se limitent aux non-valeurs, aux provisions réglementées et aux impôts différés associés. Pour les autres normes, "
        "MaroIFRS repère le sujet ou rappelle une information d'annexe, sans calculer de retraitement.",
        "Les paramètres fiscaux (taux, cotisation minimale, déficits) sont à valider auprès de la DGI.",
        "Les résultats sont déclarés partiels pour des factures seules ou pour un document sans bilan ou sans compte de résultat : aucun statut « conforme » n'est alors donné.",
        "L'indicateur de qualité des données ne mesure pas l'exactitude des montants.",
        "Les numéros de comptes (CGNC) sont à vérifier avec votre plan comptable."], "no", "flag") + '</div>', unsafe_allow_html=True)

# ---------------- CONTACT ----------------
with t_contact:
    entete("Contact", "Restons en contact")
    contact_g, contact_d = st.columns([1.3, 1], gap="large")
    with contact_g:
        with st.container(key="contact_carte"):
            st.markdown(f'<div class="tile">{ico("mail")}</div><h3 class="ct-h">Une question, une remarque ?</h3>'
                        '<p>Une question sur MaroIFRS, un document mal reconnu ou une suggestion d\'amélioration : écrivez-nous par e-mail.</p>'
                        '<p class="ct-l">Adresse</p><p class="ct-a">contact@maroifrs.ma</p>', unsafe_allow_html=True)
            st.link_button("Envoyer un e-mail", "mailto:contact@maroifrs.ma?subject=MaroIFRS", icon=":material/mail:", type="primary")
    with contact_d:
        st.markdown('<div class="panel g"><h3>Pour faciliter l\'échange</h3>' + liste([
            "Le type de document importé (balance, bilan, factures…)",
            "La page concernée de l'application",
            "Le message affiché, le cas échéant"], "ok", "check") + '</div>', unsafe_allow_html=True)

st.markdown('<div class="pied">MaroIFRS – Outil d\'aide à la conversion CGNC → IAS/IFRS, à valider par un expert-comptable.</div>',
            unsafe_allow_html=True)
