"""Interface : (1) simulateur de Streamlit (toujours exécuté) ; (2) vrai Streamlit via AppTest (uniquement si Streamlit est installé).
AUCUN de ces tests ne remplace un essai visuel dans le navigateur."""
import io

import pandas as pd

import _chemins  # noqa: F401
import harness_streamlit as hs
from _chemins import RACINE, Ignore
from _donnees import K, en_excel, entites_base

BALANCE_TVA = pd.DataFrame([
    ("1111", "Capital social", 0, 1_000_000), ("1161", "Report à nouveau", 0, 100_000), ("1481", "Emprunts", 0, 600_000),
    ("2111", "Frais préliminaires", 50_000, 0), ("2321", "Constructions", 800_000, 0), ("2340", "Matériel de transport", 200_000, 0),
    ("2832", "Amortissements", 0, 400_000), ("3111", "Marchandises", 400_000, 0), ("3421", "Clients", 1_250_000, 0),
    ("4411", "Fournisseurs", 0, 700_000), ("5141", "Banque", 500_000, 0), ("6111", "Achats revendus", 1_800_000, 0),
    ("6171", "Rémunérations", 600_000, 0), ("6701", "Impôt sur les résultats", 120_000, 0), ("7111", "Ventes", 0, 3_000_000),
    ("4455", "TVA facturée", 0, 600_000), ("34552", "TVA récupérable", 150_000, 0)], columns=["compte", "libelle", "debit", "credit"])


def _equilibrer(df):
    e = df["debit"].sum() - df["credit"].sum()
    df = df.copy()
    df.loc[len(df)] = ["1152", "Réserves (équilibrage du jeu de test)", max(-e, 0), max(e, 0)]
    return df


def _xlsx(df):
    b = io.BytesIO()
    df.to_excel(b, index=False)
    return b.getvalue()


def _sans_erreur(err):
    assert err is None, "L'exécution de app.py a levé une exception :\n" + str(err)


def test_simulateur_balance_et_rapport_complet():
    f = hs.FakeFile("balance.xlsx", _xlsx(_equilibrer(BALANCE_TVA)))
    st, g, err = hs.run({"uploads": {"main": [f]}})
    _sans_erreur(err)
    boutons = [b[0] for b in st.log["download"]]
    for b in ("Rapport synthétique – PDF", "Rapport détaillé de conformité – PDF", "Résultats de l'analyse – Excel", "Rapport détaillé de conformité – Excel"):
        assert b in boutons, b
    assert all(taille > 1000 for lib, taille in st.log["download"] if "Rapport" in lib or "Résultats" in lib)
    cg = g["cgnc_df"]
    tva = cg[cg["Contrôle"] == "TVA"]
    assert len(tva) >= 1 and (tva["Niveau"] == "À vérifier").all()            # balance seule : TVA « contrôle impossible », jamais un taux calculé
    assert tva["Constat"].str.contains("Contrôle impossible").any() and not tva["Constat"].str.contains("taux effectif", case=False).any()
    aff = hs.statuts_affiches(st)
    assert len(aff) >= 2 and len(set(aff)) == 1, aff                          # page Transformation et page Résultats : même statut


def test_simulateur_journal_detecte_piece_desequilibree():
    rows = []
    def piece(j, p, lignes):
        rows.extend([["2025-02-10", j, p, c, l, d, cr] for c, l, d, cr in lignes])
    piece("AC", "A1", [("1111", "Capital", 0, 500000), ("5141", "Banque", 500000, 0)])
    piece("VT", "V1", [("3421", "Clients", 1200000, 0), ("7111", "Ventes", 0, 1000000), ("4455", "TVA", 0, 200000)])
    piece("OD", "O1", [("6171", "Personnel", 300000, 0), ("5141", "Banque", 0, 250000)])
    j = pd.DataFrame(rows, columns=["Date", "Journal", "N° pièce", "Compte", "Libellé", "Débit", "Crédit"])
    st, g, err = hs.run({"uploads": {"main": [hs.FakeFile("journal.xlsx", _xlsx(j))]}})
    _sans_erreur(err)
    ecr = g["ecr_controles"]
    assert any(x["Niveau"] == "Erreur" and "OD/O1" in x["Détail"] for x in ecr)
    assert any("TVA" in x["Contrôle"] and "taux" in x["Détail"] for x in ecr)
    assert any(l["Document"].startswith("Journal") or "ournal" in l["Document"] for l in g["lignes_doc"])
    assert set(hs.statuts_affiches(st)) == {"NON CONFORME"}, hs.statuts_affiches(st)


