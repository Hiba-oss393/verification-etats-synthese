"""Comparaison des résultats calculés avec les comptes consolidés publiés par une société cotée à la Bourse de Casablanca.

Principes :
  - les chiffres officiels sont SAISIS ou IMPORTÉS par l'utilisateur depuis la publication de l'émetteur (rapport financier,
    site de l'émetteur, Bourse de Casablanca, AMMC) : l'application n'embarque aucun chiffre officiel et n'en invente aucun ;
  - la comparaison n'est jugée valable que si l'exercice, le référentiel, la devise et le périmètre sont les mêmes ;
    l'unité (MAD, milliers, millions) est convertie automatiquement ;
  - les résultats calculés ne sont JAMAIS modifiés pour se rapprocher des chiffres officiels : le module ne renvoie que des écarts.
"""
from __future__ import annotations

import re

import pandas as pd

INDICATEURS = [("total_actif", "Total actif"), ("total_passif", "Total passif"),
               ("ca", "Chiffre d'affaires"), ("resultat_net", "Résultat net part du groupe")]
UNITES = {"MAD": 1.0, "Milliers de MAD": 1e3, "Millions de MAD": 1e6}
REFERENTIELS = ["IFRS", "CGNC (normes marocaines)", "Autre"]
PERIMETRES = ["Consolidé (groupe)", "Social (société seule)"]
DEVISES = ["MAD", "Autre devise"]
COLS_TABLEAU = ["Indicateur", "Calculé (MAD)", "Officiel (MAD)", "Écart (MAD)", "Écart (%)", "Statut", "Origines possibles de l'écart"]
A_CHOISIR = "(à choisir)"   # valeur d'attente des listes déroulantes : jamais acceptée comme réponse
CHAMPS_OBLIGATOIRES = [("Exercice", "l'exercice"), ("Référentiel", "le référentiel"), ("Périmètre", "le périmètre"),
                       ("Unité", "l'unité"), ("Devise", "la devise")]
SEUIL_CONFORME = 0.5      # % : écart d'arrondi toléré
SEUIL_EXPLICABLE = 5.0    # % : au-delà, écart significatif

COLONNES_MODELE = ["Société", "Exercice", "Référentiel", "Périmètre", "Unité", "Devise", "Source (document, page)",
                   "Total actif", "Total passif", "Chiffre d'affaires", "Résultat net part du groupe"]


def _norm(t) -> str:
    t = str(t).lower().strip()
    for a, b in zip("éèêëàâîïôûùç", "eeeeaaiioouc"):
        t = t.replace(a, b)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def lire_officiel(df: pd.DataFrame) -> list[dict]:
    """Lit un tableau (Excel/CSV) au format du modèle. Une ligne = une société et un exercice."""
    cols = {_norm(c): c for c in df.columns}
    cle = {"societe": "Société", "exercice": "Exercice", "referentiel": "Référentiel", "perimetre": "Périmètre", "unite": "Unité", "devise": "Devise",
           "source document page": "Source", "source": "Source", "total actif": "total_actif", "total passif": "total_passif",
           "chiffre d affaires": "ca", "resultat net part du groupe": "resultat_net", "resultat net": "resultat_net"}
    manque = [k for k in ("societe", "exercice", "total actif", "total passif", "chiffre d affaires") if k not in cols]
    if manque or not ({"resultat net part du groupe", "resultat net"} & set(cols)):
        raise ValueError("Colonnes manquantes : utilisez le modèle fourni (Société, Exercice, Référentiel, Périmètre, Unité, "
                         "Total actif, Total passif, Chiffre d'affaires, Résultat net part du groupe).")
    lignes = []
    for _, r in df.iterrows():
        d = {}
        for n, orig in cols.items():
            if n in cle:
                d[cle[n]] = r[orig]
        if pd.isna(d.get("Société")) or str(d.get("Société")).strip() == "":
            continue
        for k, _ in INDICATEURS:
            v = pd.to_numeric(d.get(k), errors="coerce")
            d[k] = None if pd.isna(v) else float(v)
        ex = d.get("Exercice")
        d["Exercice"] = "" if pd.isna(ex) else str(ex).replace(".0", "").strip()
        # Aucune valeur par défaut : une donnée absente ou hors liste reste vide et devra être complétée par l'utilisateur
        for champ, valides in (("Unité", UNITES), ("Référentiel", REFERENTIELS), ("Périmètre", PERIMETRES), ("Devise", DEVISES)):
            v = d.get(champ)
            d[champ] = v if (isinstance(v, str) and v in valides) else None
        d["Source"] = "" if pd.isna(d.get("Source", "")) else str(d.get("Source", ""))
        lignes.append(d)
    if not lignes:
        raise ValueError("Aucune ligne exploitable dans le fichier.")
    return lignes


def modele_officiel() -> pd.DataFrame:
    """Modèle vide à remplir : une ligne d'exemple volontairement non chiffrée (aucun chiffre officiel n'est fourni)."""
    return pd.DataFrame([["(nom de la société cotée)", None, None, None, None, None,
                          "(rapport financier annuel, page ...)", None, None, None, None]], columns=COLONNES_MODELE)


