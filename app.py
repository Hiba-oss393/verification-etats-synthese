"""
Maroc IFRS Transform - version locale (hors ligne, sans IA externe)
Lancer :  streamlit run app.py
Normes : IAS 1, IFRS 15 / IAS 18, IAS 16, IAS 12, IAS 36
Pour ajouter une norme : écrire une fonction norme_xxx(df) -> liste de constats,
puis l'ajouter au dictionnaire NORMES en bas de la section 3.
ATTENTION : les numéros de comptes (CGNC) sont à vérifier avec votre plan comptable.
"""
import io
import re
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Maroc IFRS Transform (hors ligne)", layout="wide", initial_sidebar_state="collapsed")


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


def _est_montant(texte):
    return _nombre(texte) is not None and bool(re.search(r"\d|^[-\u2013\u2014]$", texte.strip()))


def _cellules_page(page):
    """Regroupe les mots d'une page en lignes, puis en cellules (selon l'écart horizontal)."""
    mots = sorted(page.extract_words(), key=lambda w: (w["top"], w["x0"]))
    lignes, courante, dernier = [], [], None
    for w in mots:
        if dernier is not None and abs(w["top"] - dernier) > 3:
            lignes.append(courante)
            courante = []
        courante.append(w)
        dernier = w["top"]
    if courante:
        lignes.append(courante)
    resultat = []
    for ligne in lignes:
        cellules = []
        for w in sorted(ligne, key=lambda w: w["x0"]):
            if cellules and w["x0"] - cellules[-1][2] <= 6:
                cellules[-1] = (cellules[-1][0] + " " + w["text"], cellules[-1][1], w["x1"])
            else:
                cellules.append((w["text"], w["x0"], w["x1"]))
        resultat.append(cellules)
    return resultat


