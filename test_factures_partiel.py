import numpy as np
import pandas as pd

import _chemins  # noqa: F401
import grand_livre as gl
import harness_streamlit as hs
from test_interface import _xlsx


def _tab():
    return pd.DataFrame([
        {"N° facture": "F001", "Tiers": "ABC SARL", "Sens": "Achat", "Compte": "6111", "HT": 1000.0, "TVA": 200.0, "TTC": 1200.0, "_tva_deduite": False},
        {"N° facture": "F002", "Tiers": "XYZ SA", "Sens": "Vente", "Compte": "7111", "HT": 500.0, "TVA": 100.0, "TTC": 600.0, "_tva_deduite": False},
        {"N° facture": "F003", "Tiers": "Matériel SA", "Sens": "Achat", "Compte": "2340", "HT": 800.0, "TVA": 160.0, "TTC": 960.0, "_tva_deduite": True},
        {"N° facture": "AV01", "Tiers": "ABC SARL", "Sens": "Achat", "Compte": "6111", "HT": -100.0, "TVA": -20.0, "TTC": -120.0, "_tva_deduite": False}])


def test_ecritures_proposees_equilibrees_et_comptes_cgnc():
    e = gl.ecritures_depuis_factures(_tab(), lambda c: "Libellé " + c)
    assert abs(e["Débit"].sum() - e["Crédit"].sum()) < 0.005
    for fact, g in e.groupby("Facture"):
        assert abs(g["Débit"].sum() - g["Crédit"].sum()) < 0.005, fact          # chaque pièce est équilibrée
    f1 = e[e["Facture"] == "F001"].set_index("Compte")
    assert f1.loc["6111", "Débit"] == 1000 and f1.loc["34552", "Débit"] == 200 and f1.loc["4411", "Crédit"] == 1200
    f2 = e[e["Facture"] == "F002"].set_index("Compte")
    assert f2.loc["3421", "Débit"] == 600 and f2.loc["7111", "Crédit"] == 500 and f2.loc["4455", "Crédit"] == 100
    f3 = e[e["Facture"] == "F003"].set_index("Compte")
    assert "34551" in f3.index and f3.loc["34551", "Débit"] == 160                  # immobilisation : TVA récupérable sur immobilisations
    assert "TVA non lue" in e[e["Facture"] == "F003"]["Observation"].iloc[0]       # TVA reconstituée : signalée
    av = e[e["Facture"] == "AV01"].set_index("Compte")
    assert av.loc["6111", "Crédit"] == 100 and av.loc["4411", "Débit"] == 120      # l'avoir inverse les sens
    sans_compte = _tab().assign(Compte=["", "7111", "2340", "6111"])
    assert "Compte à renseigner" in gl.ecritures_depuis_factures(sans_compte).iloc[0]["Observation"]


def test_cellule_vide_n_est_pas_une_tva_deduite():
    assert gl._vrai(np.nan) is False and gl._vrai(None) is False and gl._vrai(True) is True and gl._vrai(False) is False
    tab = _tab().copy()
    tab["_tva_deduite"] = [np.nan, np.nan, np.nan, np.nan]                          # lignes ajoutées à la main dans le tableau
    res = gl.controle_tva_factures(tab)
    assert not any("Contrôle impossible" in x["Détail"] for x in res)


def test_factures_seules_donnent_une_analyse_partielle_jamais_conforme():
    fact = pd.DataFrame([{"N° facture": "F001", "Fournisseur": "ABC SARL", "Date": "2025-03-01", "HT": 1000, "TVA": 200, "TTC": 1200},
                         {"N° facture": "F002", "Fournisseur": "XYZ SA", "Date": "2025-03-05", "HT": 500, "TVA": 107, "TTC": 607},
                         {"N° facture": "F003", "Fournisseur": "QRS", "Date": "2025-03-09", "HT": 800, "TVA": None, "TTC": 960}])
    st, g, err = hs.run({"uploads": {"main": [hs.FakeFile("factures.xlsx", _xlsx(fact))]}})
    assert err is None, err
    assert g["nature"]["complets"] is False and "factures seules" in g["nature"]["raisons"][0]
    assert any("Résultats partiels" in w for w in st.log["warning"])
    statuts = hs.statuts_affiches(st)
    assert statuts and all(x in ("ANALYSE PARTIELLE", "NON CONFORME") for x in statuts) and "CONFORME" not in statuts
    assert len(set(statuts)) == 1, statuts                                    # même statut sur toutes les pages
    ecr = g["ecr_controles"]
    assert any("F002" in x["Détail"] and "21.4%" in x["Détail"] for x in ecr)                     # taux de 21,4 % : aucun taux légal
    assert any("Contrôle impossible" in x["Détail"] and "F003" in x["Détail"] for x in ecr)       # TVA reconstituée : non contrôlable
    cg = g["cgnc_df"]
    assert not ((cg["Contrôle"] == "Cohérence") & (cg["Niveau"] == "Erreur")).any()             # « capitaux propres négatifs » n'est pas une erreur sur un document partiel
    assert g["qualite"]["composantes"].set_index("Composante").loc["Complétude du document", "Note (/100)"] == 50
