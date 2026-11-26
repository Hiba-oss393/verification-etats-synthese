"""Paramètres fiscaux marocains (impôt sur les sociétés) : chaque règle porte sa référence, sa date d'application et son niveau de vérification.

Principe : aucune règle n'est appliquée si elle n'est pas confirmée pour l'exercice demandé. Pour un exercice antérieur à 2026, les taux d'IS
transitoires (loi de finances 2023) ne sont PAS intégrés : le taux doit être saisi par l'utilisateur.

Niveau de vérification des paramètres : recoupement, le 18/11/2026, de plusieurs publications secondaires (cabinets de conseil, site fiscamaroc
reproduisant l'article 12). Le texte officiel du Code général des impôts (Direction générale des impôts) n'a pas pu être consulté : chaque
paramètre doit être validé sur ce texte avant usage dans un dossier.
"""
from __future__ import annotations

import pandas as pd

DATE_VERIF = "18/11/2026"
NIVEAU_SECONDAIRE = (f"Recoupé le {DATE_VERIF} avec plusieurs sources secondaires ; texte officiel du CGI non consulté : à valider auprès de la DGI")

PARAMETRES = [
    {"code": "IS_NORMAL", "libelle": "Taux normal de l'IS (bénéfice net fiscal inférieur à 100 M MAD)", "valeur": 0.20, "affichage": "20 %",
     "reference": "CGI art. 19-I-A ; loi de finances 2023 (n° 50-22), convergence sur quatre exercices", "application": "Exercices ouverts à compter du 01/01/2026",
     "verification": NIVEAU_SECONDAIRE},
    {"code": "IS_GRANDE", "libelle": "Taux de l'IS pour un bénéfice net fiscal d'au moins 100 M MAD", "valeur": 0.35, "affichage": "35 %",
     "reference": "CGI art. 19-I-B (exclusions : zones d'accélération industrielle, Casablanca Finance City, entre autres, à vérifier sur le texte)",
     "application": "Exercices ouverts à compter du 01/01/2026", "verification": NIVEAU_SECONDAIRE},
    {"code": "IS_FINANCE", "libelle": "Taux de l'IS des établissements de crédit, assurances, Bank Al-Maghrib, CDG", "valeur": 0.40, "affichage": "40 %",
     "reference": "CGI art. 19-I-C", "application": "Exercices ouverts à compter du 01/01/2026", "verification": NIVEAU_SECONDAIRE},
    {"code": "SEUIL_35", "libelle": "Seuil du bénéfice net fiscal déclenchant le taux de 35 %", "valeur": 100_000_000.0, "affichage": "100 000 000 MAD",
     "reference": "CGI art. 19-I-B", "application": "Exercices ouverts à compter du 01/01/2026", "verification": NIVEAU_SECONDAIRE},
    {"code": "CM_TAUX", "libelle": "Taux de la cotisation minimale", "valeur": 0.0025, "affichage": "0,25 %",
     "reference": "CGI art. 144 (taux unifié depuis la loi de finances 2023)", "application": "Exercices à compter de 2023", "verification": NIVEAU_SECONDAIRE},
    {"code": "CM_MINIMUM", "libelle": "Montant minimum de la cotisation minimale", "valeur": 3000.0, "affichage": "3 000 MAD",
     "reference": "CGI art. 144", "application": "Exercices à compter de 2023", "verification": NIVEAU_SECONDAIRE},
    {"code": "CM_TAUX_REDUIT", "libelle": "Taux réduit de la cotisation minimale (certains produits de base)", "valeur": 0.0015, "affichage": "0,15 %",
     "reference": "CGI art. 144 (liste des produits à consulter dans le texte)", "application": "Exercices à compter de 2023",
     "verification": "Une seule source secondaire consultée : à confirmer sur le texte officiel avant toute utilisation"},
    {"code": "CM_EXONERATION_MOIS", "libelle": "Durée d'exonération de la cotisation minimale pour une société nouvelle", "valeur": 36.0, "affichage": "36 mois",
     "reference": "CGI art. 144 (cessation au plus tard 60 mois après la constitution)", "application": "Exercices à compter de 2023", "verification": NIVEAU_SECONDAIRE},
    {"code": "DEFICIT_EXERCICES", "libelle": "Durée de report des déficits fiscaux (hors amortissements)", "valeur": 4.0, "affichage": "4 exercices",
     "reference": "CGI art. 12 : déduction des bénéfices des exercices suivants jusqu'au quatrième exercice",
     "application": "Règle en vigueur (texte de l'article 12 reproduit par fiscamaroc.com)", "verification": NIVEAU_SECONDAIRE},
    {"code": "DEFICIT_AMORT", "libelle": "Report des déficits correspondant à des amortissements régulièrement comptabilisés", "valeur": None, "affichage": "Sans limitation de durée",
     "reference": "CGI art. 12 (ne vise pas les amortissements des non-valeurs)", "application": "Règle en vigueur", "verification": NIVEAU_SECONDAIRE},
]
_PAR = {p["code"]: p for p in PARAMETRES}
EXERCICE_PREMIER_TAUX_DEFINITIFS = 2026
EXERCICE_PREMIER_CM = 2023


def valeur(code: str):
    return _PAR[code]["valeur"]


