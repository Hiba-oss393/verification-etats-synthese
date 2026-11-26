"""Couverture des normes IAS/IFRS, traitements par norme, comparatif CGNC / IFRS, nature des résultats et qualité des données.

Module autonome (sans Streamlit). Principe central : ne jamais laisser croire qu'une norme a été vérifiée si aucun contrôle réel n'a été exécuté.
Chaque norme du catalogue reçoit l'un des statuts suivants :
  - « Contrôlée automatiquement »     : une règle de l'application examine les comptes concernés (détail du niveau d'automatisation ci-dessous) ;
  - « Informations complémentaires »  : la règle repère le sujet mais le retraitement chiffré exige des données absentes d'une balance ;
  - « Consultable, non automatisée »  : la norme est au catalogue (fiche, points clés) mais aucune règle ne l'applique.
"""
from __future__ import annotations

import re

import pandas as pd

# Comptes CGNC réellement examinés par chaque règle (extraits automatiquement du code de app.py le 18/11/2026).
COMPTES_EXAMINES = {
    "IAS 1": ["21", "65", "75", "281"], "IFRS 15 / IAS 18": ["711", "712", "716", "4491", "7119", "7129"], "IAS 16": ["23", "231", "283", "2321", "6132"],
    "IAS 12": ["6", "7", "67"], "IAS 36": ["223"], "IAS 2": ["31", "391"], "IAS 7": ["51", "55"], "IAS 8": [], "IAS 10": [],
    "IAS 19": ["617", "1552", "4437"], "IAS 20": ["131", "716", "757"], "IAS 21": ["171", "172", "271", "272", "633", "733"], "IAS 23": ["6311"],
    "IAS 24": ["346", "446"], "IFRS 9": ["141", "1481", "3421", "3942"], "IAS 33": ["111"], "IAS 37": ["15", "45"], "IAS 38": ["22", "223"],
    "IAS 40": ["2311", "7182"], "IFRS 3": ["223"], "IFRS 10": ["251", "732"], "IFRS 5": ["6513", "7513"], "IFRS 7": [], "IFRS 8": [], "IFRS 16": ["6131", "6132"],
}
# Comptes dont la valeur comptable est présentée (quand elle diffère des comptes examinés)
COMPTES_VALEUR = {"IAS 12": ["67"], "IFRS 15 / IAS 18": ["711", "712"], "IAS 1": ["21", "281", "65", "75"]}

RETRAITEMENT = "Retraitement chiffré"
DETECTION = "Détection sur les comptes"
RAPPEL = "Rappel d'information (aucun calcul)"

