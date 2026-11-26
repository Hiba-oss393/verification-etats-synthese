import json

import pandas as pd

import _chemins  # noqa: F401
from _chemins import RACINE
import grand_livre as gl


def _journal(lignes):
    return pd.DataFrame(lignes, columns=["Date", "Journal", "N° pièce", "Compte", "Libellé", "Débit", "Crédit"])


def _niveaux(res, mot):
    return [x["Niveau"] for x in res if mot in x.get("Détail", x.get("Constat", ""))]


def test_lecture_ecritures_et_dates_iso():
    j = _journal([["2025-04-10", "VT", "V1", "3421", "Client", 1200, 0], ["2025-04-10", "VT", "V1", "7111", "Vente", 0, 1000],
                  ["10/05/2025", "VT", "V1", "4455", "TVA", 0, 200]])
    assert gl.est_liste_ecritures(j) == "journal"
    e = gl.lire_ecritures(j)
    assert e["date"].iloc[0].month == 4 and e["date"].iloc[0].day == 10          # AAAA-MM-JJ lu année d'abord
    assert e["date"].iloc[2].month == 5 and e["date"].iloc[2].day == 10          # JJ/MM/AAAA lu jour d'abord
    b = gl.balance_depuis_ecritures(e)
    assert abs(b["debit"].sum() - b["credit"].sum()) < 0.01 and len(b) == 3
    assert gl.est_liste_ecritures(pd.DataFrame(columns=["compte", "libelle", "debit", "credit"])) is None   # une balance n'est pas un journal


def test_controles_ecritures_piece_desequilibree_et_plan_comptable():
    cg = json.load(open(RACINE / "referentiel_cgnc.json", encoding="utf-8"))["comptes"]
    j = _journal([["2025-01-01", "OD", "O1", "6171", "Salaire", 300, 0], ["2025-01-01", "OD", "O1", "5141", "Banque", 0, 250],
                  ["2025-01-02", "OD", "O2", "9999", "Compte faux", 10, 0], ["2025-01-02", "OD", "O2", "5141", "Banque", 0, 10]])
    res = gl.controles_ecritures(gl.lire_ecritures(j), cg)
    texte = " | ".join(f"{x['Niveau']}:{x['Détail']}" for x in res)
    assert "Erreur:1 pièce(s) déséquilibrée(s)" in texte and "OD/O1" in texte
    assert "Erreur:Total débit ≠ total crédit" in texte
    assert "hors des classes 1 à 8" in texte


def test_tva_par_piece_taux_conformes_et_atypiques():
    j = _journal([
        ["2025-01-01", "VT", "V1", "3421", "Client", 1200, 0], ["2025-01-01", "VT", "V1", "7111", "Vente", 0, 1000], ["2025-01-01", "VT", "V1", "4455", "TVA", 0, 200],
        ["2025-01-02", "VT", "V2", "3421", "Client", 1070, 0], ["2025-01-02", "VT", "V2", "7111", "Vente", 0, 1000], ["2025-01-02", "VT", "V2", "4455", "TVA", 0, 70],
        ["2025-01-03", "VT", "V3", "3421", "Client", 1214, 0], ["2025-01-03", "VT", "V3", "7111", "Vente", 0, 1000], ["2025-01-03", "VT", "V3", "4455", "TVA", 0, 214],
        ["2025-01-04", "AC", "A1", "6111", "Achat", 500, 0], ["2025-01-04", "AC", "A1", "34552", "TVA récup", 100, 0], ["2025-01-04", "AC", "A1", "4411", "Fourn", 0, 600],
        ["2025-01-05", "VT", "V4", "3421", "Client", 500, 0], ["2025-01-05", "VT", "V4", "7111", "Vente exonérée", 0, 500]])
    res = gl.controle_tva_ecritures(gl.lire_ecritures(j))
    txt = {x["Niveau"] + "|" + x["Détail"] for x in res}
    assert any(t.startswith("Info|3 pièce(s) dont le taux") for t in txt)                 # V1 (20 %), V2 (7 %), A1 (20 %)
    assert any("V3" in t and "21.4%" in t and t.startswith("À vérifier") for t in txt)   # 21,4 % : aucun taux légal
    assert any("V4" in t and "sans TVA facturée" in t for t in txt)                       # vente sans TVA
    assert all(x["Niveau"] in ("Info", "À vérifier") for x in res)                        # jamais d'« erreur » affirmée sur un taux


