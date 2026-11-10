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
import streamlit as st

st.set_page_config(page_title="MaroIFRS - CGNC vers IAS/IFRS", layout="wide", initial_sidebar_state="collapsed")


# ============================ 1. LECTURE DE LA BALANCE ============================
def preparer_balance(df):
    """Normalise une balance : colonnes compte, libelle, debit, credit + calcul du solde."""
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
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


def _lignes_page(page):
    """Regroupe les mots d'une page en lignes (même hauteur), puis en cellules (selon l'écart horizontal)."""
    mots = page.extract_words(x_tolerance=1.5, y_tolerance=2, use_text_flow=False)
    lignes = []
    for w in sorted(mots, key=lambda w: ((w["top"] + w["bottom"]) / 2, w["x0"])):
        yc = (w["top"] + w["bottom"]) / 2
        if lignes and abs(yc - lignes[-1][0]) <= 3.5:
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


def lire_pdf(fichier):
    """Lit une balance dans un PDF TEXTE (les PDF scannés ne sont pas pris en charge).
    Deux méthodes sont essayées sur chaque page (tableau, puis position des mots) : on garde celle qui lit le plus de comptes."""
    import pdfplumber
    fichier.seek(0)
    lignes, ignorees, totaux, texte = [], [], [], ""
    xd = xc = jd = jc = None
    with pdfplumber.open(fichier) as pdf:
        pages = len(pdf.pages)
        for page in pdf.pages:
            if len(texte) < 6000:
                texte += (page.extract_text() or "") + "\n"
            lt, it, tt, xd, xc = _lire_page_texte(_lignes_page(page), xd, xc)
            try:
                lb, ib, jd, jc = _lire_page_tableaux(page, jd, jc)
            except Exception:
                lb, ib = [], []
            lus, ign = (lb, ib) if lb and len(lb) >= len(lt) else (lt, it)
            lignes += lus
            ignorees += ign
            totaux += tt
    df = pd.DataFrame(lignes, columns=["compte", "libelle", "debit", "credit"])
    t = _sans_accent(texte)
    df.attrs.update({
        "ignorees": len(ignorees), "lignes_ignorees": ignorees, "pages": pages,
        "total_pdf": totaux[-1] if totaux else None, "scanne": not t.strip(),
        "etats_financiers": ("bilan" in t or "compte de produits" in t or "etat de synthese" in t) and "balance" not in t,
    })
    return df


def charger_balance(fichier):
    """Excel (.xlsx, .xls) ou CSV : colonnes compte, libelle, debit, credit."""
    if fichier.name.lower().endswith(".csv"):
        df = pd.read_csv(fichier, sep=None, engine="python")
    else:
        df = pd.read_excel(fichier)
    return preparer_balance(df)


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
    return f"{x:,.0f} MAD"


# ============================ 2. ÉTAT DE SITUATION (IAS 1) ============================
def etat_situation(df):
    """Présentation courant / non courant (mapping simplifié à adapter)."""
    resultat = -solde(df, "6", "7")
    postes = [
        ("Actif non courant", solde(df, "2")),
        ("Actif courant", solde(df, "3") + solde(df, "51")),
        ("Capitaux propres (y compris résultat)", -solde(df, "11", "13") + resultat),
        ("Passif non courant", -solde(df, "14", "15", "16")),
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
    nv = solde(df, "21")
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
COULEURS_STATUT = {"éliminé": "#6d4c8f", "à revoir": "#e0a526", "inchangé": "#1f9d6b",
                   "reclassé": "#d9772b", "ajusté": "#c0392b"}
PUCES = {"éliminé": "🟣", "à revoir": "🟡", "inchangé": "🟢", "reclassé": "🟠", "ajusté": "🔴"}
DEFINITIONS_STATUT = {
    "éliminé": "compte sans équivalent en IFRS : il disparaît des états",
    "reclassé": "montant conservé, mais présenté ou classé autrement",
    "ajusté": "montant à recalculer par un retraitement (impôts différés, contrepartie variable)",
    "à revoir": "jugement de l'utilisateur nécessaire avant de conclure",
    "inchangé": "aucune règle ne s'applique : le compte est repris tel quel",
}
FICHIER_HISTO = Path(__file__).with_name("historique.json") if "__file__" in globals() else Path("historique.json")


def statuts_comptes(df, normes):
    """Attribue un statut à chaque compte d'après les règles des normes sélectionnées."""
    ordre = {s: i for i, s in enumerate(["éliminé", "reclassé", "ajusté", "à revoir", "inchangé"])}
    res = df[["compte", "libelle", "solde"]].copy().reset_index(drop=True)
    res["statut"] = "inchangé"
    res["motif"] = "Aucune règle applicable : repris tel quel"
    amortissable = solde(df, "23") - solde(df, "231")
    taux = (-solde(df, "283") / amortissable) if amortissable > 0 else 0.0
    regles = [  # (norme, préfixes de comptes, statut, motif, règle active ?)
        ("IAS 1", ("21",), "éliminé", "Non-valeur : pas d'actif en IFRS (IAS 38)", True),
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
        ("IAS 37", ("15",), "à revoir", "Provision pour risques et charges : critères IAS 37", True),
        ("IAS 21", ("271", "272", "171", "172"), "éliminé", "Écart de conversion : inexistant en IFRS (IAS 21)", True),
        ("IFRS 9", ("3942",), "à revoir", "Dépréciation clients : modèle de pertes attendues (IFRS 9)", True),
        ("IAS 2", ("391",), "à revoir", "Dépréciation de stocks : valeur nette de réalisation (IAS 2)", True),
    ]

    def appliquer(masque, statut, motif):
        for i in res.index[masque]:
            if ordre[statut] < ordre[res.at[i, "statut"]]:
                res.at[i, "statut"] = statut
                res.at[i, "motif"] = motif

    for norme, prefixes, statut, motif, active in regles:
        if active and norme in normes:
            appliquer(res["compte"].str.startswith(prefixes) & (res["solde"] != 0), statut, motif)
    appliquer(~res["compte"].str[:1].isin(list("1234567")), "à revoir", "Numéro hors des classes 1 à 7 du CGNC : à classer")
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
                      f'<b>{compteurs.get(s, 0)} <i style="font-style:normal;font-weight:500;color:#9a9783">· {100 * compteurs.get(s, 0) / total:.0f} %</i></b></div>' for s in STATUTS)
    return ('<div class="donut"><svg viewBox="0 0 200 200" width="190" height="190">'
            f'<circle cx="100" cy="100" r="{r}" fill="none" stroke="#f1ebdd" stroke-width="26"/>{arcs}'
            f'<text x="100" y="100" text-anchor="middle" font-size="28" font-weight="700" fill="#1c2b25">{total}</text>'
            '<text x="100" y="120" text-anchor="middle" font-size="11" fill="#7b7a68">comptes</text></svg>'
            f'<div class="legs">{legende}</div></div>')


def badge_conformite(p):
    classe = "bv" if p >= 80 else ("bo" if p >= 50 else "br")
    return f'<span class="badge {classe}">{p:.0f} %</span>'


# ---------- graphiques (Altair) ----------
_FONT = "Segoe UI, system-ui, sans-serif"
NIV_COUL = {"Erreur": "#c0392b", "Retraitement": "#d9772b", "À vérifier": "#e0a526", "Info": "#8aa196"}


