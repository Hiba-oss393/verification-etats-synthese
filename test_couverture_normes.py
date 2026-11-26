import pandas as pd

import _chemins  # noqa: F401
import couverture_normes as cn
import harness_streamlit as hs
from test_interface import _xlsx


def _bal(lignes):
    d = pd.DataFrame(lignes, columns=["compte", "libelle", "debit", "credit"])
    d["solde"] = d["debit"] - d["credit"]
    return d


def balance_complete():
    """Balance équilibrée : non-valeurs 50, provisions réglementées 100, résultat 150 (1 000 - 600 - 200 - 50)."""
    return _bal([("1111", "Capital", 0, 1000), ("1161", "Report à nouveau", 0, 250), ("135", "Provisions réglementées", 0, 100), ("2111", "Frais préliminaires", 50, 0),
                 ("2321", "Constructions", 800, 0), ("2832", "Amortissements", 0, 100), ("3111", "Stocks", 300, 0), ("3421", "Clients", 500, 0),
                 ("4411", "Fournisseurs", 0, 300), ("5141", "Banque", 250, 0), ("6111", "Achats", 600, 0), ("6171", "Personnel", 200, 0),
                 ("6701", "IS", 50, 0), ("7111", "Ventes", 0, 1000)])


def test_comparatif_cgnc_ifrs_valeurs_et_equilibre():
    df = balance_complete()
    assert df["debit"].sum() == df["credit"].sum()
    c = cn.comparatif_cgnc_ifrs(df, 0.20).set_index("Poste")
    assert c.loc["TOTAL ACTIF", "CGNC (MAD)"] == 1800 and c.loc["TOTAL ACTIF", "IFRS (MAD)"] == 1750          # non-valeurs 50 éliminées
    assert c.loc["TOTAL PASSIF", "CGNC (MAD)"] == 1800 and c.loc["TOTAL PASSIF", "IFRS (MAD)"] == 1750
    assert c.loc["Immobilisations en non-valeur", "IFRS (MAD)"] == 0 and c.loc["Immobilisations en non-valeur", "Écart (MAD)"] == -50
    assert c.loc["Impôt différé passif", "IFRS (MAD)"] == 10                       # 20 % x (50 - 100) = -10 : impôt différé net passif
    assert c.loc["Impôt différé actif", "IFRS (MAD)"] == 0
    assert c.loc["Provisions réglementées (capitaux propres assimilés)", "CGNC (MAD)"] == 100
    assert c.loc["Capitaux propres hors provisions réglementées", "IFRS (MAD)"] == 1400 - 40 + 80
    for partie in ("TOTAL ACTIF", "TOTAL PASSIF"):
        assert c.loc[partie, "Écart (MAD)"] == -50
    assert c.loc[[i for i in c.index if i.startswith("Résultat net")][0], "Écart (MAD)"] == 0


def test_nature_des_resultats():
    df = balance_complete()
    assert cn.nature_resultats(df)["complets"]
    n = cn.nature_resultats(df, factures=True)
    assert not n["complets"] and "factures seules" in n["raisons"][0]
    n = cn.nature_resultats(df[df["compte"].str.startswith(("6", "7"))])
    assert not n["complets"] and "Aucun compte de bilan" in n["raisons"][0]
    n = cn.nature_resultats(df[~df["compte"].str.startswith(("6", "7"))])
    assert not n["complets"] and "Aucun compte de résultat" in n["raisons"][0]


def test_qualite_des_donnees_explicable():
    df = balance_complete()
    q = cn.qualite_donnees(df, pd.DataFrame(columns=["Niveau"]), cn.nature_resultats(df), comptes_cgnc=["1111", "2111", "3111", "4411", "5141", "6111", "7111", "1161", "135", "2321", "2832", "3421", "6171", "6701"])
    assert q["score"] >= 90 and q["niveau"] == "Bonne" and len(q["composantes"]) == 5
    assert "exactitude" in q["avertissement"]
    mauvais = df.copy()
    mauvais.loc[0, "credit"] = 400                                            # balance déséquilibrée
    q2 = cn.qualite_donnees(mauvais, pd.DataFrame([{"Niveau": "Erreur"}]), cn.nature_resultats(mauvais), ["1111"])
    assert q2["score"] < q["score"] and q2["composantes"].iloc[0]["Note (/100)"] == 0
    partiel = cn.qualite_donnees(df, None, cn.nature_resultats(df, factures=True), None)
    assert partiel["composantes"].set_index("Composante").loc["Complétude du document", "Note (/100)"] == 50


def test_traitement_par_norme_dans_l_application():
    st, g, err = hs.run({"uploads": {"main": [hs.FakeFile("balance.xlsx", _xlsx(balance_complete()[["compte", "libelle", "debit", "credit"]]))]}})
    assert err is None, err
    t = g["traitements"].set_index("Norme")
    assert len(t) == 25 and set(t["Niveau d'automatisation"]) == {cn.RETRAITEMENT, cn.DETECTION, cn.RAPPEL}
    assert t.loc["IAS 1", "Statut du contrôle"].startswith("Exécuté") and "Non-valeurs éliminées : −50" in t.loc["IAS 1", "Ajustement proposé"]
    assert t.loc["IAS 36", "Statut du contrôle"].startswith("Sans objet")                         # aucun fonds commercial
    assert t.loc["IAS 8", "Statut du contrôle"].startswith("Rappel d'information")                # rappel : aucun calcul, jamais « vérifié »
    assert t.loc["IFRS 5", "Statut du contrôle"].startswith("Sans objet")                         # aucune cession : IFRS 5 ne s'applique pas d'office
    assert t.loc["IAS 12", "Statut du contrôle"].startswith("Exécuté") and "informations complémentaires" in t.loc["IAS 12", "Statut du contrôle"]
    assert "21 : 50" in t.loc["IAS 1", "Valeur comptable initiale (MAD)"]
    assert "IAS 16.43" in t.loc["IAS 16", "Référence de la norme"] and t.loc["IAS 16", "Informations nécessaires"] != "—"
    assert g["nature"]["complets"] is True and g["qualite"]["score"] >= 70


def test_document_sans_resultat_est_partiel_et_les_rappels_sont_non_applicables():
    bilan = balance_complete()
    bilan = bilan[~bilan["compte"].str.startswith(("6", "7"))].copy()
    bilan.loc[bilan["compte"] == "1161", "credit"] = 400                       # report à nouveau rééquilibrant le bilan seul (débit 1 900 = crédit 1 900)
    assert bilan["debit"].sum() == bilan["credit"].sum()
    st, g, err = hs.run({"uploads": {"main": [hs.FakeFile("bilan.xlsx", _xlsx(bilan[["compte", "libelle", "debit", "credit"]]))]}})
    assert err is None, err
    assert g["nature"]["complets"] is False
    t = g["traitements"].set_index("Norme")
    assert t.loc["IAS 8", "Statut du contrôle"].startswith("Non applicable")                      # jeu incomplet : aucun rappel affiché comme contrôle
    assert any("Contrôle non exécuté" in x or "non applicable" in x for x in g["non_executes"])
    aff = hs.statuts_affiches(st)
    assert aff and set(aff) == {"ANALYSE PARTIELLE"}, aff                     # le même statut sur toutes les pages
    dash = [x for x in st.log["textes"] if "Chiffre d'affaires" in x]
    assert any("n/c : CPC absent" in x for x in dash)                         # tableau de bord : « n/c » et non un chiffre d'affaires à 0
