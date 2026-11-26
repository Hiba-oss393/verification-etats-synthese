"""MaroIFRS - transformation compte par compte (CGNC -> IAS/IFRS) pour les rapports.

Module sans Streamlit : il met en forme, pour le rapport, le résultat de `statuts_comptes` (app.py) en montrant pour CHAQUE compte
le montant d'origine (CGNC), le traitement appliqué, le montant après traitement et l'écart. Il ne calcule aucun nouveau retraitement :
  - « éliminé »  : le compte n'existe pas en IFRS -> montant après = 0 (écart = - montant d'origine) ;
  - « reclassé » : reclassement de présentation, montant inchangé ;
  - « ajusté » / « à revoir » : traitement signalé mais NON chiffré (il exige des informations absentes de la balance), montant inchangé ;
  - « inchangé » : repris tel quel.
La colonne « Chiffré dans le comparatif » indique si l'écart du compte se retrouve dans le comparatif agrégé CGNC / IFRS
(non-valeurs 21 / 281 uniquement) : un écart non repris y est signalé comme tel au lieu d'être masqué.
"""
import re

import pandas as pd

COLONNES = ["Compte", "Libellé", "Solde d'origine CGNC (MAD)", "Statut", "Traitement appliqué", "Solde après traitement IFRS (MAD)",
            "Écart (MAD)", "Norme", "Justification", "Chiffré dans le comparatif"]

TRAITEMENT = {
    "éliminé": "Éliminé : le compte n'existe pas en IFRS (montant ramené à 0)",
    "reclassé": "Reclassé : changement de présentation, montant inchangé",
    "ajusté": "Ajustement à calculer : informations complémentaires requises, montant non modifié",
    "à revoir": "À revoir : jugement requis, montant non modifié",
    "inchangé": "Repris tel quel",
}
PREFIXES_COMPARATIF = ("21", "281")   # seuls comptes dont l'élimination est reprise dans couverture_normes.comparatif_cgnc_ifrs


def _norme(motif: str) -> str:
    refs = re.findall(r"(?:IAS|IFRS|IFRIC|SIC)\s?\d+(?:\s?/\s?(?:IAS|IFRS)\s?\d+)?", str(motif))
    return ", ".join(dict.fromkeys(r.strip() for r in refs)) if refs else "—"


def comptes_retraites(res_statuts: pd.DataFrame) -> pd.DataFrame:
    """res_statuts : DataFrame (compte, libelle, solde, statut, motif) produit par app.statuts_comptes."""
    if res_statuts is None or res_statuts.empty:
        return pd.DataFrame(columns=COLONNES)
    lignes = []
    for r in res_statuts.itertuples(index=False):
        avant = float(r.solde)
        apres = 0.0 if r.statut == "éliminé" else avant
        chiffre = ("Oui" if str(r.compte).startswith(PREFIXES_COMPARATIF) else "Non : écart non repris au comparatif") if r.statut == "éliminé" \
            else ("Non chiffré" if r.statut in ("ajusté", "à revoir") else "Sans écart")
        lignes.append((str(r.compte), r.libelle, avant, r.statut, TRAITEMENT.get(r.statut, r.statut), apres, apres - avant,
                       _norme(r.motif), r.motif, chiffre))
    return pd.DataFrame(lignes, columns=COLONNES)


def rapprochement(tab: pd.DataFrame) -> dict:
    """Totaux de contrôle : somme des soldes d'origine, somme après traitement, somme des écarts, nombre de comptes par statut."""
    if tab is None or tab.empty:
        return {"comptes": 0, "avant": 0.0, "apres": 0.0, "ecart": 0.0, "par_statut": {}}
    avant, apres = float(tab[COLONNES[2]].sum()), float(tab[COLONNES[5]].sum())
    return {"comptes": int(len(tab)), "avant": avant, "apres": apres, "ecart": apres - avant,
            "par_statut": {k: int(v) for k, v in tab["Statut"].value_counts().items()}}