def _style_alt(ch):
    return (ch.configure_view(strokeWidth=0)
            .configure_axis(labelColor="#5f6b63", titleColor="#5f6b63", gridColor="#ece3d0", domainColor="#d8cdb2",
                            tickColor="#d8cdb2", labelFont=_FONT, titleFont=_FONT, labelFontSize=12,
                            titleFontSize=12, titleFontWeight="normal")
            .configure_legend(labelFont=_FONT, labelColor="#1c2b25", labelFontSize=12, orient="top", title=None,
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
        reperes += (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#b8a26a" stroke-width="2"/>'
                    f'<text x="{xt:.1f}" y="{yt:.1f}" text-anchor="middle" font-size="9" fill="#9a9783">{v}</text>')
    arc = "M 20 100 A 80 80 0 0 1 180 100"
    return ('<div style="text-align:center"><svg viewBox="0 0 200 128" width="100%" style="max-width:340px">'
            f'<path d="{arc}" fill="none" stroke="#ece3d0" stroke-width="16" stroke-linecap="round"/>'
            f'<path d="{arc}" fill="none" stroke="{couleur}" stroke-width="16" stroke-linecap="round" pathLength="100" '
            f'stroke-dasharray="{p:.1f} 100"/>{reperes}'
            f'<text x="100" y="92" text-anchor="middle" font-family="Georgia,serif" font-size="38" font-weight="600" fill="#1c2b25">{p:.0f}<tspan font-size="18" fill="#7b7a68"> %</tspan></text>'
            '<text x="100" y="114" text-anchor="middle" font-size="10" fill="#7b7a68">des comptes sans retraitement</text></svg></div>')


def graph_evolution(histo):
    ordre = _etiquettes(histo)
    lignes = []
    for lab, e in zip(ordre, histo):
        lignes.append({"Fichier": lab, "Indicateur": "En nombre de comptes", "Conformité": e["conformite"]})
        lignes.append({"Fichier": lab, "Indicateur": "En valeur (MAD)", "Conformité": e["valeur"]})
    d = pd.DataFrame(lignes)
    x = alt.X("Fichier:N", sort=ordre, title=None, axis=alt.Axis(labelAngle=0, labelLimit=200, labelPadding=8))
    y = alt.Y("Conformité:Q", scale=alt.Scale(domain=[0, 108]), title="Conformité (%)", axis=alt.Axis(values=[0, 20, 40, 60, 80, 100]))
    off = alt.XOffset("Indicateur:N")
    coul = alt.Color("Indicateur:N", scale=alt.Scale(domain=["En nombre de comptes", "En valeur (MAD)"], range=["#0f6b4f", "#d4a24c"]))
    base = alt.Chart(d)
    barres = base.mark_bar(cornerRadiusTopLeft=7, cornerRadiusTopRight=7).encode(
        x=x, y=y, xOffset=off, color=coul,
        tooltip=["Fichier", "Indicateur", alt.Tooltip("Conformité:Q", format=".1f")])
    texte = (base.transform_calculate(lab="format(datum['Conformité'], '.0f') + ' %'")
             .mark_text(dy=-9, fontWeight="bold", fontSize=12, color="#1c2b25")
             .encode(x=x, y=y, xOffset=off, text="lab:N"))
    seuil = alt.Chart(pd.DataFrame({"y": [80]})).mark_rule(strokeDash=[6, 5], color="#b8862b", strokeWidth=1.6).encode(y="y:Q")
    lseuil = (alt.Chart(pd.DataFrame({"y": [80]})).mark_text(align="left", dx=6, dy=-8, fontSize=11, color="#b8862b", fontWeight="bold")
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
    texte = base.mark_text(align="left", dx=7, fontWeight="bold", fontSize=13, color="#1c2b25").encode(text="Constats:Q")
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
        color=alt.Color("Côté:N", scale=alt.Scale(domain=["Actif", "Passif et capitaux propres"], range=["#0f6b4f", "#d4a24c"])),
        tooltip=["Poste", "Côté", alt.Tooltip("Montant:Q", format=",.0f")])
    pos = base.transform_filter("datum.Montant >= 0").mark_text(align="left", dx=6, fontSize=12, fontWeight="bold", color="#1c2b25").encode(text="lab:N")
    neg = base.transform_filter("datum.Montant < 0").mark_text(align="right", dx=-6, fontSize=12, fontWeight="bold", color="#1c2b25").encode(text="lab:N")
    return _style_alt(alt.layer(barres, pos, neg).properties(height=260))


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
    tres = solde(df, "514", "516", "511") + solde(df, "51")
    return [constat("IAS 7", "Info", f"Trésorerie à l'actif (comptes 51) : {m(solde(df, '51'))}",
                    "Établir le tableau des flux (exploitation, investissement, financement) ; "
                    "rapprocher la trésorerie nette du TFT avec le bilan"),
            ] if tres else [constat("IAS 7", "Info", "Tableau des flux de trésorerie obligatoire en IFRS", "")]


def norme_ias8(df):
    return [constat("IAS 8", "Info", "Changements de méthodes et corrections d'erreurs",
                    "Appliquer rétrospectivement : retraiter les périodes comparatives et les capitaux propres d'ouverture")]


def norme_ias10(df):
    return [constat("IAS 10", "Info", "Événements postérieurs à la clôture",
                    "Distinguer événements donnant lieu à ajustement et événements sans ajustement (information en annexe)")]


def norme_ias19(df):
    r = []
    pers = solde(df, "617")
    prov = -solde(df, "1555", "4432")
    if pers > 0:
        r.append(constat("IAS 19", "À vérifier", f"Charges de personnel : {m(pers)}",
                         "Vérifier la provision pour congés payés, primes et avantages postérieurs à l'emploi (indemnités de départ)"))
    if prov == 0 and pers > 0:
        r.append(constat("IAS 19", "Retraitement", "Aucun engagement de retraite / indemnités de fin de carrière provisionné",
                         "Évaluer l'obligation par la méthode des unités de crédit projetées"))
    return r


def norme_ias20(df):
    sub = -solde(df, "716", "757", "1311", "1312")
    if sub:
        return [constat("IAS 20", "À vérifier", f"Subventions publiques : {m(sub)}",
                        "Comptabiliser en résultat sur les périodes où les coûts correspondants sont constatés")]
    return []


def norme_ias21(df):
    r = []
    ec = solde(df, "271", "272", "171", "172")
    pg = -solde(df, "7386", "6386")
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
    return [constat("IAS 24", "À vérifier", f"Comptes d'associés / apparentés : {m(solde(df, '455'))}",
                    "Identifier les parties liées, leurs transactions et soldes ; informations en annexe")] if solde(df, "455") else \
        [constat("IAS 24", "Info", "Informations sur les parties liées à fournir en annexe", "")]


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
    dette = -solde(df, "148")
    if dette:
        r.append(constat("IFRS 9", "À vérifier", f"Emprunts bancaires : {m(dette)}",
                         "Évaluation au coût amorti (taux d'intérêt effectif), frais d'émission inclus"))
    return r


def norme_ias33(df):
    return [constat("IAS 33", "Info", "Résultat par action (entités cotées ou en cours de cotation)",
                    "Résultat net part du groupe / nombre moyen pondéré d'actions ordinaires ; calculer aussi le dilué")]


def norme_ias37(df):
    r = []
    prov = -solde(df, "15")
    if prov:
        r.append(constat("IAS 37", "À vérifier", f"Provisions durables pour risques et charges : {m(prov)}",
                         "Conserver uniquement si obligation actuelle, sortie probable et estimation fiable (IAS 37.14)"))
    else:
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
    im = solde(df, "2330", "2331")
    if im > 0:
        return [constat("IAS 40", "À vérifier", f"Immeubles de rapport : {m(im)}",
                        "Choisir entre le modèle du coût et la juste valeur (variations en résultat)")]
    return []


def norme_ifrs3(df):
    fc = solde(df, "223")
    if fc > 0:
        return [constat("IFRS 3", "À vérifier", f"Fonds commercial : {m(fc)}",
                        "Distinguer le goodwill d'acquisition (non amorti, test IAS 36) du fonds commercial acheté (actif incorporel)")]
    return []


def norme_ifrs5(df):
    return [constat("IFRS 5", "Info", "Actifs non courants détenus en vue de la vente",
                    "Présenter séparément au plus bas de la valeur comptable et de la juste valeur nette ; arrêter l'amortissement")]


def norme_ifrs7(df):
    return [constat("IFRS 7", "Info", "Informations sur les instruments financiers",
                    "Risques de crédit, de liquidité et de marché : informations qualitatives et quantitatives en annexe")]


def norme_ifrs8(df):
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
    "IAS 38": norme_ias38, "IAS 40": norme_ias40, "IFRS 3": norme_ifrs3, "IFRS 5": norme_ifrs5,
    "IFRS 7": norme_ifrs7, "IFRS 8": norme_ifrs8, "IFRS 16": norme_ifrs16,
})
NORMES = dict(sorted(NORMES.items(), key=lambda kv: (kv[0][:3] != "IAS", int(re.search(r"\d+", kv[0]).group()))))



# ============================ 4b. CONTRÔLE FISCAL (résultat comptable -> résultat fiscal -> IS) ============================
LIGNES_REINT = ["Amendes et pénalités", "Dons et cadeaux non déductibles", "Amortissements excédentaires",
                "Provisions non déductibles", "Impôt sur les sociétés comptabilisé (si charge de l'exercice)"]
LIGNES_DEDUC = ["Produits de participation (dividendes)", "Plus-values exonérées", "Reprises de provisions non déductibles",
                "Amortissements déductibles non comptabilisés"]


def tableau_fiscal_vide(lignes):
    return pd.DataFrame({"Libellé": lignes, "Montant": [0.0] * len(lignes)})


# Paramètres du CGI 2026 (art. 19-I et 144-I)
TAUX_IS_NORMAL, TAUX_IS_GRANDE, TAUX_IS_FINANCE = 0.20, 0.35, 0.40
SEUIL_TAUX_35 = 100_000_000  # bénéfice net à partir duquel le taux de 35 % s'applique (art. 19-I-B)
CM_TAUX, CM_TAUX_REDUIT, CM_MINIMUM = 0.0025, 0.0015, 3000.0  # art. 144-I-D
CATEGORIES_IS = {
    "ordinaire": "Société ordinaire : 20 % (35 % si bénéfice net ≥ 100 M MAD)",
    "credit": "Établissement de crédit, assurance, Bank Al-Maghrib, CDG : 40 %",
    "libre": "Autre taux (exercice avant 2026, régime particulier)",
}
# Base de la cotisation minimale (art. 144-I-B), comptes CGNC à vérifier avec votre plan comptable :
# CA (711, 712), subventions d'exploitation (716), autres produits d'exploitation (718),
# produits financiers hors reprises (731, 732, 733), subventions d'équilibre et autres produits non courants (756, 758).
COMPTES_BASE_CM = ("711", "712", "716", "718", "731", "732", "733", "756", "758")


