import io

import pandas as pd
import pdfplumber

import _chemins  # noqa: F401
import harness_streamlit as hs
import rapport_conformite as rc
from test_couverture_normes import balance_complete
from test_interface import _xlsx


def _run(scn=None):
    scn = dict(scn or {})
    scn.setdefault("uploads", {"main": [hs.FakeFile("balance.xlsx", _xlsx(balance_complete()[["compte", "libelle", "debit", "credit"]]))]})
    st, g, err = hs.run(scn)
    assert err is None, err
    return st, g


def test_valeur_utilite_formule_independante():
    st, g = _run({"uploads": {}})
    vu = g["valeur_utilite"]([100.0] * 5, 0.10, 0.02)
    attendu = sum(100 / 1.1 ** i for i in range(1, 6)) + 100 * 1.02 / (0.10 - 0.02) / 1.1 ** 5
    assert abs(vu - attendu) < 1e-6
    assert abs(g["valeur_utilite"]([100.0] * 5, 0.10, 0.0) - (sum(100 / 1.1 ** i for i in range(1, 6)) + 100 / 0.10 / 1.1 ** 5)) < 1e-6


def test_impots_differes_actif_et_passif():
    st, g = _run({"uploads": {}})
    tab = pd.DataFrame({"Libellé": ["Immo", "Provision"], "Nature": ["Actif", "Passif"], "Valeur comptable IFRS": [1000.0, 500.0], "Base fiscale": [800.0, 300.0]})
    r = g["calc_impots_differes"](tab, 0.20).set_index("Libellé")
    assert r.loc["Immo", "Différence temporaire"] == 200 and abs(r.loc["Immo", "Impôt différé"] - 40) < 1e-9 and r.loc["Immo", "Type"] == "Passif d'impôt différé"
    assert r.loc["Provision", "Différence temporaire"] == -200 and abs(r.loc["Provision", "Impôt différé"] + 40) < 1e-9 and r.loc["Provision", "Type"] == "Actif d'impôt différé"


def test_calculateur_ias36_aucune_valeur_par_defaut_trompeuse():
    st, g = _run()
    assert g["ugt"].empty                                          # taux d'actualisation non saisi : ni valeur d'utilité ni perte de valeur affichées
    st, g = _run({"number": {"taux36": 10.0, "g36": 2.0}})
    ugt = g["ugt"].set_index("Indicateur")["Montant"]
    vc0, flux0 = g["valeurs_ias36_depuis_balance"](g["df"])
    vu = sum(flux0 / 1.1 ** i for i in range(1, 6)) + flux0 * 1.02 / 0.08 / 1.1 ** 5
    assert abs(ugt["Valeur d'utilité"] - vu) < 0.01 and abs(ugt["Perte de valeur"] - max(0.0, vc0 - vu)) < 0.01
    st, g = _run({"number": {"taux36": 2.0, "g36": 2.0}})            # croissance = taux : valeur terminale non calculable, rien n'est affiché
    assert g["ugt"].empty and any("strictement inférieure" in e for e in st.log["error"])


def test_rapport_complet_nouvelles_sections_et_statut():
    st, g = _run()
    rap = g["rap"]
    stat, motifs = rap["statut"]
    assert stat == "CONFORME AVEC RÉSERVES", (stat, motifs)         # document complet mais retraitements, informations manquantes et contrôles non exécutés
    assert any("informations complémentaires" in m for m in motifs) and any("non exécuté" in m for m in motifs)
    pdf, xls = rc.construire_pdf(rap), rc.construire_excel(rap)
    with pdfplumber.open(io.BytesIO(pdf)) as p:
        texte = " ".join(" ".join(pg.extract_text() or "" for pg in p.pages).split())
    for mot in ("Nature des résultats et qualité des données", "Traitement par norme", "Comparatif des états avant (CGNC)", "Informations manquantes et contrôles non exécutés",
                "Contrôle fiscal : paramètres appliqués", "IAS 16.43"):
        assert mot in texte, mot
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(xls))
    for feuille in ("Traitement par norme", "Comparatif CGNC-IFRS", "Qualité des données", "Manquants et non exécutés", "Paramètres fiscaux"):
        assert feuille in wb.sheetnames, feuille
    ws = wb["Traitement par norme"]
    entetes = [c.value for c in ws[3]]
    for col in ("Norme", "Statut du contrôle", "Poste ou compte concerné", "Valeur comptable initiale (MAD)", "Ajustement proposé", "Règle appliquée",
                "Justification du retraitement", "Référence de la norme", "Informations nécessaires"):
        assert col in entetes, col


def test_jamais_conforme_sans_controles_complets():
    ok = pd.DataFrame(columns=["Niveau"])
    nature_ok, nature_ko = {"complets": True, "raisons": []}, {"complets": False, "raisons": ["factures seules"]}
    assert rc.statut_final(ok, ok, nature=nature_ok)[0] == "CONFORME"
    assert rc.statut_final(ok, ok, nature=nature_ko)[0] == "ANALYSE PARTIELLE"                     # document partiel : jamais « conforme »
    assert rc.statut_final(ok, ok, nature=nature_ok, non_executes=["TVA : détail requis"])[0] == "CONFORME AVEC RÉSERVES"
    assert rc.statut_final(ok, ok, nature=nature_ok, manquantes=["IAS 16 : durées d'utilité"])[0] == "CONFORME AVEC RÉSERVES"
    assert rc.statut_final(pd.DataFrame([{"Niveau": "Erreur"}]), ok, nature=nature_ko)[0] == "NON CONFORME"   # une vraie erreur prime