def _fichiers_groupe():
    return [hs.FakeFile("Mere_SA.xlsx", en_excel(entites_base()[0]["df"])), hs.FakeFile("Filiale_SARL.xlsx", en_excel(entites_base()[1]["df"]))]


def test_simulateur_consolidation_refuse_sans_parametres_puis_calcule():
    st, g, err = hs.run({"uploads": {"fich_groupe": _fichiers_groupe()}})
    _sans_erreur(err)
    assert g["conso_groupe"] is None and any("Consolidation impossible" in e or "pourcentages" in e or "coût d'acquisition" in e for e in st.log["error"])

    def remplir(df):
        d = df.copy()
        d.loc[1, ["% contrôle", "% intérêt", "Coût des titres (MAD)", "Capitaux propres à l'acquisition (MAD)", "Écart d'évaluation (MAD)"]] = [80, 80, 600 * K, 450 * K, 50 * K]
        return d
    st, g, err = hs.run({"uploads": {"fich_groupe": _fichiers_groupe()}, "editors": {"perim_": remplir}})
    _sans_erreur(err)
    c = g["conso_groupe"]
    assert c is not None and abs(c["ecart_bilan"]) < 1
    assert abs(c["resultat_groupe"] - 180 * K) < 1 and abs(c["total_actif"] - 1450 * K) < 1       # mêmes valeurs que le calcul fait à la main
    assert c["donnees_manquantes"]                                                                 # absence d'éliminations signalée


def test_simulateur_comparaison_exige_les_informations():
    f = hs.FakeFile("balance.xlsx", _xlsx(_equilibrer(BALANCE_TVA)))
    textes = {"ex_calc": "2025", "ex_off": "2025", "v_ta": "3 000", "v_tp": "3 010", "v_ca": "2 800", "v_rn": "350"}
    st, g, err = hs.run({"uploads": {"main": [f]}, "text": textes})
    _sans_erreur(err)
    c = g["comparaison"]                       # les listes déroulantes restent sur « (à choisir) » : rien n'est supposé
    assert c is not None and c["statut"] == "Informations à compléter" and c["tableau"].empty and c["manquants"]
    assert any("Renseignez" in w for w in st.log["warning"])


def test_simulateur_comparaison_complete_concordance():
    """Comparaison de bout en bout par l'interface : les chiffres officiels saisis (en milliers) égalent les chiffres calculés -> concordance."""
    f = lambda: hs.FakeFile("balance.xlsx", _xlsx(_equilibrer(BALANCE_TVA)))
    st, g, err = hs.run({"uploads": {"main": [f()]}})
    _sans_erreur(err)
    cs = g["c_s"]                                           # comptes retraités de la société seule, en milliers de MAD
    val = lambda tab, poste: float(tab.loc[tab["Poste"] == poste, "Montant"].iloc[0])
    textes = {"ex_calc": "2025", "ex_off": "2025", "v_ta": str(val(cs["actif"], "TOTAL ACTIF")), "v_tp": str(val(cs["passif"], "TOTAL PASSIF")),
              "v_ca": str(val(cs["cpc"], "Chiffre d'affaires")), "v_rn": str(val(cs["cpc"], "Résultat net part Groupe"))}
    choix = {"ref_calc": "IFRS", "ref_off": "IFRS", "per_off": "Social (société seule)", "uni_off": "Milliers de MAD", "dev_off": "MAD"}
    st, g, err = hs.run({"uploads": {"main": [f()]}, "text": textes, "select": choix})
    _sans_erreur(err)
    c = g["comparaison"]
    assert c["comparable"] and c["statut"] == "Concordance avec les comptes officiels", (c["statut"], c["problemes"])
    assert len(c["tableau"]) == 4 and (c["tableau"]["Écart (MAD)"].abs() < 1000).all()
    # même saisie mais exercice différent : comparaison non valable
    st, g, err = hs.run({"uploads": {"main": [f()]}, "text": dict(textes, ex_off="2024"), "select": choix})
    _sans_erreur(err)
    assert g["comparaison"]["statut"] == "Comparaison non valable"
    # même saisie mais périmètre publié « consolidé » alors que les comptes calculés sont ceux d'une société seule
    st, g, err = hs.run({"uploads": {"main": [f()]}, "text": textes, "select": dict(choix, per_off="Consolidé (groupe)")})
    _sans_erreur(err)
    assert g["comparaison"]["statut"] == "Comparaison non valable"