def calc_fiscal(df, reint, deduc, deficits, categorie="ordinaire", exclu_35=False, taux_libre=0.20,
                exonere_cm=False, ventes_taux_reduit=0.0):
    """Passage du résultat comptable au résultat fiscal, puis IS (art. 19) et cotisation minimale (art. 144)."""
    impot_compta = solde(df, "67")
    rnc = -solde(df, "6", "7")  # résultat net comptable
    rcav = rnc + impot_compta  # résultat comptable avant impôt
    tr = pd.to_numeric(reint["Montant"], errors="coerce").fillna(0.0).sum()
    td = pd.to_numeric(deduc["Montant"], errors="coerce").fillna(0.0).sum()
    rf_brut = rcav + tr - td
    deficit_imputable = min(max(rf_brut, 0.0), max(deficits, 0.0))
    rf_net = rf_brut - deficit_imputable if rf_brut > 0 else rf_brut
    if categorie == "credit":
        taux_is = TAUX_IS_FINANCE
    elif categorie == "libre":
        taux_is = taux_libre
    else:
        taux_is = TAUX_IS_GRANDE if (rf_net >= SEUIL_TAUX_35 and not exclu_35) else TAUX_IS_NORMAL
    is_theorique = max(rf_net, 0.0) * taux_is
    ca = -solde(df, "711", "712")
    base_cm = max(-solde(df, *COMPTES_BASE_CM), 0.0)
    reduit = min(max(ventes_taux_reduit, 0.0), base_cm)
    cm_calcule = (base_cm - reduit) * CM_TAUX + reduit * CM_TAUX_REDUIT
    cm = 0.0 if exonere_cm else max(cm_calcule, CM_MINIMUM)
    is_du = max(is_theorique, cm)
    return {"rnc": rnc, "impot_compta": impot_compta, "rcav": rcav, "reintegrations": tr, "deductions": td,
            "rf_brut": rf_brut, "deficit_impute": deficit_imputable, "rf_net": rf_net, "is_theorique": is_theorique,
            "base_cm": base_cm, "cm": cm, "is_du": is_du, "ecart_is": is_du - impot_compta,
            "taux_is": taux_is, "ca": ca, "categorie": categorie, "exonere_cm": exonere_cm}


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
        (f"IS théorique au taux de {f['taux_is'] * 100:g} %", f["is_theorique"]),
        ("Base de la cotisation minimale (art. 144-I-B)", f["base_cm"]),
        ("Cotisation minimale" + (" (exonérée : 36 premiers mois)" if f.get("exonere_cm") else ""), f["cm"]),
        ("IS dû (le plus élevé des deux)", f["is_du"]),
        ("Écart avec l'IS comptabilisé", f["ecart_is"]),
    ], columns=["Ligne", "Montant (MAD)"])


def constats_fiscaux(f):
    r = []
    if abs(f["ecart_is"]) > 1:
        sens = "insuffisant" if f["ecart_is"] > 0 else "excédentaire"
        r.append(constat("Contrôle fiscal", "À vérifier",
                         f"IS comptabilisé {m(f['impot_compta'])} ≠ IS dû {m(f['is_du'])} (provision {sens}, écart {m(f['ecart_is'])})",
                         "Ajuster la charge d'impôt exigible ou justifier l'écart (réintégrations, déficits, cotisation minimale)"))
    else:
        r.append(constat("Contrôle fiscal", "Info", "IS comptabilisé cohérent avec l'IS calculé", ""))
    r.append(constat("Contrôle fiscal", "Info", f"Taux d'IS appliqué : {f['taux_is'] * 100:g} % (CGI 2026, art. 19-I)", ""))
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


# ============================ 4c. RAPPORT PDF ============================
def rapport_pdf(df, constats_df, etat, res_statuts, conf_n, conf_v, fiscal=None, dt=None, ugt=None, fichier=""):
    """Construit le rapport PDF (reportlab) et retourne les octets."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak

    bleu, fond = colors.HexColor("#0f4c3a"), colors.HexColor("#f1e8d1")
    st_ = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=st_["Title"], textColor=bleu, fontSize=22, alignment=0, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=st_["Heading2"], textColor=bleu, fontSize=14, spaceBefore=14, spaceAfter=6)
    nor = ParagraphStyle("n", parent=st_["BodyText"], fontSize=9, leading=12)
    pet = ParagraphStyle("p", parent=nor, fontSize=8, leading=10)

    def mad(x):
        return f"{x:,.0f}".replace(",", " ")

    def net(t):  # la police standard du PDF n'a pas ces symboles
        return str(t).replace("→", "vers").replace("≠", "différent de").replace("≥", ">=").replace("≤", "<=").replace("\u202f", " ")

    def tableau(entetes, lignes, largeurs):
        data = [[Paragraph(f"<b>{net(e)}</b>", pet) for e in entetes]]
        for lg in lignes:
            data.append([Paragraph(net(c), pet) for c in lg])
        t = Table(data, colWidths=largeurs, repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), fond), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e0d5bb")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fbf8f1")])]))
        return t

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.6 * cm,
                            bottomMargin=1.6 * cm, title="Rapport MaroIFRS")
    el = [Paragraph("Rapport de conversion CGNC vers IAS / IFRS", h1),
          Paragraph(f"Fichier : <b>{fichier or 'balance importée'}</b> &nbsp;·&nbsp; "
                    f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}", nor), Spacer(1, 10)]
    kp = Table([[Paragraph(f"<b>{len(df)}</b><br/>comptes analysés", nor), Paragraph(f"<b>{conf_n:.0f} %</b><br/>conformité (comptes)", nor),
                 Paragraph(f"<b>{conf_v:.0f} %</b><br/>conformité (valeur)", nor),
                 Paragraph(f"<b>{len(constats_df)}</b><br/>constats", nor)]], colWidths=[4.4 * cm] * 4)
    kp.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), fond), ("BOX", (0, 0), (-1, -1), 0.5, bleu),
                            ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.white), ("TOPPADDING", (0, 0), (-1, -1), 8),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    el += [kp, Paragraph("Statut des comptes", h2)]
    comp = compteurs_statuts(res_statuts)
    el.append(tableau(["Statut", "Nombre de comptes", "Signification"],
                      [[s, comp[s], DEFINITIONS_STATUT[s]] for s in STATUTS], [3 * cm, 3.4 * cm, 11 * cm]))
    el.append(Paragraph("Constats et retraitements suggérés", h2))
    el.append(tableau(["Norme", "Niveau", "Constat", "Retraitement suggéré"],
                      constats_df[["Norme", "Niveau", "Constat", "Retraitement suggéré"]].values.tolist(),
                      [2.4 * cm, 2.2 * cm, 6.4 * cm, 6.4 * cm]))
    el.append(Paragraph("État de situation financière (IAS 1)", h2))
    el.append(tableau(["Poste", "Montant (MAD)"], [[p, mad(v)] for p, v in etat.values.tolist()], [11 * cm, 5.4 * cm]))
    if fiscal:
        el += [PageBreak(), Paragraph("Contrôle fiscal : du résultat comptable au résultat fiscal", h2)]
        el.append(tableau(["Ligne", "Montant (MAD)"], [[a, mad(b)] for a, b in tableau_passage(fiscal).values.tolist()],
                          [11 * cm, 5.4 * cm]))
    if dt is not None and not dt.empty:
        el.append(Paragraph("IAS 12 : impôts différés", h2))
        el.append(tableau(["Libellé", "Différence temporaire", "Impôt différé", "Type"],
                          [[r["Libellé"], mad(r["Différence temporaire"]), mad(r["Impôt différé"]), r["Type"]] for _, r in dt.iterrows()],
                          [5 * cm, 4 * cm, 3.6 * cm, 3.8 * cm]))
    if ugt is not None and not ugt.empty:
        el.append(Paragraph("IAS 36 : test de dépréciation", h2))
        el.append(tableau(["Indicateur", "Montant (MAD)"], [[a, mad(b)] for a, b in ugt.values.tolist()], [11 * cm, 5.4 * cm]))
    el += [Spacer(1, 14), Paragraph("<i>Outil d'aide à la décision : mapping des comptes simplifié, résultats à valider par un "
                                     "expert-comptable.</i>", pet)]
    doc.build(el)
    return buf.getvalue()


# ============================ 4d. EXPORT EXCEL MIS EN FORME ============================
def excel_formate(df, constats_df, etat, res_statuts, conf_n, conf_v, fiscal=None, dt=None, ugt=None, fichier=""):
    """Classeur Excel professionnel : synthèse, constats, statut des comptes, situation, fiscalité, IAS 12 / IAS 36."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    BLEU, BLEU_CL, GRIS = "0F4C3A", "F1E8D1", "F8F3E8"
    f_titre = Font(name="Calibri", size=20, bold=True, color=BLEU)
    f_sous = Font(name="Calibri", size=10, italic=True, color="7B7A68")
    f_tete = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    f_norm = Font(name="Calibri", size=10, color="1E293B")
    f_gras = Font(name="Calibri", size=10, bold=True, color="1C2B25")
    fond_tete = PatternFill("solid", fgColor=BLEU)
    fond_cl = PatternFill("solid", fgColor=BLEU_CL)
    fond_zebra = PatternFill("solid", fgColor=GRIS)
    fin = Side(style="thin", color="E0D5BB")
    cadre = Border(left=fin, right=fin, top=fin, bottom=fin)
    NUM = '#,##0;[Red]-#,##0;"-"'
    COULEURS_NIV = {"Erreur": ("FEE2E2", "991B1B"), "Retraitement": ("FFEDD5", "9A3412"),
                    "À vérifier": ("FEF3C7", "92400E"), "Info": ("E9EEEA", "4B5E54")}
    COULEURS_ST = {"éliminé": ("E8DFF3", "6D4C8F"), "à revoir": ("FDF0CF", "8A5A00"), "inchangé": ("DCFCE7", "166534"),
                   "reclassé": ("FEF3C7", "92400E"), "ajusté": ("FEE2E2", "991B1B")}

    wb = Workbook()

    def feuille(nom, titre, sous_titre, premiere=False):
        ws = wb.active if premiere else wb.create_sheet()
        ws.title = nom
        ws.sheet_view.showGridLines = False
        ws.sheet_properties.tabColor = BLEU
        ws["A1"], ws["A1"].font = titre, f_titre
        ws["A2"], ws["A2"].font = sous_titre, f_sous
        ws.row_dimensions[1].height = 30
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.oddFooter.center.text = "MaroIFRS - Page &P / &N"
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
        return ligne + len(lignes) + 2

    maintenant = datetime.now().strftime("%d/%m/%Y à %H:%M")
    sous = f"Fichier : {fichier or 'balance importée'}  ·  Généré le {maintenant}"

    # ---- Synthèse ----
    ws = feuille("Synthèse", "MaroIFRS - Conversion CGNC vers IAS / IFRS", sous, premiere=True)
    kpis = [("Comptes analysés", len(df), "0"), ("Conformité (comptes)", conf_n / 100, "0%"),
            ("Conformité (en valeur)", conf_v / 100, "0%"), ("Constats", len(constats_df), "0")]
    for j, (lib, val, fmt) in enumerate(kpis):
        col = 1 + j * 2
        ws.merge_cells(start_row=4, start_column=col, end_row=4, end_column=col + 1)
        ws.merge_cells(start_row=5, start_column=col, end_row=5, end_column=col + 1)
        a, b = ws.cell(row=4, column=col, value=lib), ws.cell(row=5, column=col, value=val)
        a.font, a.alignment = Font(name="Calibri", size=9, bold=True, color="7B7A68"), Alignment(horizontal="center")
        b.font, b.number_format, b.alignment = Font(name="Calibri", size=22, bold=True, color=BLEU), fmt, Alignment(horizontal="center")
        for rr in (4, 5):
            for cc in (col, col + 1):
                ws.cell(row=rr, column=cc).fill = fond_cl
                ws.cell(row=rr, column=cc).border = Border(top=Side(style="medium", color=BLEU) if rr == 4 else None)
    ws.row_dimensions[5].height = 38
    comp = compteurs_statuts(res_statuts)
    ligne = tableau(ws, 8, ["Statut", "Comptes", "Signification"],
                    [[s, comp[s], DEFINITIONS_STATUT[s]] for s in STATUTS], [26, 16, 70, 16, 16, 16, 16, 16],
                    couleurs={1: COULEURS_ST}, centrer=(2,))
    niv = constats_df["Niveau"].value_counts()
    tableau(ws, ligne + 1, ["Niveau des constats", "Nombre"], [[n, int(niv.get(n, 0))] for n in COULEURS_NIV],
            [26, 16, 70, 16, 16, 16, 16, 16], couleurs={1: COULEURS_NIV}, centrer=(2,))
    ws.cell(row=ligne + 8, column=1,
            value="Outil d'aide à la décision : mapping des comptes simplifié, résultats à valider par un expert-comptable.").font = f_sous

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

    # ---- Contrôle fiscal ----
    if fiscal:
        ws = feuille("Contrôle fiscal", "Du résultat comptable au résultat fiscal", sous)
        passage = tableau_passage(fiscal).values.tolist()
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
FONDS = ["linear-gradient(135deg,#f7ecd0,#efd9a3)", "linear-gradient(135deg,#dff1e6,#bfe3cf)",
         "linear-gradient(135deg,#efe6f7,#ddcdee)", "linear-gradient(135deg,#fde8d6,#f8cfa8)",
         "linear-gradient(135deg,#fbe4e0,#f3c4bd)"]
