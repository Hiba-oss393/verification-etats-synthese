import pandas as pd

import _chemins  # noqa: F401
import harness_streamlit as hs
import parametres_fiscaux as pf


def test_parametres_references_dates_et_verification():
    tab = pf.tableau_parametres()
    assert list(tab.columns) == ["Paramètre", "Valeur", "Référence", "Date d'application", "Niveau de vérification"]
    assert (tab["Référence"].str.contains("CGI")).all() and (tab["Niveau de vérification"].str.len() > 20).all()
    assert pf.valeur("IS_NORMAL") == 0.20 and pf.valeur("IS_GRANDE") == 0.35 and pf.valeur("IS_FINANCE") == 0.40
    assert pf.valeur("CM_TAUX") == 0.0025 and pf.valeur("CM_MINIMUM") == 3000.0 and pf.valeur("DEFICIT_EXERCICES") == 4.0
    assert "à confirmer" in tab.loc[tab["Paramètre"].str.contains("réduit de la cotisation"), "Niveau de vérification"].iloc[0]


def test_taux_is_aucun_taux_suppose():
    assert pf.taux_is(2026, "ordinaire", 50_000_000)[0] == 0.20
    assert pf.taux_is(2026, "ordinaire", 100_000_000)[0] == 0.35
    assert pf.taux_is(2026, "ordinaire", 100_000_000, exclu_35=True)[0] == 0.20
    assert pf.taux_is(2027, "credit", 1)[0] == 0.40
    for exercice in (None, 0, 2025, 2023):                       # exercice absent ou antérieur à 2026 : taux non déterminé
        taux, motif = pf.taux_is(exercice, "ordinaire", 1000)
        assert taux is None and motif, exercice
    assert pf.taux_is(2025, "libre", 1000, taux_libre=0.2625)[0] == 0.2625     # le taux saisi par l'utilisateur est respecté
    assert pf.taux_is(2025, "libre", 1000, taux_libre=None)[0] is None
    assert pf.cotisation_minimale_applicable(2025)[0] and not pf.cotisation_minimale_applicable(2022)[0] and not pf.cotisation_minimale_applicable(None)[0]


def test_deficits_report_quatre_exercices_et_amortissements():
    tab = pd.DataFrame({pf.COLONNES_DEFICITS[0]: [2021, 2022, 2023, 2018, 2026, None],
                        pf.COLONNES_DEFICITS[1]: [1000.0, 500.0, 800.0, 400.0, 100.0, 50.0],
                        pf.COLONNES_DEFICITS[2]: [0.0, 0.0, 300.0, 400.0, 0.0, 0.0]})
    total, det, avert = pf.deficits_disponibles(tab, 2026)
    assert total == 500 + 800 + 400                  # 2021 expiré (5 ans), 2022 : 4e exercice, 2023 entier, 2018 : seuls les amortissements restent
    st = dict(zip(det["Exercice"], det["Statut"]))
    assert "expirée" in st[2021] and "Reportable" in st[2022] and "expirée" in st[2018]
    assert any("non antérieur" in a for a in avert) and any("incomplète" in a for a in avert)    # 2026 et ligne incomplète écartés, signalés
    assert pf.deficits_disponibles(tab, None)[0] == 0.0 and pf.deficits_disponibles(pf.deficits_vide(), 2026)[0] == 0.0
    plafond = pd.DataFrame({pf.COLONNES_DEFICITS[0]: [2025], pf.COLONNES_DEFICITS[1]: [100.0], pf.COLONNES_DEFICITS[2]: [150.0]})
    t2, _, a2 = pf.deficits_disponibles(plafond, 2026)
    assert t2 == 100.0 and any("plafonnée" in a for a in a2)


def _calc():
    st, g, err = hs.run({})
    assert err is None, err
    return g["calc_fiscal"], g["tableau_passage"], g["constats_fiscaux"]


def _balance():
    d = pd.DataFrame([("7111", "Ventes", 0, 1_000_000), ("6111", "Achats", 600_000, 0), ("6701", "IS", 80_000, 0), ("5141", "Banque", 1_000_000, 0),
                      ("1111", "Capital", 0, 1_320_000)], columns=["compte", "libelle", "debit", "credit"])
    d["solde"] = d["debit"] - d["credit"]
    return d


def test_calc_fiscal_valeurs_calculees_a_la_main():
    calc, passage, constats = _calc()
    reint = pd.DataFrame({"Libellé": ["Amendes"], "Montant": [20_000.0]})
    deduc = pd.DataFrame({"Libellé": ["x"], "Montant": [0.0]})
    f = calc(_balance(), reint, deduc, 50_000.0, "ordinaire", False, None, False, 0.0, 2026)
    assert f["rnc"] == 320_000 and f["rcav"] == 400_000 and f["rf_brut"] == 420_000          # 1 000 - 600 - 80 ; + 80 d'impôt ; + 20 de réintégrations
    assert f["deficit_impute"] == 50_000 and f["rf_net"] == 370_000
    assert abs(f["is_theorique"] - 74_000) < 0.01 and f["cm"] == 3000.0                        # 20 % x 370 000 ; 0,25 % x 1 000 000 = 2 500 < 3 000
    assert abs(f["is_du"] - 74_000) < 0.01 and abs(f["ecart_is"] + 6_000) < 0.01 and f["is_calcule"]
    f2 = calc(_balance(), reint, deduc, 0.0, "ordinaire", False, None, False, 0.0, 2026)       # sans déficit : 20 % x 420 000 = 84 000
    assert abs(f2["is_du"] - 84_000) < 0.01
    fe = calc(_balance(), reint, deduc, 0.0, "ordinaire", False, None, True, 0.0, 2026)        # cotisation minimale exonérée
    assert fe["cm"] == 0.0 and abs(fe["is_du"] - 84_000) < 0.01


def test_calc_fiscal_sans_exercice_ou_taux_aucun_resultat_suppose():
    calc, passage, constats = _calc()
    reint = pd.DataFrame({"Libellé": ["x"], "Montant": [0.0]})
    for ex, cat, taux in ((None, "ordinaire", None), (2025, "ordinaire", None), (2025, "libre", None)):
        f = calc(_balance(), reint, reint, 0.0, cat, False, taux, False, 0.0, ex)
        assert not f["is_calcule"] and f["is_du"] != f["is_du"]                                # NaN : jamais un zéro trompeur
        assert f["rf_net"] == 400_000                                                          # le résultat fiscal reste calculé (indépendant du taux)
        cs = constats(f)
        assert cs[0]["Niveau"] == "À vérifier" and "non calculé" in cs[0]["Constat"]
        lignes = dict(passage(f).values.tolist())
        assert lignes["IS dû (le plus élevé des deux)"] != lignes["IS dû (le plus élevé des deux)"]
    f = calc(_balance(), reint, reint, 0.0, "libre", False, 0.25, False, 0.0, 2025)             # exercice 2025 : taux saisi, cotisation minimale connue (2023+)
    assert f["is_calcule"] and abs(f["is_du"] - 100_000) < 0.01                                  # 25 % x 400 000
