"""Jeux de données FICTIFS dont les résultats consolidés ont été calculés à la main (en milliers : 1 = 1 000 MAD)."""
import io

import pandas as pd

import _chemins  # noqa: F401
from consolidation_groupe import preparer

K = 1000


def bal(lignes):
    return preparer(pd.DataFrame([(c, l, d * K, cr * K) for c, l, d, cr in lignes], columns=["compte", "libelle", "debit", "credit"]))


def mere(banque=450, titres=600, avec_div=False):
    """Capitaux propres 1 100 (capital 1 000 + résultat 100). Actif 1 100 = titres + clients 50 + banque."""
    l = [("1111", "Capital", 0, 1000), ("2511", "Titres de participation", titres, 0), ("3421", "Clients", 50, 0), ("5141", "Banque", banque, 0),
         ("7111", "Ventes", 0, 300), ("6111", "Achats", 200, 0)]
    if avec_div:
        l += [("7321", "Produits de participation", 0, 40), ("5141", "Banque (dividendes)", 40, 0)]
    return bal(l)


def filiale():
    """Capitaux propres de clôture 600 (capital 400 + réserves 100 + résultat 100), dettes fournisseurs 100, actif 700."""
    return bal([("1111", "Capital", 0, 400), ("1152", "Réserves", 0, 100), ("2321", "Constructions", 400, 0), ("5141", "Banque", 300, 0),
                ("4411", "Fournisseurs", 0, 100), ("7111", "Ventes", 0, 250), ("6111", "Achats", 150, 0)])


def associee():
    """Capitaux propres de clôture 340 (capital 300 + résultat 40)."""
    return bal([("1111", "Capital", 0, 300), ("5141", "Banque", 340, 0), ("7111", "Ventes", 0, 100), ("6111", "Achats", 60, 0)])


def entites_base(avec_associee=False):
    e = [dict(nom="Mère SA", role="mere", df=mere(banque=330, titres=720) if avec_associee else mere()),
         dict(nom="Filiale SARL", role="filiale", methode="IG", pct_controle=80, pct_interet=80, df=filiale(),
              cout_titres=600 * K, fp_acquisition=450 * K, ecart_evaluation=50 * K)]
    if avec_associee:
        e.append(dict(nom="Associée SA", role="filiale", methode="MEE", pct_controle=30, pct_interet=30, df=associee(),
                      cout_titres=120 * K, fp_acquisition=300 * K))
    return e


def en_excel(df):
    b = io.BytesIO()
    df[["compte", "libelle", "debit", "credit"]].to_excel(b, index=False)
    return b.getvalue()