META = {
    "IAS 1": (RETRAITEMENT, "Non-valeurs éliminées, éléments non courants reclassés ; équilibre de la balance", [], "IAS 1.60-76 ; IAS 1.87 ; IAS 38.69"),
    "IFRS 15 / IAS 18": (DETECTION, "Rabais accordés, produits constatés d'avance et subventions repérés dans le chiffre d'affaires",
                         ["Contrats clients, obligations de prestation, prix de transaction et calendrier de transfert du contrôle"], "IFRS 15.31 ; IFRS 15.50"),
    "IAS 16": (DETECTION, "Immobilisations sans amortissement, taux d'amortissement élevé, constructions en bloc, crédit-bail",
               ["Date de mise en service, durées d'utilité, valeurs résiduelles, composants des constructions"], "IAS 16.43 ; IAS 16.50-51"),
    "IAS 12": (RETRAITEMENT, "Impôts différés sur les non-valeurs et provisions réglementées ; taux d'impôt effectif",
               ["Base fiscale et valeur comptable de chaque actif et passif, déficits reportables, taux d'impôt"], "IAS 12.15 ; IAS 12.24 ; IAS 12.34"),
    "IAS 36": (DETECTION, "Fonds commercial présent : test de dépréciation annuel exigé",
               ["Valeur recouvrable : flux de trésorerie prévisionnels et taux d'actualisation (onglet Calculateurs IAS 36)"], "IAS 36.9-10 ; IAS 36.59"),
    "IAS 2": (DETECTION, "Dépréciation de stocks : comparaison au plus bas du coût et de la valeur nette de réalisation",
              ["Coût de revient et valeur nette de réalisation par catégorie, ancienneté et rotation des stocks"], "IAS 2.9 ; IAS 2.28-33"),
    "IAS 7": (DETECTION, "Comptes de trésorerie repérés ; le tableau des flux est exigé",
              ["Bilans d'ouverture et de clôture, tableau de financement ou flux de l'exercice"], "IAS 7.10"),
    "IAS 8": (RAPPEL, "Changements de méthodes, d'estimations et corrections d'erreurs : rappel", ["Informations d'annexe (ETIC) et historique des méthodes"], "IAS 8.41-49"),
    "IAS 10": (RAPPEL, "Événements postérieurs à la clôture : rappel", ["Événements survenus entre la clôture et l'arrêté des comptes"], "IAS 10.8-9"),
    "IAS 19": (DETECTION, "Charges de personnel et provisions de retraite repérées",
               ["Évaluation actuarielle des engagements (hypothèses de mortalité, de salaires, d'actualisation), effectif"], "IAS 19.57"),
    "IAS 20": (DETECTION, "Subventions d'investissement et d'exploitation repérées", ["Conditions d'octroi des subventions, date de réception, actif financé"], "IAS 20.7 ; IAS 20.24"),
    "IAS 21": (DETECTION, "Écarts de conversion (inexistants en IFRS) repérés", ["Monnaie de chaque transaction, cours de clôture, éléments monétaires concernés"], "IAS 21.21-23"),
    "IAS 23": (DETECTION, "Intérêts d'emprunt repérés : capitalisation possible", ["Actifs qualifiés, durée de construction, taux des emprunts affectés"], "IAS 23.8"),
    "IAS 24": (DETECTION, "Comptes d'associés et de parties liées repérés", ["Liste des parties liées, nature et montant des transactions"], "IAS 24.18"),
    "IFRS 9": (DETECTION, "Créances clients, prêts et dépréciations repérés : modèle de pertes attendues",
               ["Ventilation des créances par ancienneté, historique de pertes, taux de pertes attendues"], "IFRS 9.5.5"),
    "IAS 33": (RAPPEL, "Résultat par action (sociétés cotées) : rappel", ["Nombre moyen pondéré d'actions"], "IAS 33.10"),
    "IAS 37": (DETECTION, "Provisions pour risques et charges repérées : critères de constatation",
               ["Existence d'une obligation, probabilité de sortie de ressources, estimation fiable, échéancier"], "IAS 37.14 ; IAS 37.36"),
    "IAS 38": (DETECTION, "Immobilisations incorporelles et fonds commercial repérés", ["Critères de capitalisation des frais de développement, durée de vie utile"], "IAS 38.57 ; IAS 38.69"),
    "IAS 40": (DETECTION, "Immeubles de placement repérés", ["Modèle retenu (coût ou juste valeur) et juste valeur des immeubles"], "IAS 40.30-33"),
    "IFRS 3": (DETECTION, "Fonds commercial repéré : regroupement d'entreprises à documenter", ["Prix d'acquisition, juste valeur des actifs et passifs identifiables"], "IFRS 3.32"),
    "IFRS 10": (DETECTION, "Titres de participation et dividendes repérés : contrôle à qualifier", ["Pourcentages de contrôle et d'intérêt, droits de vote, accords entre actionnaires"], "IFRS 10.7 ; IFRS 10.B86"),
    "IFRS 5": (DETECTION, "Cessions d'immobilisations repérées", ["Décision de cession, juste valeur nette des coûts de la vente"], "IFRS 5.6-8"),
    "IFRS 7": (RAPPEL, "Informations sur les instruments financiers : rappel", ["Informations d'annexe sur les risques de crédit, de liquidité et de marché"], "IFRS 7.31"),
    "IFRS 8": (RAPPEL, "Information sectorielle (sociétés cotées) : rappel", ["Résultat par secteur opérationnel"], "IFRS 8.20"),
    "IFRS 16": (DETECTION, "Loyers et crédit-bail repérés : droit d'utilisation et dette locative à constater",
                ["Contrats de location : durée, loyers, taux d'actualisation, options de renouvellement"], "IFRS 16.22 ; IFRS 16.26"),
}


def _motif(token: str) -> str:
    return r"(?<![A-Za-z0-9])" + re.escape(token.strip()) + r"(?!\d)"


def _constats_norme(constats_df: pd.DataFrame, cle: str) -> pd.DataFrame:
    if constats_df is None or constats_df.empty:
        return pd.DataFrame(columns=["Norme", "Niveau", "Constat", "Retraitement suggéré"])
    rx = "|".join(_motif(t) for t in cle.split("/"))
    return constats_df[constats_df["Norme"].astype(str).str.contains(rx, regex=True)]


def _s(df, *prefixes) -> float:
    return float(df.loc[df["compte"].astype(str).str.startswith(tuple(prefixes)), "solde"].sum()) if prefixes else 0.0


def _fmt(x: float) -> str:
    return f"{x:,.0f}".replace(",", " ")