def _explications(ind: str, calc: float, off: float, ecart_pct: float, ctx: dict) -> list[str]:
    ex = []
    if calc and off:
        ratio = off / calc
        for f, nom in ((1e3, "milliers"), (1e6, "millions")):
            if 0.97 < ratio / f < 1.03 or 0.97 < (1 / ratio) / f < 1.03:
                ex.append(f"Rapport de ≈ {f:,.0f} entre les deux montants : unité mal saisie ({nom} de dirhams) ?".replace(",", " "))
    if ind in ("total_actif", "total_passif"):
        ex += ["Périmètre : filiales ou entreprises associées absentes de l'import, ou intégrées selon une autre méthode",
               "Retraitements IFRS non couverts par l'application (IFRS 16, IAS 19, IFRS 9 / pertes de crédit attendues, instruments dérivés...)",
               "Écarts d'évaluation et goodwill calculés par le groupe à la date d'acquisition (non disponibles ici)",
               "Reclassements de présentation (courant / non courant, compensation d'actifs et de passifs d'impôt)"]
    if ind == "ca":
        ex += ["Ventes intragroupe non éliminées ou éliminées partiellement",
               "Présentation du chiffre d'affaires selon IFRS 15 (agent / principal, remises, produits des activités ordinaires)",
               "Changement de périmètre en cours d'exercice (entrées et sorties de filiales)"]
    if ind == "resultat_net":
        ex += ["Résultat de l'ensemble consolidé comparé au résultat part du groupe (intérêts ne donnant pas le contrôle)",
               "Impôts différés et retraitements IFRS propres au groupe",
               "Quote-part de résultat des sociétés mises en équivalence non intégrée",
               "Éléments non courants ou activités abandonnées (IFRS 5) présentés séparément"]
        nci = ctx.get("nci_resultat")
        if nci and abs(abs(calc - off) - abs(nci)) <= max(1.0, 0.02 * abs(nci)):
            ex.insert(0, "L'écart correspond au montant des intérêts ne donnant pas le contrôle : résultat de l'ensemble et part du groupe confondus ?")
    if ecart_pct <= SEUIL_CONFORME:
        return ["Écart compatible avec les arrondis de publication"]
    return ex


def comparer(calcule: dict, officiel: dict, meta_calcule: dict, meta_officiel: dict, contexte: dict | None = None) -> dict:
    """calcule / officiel : {total_actif, total_passif, ca, resultat_net} dans l'unité indiquée par meta['Unité'].
    meta_* : {Exercice, Référentiel, Périmètre, Unité, Devise}, tous obligatoires (aucune valeur par défaut).
    Retourne {comparable: bool, problemes: [...], tableau: DataFrame, statut: str, ajustement: 'aucun'}."""
    contexte = contexte or {}
    manquants, problemes = [], []
    for cle, lib in CHAMPS_OBLIGATOIRES:
        for cote, meta in (("des comptes calculés", meta_calcule), ("des comptes officiels", meta_officiel)):
            v = meta.get(cle)
            if v is None or str(v).strip() in ("", A_CHOISIR, "nan", "None"):
                manquants.append(f"Renseignez {lib} {cote} avant toute comparaison.")
    if manquants:  # rien n'est supposé : la comparaison attend les informations manquantes
        return {"comparable": False, "manquants": manquants, "problemes": manquants,
                "tableau": pd.DataFrame(columns=COLS_TABLEAU),
                "statut": "Informations à compléter", "ajustement": "aucun"}
    for cle, lib in (("Exercice", "exercice"), ("Référentiel", "référentiel"), ("Périmètre", "périmètre"), ("Devise", "devise")):
        a, b = str(meta_calcule[cle]).strip(), str(meta_officiel[cle]).strip()
        if a != b:
            problemes.append(f"{lib.capitalize()} différent (calculé : {a} ; officiel : {b}) : comparaison non valable"
                             + (" (aucune conversion de devises n'est appliquée)." if cle == "Devise" else "."))
    if meta_calcule["Unité"] not in UNITES or meta_officiel["Unité"] not in UNITES:
        return {"comparable": False, "manquants": [], "problemes": ["Unité inconnue : choisissez MAD, milliers ou millions de MAD."],
                "tableau": pd.DataFrame(columns=COLS_TABLEAU), "statut": "Informations à compléter", "ajustement": "aucun"}
    fc, fo = UNITES[meta_calcule["Unité"]], UNITES[meta_officiel["Unité"]]
    comparable = not problemes
    lignes = []
    for cle, nom in INDICATEURS:
        c, o = calcule.get(cle), officiel.get(cle)
        if c is None or o is None:
            lignes.append({"Indicateur": nom, "Calculé (MAD)": None if c is None else c * fc, "Officiel (MAD)": None if o is None else o * fo,
                           "Écart (MAD)": None, "Écart (%)": None, "Statut": "Donnée manquante",
                           "Origines possibles de l'écart": "Montant non renseigné d'un côté ou de l'autre"})
            continue
        c_m, o_m = c * fc, o * fo
        ecart = c_m - o_m
        pct = abs(ecart) / abs(o_m) * 100 if o_m else (0.0 if not c_m else 100.0)
        if not comparable:
            statut = "Non comparable"
        elif pct <= SEUIL_CONFORME:
            statut = "Conforme (arrondis)"
        elif pct <= SEUIL_EXPLICABLE:
            statut = "Écart à expliquer"
        else:
            statut = "Écart significatif"
        lignes.append({"Indicateur": nom, "Calculé (MAD)": c_m, "Officiel (MAD)": o_m, "Écart (MAD)": ecart, "Écart (%)": pct,
                       "Statut": statut, "Origines possibles de l'écart": " ; ".join(_explications(cle, c_m, o_m, pct, contexte))})
    tab = pd.DataFrame(lignes)
    if not comparable:
        statut = "Comparaison non valable"
    elif (tab["Statut"] == "Donnée manquante").all():
        statut = "Comparaison impossible"
    elif (tab["Statut"].isin(["Conforme (arrondis)"])).all():
        statut = "Concordance avec les comptes officiels"
    elif (tab["Statut"] == "Écart significatif").any():
        statut = "Écarts significatifs"
    else:
        statut = "Écarts à expliquer"
    return {"comparable": comparable, "manquants": [], "problemes": problemes, "tableau": tab, "statut": statut, "ajustement": "aucun"}