def tableau_parametres() -> pd.DataFrame:
    return pd.DataFrame([{"Paramètre": p["libelle"], "Valeur": p["affichage"], "Référence": p["reference"], "Date d'application": p["application"],
                          "Niveau de vérification": p["verification"]} for p in PARAMETRES])


def taux_is(exercice, categorie: str, benefice_net: float, exclu_35: bool = False, taux_libre: float | None = None):
    """Retourne (taux ou None, motif). None = IS non calculable : aucun taux n'est supposé."""
    if categorie == "libre":
        if taux_libre is None or taux_libre <= 0:
            return None, "Taux d'IS à saisir (catégorie « autre taux ») : aucun taux n'est supposé."
        return float(taux_libre), f"Taux saisi par l'utilisateur : {taux_libre:.2%}"
    if not exercice:
        return None, "Exercice non indiqué : le taux d'IS dépend de l'exercice, indiquez-le."
    if int(exercice) < EXERCICE_PREMIER_TAUX_DEFINITIFS:
        return None, (f"Exercice {exercice} : les taux transitoires de la loi de finances 2023 ne sont pas intégrés à l'application ; "
                      "choisissez « Autre taux » et saisissez le taux applicable.")
    if categorie == "credit":
        return valeur("IS_FINANCE"), "Taux de 40 % (CGI art. 19-I-C)"
    if benefice_net >= valeur("SEUIL_35") and not exclu_35:
        return valeur("IS_GRANDE"), "Taux de 35 % (CGI art. 19-I-B, bénéfice net fiscal d'au moins 100 M MAD)"
    return valeur("IS_NORMAL"), "Taux normal de 20 % (CGI art. 19-I-A)"


def cotisation_minimale_applicable(exercice) -> tuple[bool, str]:
    if not exercice:
        return False, "Exercice non indiqué : cotisation minimale non calculée."
    if int(exercice) < EXERCICE_PREMIER_CM:
        return False, f"Exercice {exercice} : les paramètres de cotisation minimale antérieurs à 2023 ne sont pas intégrés."
    return True, "Cotisation minimale : 0,25 % (minimum 3 000 MAD), CGI art. 144"


COLONNES_DEFICITS = ["Exercice du déficit", "Déficit total (MAD)", "dont amortissements régulièrement comptabilisés (MAD)"]


def deficits_vide() -> pd.DataFrame:
    return pd.DataFrame({COLONNES_DEFICITS[0]: pd.Series(dtype="float"), COLONNES_DEFICITS[1]: pd.Series(dtype="float"),
                         COLONNES_DEFICITS[2]: pd.Series(dtype="float")})


def deficits_disponibles(tab: pd.DataFrame, exercice) -> tuple[float, pd.DataFrame, list[str]]:
    """Déficits encore imputables sur l'exercice demandé : fraction ordinaire reportable 4 exercices, fraction « amortissements » sans limite.
    Retourne (montant total imputable, détail, avertissements). Les lignes incomplètes ou postérieures à l'exercice sont écartées avec avertissement."""
    avert, lignes = [], []
    if tab is None or len(tab) == 0:
        return 0.0, pd.DataFrame(columns=["Exercice", "Ordinaire imputable", "Amortissements imputables", "Statut"]), avert
    if not exercice:
        return 0.0, pd.DataFrame(columns=["Exercice", "Ordinaire imputable", "Amortissements imputables", "Statut"]), \
            ["Exercice non indiqué : les déficits reportables ne peuvent pas être vérifiés."]
    duree = int(valeur("DEFICIT_EXERCICES"))
    for _, r in tab.iterrows():
        ex = pd.to_numeric(r[COLONNES_DEFICITS[0]], errors="coerce")
        tot = pd.to_numeric(r[COLONNES_DEFICITS[1]], errors="coerce")
        am = pd.to_numeric(r[COLONNES_DEFICITS[2]], errors="coerce")
        if pd.isna(ex) or pd.isna(tot):
            avert.append("Ligne de déficit incomplète (exercice ou montant manquant) : ignorée.")
            continue
        am = 0.0 if pd.isna(am) else float(am)
        if am > tot + 0.005:
            avert.append(f"Déficit {int(ex)} : la fraction d'amortissements ({am:,.0f}) dépasse le déficit total ({tot:,.0f}) : plafonnée.".replace(",", " "))
            am = float(tot)
        age = int(exercice) - int(ex)
        if age < 1:
            avert.append(f"Déficit {int(ex)} : exercice non antérieur à {int(exercice)} : ignoré.")
            continue
        ordinaire = float(tot) - am
        if age > duree:
            statut = f"Fraction ordinaire expirée (plus de {duree} exercices) ; amortissements reportables"
            ordinaire = 0.0
        else:
            statut = f"Reportable (exercice {age} sur {duree})"
        lignes.append({"Exercice": int(ex), "Ordinaire imputable": ordinaire, "Amortissements imputables": am, "Statut": statut})
    det = pd.DataFrame(lignes, columns=["Exercice", "Ordinaire imputable", "Amortissements imputables", "Statut"]).sort_values("Exercice").reset_index(drop=True) \
        if lignes else pd.DataFrame(columns=["Exercice", "Ordinaire imputable", "Amortissements imputables", "Statut"])
    total = float(det["Ordinaire imputable"].sum() + det["Amortissements imputables"].sum()) if len(det) else 0.0
    return total, det, avert