ICONES_NORMES = ["filetext", "coins", "factory", "receipt", "down", "shield", "bank", "layers", "scale", "chart"]

CSS = """<style>
html, body, .stApp, .stApp p, .stApp label, .stApp li, .stApp button, .stApp input {font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;}
#MainMenu, footer, header {visibility: hidden;}
[data-testid="stSidebar"], [data-testid="collapsedControl"] {display: none;}
.stApp {background: linear-gradient(180deg,#faf6ee 0%,#f4eedf 100%);}
.block-container {padding: .8rem 1.6rem 2rem 1.6rem !important; max-width: 100% !important;}
@keyframes fadeUp {from {opacity: 0; transform: translateY(14px);} to {opacity: 1; transform: none;}}
@keyframes drift {0%,100% {transform: translate(0,0) scale(1);} 50% {transform: translate(24px,-18px) scale(1.08);}}
@keyframes shine {0% {background-position: 0% 50%;} 100% {background-position: 200% 50%;}}

/* ===== Transitions façon PowerPoint ===== */
@keyframes slideIn {from {opacity: 0; transform: translateX(60px);} to {opacity: 1; transform: none;}}
@keyframes zoomIn {from {opacity: 0; transform: scale(.92);} to {opacity: 1; transform: none;}}
@keyframes flipUp {from {opacity: 0; transform: perspective(900px) rotateX(10deg) translateY(30px); transform-origin: top;} to {opacity: 1; transform: none;}}
@keyframes blurIn {from {opacity: 0; filter: blur(10px); transform: translateY(10px);} to {opacity: 1; filter: none; transform: none;}}
@keyframes popIn {0% {opacity: 0; transform: translateY(24px) scale(.94);} 70% {transform: translateY(-3px) scale(1.01);} 100% {opacity: 1; transform: none;}}
@keyframes growX {from {transform: scaleX(0);} to {transform: scaleX(1);}}
@keyframes spinIn {from {opacity: 0; transform: rotate(-120deg) scale(.6);} to {opacity: 1; transform: none;}}
@keyframes glow {0%,100% {box-shadow: 0 4px 12px rgba(23,160,111,.30);} 50% {box-shadow: 0 4px 22px rgba(23,160,111,.62);}}
@keyframes tabPop {0% {transform: scale(.9);} 60% {transform: scale(1.06);} 100% {transform: scale(1);}}
[data-baseweb="tab-panel"], div[role="tabpanel"] {animation: fadeUp .5s cubic-bezier(.22,.8,.3,1) both;}
[data-baseweb="tab-panel"]:nth-of-type(4n+1) {animation-name: slideIn;}
[data-baseweb="tab-panel"]:nth-of-type(4n+2) {animation-name: zoomIn;}
[data-baseweb="tab-panel"]:nth-of-type(4n+3) {animation-name: flipUp;}
[data-baseweb="tab-panel"]:nth-of-type(4n+4) {animation-name: blurIn;}
div[role="tab"][aria-selected="true"], [data-baseweb="tab"][aria-selected="true"] {animation: tabPop .35s ease;}
/* apparition en cascade des cartes et blocs */
.stat, .step, .nc, .kpi, .panel, .note {animation: popIn .6s cubic-bezier(.22,.8,.3,1) both;}
[data-testid="stColumn"]:nth-child(1) .step, [data-testid="stColumn"]:nth-child(1) .nc, [data-testid="stColumn"]:nth-child(1) .kpi, [data-testid="stColumn"]:nth-child(1) .panel {animation-delay: .05s;}
[data-testid="stColumn"]:nth-child(2) .step, [data-testid="stColumn"]:nth-child(2) .nc, [data-testid="stColumn"]:nth-child(2) .kpi, [data-testid="stColumn"]:nth-child(2) .panel {animation-delay: .15s;}
[data-testid="stColumn"]:nth-child(3) .step, [data-testid="stColumn"]:nth-child(3) .nc, [data-testid="stColumn"]:nth-child(3) .kpi, [data-testid="stColumn"]:nth-child(3) .panel {animation-delay: .25s;}
[data-testid="stColumn"]:nth-child(4) .step, [data-testid="stColumn"]:nth-child(4) .nc, [data-testid="stColumn"]:nth-child(4) .kpi, [data-testid="stColumn"]:nth-child(4) .panel {animation-delay: .35s;}
.stat:nth-child(1) {animation-delay: .05s;} .stat:nth-child(2) {animation-delay: .15s;}
.stat:nth-child(3) {animation-delay: .25s;} .stat:nth-child(4) {animation-delay: .35s;}
.hero {animation: zoomIn .8s cubic-bezier(.22,.8,.3,1) both;}
.hero-r svg {animation: blurIn 1.1s .3s ease both;}
.brand {animation: slideIn .6s ease both;}
.bar i {transform-origin: left; animation: growX 1.1s .2s cubic-bezier(.22,.8,.3,1) both;}
.donut svg {animation: spinIn .9s cubic-bezier(.22,.8,.3,1) both;}
.leg {animation: slideIn .5s ease both;}
.leg:nth-child(2) {animation-delay: .08s;} .leg:nth-child(3) {animation-delay: .16s;}
.leg:nth-child(4) {animation-delay: .24s;} .leg:nth-child(5) {animation-delay: .32s;}
.pipe span {animation: popIn .5s ease both;}
.pipe span:nth-child(2) {animation-delay: .12s;} .pipe span:nth-child(3) {animation-delay: .24s;} .pipe span:nth-child(4) {animation-delay: .36s;}
.stDownloadButton button, .stButton button {animation: glow 2.8s ease-in-out infinite;}
.stDownloadButton button:active, .stButton button:active {transform: scale(.96);}
.btn {transition: all .2s ease;} .btn:hover {transform: translateY(-2px) scale(1.03); box-shadow: 0 10px 24px rgba(23,160,111,.4);}
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {animation: fadeUp .6s ease both;}
[data-testid="stAlert"] {animation: slideIn .45s ease both;}
@media (prefers-reduced-motion: reduce) {*, *::before, *::after {animation: none !important; transition: none !important;}}

.brand {display: flex; align-items: center; gap: .9rem; background: linear-gradient(135deg,#06241c,#0f4c3a); border-radius: 18px; padding: .9rem 1.5rem; color: white; box-shadow: 0 6px 22px rgba(6,36,28,.22);}
.logo {width: 46px; height: 46px; border-radius: 13px; background: linear-gradient(135deg,#f0d28a,#c8963e); color: white; display: flex; align-items: center; justify-content: center; box-shadow: 0 4px 14px rgba(233,196,106,.5);}
.brand b {font-family: Georgia, serif; font-size: 1.4rem; color: white; display: block; line-height: 1.1; letter-spacing: .02em;}
.brand small {font-size: .66rem; letter-spacing: .2em; color: #e3cf9b; text-transform: uppercase;}
.brand .sp {flex: 1;}
.brand .chip {border: 1px solid rgba(255,255,255,.25); background: rgba(255,255,255,.08); padding: .3rem .9rem; border-radius: 999px; font-size: .78rem; font-weight: 600; color: #f1e3bd;}
div[role="tablist"], [data-baseweb="tab-list"] {gap: 6px; background: white; border: 1px solid #e8dfcc; padding: .45rem; border-radius: 16px; flex-wrap: wrap; margin-top: .8rem; box-shadow: 0 2px 10px rgba(15,76,58,.06);}
div[role="tab"], [data-baseweb="tab"] {border-radius: 11px; padding: .5rem 1.2rem; height: auto; transition: all .22s ease;}
div[role="tab"]:hover, [data-baseweb="tab"]:hover {background: #f6efdc; transform: translateY(-1px);}
div[role="tab"] p, [data-baseweb="tab"] p {font-weight: 600; color: #475569 !important; font-size: .95rem;}
div[role="tab"][aria-selected="true"], [data-baseweb="tab"][aria-selected="true"] {background: linear-gradient(135deg,#17a06f,#0c6b4a) !important; box-shadow: 0 4px 12px rgba(23,160,111,.35);}
div[role="tab"][aria-selected="true"] p, [data-baseweb="tab"][aria-selected="true"] p {color: white !important;}
.react-aria-SelectionIndicator, [data-baseweb="tab-highlight"], [data-baseweb="tab-border"] {display: none !important;}
.hero {position: relative; overflow: hidden; display: flex; flex-wrap: wrap; gap: 2rem; align-items: center; color: white; border-radius: 28px; padding: 4rem 3.5rem; margin: 1.1rem 0 2.2rem 0; background: radial-gradient(circle at 88% 15%, rgba(233,196,106,.45), transparent 45%), linear-gradient(135deg,#06241c 0%,#0f4c3a 65%,#15634b 100%); box-shadow: 0 18px 50px rgba(6,36,28,.30);}
.hero::before {content: ""; position: absolute; width: 420px; height: 420px; right: -120px; top: -140px; border-radius: 50%; background: radial-gradient(circle, rgba(94,234,212,.28), transparent 65%); animation: drift 9s ease-in-out infinite;}
.hero::after {content: ""; position: absolute; width: 320px; height: 320px; left: -100px; bottom: -150px; border-radius: 50%; background: radial-gradient(circle, rgba(233,196,106,.35), transparent 65%); animation: drift 12s ease-in-out infinite reverse;}
.hero-l {flex: 1 1 460px; position: relative; z-index: 1;}
.hero-r {flex: 1 1 380px; position: relative; z-index: 1;}
.pill {display: inline-flex; align-items: center; gap: .45rem; border: 1px solid rgba(255,255,255,.3); background: rgba(255,255,255,.09); border-radius: 999px; padding: .32rem .95rem; font-size: .8rem; letter-spacing: .08em; font-weight: 600;}
.hero h1 {font-family: Georgia, 'Palatino Linotype', serif; font-weight: 500; font-size: 3.3rem; line-height: 1.1; color: white; margin: 1.2rem 0 1rem 0; padding: 0;}
.hero h1 em {font-style: normal; background: linear-gradient(90deg,#f4d58d,#9be0bf,#f4d58d); background-size: 200% auto; -webkit-background-clip: text; background-clip: text; color: transparent; animation: shine 5s linear infinite;}
.hero p {color: #dfe9e2; font-size: 1.1rem; line-height: 1.65; max-width: 620px; margin: 0 0 1.5rem 0;}
.tags span {display: inline-block; margin: 0 .5rem .5rem 0; padding: .55rem 1.1rem; border-radius: 12px; font-weight: 600; font-size: .9rem; background: rgba(255,255,255,.1); border: 1px solid rgba(255,255,255,.28);}
.tags span.p {background: linear-gradient(135deg,#17a06f,#0c6b4a); border-color: transparent;}
.stats {display: grid; grid-template-columns: repeat(auto-fit,minmax(170px,1fr)); gap: 1rem; margin: -.4rem 0 2rem 0;}
.stat {background: white; border: 1px solid #e8dfcc; border-radius: 18px; padding: 1.2rem 1.4rem; box-shadow: 0 2px 10px rgba(15,76,58,.06); transition: all .25s ease;}
.stat:hover {transform: translateY(-4px); box-shadow: 0 12px 28px rgba(15,76,58,.14);}
.stat b {display: block; font-family: Georgia, serif; font-size: 2.1rem; color: #0f4c3a; line-height: 1.1;}
.stat span {color: #7b7a68; font-size: .85rem;}
.eyebrow {color: #0f8a5f; letter-spacing: .2em; font-size: .75rem; font-weight: 700; text-transform: uppercase; margin-top: 1.2rem;}
.h2 {font-family: Georgia, 'Palatino Linotype', serif; color: #0f4c3a; font-size: 2.2rem; font-weight: 500; margin: .3rem 0 1.2rem 0;}
.step, .nc, .panel, .kpi {transition: transform .25s ease, box-shadow .25s ease;}
.step:hover, .nc:hover, .kpi:hover {transform: translateY(-4px); box-shadow: 0 14px 30px rgba(15,76,58,.15);}
.step {position: relative; background: white; border: 1px solid #e8dfcc; border-radius: 18px; padding: 1.4rem; min-height: 190px; box-shadow: 0 1px 3px rgba(15,76,58,.05);}
.step .n {position: absolute; top: 1rem; right: 1.2rem; font-family: Georgia, serif; font-size: 1.7rem; color: #eadfc4;}
.tile {width: 46px; height: 46px; border-radius: 13px; background: linear-gradient(135deg,#f6ecd2,#ecdcae); color: #0f4c3a; display: flex; align-items: center; justify-content: center; margin-bottom: 1.1rem;}
.step h3, .nc h3 {font-family: Georgia, serif; font-weight: 600; color: #1c2b25; font-size: 1.1rem; margin: 0 0 .4rem 0; padding: 0;}
.step p, .nc p {color: #5f6b63; font-size: .9rem; line-height: 1.5; margin: 0;}
.nc {background: white; border: 1px solid #e8dfcc; border-radius: 18px; overflow: hidden; box-shadow: 0 1px 3px rgba(15,76,58,.05); min-height: 270px;}
.nc .top {height: 110px; position: relative; display: flex; align-items: center; justify-content: center; color: rgba(15,76,58,.28);}
.nc .top .b {position: absolute; top: .9rem; left: .9rem; background: white; color: #0f4c3a; border-radius: 999px; padding: .28rem .8rem; font-weight: 700; font-size: .85rem; display: inline-flex; gap: .4rem; align-items: center; box-shadow: 0 1px 4px rgba(0,0,0,.08);}
.nc .top .a {position: absolute; top: .9rem; right: .9rem; border-radius: 999px; padding: .22rem .7rem; font-weight: 700; font-size: .72rem;}
.a.on {background: #dcfce7; color: #166534;}
.a.off {background: #f1ebdd; color: #64748b;}
.nc .body {padding: 1.1rem 1.3rem 1.3rem 1.3rem;}
.panel {background: white; border: 1px solid #e8dfcc; border-radius: 22px; padding: 2rem; min-height: 280px; box-shadow: 0 2px 10px rgba(15,76,58,.05);}
.panel.g {background: #f1ebdd; border-color: #e9e0cc;}
.panel h3 {font-family: Georgia, serif; color: #0f4c3a; font-weight: 500; font-size: 1.6rem; margin: .6rem 0 1.2rem 0; padding: 0;}
.panel.g h3 {color: #1c2b25; font-size: 1.2rem;}
.li {display: flex; gap: .7rem; align-items: flex-start; color: #1e293b; font-size: .93rem; margin-bottom: .75rem; line-height: 1.45;}
.li svg {flex: none; margin-top: 2px;}
.ok svg {color: #16a34a;}
.no svg {color: #f59e0b;}
.kpi {display: flex; gap: .9rem; align-items: center; background: white; border: 1px solid #e8dfcc; border-radius: 16px; padding: 1rem 1.2rem; box-shadow: 0 2px 8px rgba(15,76,58,.05);}
.kpi .kico {width: 46px; height: 46px; flex: none; border-radius: 12px; background: linear-gradient(135deg,#f6ecd2,#ecdcae); color: #0f4c3a; display: flex; align-items: center; justify-content: center;}
.kpi span {display: block; color: #7b7a68; font-size: .8rem;}
.kpi b {font-family: Georgia, serif; color: #1c2b25; font-size: 1.4rem; font-weight: 600;}
.btn {display: inline-block; background: linear-gradient(135deg,#17a06f,#0c6b4a); color: white !important; padding: .75rem 1.5rem; border-radius: 12px; text-decoration: none; font-weight: 700;}
.pied {text-align: center; color: #7b7a68; font-size: .85rem; margin: 2.5rem 0 1rem 0;}
.stDownloadButton button, .stButton button {background: linear-gradient(135deg,#17a06f,#0c6b4a); border: none; border-radius: 12px; padding: .55rem 1.2rem; transition: all .2s ease; box-shadow: 0 4px 12px rgba(23,160,111,.3);}
.stDownloadButton button:hover, .stButton button:hover {transform: translateY(-2px); box-shadow: 0 8px 20px rgba(23,160,111,.42);}
.stDownloadButton button p, .stButton button p {color: white; font-weight: 600;}
[data-testid="stFileUploaderDropzone"] {background: white; border: 1.5px dashed #e3cf9b; border-radius: 16px; transition: all .2s ease;}
[data-testid="stFileUploaderDropzone"]:hover {background: #fbf6e8; border-color: #17a06f;}
[data-testid="stAlert"] {border-radius: 14px;}
[data-testid="stDataFrame"] {border-radius: 14px; overflow: hidden; border: 1px solid #e8dfcc;}
.pipe {display: flex; flex-wrap: wrap; gap: .5rem; margin: .4rem 0 1rem 0;}
.pipe span {background: #e8f7ee; color: #166534; border-radius: 999px; padding: .3rem .9rem; font-size: .82rem; font-weight: 700;}
.score {font-family: Georgia, serif; font-size: 3.4rem; color: #0f4c3a; line-height: 1;}
.score span {font-size: 1.6rem; color: #7b7a68; margin-left: .2rem;}
.bar {height: 10px; background: #ece3d0; border-radius: 999px; overflow: hidden; margin: .8rem 0;}
.bar i {display: block; height: 100%; border-radius: 999px;}
.donut {display: flex; flex-wrap: wrap; align-items: center; gap: 1.2rem;}
.legs {flex: 1 1 140px;}
.leg {display: flex; align-items: center; gap: .55rem; font-size: .9rem; color: #1e293b; padding: .22rem 0;}
.leg b {margin-left: auto; color: #1c2b25;}
.dot {width: 9px; height: 9px; border-radius: 50%; display: inline-block;}
.def {font-size: .83rem; color: #5f6b63; margin: .3rem 0;}
.badge {display: inline-block; padding: .15rem .75rem; border-radius: 999px; font-weight: 700; font-size: .8rem;}
.bv {background: #dcfce7; color: #166534;}
.bo {background: #fef3c7; color: #92400e;}
.br {background: #fee2e2; color: #991b1b;}
.titre2 {font-family: Georgia, serif; font-size: 1.25rem; color: #0f4c3a; margin: 1.4rem 0 .6rem 0;}
.hh {font-size: .72rem; letter-spacing: .1em; color: #7b7a68; font-weight: 700; text-transform: uppercase;}
.note {background: #fff8e6; border: 1px solid #f6e0a6; color: #7a5a00; border-radius: 12px; padding: .7rem 1rem; font-size: .85rem; margin: .6rem 0;}
</style>"""
st.markdown(CSS, unsafe_allow_html=True)
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
    badge = '<span class="a on">Contrôle auto</span>' if auto else '<span class="a off">Référence</span>'
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


