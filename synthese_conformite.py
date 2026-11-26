"""MaroIFRS - présentation UNIQUE du statut de conformité, utilisée par l'interface ET par les rapports.

Le statut vient de rapport_conformite.statut_final (même fonction pour l'écran et le PDF) : il n'existe aucun second calcul.
Les pourcentages de l'écran ne sont plus appelés « conformité » : ils mesurent seulement la part des comptes pour lesquels AUCUNE règle de
norme ne s'est déclenchée (statut « inchangé »). Ils ne disent rien du respect des contrôles CGNC, de la fiscalité ou des écritures.
"""
from html import escape

ETIQUETTE_COMPTES = "Comptes sans retraitement"
ETIQUETTE_VALEUR = "Valeur sans retraitement"
DEFINITION_COMPTES = ("Part des comptes au statut « inchangé » (aucune règle des normes sélectionnées ne s'applique). "
                      "Ce n'est pas un taux de conformité : le verdict de conformité est le statut final.")
DEFINITION_VALEUR = "Somme des soldes (valeur absolue) des comptes « inchangés » ÷ somme des soldes de tous les comptes."

COULEURS = {"CONFORME": "#2E7D32", "CONFORME AVEC RÉSERVES": "#BF360C", "ANALYSE PARTIELLE": "#8A3000",
            "NON CONFORME": "#B94A48", "NON DÉTERMINÉ": "#5E6C63"}
FONDS = {"CONFORME": "#E8F5E9", "CONFORME AVEC RÉSERVES": "#FFE3D1", "ANALYSE PARTIELLE": "#FFE3D1",
         "NON CONFORME": "#F3DAD9", "NON DÉTERMINÉ": "#EEF3EE"}


def texte_statut(statut: str, motifs: list) -> str:
    """Texte identique à celui du rapport (page de garde et section « Statut final »)."""
    return f"Statut final : {statut} — " + "; ".join(motifs)


def bandeau_statut_html(statut: str, motifs: list, source: str = "") -> str:
    coul, fond = COULEURS.get(statut, "#5E6C63"), FONDS.get(statut, "#EEF3EE")
    suite = f'<div class="def" style="margin-top:.35rem">{escape(source)}</div>' if source else ""
    return (f'<div class="statut-final" style="background:{fond};border:1px solid {coul};border-left:6px solid {coul};border-radius:14px;'
            f'padding:.9rem 1.2rem;margin:.4rem 0 1rem 0"><div style="font-weight:800;font-size:1.15rem;color:{coul}">Statut de conformité : {escape(statut)}</div>'
            f'<div style="color:#26322B;font-size:.9rem;margin-top:.25rem">{escape("; ".join(motifs))}.</div>{suite}</div>')


def indicateurs(conf_n: float, conf_v: float, n_comptes: int) -> list:
    """Lignes (indicateur, valeur, définition) reprises à l'identique à l'écran et dans les rapports."""
    return [("Comptes analysés", str(int(n_comptes)), "Nombre de comptes lus dans le document importé."),
            (ETIQUETTE_COMPTES, f"{conf_n:.0f} %", DEFINITION_COMPTES),
            (ETIQUETTE_VALEUR, f"{conf_v:.0f} %", DEFINITION_VALEUR)]