def _lire_cellules(lignes):
    """Transforme des lignes de cellules en comptes. Retourne (lignes lues, nombre de lignes ignorées)."""
    pleines, partielles, ignorees = [], [], 0
    for cel in lignes:
        txt = [c[0] for c in cel]
        if len(cel) < 3 or not re.fullmatch(r"\d{2,8}", txt[0]):
            continue
        if _est_montant(txt[-1]) and _est_montant(txt[-2]) and len(cel) >= 4:
            pleines.append((txt[0], " ".join(txt[1:-2]), _nombre(txt[-2]), _nombre(txt[-1]),
                            (cel[-2][1] + cel[-2][2]) / 2, (cel[-1][1] + cel[-1][2]) / 2))
        elif _est_montant(txt[-1]):
            partielles.append((txt[0], " ".join(txt[1:-1]), _nombre(txt[-1]), (cel[-1][1] + cel[-1][2]) / 2))
        else:
            ignorees += 1
    res = [(a, b, c, d) for a, b, c, d, _, _ in pleines]
    if partielles:
        xd = xc = None
        if pleines:  # position des colonnes d'après les lignes complètes
            xd = sorted(p[4] for p in pleines)[len(pleines) // 2]
            xc = sorted(p[5] for p in pleines)[len(pleines) // 2]
        else:  # sinon, d'après la ligne d'en-tête « Débit ... Crédit »
            for cel in lignes:
                if cel and not re.fullmatch(r"\d{2,8}", cel[0][0]):
                    d = [c for c in cel if "debit" in c[0].lower().replace("\u00e9", "e")]
                    k = [c for c in cel if "credit" in c[0].lower().replace("\u00e9", "e")]
                    if d and k:
                        xd, xc = (d[-1][1] + d[-1][2]) / 2, (k[-1][1] + k[-1][2]) / 2
        if xd is None:
            ignorees += len(partielles)
        else:
            for a, b, v, x in partielles:
                res.append((a, b, v, 0.0) if abs(x - xd) <= abs(x - xc) else (a, b, 0.0, v))
    return res, ignorees


def lire_pdf(fichier):
    """Lit une balance dans un PDF TEXTE (les PDF scannés ne sont pas pris en charge)."""
    import pdfplumber
    fichier.seek(0)
    lignes, ignorees = [], 0
    with pdfplumber.open(fichier) as pdf:
        for page in pdf.pages:
            cellules_tab = []
            for tableau in page.extract_tables():
                for ligne in tableau:
                    cellules_tab.append([((c or "").replace("\n", " ").strip(), 0, 0) for c in ligne])
            lus, ign = [], 0
            if cellules_tab:  # PDF avec traits de tableau : les cellules vides restent à leur place
                for cel in cellules_tab:
                    txt = [c[0] for c in cel]
                    if len(txt) >= 4 and re.fullmatch(r"\d{2,8}", txt[0]):
                        d, c = _nombre(txt[-2]), _nombre(txt[-1])
                        if d is None or c is None:
                            ign += 1
                        else:
                            lus.append((txt[0], " ".join(t for t in txt[1:-2] if t), d, c))
            if not lus:  # PDF sans traits : lecture par position des mots
                lus, ign = _lire_cellules(_cellules_page(page))
            lignes += lus
            ignorees += ign
    df = pd.DataFrame(lignes, columns=["compte", "libelle", "debit", "credit"])
    df.attrs["ignorees"] = ignorees
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


# ============================ 5. INTERFACE ============================
NIVEAUX = {"Erreur": "🔴 Erreur", "Retraitement": "🟠 Retraitement", "À vérifier": "🟡 À vérifier", "Info": "🔵 Info"}

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


INFOS_NORMES = {
    "IAS 1": ("filetext", "Présentation des états financiers",
              "Structure et contenu minimal des états financiers pour améliorer la transparence et la comparabilité.",
              "Équilibre de la balance · éléments non courants · non-valeurs",
              "linear-gradient(135deg,#e3f0ff,#c9e0fb)"),
    "IFRS 15 / IAS 18": ("coins", "Produits des activités ordinaires",
                         "Reconnaissance du chiffre d'affaires issu des contrats conclus avec les clients.",
                         "Remises accordées · produits constatés d'avance · subventions",
                         "linear-gradient(135deg,#dff6f1,#bfe8e0)"),
    "IAS 16": ("factory", "Immobilisations corporelles",
               "Comptabilisation, amortissement par composants et durées d'utilité des immobilisations.",
               "Taux d'amortissement · composants · crédit-bail",
               "linear-gradient(135deg,#e8e9fb,#d0d3f5)"),
    "IAS 12": ("receipt", "Impôts sur le résultat",
               "Impôts exigibles et impôts différés issus des différences temporaires.",
               "Taux d'impôt effectif · impôts différés",
               "linear-gradient(135deg,#fdf0dc,#f8dfb8)"),
    "IAS 36": ("down", "Dépréciation d'actifs",
               "Tests de perte de valeur : valeur d'utilité, juste valeur et valeur recouvrable.",
               "Fonds commercial · valeur recouvrable",
               "linear-gradient(135deg,#fbe4ea,#f5c8d3)"),
}

CSS = """<style>
html, body, .stApp, .stApp p, .stApp label, .stApp li, .stApp button, .stApp input {font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;}
#MainMenu, footer, header {visibility: hidden;}
[data-testid="stSidebar"], [data-testid="collapsedControl"] {display: none;}
.stApp {background: #f6f9fd;}
.block-container {padding-top: 1.2rem; max-width: 1180px;}
.serif {font-family: Georgia, 'Palatino Linotype', 'Book Antiqua', serif;}
.brand {display: flex; align-items: center; gap: .8rem; background: white; border: 1px solid #dfe7f1; border-radius: 16px; padding: .8rem 1.2rem;}
.logo {width: 44px; height: 44px; border-radius: 12px; background: linear-gradient(135deg,#1b6fd1,#0f4c9c); color: white; display: flex; align-items: center; justify-content: center;}
.brand b {font-family: Georgia, serif; font-size: 1.3rem; color: #0b4a82; display: block; line-height: 1.1;}
.brand small {font-size: .66rem; letter-spacing: .18em; color: #6b7f96; text-transform: uppercase;}
.stTabs [data-baseweb="tab-list"] {gap: 4px; background: white; border: 1px solid #dfe7f1; padding: .4rem; border-radius: 14px; flex-wrap: wrap;}
.stTabs [data-baseweb="tab"] {border-radius: 10px; padding: .45rem 1.1rem; height: auto;}
.stTabs [data-baseweb="tab"] p {font-weight: 600; color: #475569; font-size: .95rem;}
.stTabs [aria-selected="true"] {background: #e0efff;}
.stTabs [aria-selected="true"] p {color: #0b4a82;}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {display: none;}
.hero {display: flex; flex-wrap: wrap; gap: 2rem; align-items: center; color: white; border-radius: 26px; padding: 3.2rem 3rem; margin: 1.2rem 0 2.2rem 0; background: radial-gradient(circle at 88% 15%, rgba(46,134,222,.38), transparent 45%), linear-gradient(135deg,#041a33 0%,#0a3566 70%,#0d4a8a 100%);}
.hero-l {flex: 1 1 400px;}
.hero-r {flex: 1 1 330px;}
.pill {display: inline-flex; align-items: center; gap: .45rem; border: 1px solid rgba(255,255,255,.3); background: rgba(255,255,255,.08); border-radius: 999px; padding: .3rem .9rem; font-size: .8rem; letter-spacing: .08em; font-weight: 600;}
.hero h1 {font-family: Georgia, 'Palatino Linotype', serif; font-weight: 500; font-size: 2.9rem; line-height: 1.12; color: white; margin: 1.1rem 0 1rem 0; padding: 0;}
.hero p {color: #c8d8ee; font-size: 1.05rem; line-height: 1.6; max-width: 560px; margin: 0 0 1.4rem 0;}
.tags span {display: inline-block; margin: 0 .5rem .5rem 0; padding: .55rem 1.1rem; border-radius: 12px; font-weight: 600; font-size: .9rem; background: rgba(255,255,255,.1); border: 1px solid rgba(255,255,255,.28);}
.tags span.p {background: linear-gradient(135deg,#1b7be0,#1565c0); border-color: transparent;}
.eyebrow {color: #1b6fd1; letter-spacing: .2em; font-size: .75rem; font-weight: 700; text-transform: uppercase; margin-top: 1.2rem;}
.h2 {font-family: Georgia, 'Palatino Linotype', serif; color: #0b4a82; font-size: 2.1rem; font-weight: 500; margin: .3rem 0 1.2rem 0;}
.step {position: relative; background: white; border: 1px solid #dfe7f1; border-radius: 18px; padding: 1.4rem; min-height: 190px; box-shadow: 0 1px 3px rgba(11,74,130,.05);}
.step .n {position: absolute; top: 1rem; right: 1.2rem; font-family: Georgia, serif; font-size: 1.7rem; color: #dbe6f3;}
.tile {width: 44px; height: 44px; border-radius: 12px; background: #e0efff; color: #0b4a82; display: flex; align-items: center; justify-content: center; margin-bottom: 1.1rem;}
.step h3, .nc h3 {font-family: Georgia, serif; font-weight: 600; color: #0f2742; font-size: 1.1rem; margin: 0 0 .4rem 0; padding: 0;}
.step p, .nc p {color: #5b6b80; font-size: .9rem; line-height: 1.5; margin: 0;}
.nc {background: white; border: 1px solid #dfe7f1; border-radius: 18px; overflow: hidden; box-shadow: 0 1px 3px rgba(11,74,130,.05); min-height: 330px;}
.nc .top {height: 130px; position: relative; display: flex; align-items: center; justify-content: center; color: rgba(11,74,130,.28);}
.nc .top .b {position: absolute; top: 1rem; left: 1rem; background: white; color: #0b4a82; border-radius: 999px; padding: .3rem .8rem; font-weight: 700; font-size: .85rem; display: inline-flex; gap: .4rem; align-items: center; box-shadow: 0 1px 4px rgba(0,0,0,.08);}
.nc .body {padding: 1.2rem 1.3rem 1.4rem 1.3rem;}
.nc .ctl {margin-top: .9rem; font-size: .78rem; color: #1b6fd1; font-weight: 600;}
.panel {background: white; border: 1px solid #dfe7f1; border-radius: 22px; padding: 2rem; min-height: 300px;}
.panel.g {background: #eef2f7; border-color: #e1e8f0;}
.panel h3 {font-family: Georgia, serif; color: #0b4a82; font-weight: 500; font-size: 1.6rem; margin: .6rem 0 1.2rem 0; padding: 0;}
.panel.g h3 {color: #0f2742; font-size: 1.2rem;}
.li {display: flex; gap: .7rem; align-items: flex-start; color: #1e293b; font-size: .93rem; margin-bottom: .75rem; line-height: 1.45;}
.li svg {flex: none; margin-top: 2px;}
.ok svg {color: #16a34a;}
.no svg {color: #f59e0b;}
.kpi {display: flex; gap: .9rem; align-items: center; background: white; border: 1px solid #dfe7f1; border-radius: 16px; padding: 1rem 1.2rem;}
.kpi .kico {width: 44px; height: 44px; flex: none; border-radius: 12px; background: #e0efff; color: #0b4a82; display: flex; align-items: center; justify-content: center;}
.kpi span {display: block; color: #6b7f96; font-size: .8rem;}
.kpi b {font-family: Georgia, serif; color: #0f2742; font-size: 1.4rem; font-weight: 600;}
.btn {display: inline-block; background: linear-gradient(135deg,#1b7be0,#1565c0); color: white !important; padding: .75rem 1.5rem; border-radius: 12px; text-decoration: none; font-weight: 700;}
.pied {text-align: center; color: #6b7f96; font-size: .85rem; margin: 2.5rem 0 1rem 0;}
.stDownloadButton button, .stButton button {background: linear-gradient(135deg,#1b7be0,#1565c0); border: none; border-radius: 12px; padding: .55rem 1.1rem;}
.stDownloadButton button p, .stButton button p {color: white; font-weight: 600;}
[data-testid="stFileUploaderDropzone"] {background: white; border: 1.5px dashed #9fc2ea; border-radius: 16px;}
[data-testid="stAlert"] {border-radius: 14px;}
</style>"""
st.markdown(CSS, unsafe_allow_html=True)


def kpi(icone, label, valeur):
    return f'<div class="kpi"><div class="kico">{ico(icone)}</div><div><span>{label}</span><b>{valeur}</b></div></div>'


def etape(num, icone, titre, texte):
    return (f'<div class="step"><div class="n">{num}</div><div class="tile">{ico(icone)}</div>'
            f'<h3>{titre}</h3><p>{texte}</p></div>')


def carte_norme(nom):
    icone, titre, desc, controles, fond = INFOS_NORMES[nom]
    return (f'<div class="nc"><div class="top" style="background:{fond}">'
            f'<div class="b">{ico(icone, 16)} {nom}</div>{ico(icone, 64)}</div>'
            f'<div class="body"><h3>{titre}</h3><p>{desc}</p><div class="ctl">Contrôles : {controles}</div></div></div>')


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
            '<defs><linearGradient id="gb" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#6db8ff"/><stop offset="1" stop-color="#1b6fd1"/></linearGradient></defs>'
            '<g fill="rgba(255,255,255,.07)" stroke="rgba(255,255,255,.2)">'
            '<rect x="8" y="8" width="200" height="120" rx="14"/><rect x="226" y="8" width="206" height="120" rx="14"/>'
            '<rect x="8" y="144" width="200" height="128" rx="14"/><rect x="226" y="144" width="206" height="128" rx="14"/></g>'
            '<rect x="28" y="28" width="64" height="8" rx="4" fill="rgba(255,255,255,.35)"/>'
            '<g fill="url(#gb)"><rect x="34" y="82" width="18" height="30" rx="3"/><rect x="62" y="68" width="18" height="44" rx="3"/>'
            '<rect x="90" y="74" width="18" height="38" rx="3"/><rect x="118" y="54" width="18" height="58" rx="3"/>'
            '<rect x="146" y="44" width="18" height="68" rx="3"/><rect x="174" y="30" width="18" height="82" rx="3"/></g>'
            '<rect x="246" y="28" width="64" height="8" rx="4" fill="rgba(255,255,255,.35)"/>'
            '<polyline points="246,104 278,88 308,96 340,66 370,72 414,36" fill="none" stroke="#5eead4" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
            '<g fill="#5eead4"><circle cx="278" cy="88" r="4"/><circle cx="340" cy="66" r="4"/><circle cx="414" cy="36" r="4"/></g>'
            '<circle cx="74" cy="214" r="36" fill="none" stroke="rgba(255,255,255,.15)" stroke-width="13"/>'
            '<circle cx="74" cy="214" r="36" fill="none" stroke="#4aa3ff" stroke-width="13" stroke-dasharray="136 227" transform="rotate(-90 74 214)" stroke-linecap="round"/>'
            '<g fill="rgba(255,255,255,.3)"><rect x="130" y="190" width="60" height="8" rx="4"/><rect x="130" y="212" width="46" height="8" rx="4"/><rect x="130" y="234" width="54" height="8" rx="4"/></g>'
            '<g fill="rgba(255,255,255,.14)"><rect x="248" y="174" width="164" height="10" rx="5"/><rect x="248" y="204" width="164" height="10" rx="5"/><rect x="248" y="234" width="164" height="10" rx="5"/></g>'
            '<g fill="#4aa3ff"><rect x="248" y="174" width="120" height="10" rx="5"/><rect x="248" y="204" width="90" height="10" rx="5" fill="#5eead4"/><rect x="248" y="234" width="140" height="10" rx="5"/></g></svg>')

# ---------------- EN-TÊTE ----------------
st.markdown(f'<div class="brand"><div class="logo">{ico("layers", 24)}</div>'
            '<div><b>MaroIFRS</b><small>Conversion IAS · IFRS</small></div></div>', unsafe_allow_html=True)

(t_acc, t_norm, t_trans, t_calc, t_res, t_dash, t_apropos, t_contact) = st.tabs(
    ["Accueil", "Normes", "Transformation", "Calculateurs", "Résultats", "Tableau de bord", "À propos", "Contact"])

# ---------------- ACCUEIL ----------------
with t_acc:
    st.markdown('<div class="hero"><div class="hero-l">'
                f'<span class="pill">{ico("shield", 16)} CGNC → IAS / IFRS</span>'
                '<h1>Vos états de synthèse marocains, aux normes internationales.</h1>'
                '<p>Importez votre balance. La plateforme analyse vos comptes, applique les règles IAS/IFRS '
                'et produit des constats exploitables, sans connexion internet.</p>'
                f'<div class="tags"><span class="p">Fonctionne hors ligne</span><span>Gratuit et illimité</span>'
                f'<span>{len(NORMES)} normes intégrées</span></div></div>'
                f'<div class="hero-r">{HERO_SVG}</div></div>', unsafe_allow_html=True)

    entete("Méthode", "Quatre étapes, de la balance aux états IFRS")
    grille([etape("01", "upload", "Importer", "Déposez la balance générale de l'entreprise (Excel ou CSV)."),
            etape("02", "scan", "Identifier", "Détection automatique des comptes du plan comptable marocain."),
            etape("03", "scale", "Appliquer", "Règles de passage IAS 1, IFRS 15, IAS 16, IAS 12 et IAS 36."),
            etape("04", "filecheck", "Restituer", "Constats, états et résultats téléchargeables en Excel.")])
    st.write("")
    c1, c2 = st.columns([3, 2])
    c1.markdown('<div class="panel"><div style="color:#1b6fd1">' + ico("chart", 30) + '</div>'
                '<h3>Fonctionnalités réellement disponibles</h3>' +
                liste(["Lecture des fichiers Excel (.xlsx, .xls), CSV et PDF texte (avec contrôle des montants)",
                       "Détection des comptes par numéro CGNC",
                       "Contrôles automatiques : IAS 1, IFRS 15 / IAS 18, IAS 16, IAS 12, IAS 36",
                       "Calculateurs d'impôts différés (IAS 12) et de dépréciation (IAS 36)",
                       "Export des résultats au format Excel"], "ok", "check") + '</div>', unsafe_allow_html=True)
    c2.markdown('<div class="panel g"><h3>Limites actuelles</h3>' +
                liste(["Les PDF scannés (images) ne sont pas lus ; les montants lus dans un PDF doivent être vérifiés",
                       "Les retraitements de valeur (juste valeur, IFRS 16) sont signalés mais non calculés",
                       "Le mapping des comptes est simplifié : à vérifier avec votre plan comptable",
                       "Les résultats doivent être validés par un expert-comptable"], "no", "clock") + '</div>',
                unsafe_allow_html=True)

# ---------------- NORMES ----------------
with t_norm:
    entete("Référentiel", "Comprendre les normes IAS/IFRS")
    grille([carte_norme(n) for n in NORMES], n=3)
    st.caption("D'autres normes peuvent être ajoutées en écrivant une fonction de plus dans app.py.")

# ---------------- TRANSFORMATION ----------------
df = None
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
                brut = lire_pdf(fichier)
                if brut.empty:
                    st.error("Aucun compte reconnu dans ce PDF (PDF scanné ou mise en page non reconnue). "
                             "Utilisez un PDF texte, un fichier Excel ou CSV.")
                else:
                    st.warning("Balance lue depuis un PDF : vérifiez les montants ci-dessous et corrigez-les si besoin "
                               "(le tableau est modifiable) avant d'utiliser les résultats.")
                    if brut.attrs.get("ignorees"):
                        st.error(f"{brut.attrs['ignorees']} ligne(s) du PDF n'ont pas pu être lues : "
                                 "ajoutez-les à la main dans le tableau ou utilisez Excel/CSV.")
                    edite = st.data_editor(brut, num_rows="dynamic", key=f"pdf_{fichier.name}_{fichier.size}")
                    df = preparer_balance(edite)
                    if abs(df["debit"].sum() - df["credit"].sum()) > 1:
                        st.error(f"Total débit ({m(df['debit'].sum())}) différent du total crédit ({m(df['credit'].sum())}) : "
                                 "un montant a probablement été mal lu.")
                    else:
                        st.success(f"Balance lue : {len(df)} comptes, total débit = total crédit ({m(df['debit'].sum())}). "
                                   "Ouvrez l'onglet « Résultats ».")
            else:
                df = charger_balance(fichier)
                st.success(f"Balance chargée : {len(df)} comptes. Ouvrez l'onglet « Résultats ».")
                st.dataframe(df)
        except Exception as e:
            st.error(f"Erreur de lecture : {e}")

constats_df = pd.DataFrame(columns=["Norme", "Niveau", "Constat", "Retraitement suggéré"])
etat = pd.DataFrame(columns=["Poste", "Montant"])
actif = passif = 0.0
if df is not None:
    constats_df = pd.DataFrame([c for n in choix for c in NORMES[n](df)],
                               columns=["Norme", "Niveau", "Constat", "Retraitement suggéré"])
    etat, actif, passif = etat_situation(df)

# ---------------- CALCULATEURS ----------------
dt = pd.DataFrame()
ugt = pd.DataFrame()
with t_calc:
    entete("Outils", "Calculateurs IAS 12 et IAS 36")
    s12, s36 = st.tabs(["IAS 12 – Impôts différés", "IAS 36 – Dépréciation"])
    with s12:
        taux_is = st.number_input("Taux d'impôt (%)", 0.0, 100.0, 20.0) / 100
        base = pd.DataFrame({"Libellé": ["Constructions"], "Nature": ["Actif"],
                             "Valeur comptable IFRS": [500_000.0], "Base fiscale": [400_000.0]})
        saisie = st.data_editor(base, num_rows="dynamic",
                                column_config={"Nature": st.column_config.SelectboxColumn(options=["Actif", "Passif"])})
        if not saisie.dropna(subset=["Libellé"]).empty:
            dt = calc_impots_differes(saisie, taux_is)
            st.dataframe(dt, hide_index=True)
            st.markdown(kpi("receipt", "Impôt différé net (positif = passif)", m(dt["Impôt différé"].sum())),
                        unsafe_allow_html=True)
    with s36:
        a1, a2 = st.columns(2)
        vc = a1.number_input("Valeur comptable de l'UGT", value=1_000_000.0, step=10_000.0)
        jv = a1.number_input("Juste valeur - coûts de cession", value=800_000.0, step=10_000.0)
        taux = a2.number_input("Taux d'actualisation (%)", 0.0, 100.0, 10.0) / 100
        g = a2.number_input("Croissance à l'infini (%)", 0.0, 20.0, 2.0) / 100
        flux_df = st.data_editor(pd.DataFrame({"Année": [1, 2, 3, 4, 5], "Flux de trésorerie": [150_000.0] * 5}))
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
        affiche = constats_df.copy()
        affiche["Niveau"] = affiche["Niveau"].map(NIVEAUX)
        st.markdown("#### Constats et retraitements suggérés")
        st.dataframe(affiche, hide_index=True)
        st.markdown("#### État de situation financière (IAS 1)")
        st.dataframe(etat.style.format({"Montant": "{:,.0f}"}), hide_index=True)
        st.write(f"Total actif : **{m(actif)}** | Total passif : **{m(passif)}**")
        sortie = io.BytesIO()
        with pd.ExcelWriter(sortie, engine="openpyxl") as w:
            constats_df.to_excel(w, sheet_name="Constats", index=False)
            etat.to_excel(w, sheet_name="Situation financiere", index=False)
            if not dt.empty:
                dt.to_excel(w, sheet_name="IAS 12", index=False)
            ugt.to_excel(w, sheet_name="IAS 36", index=False)
        st.download_button("Télécharger les résultats (Excel)", sortie.getvalue(), "resultats_ifrs.xlsx")

# ---------------- TABLEAU DE BORD ----------------
with t_dash:
    entete("Synthèse", "Tableau de bord")
    if df is None:
        st.info("Importez d'abord une balance dans l'onglet « Transformation ».")
    else:
        ca = -solde(df, "711", "712")
        resultat = -solde(df, "6", "7")
        k = st.columns(4)
        k[0].markdown(kpi("coins", "Chiffre d'affaires", m(ca)), unsafe_allow_html=True)
        k[1].markdown(kpi("chart", "Résultat net", m(resultat)), unsafe_allow_html=True)
        k[2].markdown(kpi("bank", "Total actif", m(actif)), unsafe_allow_html=True)
        k[3].markdown(kpi("flag", "Constats", str(len(constats_df))), unsafe_allow_html=True)
        st.write("")
        g1, g2 = st.columns(2)
        g1.markdown("**Répartition des constats par niveau**")
        g1.bar_chart(constats_df["Niveau"].value_counts(), color="#1b6fd1")
        g2.markdown("**Structure de l'état de situation**")
        g2.bar_chart(etat.set_index("Poste"), color="#1b998b")

# ---------------- À PROPOS ----------------
with t_apropos:
    entete("Projet", "À propos")
    st.markdown("Projet réalisé dans le module **Consolidation et normes IFRS**. L'objectif est de faciliter "
                "le travail des comptables, auditeurs et financiers en automatisant une partie de la "
                "conversion du référentiel marocain (CGNC) vers les normes IAS/IFRS.")
    st.write("")
    grille([etape("", "target", "Objectif", "Un outil d'aide à la décision, qui ne remplace pas l'expert-comptable."),
            etape("", "lock", "Hors ligne", "Aucune donnée n'est envoyée sur internet."),
            etape("", "puzzle", "Extensible", "Chaque norme est une fonction que l'on peut ajouter facilement.")], n=3)

# ---------------- CONTACT ----------------
with t_contact:
    entete("Échangeons", "Contact")
    st.markdown(f'<div class="panel"><div class="tile">{ico("mail")}</div><h3>Une question sur la plateforme ?</h3>'
                '<p style="color:#5b6b80;margin:0 0 1.2rem 0">Écrivez-nous : <b>contact@maroifrs.ma</b></p>'
                '<a class="btn" href="mailto:contact@maroifrs.ma?subject=MaroIFRS">Envoyer un message</a></div>',
                unsafe_allow_html=True)

st.markdown('<div class="pied">MaroIFRS – Outil d\'aide à la conversion CGNC → IAS/IFRS, à valider par un expert-comptable.</div>',
            unsafe_allow_html=True)