HERO_SVG = ('<svg viewBox="0 0 440 280" width="100%" xmlns="http://www.w3.org/2000/svg">'
            '<defs><linearGradient id="gb" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f3d98f"/><stop offset="1" stop-color="#0f8a5f"/></linearGradient></defs>'
            '<g fill="rgba(255,255,255,.07)" stroke="rgba(255,255,255,.2)">'
            '<rect x="8" y="8" width="200" height="120" rx="14"/><rect x="226" y="8" width="206" height="120" rx="14"/>'
            '<rect x="8" y="144" width="200" height="128" rx="14"/><rect x="226" y="144" width="206" height="128" rx="14"/></g>'
            '<rect x="28" y="28" width="64" height="8" rx="4" fill="rgba(255,255,255,.35)"/>'
            '<g fill="url(#gb)"><rect x="34" y="82" width="18" height="30" rx="3"/><rect x="62" y="68" width="18" height="44" rx="3"/>'
            '<rect x="90" y="74" width="18" height="38" rx="3"/><rect x="118" y="54" width="18" height="58" rx="3"/>'
            '<rect x="146" y="44" width="18" height="68" rx="3"/><rect x="174" y="30" width="18" height="82" rx="3"/></g>'
            '<rect x="246" y="28" width="64" height="8" rx="4" fill="rgba(255,255,255,.35)"/>'
            '<polyline points="246,104 278,88 308,96 340,66 370,72 414,36" fill="none" stroke="#9be0bf" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
            '<g fill="#9be0bf"><circle cx="278" cy="88" r="4"/><circle cx="340" cy="66" r="4"/><circle cx="414" cy="36" r="4"/></g>'
            '<circle cx="74" cy="214" r="36" fill="none" stroke="rgba(255,255,255,.15)" stroke-width="13"/>'
            '<circle cx="74" cy="214" r="36" fill="none" stroke="#e9c46a" stroke-width="13" stroke-dasharray="136 227" transform="rotate(-90 74 214)" stroke-linecap="round"/>'
            '<g fill="rgba(255,255,255,.3)"><rect x="130" y="190" width="60" height="8" rx="4"/><rect x="130" y="212" width="46" height="8" rx="4"/><rect x="130" y="234" width="54" height="8" rx="4"/></g>'
            '<g fill="rgba(255,255,255,.14)"><rect x="248" y="174" width="164" height="10" rx="5"/><rect x="248" y="204" width="164" height="10" rx="5"/><rect x="248" y="234" width="164" height="10" rx="5"/></g>'
            '<g fill="#e9c46a"><rect x="248" y="174" width="120" height="10" rx="5"/><rect x="248" y="204" width="90" height="10" rx="5" fill="#9be0bf"/><rect x="248" y="234" width="140" height="10" rx="5"/></g></svg>')