def jeu_complet(df: pd.DataFrame) -> bool:
    c = df["compte"].astype(str)
    return bool(c.str.startswith(("1", "2", "3", "4", "5")).any() and c.str.startswith(("6", "7")).any())


def _plus_courts(prefixes: list[str]) -> list[str]:
    return [p for p in prefixes if not any(q != p and p.startswith(q) for q in prefixes)]


def _valeur_initiale(df, cle) -> str:
    prefixes = COMPTES_VALEUR.get(cle, _plus_courts(COMPTES_EXAMINES.get(cle, [])))
    morceaux = []
    for p in prefixes:
        v = _s(df, p)
        if abs(v) > 0.005:
            morceaux.append(f"{p} : {_fmt(v)}")
    return " ; ".join(morceaux) if morceaux else "—"


def _comptes_presents(df, cle) -> list[str]:
    pref = ["67"] if cle == "IAS 12" else COMPTES_EXAMINES.get(cle, [])
    return [p for p in _plus_courts(list(pref)) if abs(_s(df, p)) > 0.005]


def _ajustement(df, cle, taux_id) -> str:
    """Ajustement chiffré uniquement lorsque l'application le calcule réellement ; sinon, indication explicite de ce qui manque."""
    nonval = max(_s(df, "21", "281"), 0.0)
    preg = max(-_s(df, "135"), 0.0)
    if cle == "IAS 1" and nonval > 0:
        return (f"Non-valeurs éliminées : −{_fmt(nonval)} MAD à l'actif ; réserves −{_fmt(nonval * (1 - taux_id))} MAD ; "
                f"impôt différé actif +{_fmt(nonval * taux_id)} MAD (taux {taux_id:.0%})")
    if cle == "IAS 12":
        id_net = nonval * taux_id - preg * taux_id
        if nonval > 0 or preg > 0:
            return f"Impôt différé net sur non-valeurs et provisions réglementées : {_fmt(id_net)} MAD (taux {taux_id:.0%}) ; autres différences temporaires à saisir"
    return ""


def tableau_traitements(df: pd.DataFrame, constats_df: pd.DataFrame, normes_choisies, taux_id: float = 0.20) -> pd.DataFrame:
    """Une ligne par norme contrôlable automatiquement : statut réel, comptes concernés, valeur comptable initiale, ajustement proposé,
    règle, justification, référence et informations nécessaires."""
    choisies = set(normes_choisies or [])
    complet = jeu_complet(df)
    lignes = []
    for cle in COMPTES_EXAMINES:
        niveau, regle, infos, ref = META[cle]
        sel = cle in choisies
        cons = _constats_norme(constats_df, cle)
        hors_info = cons[cons["Niveau"] != "Info"]
        presents = _comptes_presents(df, cle)
        if not sel:
            statut = "Non sélectionnée : aucun contrôle exécuté"
        elif niveau == RAPPEL:
            statut = "Rappel d'information : aucun calcul réalisé" if complet else "Non applicable : états incomplets"
        elif not presents and hors_info.empty:
            statut = "Sans objet : aucun compte concerné dans le document"
        elif hors_info.empty:
            statut = "Exécuté : aucun point à traiter"
        else:
            statut = f"Exécuté : {len(hors_info)} point(s) à traiter" + (" — informations complémentaires requises" if infos and niveau != RAPPEL else "")
        just = " ; ".join(dict.fromkeys(c for c in cons["Retraitement suggéré"].astype(str) if c.strip())) if len(cons) else ""
        ajust = _ajustement(df, cle, taux_id) if sel else ""
        if sel and not ajust and not hors_info.empty and infos and niveau != RAPPEL:
            ajust = "Non calculé : informations complémentaires requises"
        lignes.append({"Norme": cle, "Niveau d'automatisation": niveau, "Statut du contrôle": statut,
                       "Poste ou compte concerné": ", ".join(presents) if presents else ("—" if COMPTES_EXAMINES[cle] else "Aucun compte (rappel)"),
                       "Valeur comptable initiale (MAD)": _valeur_initiale(df, cle) if sel else "—",
                       "Ajustement proposé": ajust or "—", "Règle appliquée": regle,
                       "Justification du retraitement": just or "—", "Référence de la norme": ref,
                       "Informations nécessaires": " ; ".join(infos) if infos else "—"})
    return pd.DataFrame(lignes)