def test_tva_ecritures_sans_piece_ou_sans_base_controle_impossible():
    j = _journal([["2025-01-01", "VT", "", "3421", "Client", 1200, 0], ["2025-01-01", "VT", "", "7111", "Vente", 0, 1000], ["2025-01-01", "VT", "", "4455", "TVA", 0, 200]])
    r = gl.controle_tva_ecritures(gl.lire_ecritures(j))
    assert r[0]["Niveau"] == "À vérifier" and "Contrôle impossible" in r[0]["Détail"]
    j2 = _journal([["2025-01-01", "OD", "T1", "4455", "TVA seule", 0, 200], ["2025-01-01", "OD", "T1", "5141", "Banque", 200, 0]])
    assert any("sans base HT" in x["Détail"] or "Contrôle impossible" in x["Détail"] for x in gl.controle_tva_ecritures(gl.lire_ecritures(j2)))
    j3 = _journal([["2025-01-01", "OD", "T1", "6171", "Salaire", 100, 0], ["2025-01-01", "OD", "T1", "5141", "Banque", 0, 100]])
    assert gl.controle_tva_ecritures(gl.lire_ecritures(j3))[0]["Niveau"] == "Info"      # pas de TVA : sans objet


def _balance(lignes):
    d = pd.DataFrame(lignes, columns=["compte", "libelle", "debit", "credit"])
    d["solde"] = d["debit"] - d["credit"]
    return d


def test_tva_balance_aucun_taux_calcule_sur_solde_de_cloture():
    """Le solde de 4455 ne sert jamais à calculer un taux : 30 % ou 5 % du CA ne produisent aucun constat de taux."""
    for tva in (300_000, 50_000):
        b = _balance([("7111", "Ventes", 0, 1_000_000), ("4455", "TVA", 0, tva), ("3421", "Clients", 1_000_000 + tva, 0)])
        res = gl.controle_tva(b)
        textes = " ".join(x["Constat"] for x in res)
        assert "taux effectif" not in textes.lower()
        assert res and res[0]["Niveau"] == "À vérifier" and "Contrôle impossible" in res[0]["Constat"]
    b = _balance([("7111", "Ventes", 0, 1_000_000), ("4455", "TVA", 0, 200_000), ("3421", "Clients", 1_200_000, 0)])
    assert gl.controle_tva(b, detail_disponible=True) == []      # le détail existe : le contrôle de taux est fait ailleurs
    assert gl.controle_tva(_balance([("6171", "Salaires", 100, 0), ("5141", "Banque", 0, 100)])) == []    # ni vente ni TVA : sans objet


def test_tva_balance_sens_anormaux():
    b = _balance([("4455", "TVA facturée", 5000, 0), ("3455", "TVA récupérable", 0, 4000), ("4456", "TVA due", 0, 100), ("3456", "Crédit TVA", 100, 0),
                  ("5141", "Banque", 0, 1000)])
    t = " | ".join(x["Constat"] for x in gl.controle_tva(b, detail_disponible=True))
    assert "4455 à solde débiteur" in t and "3455" in t and "simultanément" in t


def test_tva_factures():
    tab = pd.DataFrame([
        {"N° facture": "F1", "HT": 1000.0, "TVA": 200.0, "TTC": 1200.0, "_tva_deduite": False},
        {"N° facture": "F2", "HT": 1000.0, "TVA": 100.0, "TTC": 1100.0, "_tva_deduite": False},     # 10 % : taux réduit légal
        {"N° facture": "F3", "HT": 1000.0, "TVA": 235.0, "TTC": 1235.0, "_tva_deduite": False},     # 23,5 % : aucun taux
        {"N° facture": "F4", "HT": 1000.0, "TVA": 200.0, "TTC": 1200.0, "_tva_deduite": True},      # TVA reconstituée par l'application
        {"N° facture": "F5", "HT": 1000.0, "TVA": 200.0, "TTC": 1300.0, "_tva_deduite": False}])    # TTC incohérent
    res = gl.controle_tva_factures(tab)
    t = {x["Niveau"] + "|" + x["Détail"] for x in res}
    assert any(s.startswith("Info|3 facture(s) dont le taux") for s in t)
    assert any("F3" in s and "23.5%" in s for s in t)
    assert any("Contrôle impossible pour 1 facture" in s and "F4" in s for s in t)
    assert any("TTC diffère" in s and "F5" in s for s in t)