# ---------------- EN-TÊTE ----------------
st.markdown(f'<div class="brand"><div class="logo">{ico("layers", 24)}</div>'
            '<div><b>MaroIFRS</b><small>Conversion CGNC · IAS · IFRS</small></div><div class="sp"></div>'
            f'<span class="chip">{len(NORMES)} normes contrôlées · {len(CATALOGUE)} au référentiel</span></div>',
            unsafe_allow_html=True)

(t_acc, t_norm, t_trans, t_fisc, t_calc, t_res, t_dash, t_apropos, t_contact) = st.tabs(
    ["Accueil", "Normes", "Transformation", "Contrôle fiscal", "Calculateurs", "Résultats", "Tableau de bord", "À propos", "Contact"])

# ---------------- ACCUEIL ----------------
with t_acc:
    st.markdown('<div class="hero"><div class="hero-l">'
                f'<span class="pill">{ico("shield", 16)} CGNC → IAS / IFRS</span>'
                '<h1>Vos états de synthèse marocains, <em>aux normes internationales.</em></h1>'
                '<p>Importez votre balance. La plateforme analyse vos comptes, applique les règles IAS/IFRS, '
                'contrôle le passage au résultat fiscal et produit un rapport PDF prêt à présenter.</p>'
                f'<div class="tags"><span class="p">Accessible en ligne</span><span>Gratuit et illimité</span>'
                f'<span>{len(NORMES)} normes contrôlées</span><span>Rapport PDF + Excel</span></div></div>'
                f'<div class="hero-r">{HERO_SVG}</div></div>', unsafe_allow_html=True)
    st.markdown(f'<div class="stats"><div class="stat"><b>{len(CATALOGUE)}</b><span>normes IAS / IFRS au référentiel</span></div>'
                f'<div class="stat"><b>{len(NORMES)}</b><span>contrôles automatiques</span></div>'
                '<div class="stat"><b>3</b><span>formats : Excel, CSV, PDF texte</span></div>'
                '<div class="stat"><b>PDF</b><span>rapport complet téléchargeable</span></div></div>', unsafe_allow_html=True)
    entete("Méthode", "Quatre étapes, de la balance aux états IFRS")
    grille([etape("01", "upload", "Importer", "Déposez la balance générale de l'entreprise (Excel, CSV ou PDF texte)."),
            etape("02", "scan", "Identifier", "Détection automatique des comptes du plan comptable marocain."),
            etape("03", "scale", "Appliquer", "Contrôles IAS/IFRS et passage du résultat comptable au résultat fiscal."),
            etape("04", "filecheck", "Restituer", "Constats, états, score de conformité, rapport PDF et Excel.")])
    st.write("")
    c1, c2 = st.columns([3, 2])
    c1.markdown('<div class="panel"><div style="color:#0f8a5f">' + ico("chart", 30) + '</div>'
                '<h3>Fonctionnalités disponibles</h3>' +
                liste(["Lecture des fichiers Excel (.xlsx, .xls), CSV et PDF texte (avec contrôle des montants)",
                       "Détection des comptes par numéro CGNC et statut de chaque compte",
                       f"{len(NORMES)} normes avec contrôles automatiques, {len(CATALOGUE)} normes au référentiel",
                       "Contrôle fiscal : résultat comptable → résultat fiscal → IS et cotisation minimale",
                       "Score de conformité IFRS en nombre de comptes et en valeur",
                       "Calculateurs d'impôts différés (IAS 12) et de dépréciation (IAS 36)",
                       "Export Excel et rapport PDF"], "ok", "check") + '</div>', unsafe_allow_html=True)
    c2.markdown('<div class="panel g"><h3>Limites actuelles</h3>' +
                liste(["Les PDF scannés (images) ne sont pas lus ; les montants lus dans un PDF doivent être vérifiés",
                       "Les retraitements de valeur (juste valeur, IFRS 16) sont signalés mais non calculés",
                       "Le mapping des comptes est simplifié : à vérifier avec votre plan comptable",
                       "Taux d'IS et cotisation minimale conformes au CGI 2026 (art. 19 et 144) ; régimes particuliers à vérifier",
                       "Les résultats doivent être validés par un expert-comptable"], "no", "clock") + '</div>',
                unsafe_allow_html=True)