def normes_non_automatisees(refs_catalogue, normes_auto) -> list[str]:
    """Normes présentes au catalogue (fiches) mais sans aucune règle automatique."""
    auto = {t.strip() for k in normes_auto for t in k.split("/")}
    return sorted((r for r in refs_catalogue if r not in auto), key=lambda x: (x[:3] != "IAS", int(re.search(r"\d+", x).group()) if re.search(r"\d+", x) else 0, x))


# ----------------------------------------------------------------------------------------------------- nature des résultats
def nature_resultats(df: pd.DataFrame, factures: bool = False, documents_detectes: dict | None = None) -> dict:
    """Résultats « complets » seulement si la balance contient bilan ET résultat et ne provient pas de factures isolées."""
    raisons = []
    c = df["compte"].astype(str)
    if factures:
        raisons.append("Les données proviennent de factures seules : la balance est reconstituée artificiellement (ni bilan d'ouverture, ni paiements, ni paie, ni amortissements). "
                       "Elle n'est ni une balance générale complète ni la base d'états financiers fiables.")
    if not c.str.startswith(("1", "2", "3", "4", "5")).any():
        raisons.append("Aucun compte de bilan (classes 1 à 5) : bilan, état de situation et contrôles de structure impossibles.")
    if not c.str.startswith(("6", "7")).any():
        raisons.append("Aucun compte de résultat (classes 6 et 7) : compte de résultat, résultat fiscal et cohérence bilan / CPC impossibles.")
    return {"complets": not raisons, "raisons": raisons}


# ----------------------------------------------------------------------------------------------------- qualité des données
def qualite_donnees(df: pd.DataFrame, cgnc_df: pd.DataFrame | None, nature: dict, comptes_cgnc=None) -> dict:
    """Indicateur de qualité des données IMPORTÉES (lecture, équilibre, plan comptable, libellés, complétude, anomalies). Il ne mesure pas
    l'exactitude comptable des montants. Chaque composante est expliquée."""
    n = max(len(df), 1)
    ecart = float(df["debit"].sum() - df["credit"].sum())
    comp = []
    comp.append(("Équilibre débit / crédit", 25, 100.0 if abs(ecart) <= 1 else 0.0,
                 "Total débit égal au total crédit" if abs(ecart) <= 1 else f"Écart de {_fmt(ecart)} MAD"))
    if comptes_cgnc:
        rub = {str(c)[:2] for c in comptes_cgnc}
        reco = float(sum(str(c)[:2] in rub for c in df["compte"])) / n * 100
        comp.append(("Comptes reconnus dans le plan CGNC", 25, reco, f"{reco:.0f} % des comptes ont une rubrique existante au CGNC"))
    lib = float((df["libelle"].fillna("").astype(str).str.strip() != "").sum()) / n * 100 if "libelle" in df else 0.0
    comp.append(("Libellés renseignés", 10, lib, f"{lib:.0f} % des comptes ont un libellé"))
    c = df["compte"].astype(str)
    a_b, a_r = bool(c.str.startswith(("1", "2", "3", "4", "5")).any()), bool(c.str.startswith(("6", "7")).any())
    compl = 100.0 if (a_b and a_r and nature.get("complets")) else (50.0 if (a_b or a_r) else 0.0)
    comp.append(("Complétude du document", 20, compl, "Bilan et résultat présents" if compl == 100 else "Document partiel : " + (nature["raisons"][0][:90] + "…" if nature.get("raisons") else "")))
    if cgnc_df is not None:
        nb_e, nb_v = int((cgnc_df["Niveau"] == "Erreur").sum()), int((cgnc_df["Niveau"] == "À vérifier").sum())
        cg = max(0.0, 100.0 - 25 * nb_e - 5 * nb_v)
        comp.append(("Anomalies CGNC", 20, cg, f"{nb_e} erreur(s), {nb_v} point(s) à vérifier"))
    poids = sum(w for _, w, _, _ in comp)
    score = sum(w * v for _, w, v, _ in comp) / poids if poids else 0.0
    niveau = "Bonne" if score >= 80 else ("Moyenne" if score >= 55 else "Faible")
    tab = pd.DataFrame([{"Composante": a, "Poids (%)": w, "Note (/100)": round(v), "Explication": e} for a, w, v, e in comp])
    return {"score": round(score), "niveau": niveau, "composantes": tab,
            "avertissement": "Cet indicateur mesure la qualité des données importées ; il ne garantit pas l'exactitude des montants."}