def _apptest():
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as e:
        raise Ignore("Streamlit non installé (%s) : test non exécuté" % e.__class__.__name__)
    import os
    os.environ["MAROIFRS_HISTORIQUE"] = os.path.join(__import__("tempfile").mkdtemp(prefix="maroifrs_histo_"), "historique.json")
    os.chdir(RACINE)
    return AppTest.from_file(str(RACINE / "app.py"), default_timeout=180)


def test_streamlit_reel_apptest():
    """Exécution dans le vrai moteur Streamlit (sans navigateur). Ignoré si Streamlit n'est pas installé."""
    at = _apptest().run()
    assert not at.exception, [str(x.value) for x in at.exception]
    assert at.session_state["page"] == "accueil"
    assert len(at.tabs) == 0 or len(at.tabs) < 10          # plus de rangée de 10 onglets : navigation hybride


def test_navigation_hybride_pages_reelles():
    """Barre horizontale (4 pages) + menu ☰ (7 pages) : chaque bouton change réellement la page affichée."""
    import interface_maroifrs as ui
    at = _apptest().run()
    assert not at.exception, [str(x.value) for x in at.exception]
    for p in ui.PAGES_BARRE:
        assert any(b.key == "nav_" + p for b in at.button), p
    at.button(key="nav_burger").click().run()
    assert at.session_state["menu_ouvert"] is True
    for p in ui.PAGES_MENU:
        assert any(b.key == "menu_" + p for b in at.button), p
    for p in ui.PAGES_MENU + ui.PAGES_BARRE:
        cle = ("menu_" if p in ui.PAGES_MENU else "nav_") + p
        if not any(b.key == cle for b in at.button):                 # le menu est refermé après un choix : on le rouvre
            at.button(key="nav_burger").click().run()
        at.button(key=cle).click().run()
        assert not at.exception, (p, [str(x.value) for x in at.exception])
        assert at.session_state["page"] == p and at.session_state["menu_ouvert"] is False, p


def test_boutons_accueil_ouvrent_les_pages():
    at = _apptest().run()
    at.button(key="hero_importer").click().run()
    assert at.session_state["page"] == "transformation"
    at.button(key="nav_accueil").click().run()
    at.button(key="ouvrir_fiscal").click().run()
    assert at.session_state["page"] == "fiscal"


def test_feuille_de_style_palette_et_pages():
    """La feuille de style : palette demandée, aucun dégradé vert-orange, un seul conteneur visible à la fois."""
    import re
    import interface_maroifrs as ui
    css = ui.CSS.lower()
    for couleur in ("#4caf50", "#e65100", "#b94a48", "#edf6ee", "#ffffff"):          # vert et orange demandés + blanc
        assert couleur in css, couleur
    for ancien in ("#91bd70", "#e8892b"):
        assert ancien not in css, ancien                                                # l'ancienne charte a disparu
    for degrade in re.findall(r"linear-gradient\([^)]*\)", css):
        assert not ("#e65100" in degrade and "#4caf50" in degrade), degrade
    masque = ui.css_pages("resultats")
    assert "page_resultats" not in masque and all(("page_" + p) in masque for p in ui.PAGES if p != "resultats")
    assert set(ui.PAGES_BARRE + ui.PAGES_MENU) == set(ui.PAGES)