# ---------------- NORMES ----------------
with t_norm:
    entete("Référentiel", "Toutes les normes IAS / IFRS")
    recherche = st.text_input("Rechercher une norme (numéro ou mot-clé)", placeholder="ex. IAS 16, location, impôts…")
    noms = [n for n in CATALOGUE if not recherche or recherche.lower() in (n + " " + CATALOGUE[n][0] + " " + CATALOGUE[n][1]).lower()]
    if not noms:
        st.info("Aucune norme ne correspond à cette recherche.")
    grille([carte_norme(n, i) for i, n in enumerate(noms)], n=4)
    st.caption("« Contrôle auto » : la plateforme applique des règles sur la balance. « Référence » : norme au catalogue, "
               "sans contrôle automatique sur une balance (à traiter dans l'annexe ou par un retraitement manuel).")


# ---------------- TRANSFORMATION ----------------
df = None
res_statuts = None
fichier = None
with t_trans:
    entete("Transformation", "Importez votre balance")
    c1, c2 = st.columns([2, 1])
    fichier = c1.file_uploader("Balance générale (Excel / CSV / PDF texte)", type=["xlsx", "xls", "csv", "pdf"])
    c2.write("")
    c2.write("")
    modele = io.BytesIO()
    exemple_balance().to_excel(modele, index=False)
    c2.download_button("Télécharger un modèle de balance", modele.getvalue(), "modele_balance.xlsx")
    choix = st.multiselect("Normes à appliquer", list(NORMES), default=list(NORMES))
    if fichier is None:
        st.info("Importez une balance (colonnes : compte, libelle, debit, credit) ou téléchargez le modèle. Les PDF texte sont acceptés.")
    else:
        try:
            if fichier.name.lower().endswith(".pdf"):
                with st.spinner("Lecture du PDF en cours..."):
                    brut = lire_pdf(fichier)
                at = brut.attrs
                if brut.empty:
                    st.error("Aucun compte reconnu dans ce PDF. "
                             + ("Le PDF semble scanné (image) : utilisez un PDF texte, Excel ou CSV."
                                if at.get("scanne") else
                                "Vérifiez qu'il s'agit bien d'une balance (colonnes : compte, libellé, débit, crédit), "
                                "ou utilisez Excel / CSV."))
                else:
                    if at.get("etats_financiers"):
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
                                   "Ouvrez l'onglet « Résultats ».")
            else:
                df = charger_balance(fichier)
                st.success(f"Balance chargée : {len(df)} comptes.")
        except Exception as e:
            st.error(f"Erreur de lecture : {e}")

    if df is not None and not choix:
        st.warning("Sélectionnez au moins une norme à appliquer.")
    elif df is not None:
        res_statuts = statuts_comptes(df, choix)
        par_statut = compteurs_statuts(res_statuts)
        conf_n, conf_v = conformite(res_statuts)
        st.markdown('<div class="pipe"><span>✓ Importer</span><span>✓ Identifier</span>'
                    '<span>✓ Appliquer</span><span>✓ Restituer</span></div>', unsafe_allow_html=True)
        k = st.columns(4)
        k[0].markdown(kpi("filetext", "Comptes analysés", str(len(df))), unsafe_allow_html=True)
        k[1].markdown(kpi("shield", "Conformité (comptes)", f"{conf_n:.0f} %"), unsafe_allow_html=True)
        k[2].markdown(kpi("scale", "Conformité (en valeur)", f"{conf_v:.0f} %"), unsafe_allow_html=True)
        k[3].markdown(kpi("flag", "Comptes à traiter", str(len(df) - par_statut["inchangé"])), unsafe_allow_html=True)
        st.write("")
        g1, g2 = st.columns(2)
        g1.markdown(f'<div class="panel"><h3>Statut des comptes</h3>{donut_html(par_statut)}</div>', unsafe_allow_html=True)
        couleur = "#1f9d6b" if conf_n >= 80 else ("#e0a526" if conf_n >= 50 else "#c0392b")
        definitions = "".join(f'<div class="def"><span class="dot" style="background:{COULEURS_STATUT[s]}"></span> '
                              f'<b>{s}</b> : {DEFINITIONS_STATUT[s]}</div>' for s in STATUTS)
        g2.markdown(f'<div class="panel"><h3>Conformité IFRS</h3>{gauge_html(conf_n, couleur)}'
                    f'<div class="def" style="margin-bottom:.8rem">des comptes ne nécessitent ni retraitement ni vérification.</div>'
                    f'{definitions}</div>', unsafe_allow_html=True)
        with st.expander("Comment est calculé ce score ?"):
            st.markdown("**Conformité (comptes)** = comptes au statut « inchangé » ÷ nombre total de comptes.  \n"
                        "**Conformité (en valeur)** = somme des soldes (en valeur absolue) des comptes « inchangés » ÷ somme "
                        "des soldes de tous les comptes.  \nLes statuts viennent des règles des normes sélectionnées ; "
                        "le motif de chaque statut est indiqué dans le tableau ci-dessous.")
        st.markdown('<div class="titre2">Détail des comptes</div>', unsafe_allow_html=True)
        filtre = st.multiselect("Filtrer par statut", STATUTS, default=STATUTS, key="filtre_statut")
        aff = res_statuts[res_statuts["statut"].isin(filtre)].copy()
        aff["statut"] = aff["statut"].map(lambda s: f"{PUCES[s]} {s}")
        aff = aff.rename(columns={"compte": "Compte", "libelle": "Libellé", "solde": "Solde (MAD)",
                                  "statut": "Statut", "motif": "Motif"})
        st.dataframe(aff[["Compte", "Libellé", "Solde (MAD)", "Statut", "Motif"]].style.format({"Solde (MAD)": "{:,.0f}"}),
                     hide_index=True, use_container_width=True)
        st.caption("Le détail des constats et l'état de situation se trouvent dans l'onglet « Résultats ».")