# ----------------------------------------------------------------------------------------------------- informations manquantes / non applicables
def informations_et_controles(df: pd.DataFrame, nature: dict, traitements: pd.DataFrame, detail_tva: bool) -> tuple[list[str], list[str]]:
    """(informations manquantes, contrôles non exécutés ou non applicables)."""
    manquantes, non_exec = [], []
    for r in nature.get("raisons", []):
        non_exec.append(r)
    ventes = -_s(df, "711", "712")
    if (abs(_s(df, "4455")) > 1 or abs(_s(df, "3455")) > 1 or ventes > 0) and not detail_tva:
        non_exec.append("Contrôle des taux de TVA : impossible sans journal, grand livre ou factures (le solde de clôture ne permet pas de calculer un taux).")
    if len(traitements):
        for _, r in traitements.iterrows():
            st_ = r["Statut du contrôle"]
            if st_.startswith("Exécuté") and "informations complémentaires" in st_:
                manquantes.append(f"{r['Norme']} : {r['Informations nécessaires']}")
            elif st_.startswith("Sans objet"):
                non_exec.append(f"{r['Norme']} : sans objet (aucun compte concerné)")
            elif st_.startswith("Non applicable"):
                non_exec.append(f"{r['Norme']} : non applicable (états incomplets)")
            elif st_.startswith("Non sélectionnée"):
                non_exec.append(f"{r['Norme']} : non sélectionnée, aucun contrôle exécuté")
    return manquantes, non_exec


# ----------------------------------------------------------------------------------------------------- comparatif CGNC / IFRS
def comparatif_cgnc_ifrs(df: pd.DataFrame, taux_id: float = 0.20) -> pd.DataFrame:
    """États avant (CGNC) et après (IFRS) retraitement, en MAD, avec la norme justifiant chaque écart. Les retraitements chiffrés sont
    ceux que l'application calcule réellement : non-valeurs (IAS 1 / IAS 38) et provisions réglementées (compte 135) avec leurs impôts différés (IAS 12)."""
    s = lambda *p: _s(df, *p)
    nonval = max(s("21", "281"), 0.0)
    preg = max(-s("135"), 0.0)
    id_nonval, id_preg = nonval * taux_id, -preg * taux_id
    id_net = id_nonval + id_preg
    dta, idp = max(id_net, 0.0), max(-id_net, 0.0)
    cp_nonval, cp_preg = -nonval * (1 - taux_id), preg * (1 - taux_id)
    a_cpc = df["compte"].astype(str).str.startswith(("6", "7")).any()
    r_net = -s("6", "7") if a_cpc else -s("119")
    tres_passif = max(-s("55"), 0.0)
    actif_cgnc = s("2", "3", "5") + tres_passif
    actif_ifrs = actif_cgnc - nonval + dta
    base_cp = -s("113", "114", "115", "116", "118") - s("111") - s("112") - s("131") + r_net
    prov, emprunts = -s("15"), -s("14")
    fourn = -s("441")
    autres_dettes = -s("4") - fourn - s("16", "17")
    dettes_hors_idp = emprunts + fourn + autres_dettes + tres_passif
    passif_cgnc = base_cp + preg + prov + dettes_hors_idp
    passif_ifrs = base_cp + cp_nonval + cp_preg + prov + dettes_hors_idp + idp
    L = [
        ("Actif", "Immobilisations en non-valeur", nonval, 0.0, "IAS 38.69 ; IAS 1 : pas d'actif en IFRS"),
        ("Actif", "Impôt différé actif", 0.0, dta, "IAS 12.24"),
        ("Actif", "TOTAL ACTIF", actif_cgnc, actif_ifrs, ""),
        ("Passif", "Provisions réglementées (capitaux propres assimilés)", preg, 0.0, "Pas de provisions purement fiscales en IFRS (reclassées en réserves après impôt différé)"),
        ("Passif", "Capitaux propres hors provisions réglementées", base_cp, base_cp + cp_nonval + cp_preg, "IAS 1 ; IAS 12 (effet net d'impôt différé)"),
        ("Passif", "Impôt différé passif", 0.0, idp, "IAS 12.15"),
        ("Passif", "TOTAL PASSIF", passif_cgnc, passif_ifrs, ""),
        ("Résultat", "Résultat net de l'exercice" + ("" if a_cpc else " (compte 119, CPC absent)"), r_net, r_net,
         "Inchangé : l'effet sur l'exercice des retraitements ne peut être isolé sans la balance de l'exercice précédent"),
    ]
    tab = pd.DataFrame(L, columns=["Partie", "Poste", "CGNC (MAD)", "IFRS (MAD)", "Norme / justification"])
    tab["Écart (MAD)"] = tab["IFRS (MAD)"] - tab["CGNC (MAD)"]
    return tab[["Partie", "Poste", "CGNC (MAD)", "IFRS (MAD)", "Écart (MAD)", "Norme / justification"]]