constats_df = pd.DataFrame(columns=["Norme", "Niveau", "Constat", "Retraitement suggéré"])
etat = pd.DataFrame(columns=["Poste", "Montant"])
actif = passif = 0.0
conf_n = conf_v = 0.0
if df is not None:
    constats_df = pd.DataFrame([c for n in choix for c in NORMES[n](df)],
                               columns=["Norme", "Niveau", "Constat", "Retraitement suggéré"])
    etat, actif, passif = etat_situation(df)
    if res_statuts is not None:  # enregistrement dans l'historique des transformations
        par_statut_h = compteurs_statuts(res_statuts)
        conf_n, conf_v = conformite(res_statuts)
        entree = {"cle": f"{fichier.name}|{fichier.size}", "fichier": fichier.name,
                  "date": datetime.now().strftime("%d/%m/%Y %H:%M:%S"), "comptes": int(len(df)),
                  "conformite": round(conf_n, 1), "valeur": round(conf_v, 1), "statuts": par_statut_h,
                  "constats": constats_df.to_dict("records")}
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
    entete("Fiscalité", "Contrôle fiscal : résultat comptable → résultat fiscal")
    if df is None:
        st.info("Importez d'abord une balance dans l'onglet « Transformation » : le résultat comptable en est déduit.")
    else:
        st.markdown('<div class="note">Règles appliquées : CGI 2026, article 19-I (IS : 20 % ; 35 % si bénéfice net ≥ 100 M MAD ; '
                    '40 % pour les établissements de crédit et assurances) et article 144-I (cotisation minimale : 0,25 % des produits, '
                    'minimum 3 000 MAD). Pour un exercice ouvert avant le 1er janvier 2026, des taux transitoires s\'appliquent '
                    '(art. 247-XXXVII-A) : choisissez « Autre taux ».</div>', unsafe_allow_html=True)
        p1, p2, p3 = st.columns([2, 1, 1])
        cat_label = p1.selectbox("Catégorie de société (art. 19-I)", list(CATEGORIES_IS.values()))
        categorie = next(k for k, v in CATEGORIES_IS.items() if v == cat_label)
        deficits = p2.number_input("Déficits antérieurs reportables (MAD)", 0.0, 1e12, 0.0, step=10000.0)
        ventes_reduit = p3.number_input("Ventes au taux de 0,15 % (MAD)", 0.0, 1e13, 0.0, step=10000.0,
                                        help="Ventes de produits pétroliers, gaz, beurre, huile, sucre, farine, eau, électricité "
                                             "et médicaments par des entreprises commerciales (art. 144-I-D).")
        q1, q2, q3 = st.columns([1, 2, 2])
        taux_libre = q1.number_input("Taux d'IS (%)", 0.0, 60.0, 20.0, step=0.25, disabled=(categorie != "libre")) / 100
        exclu_35 = q2.checkbox("Exclue du taux de 35 % (CFC, zone d'accélération industrielle, convention d'investissement ≥ 1,5 Md MAD)",
                               disabled=(categorie != "ordinaire"))
        exonere_cm = q3.checkbox("Société dans ses 36 premiers mois d'exploitation (exonérée de cotisation minimale)")
        e1, e2 = st.columns(2)
        e1.markdown("**Réintégrations fiscales**")
        reint = e1.data_editor(tableau_fiscal_vide(LIGNES_REINT), num_rows="dynamic", hide_index=True, key="reint",
                               use_container_width=True)
        e2.markdown("**Déductions fiscales**")
        deduc = e2.data_editor(tableau_fiscal_vide(LIGNES_DEDUC), num_rows="dynamic", hide_index=True, key="deduc",
                               use_container_width=True)
        fiscal = calc_fiscal(df, reint, deduc, deficits, categorie, exclu_35, taux_libre, exonere_cm, ventes_reduit)
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
    entete("Outils", "Calculateurs IAS 12 et IAS 36")
    s12, s36 = st.tabs(["IAS 12 – Impôts différés", "IAS 36 – Dépréciation"])
    with s12:
        taux_id = st.number_input("Taux d'impôt (%)", 0.0, 100.0, 20.0, key="taux_id") / 100
        base = pd.DataFrame({"Libellé": ["Constructions"], "Nature": ["Actif"],
                             "Valeur comptable IFRS": [500_000.0], "Base fiscale": [400_000.0]})
        saisie = st.data_editor(base, num_rows="dynamic", use_container_width=True,
                                column_config={"Nature": st.column_config.SelectboxColumn(options=["Actif", "Passif"])})
        if not saisie.dropna(subset=["Libellé"]).empty:
            dt = calc_impots_differes(saisie, taux_id)
            st.dataframe(dt, hide_index=True, use_container_width=True)
            st.markdown(kpi("receipt", "Impôt différé net (positif = passif)", m(dt["Impôt différé"].sum())),
                        unsafe_allow_html=True)
    with s36:
        a1, a2 = st.columns(2)
        vc = a1.number_input("Valeur comptable de l'UGT", value=1_000_000.0, step=10_000.0)
        jv = a1.number_input("Juste valeur - coûts de cession", value=800_000.0, step=10_000.0)
        taux = a2.number_input("Taux d'actualisation (%)", 0.0, 100.0, 10.0) / 100
        g = a2.number_input("Croissance à l'infini (%)", 0.0, 20.0, 2.0) / 100
        flux_df = st.data_editor(pd.DataFrame({"Année": [1, 2, 3, 4, 5], "Flux de trésorerie": [150_000.0] * 5}),
                                 use_container_width=True)
        flux = flux_df["Flux de trésorerie"].fillna(0).tolist()
        vu = valeur_utilite(flux, taux, g)
        recouvrable = max(vu, jv)
        perte = max(0.0, vc - recouvrable)
        k1, k2, k3 = st.columns(3)
        k1.markdown(kpi("chart", "Valeur d'utilité", m(vu)), unsafe_allow_html=True)
        k2.markdown(kpi("target", "Valeur recouvrable", m(recouvrable)), unsafe_allow_html=True)
        k3.markdown(kpi("down", "Perte de valeur", m(perte)), unsafe_allow_html=True)
        ugt = pd.DataFrame({"Indicateur": ["Valeur comptable", "Juste valeur nette", "Valeur d'utilité",
                                           "Valeur recouvrable", "Perte de valeur"],
                            "Montant": [vc, jv, vu, recouvrable, perte]})

# ---------------- RÉSULTATS ----------------
with t_res:
    entete("Résultats", "Constats et états IFRS")
    if df is None:
        st.info("Importez d'abord une balance dans l'onglet « Transformation ».")
    else:
        tous = pd.concat([constats_df, constats_fisc], ignore_index=True)
        affiche = tous.copy()
        affiche["Niveau"] = affiche["Niveau"].map(NIVEAUX)
        st.markdown("#### Constats et retraitements suggérés")
        st.dataframe(affiche, hide_index=True, use_container_width=True)
        st.markdown("#### État de situation financière (IAS 1)")
        st.dataframe(etat.style.format({"Montant": "{:,.0f}"}), hide_index=True, use_container_width=True)
        st.write(f"Total actif : **{m(actif)}** | Total passif : **{m(passif)}**")
        try:
            sortie_xlsx = excel_formate(df, tous, etat, res_statuts, conf_n, conf_v, fiscal, dt, ugt,
                                        fichier.name if fichier else "")
        except Exception as e:
            sortie_xlsx = b""
            st.error(f"Export Excel indisponible : {e}")
        b1, b2 = st.columns(2)
        b1.download_button("Télécharger les résultats (Excel)", sortie_xlsx, "resultats_ifrs.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
        if res_statuts is not None:
            try:
                pdf_octets = rapport_pdf(df, tous, etat, res_statuts, conf_n, conf_v, fiscal, dt, ugt,
                                         fichier.name if fichier else "")
                b2.download_button("Télécharger le rapport (PDF)", pdf_octets, "rapport_ifrs.pdf", "application/pdf",
                                   use_container_width=True)
            except Exception as e:
                b2.error(f"Rapport PDF indisponible : {e}")

# ---------------- TABLEAU DE BORD ----------------
with t_dash:
    entete("Synthèse", "Tableau de bord")
    histo = st.session_state["historique"]
    if df is not None:
        ca = -solde(df, "711", "712")
        resultat = -solde(df, "6", "7")
        k = st.columns(4)
        k[0].markdown(kpi("coins", "Chiffre d'affaires", m(ca)), unsafe_allow_html=True)
        k[1].markdown(kpi("chart", "Résultat net", m(resultat)), unsafe_allow_html=True)
        k[2].markdown(kpi("bank", "Total actif", m(actif)), unsafe_allow_html=True)
        k[3].markdown(kpi("flag", "Constats", str(len(constats_df) + len(constats_fisc))), unsafe_allow_html=True)
        st.write("")
    if not histo:
        st.info("Aucune transformation enregistrée : importez une balance dans l'onglet « Transformation ».")
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
        h1, h2 = st.columns([6, 1.4])
        h1.markdown('<div class="titre2">Historique des transformations</div>', unsafe_allow_html=True)
        h2.write("")
        if h2.button("🗑 Effacer", key="effacer_histo"):
            st.session_state["historique"] = []
            st.session_state["voir"] = None
            sauver_historique([])
            st.rerun()
        tete = st.columns([4, 3, 1.6, 2, 1.2])
        for col, t in zip(tete, ["Fichier", "Date", "Comptes", "Conformité", ""]):
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
            d2.markdown(f'<div class="panel"><h3>Conformité IFRS</h3><div class="score">{e["conformite"]:.0f}<span>%</span></div>'
                        f'<div class="def">en valeur : {e["valeur"]:.0f} %</div>'
                        f'<div class="def">{len(e["constats"])} constats enregistrés</div></div>', unsafe_allow_html=True)
            detail = pd.DataFrame(e["constats"], columns=["Norme", "Niveau", "Constat", "Retraitement suggéré"])
            detail["Niveau"] = detail["Niveau"].map(NIVEAUX).fillna(detail["Niveau"])
            st.dataframe(detail, hide_index=True, use_container_width=True)
            if st.button("Fermer le détail", key="fermer_detail"):
                st.session_state["voir"] = None
                st.rerun()
        st.caption("L'historique est enregistré dans le fichier historique.json de l'application ; il ne contient que les résultats, pas la balance. "
                   "Sur le site en ligne, il peut être remis à zéro lorsque l'application redémarre.")
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

# ---------------- À PROPOS ----------------
with t_apropos:
    entete("Projet", "À propos")
    st.markdown("Projet réalisé dans le module **Consolidation et normes IFRS**. L'objectif est de faciliter "
                "le travail des comptables, auditeurs et financiers en automatisant une partie de la "
                "conversion du référentiel marocain (CGNC) vers les normes IAS/IFRS.")
    st.write("")
    grille([etape("", "target", "Objectif", "Un outil d'aide à la décision, qui ne remplace pas l'expert-comptable."),
            etape("", "lock", "Confidentialité", "Les balances ne sont pas conservées : seuls les résultats figurent dans l'historique."),
            etape("", "puzzle", "Extensible", "Chaque norme est une fonction que l'on peut ajouter facilement.")], n=3)

# ---------------- CONTACT ----------------
with t_contact:
    entete("Échangeons", "Contact")
    st.markdown(f'<div class="panel"><div class="tile">{ico("mail")}</div><h3>Une question sur la plateforme ?</h3>'
                '<p style="color:#5f6b63;margin:0 0 1.2rem 0">Écrivez-nous : <b>contact@maroifrs.ma</b></p>'
                '<a class="btn" href="mailto:contact@maroifrs.ma?subject=MaroIFRS">Envoyer un message</a></div>',
                unsafe_allow_html=True)

st.markdown('<div class="pied">MaroIFRS – Outil d\'aide à la conversion CGNC → IAS/IFRS, à valider par un expert-comptable.</div>',
            unsafe_allow_html=True)
