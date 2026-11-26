"""Modèle Excel de balance (réimporté dans l'importateur), importateur, rapports PDF, charte, performances de navigation."""
import io
import re

import openpyxl
import pandas as pd
import pdfplumber

import _chemins  # noqa: F401
import charte_pdf as cp
import harness_streamlit as hs
import interface_maroifrs as ui
import modeles_excel as me
import rapport_conformite as rc
from _chemins import RACINE
from test_interface import BALANCE_TVA, _equilibrer, _xlsx


def _fic(octets, nom):
    f = io.BytesIO(octets)
    f.name = nom
    return f


def _app():
    st, g, err = hs.run({})
    assert err is None, err
    return g


def test_modele_balance_structure():
    wb = openpyxl.load_workbook(io.BytesIO(me.modele_balance()))
    assert wb.sheetnames == ["Balance comptable", "Instructions"]
    ws = wb["Balance comptable"]
    assert ws["A1"].value == "Balance comptable"
    assert [ws.cell(row=me.LIGNE_ENTETES, column=j).value for j in range(1, 5)] == ["Code compte", "Intitulé du compte", "Débit", "Crédit"]
    assert ws.auto_filter.ref.startswith(f"A{me.LIGNE_ENTETES}:D") and ws.freeze_panes == f"A{me.LIGNE_ENTETES + 1}"
    # aucune cellule fusionnée, aucune ligne décorative dans la zone de données (à partir de la ligne d'en-têtes)
    assert all(r.min_row < me.LIGNE_ENTETES for r in ws.merged_cells.ranges)
    assert ws["C9"].value.startswith("=SUM(") and ws["D9"].value.startswith("=SUM(")          # totaux au-dessus du tableau, en formules
    codes = [ws.cell(row=r, column=1).value for r in range(me.LIGNE_ENTETES + 1, me.LIGNE_ENTETES + 20)]
    assert codes[0] == "1111" and all(c and re.fullmatch(r"\d{4,5}", c) for c in codes)
    ins = wb["Instructions"]
    texte = " ".join(str(c.value) for ligne in ins.iter_rows() for c in ligne if c.value)
    for mot in ("Code compte", "Intitulé du compte", "Débit", "Crédit", "Règles de saisie", "Exemple de remplissage", "Formats acceptés"):
        assert mot in texte, mot


def test_modele_balance_se_reimporte_a_l_identique():
    g = _app()
    df = g["charger_balance"](_fic(me.modele_balance(), "modele.xlsx"))
    ref = g["exemple_balance"]()
    assert len(df) == len(ref) == 19 and abs(df["debit"].sum() - df["credit"].sum()) < 0.01
    a = df[["compte", "libelle", "debit", "credit"]].reset_index(drop=True).astype({"debit": float, "credit": float})
    assert a.equals(ref.reset_index(drop=True).astype({"debit": float, "credit": float}))


def test_importateur_variantes_de_fichiers():
    g = _app()
    charger = g["charger_balance"]
    mes = [("1111", "Capital", None, 500000), ("5141", "Banque", 500000, None), ("6111", "Achats", 1000, None), ("7111", "Ventes", None, 1000)]
    # modèle rempli par l'utilisateur (exemples supprimés)
    wb = openpyxl.load_workbook(io.BytesIO(me.modele_balance()))
    ws = wb["Balance comptable"]
    for r in range(me.LIGNE_ENTETES + 1, me.LIGNE_ENTETES + 20):
        for c in range(1, 5):
            ws.cell(r, c).value = None
    for i, ligne in enumerate(mes):
        for j, v in enumerate(ligne, 1):
            ws.cell(me.LIGNE_ENTETES + 1 + i, j).value = v
    o = io.BytesIO()
    wb.save(o)
    d = charger(_fic(o.getvalue(), "rempli.xlsx"))
    assert d[["compte", "debit", "credit"]].values.tolist() == [["1111", 0.0, 500000.0], ["5141", 500000.0, 0.0], ["6111", 1000.0, 0.0], ["7111", 0.0, 1000.0]]
    # ancien format (en-têtes minuscules) et en-têtes du modèle en première ligne
    for cols in (["compte", "libelle", "debit", "credit"], ["Code compte", "Intitulé du compte", "Débit", "Crédit"]):
        b = io.BytesIO()
        pd.DataFrame(mes, columns=cols).to_excel(b, index=False)
        assert len(charger(_fic(b.getvalue(), "x.xlsx"))) == 4
    # CSV
    assert len(charger(_fic(pd.DataFrame(mes, columns=["compte", "libelle", "debit", "credit"]).to_csv(index=False).encode(), "x.csv"))) == 4
    # feuille « Instructions » placée en premier : jamais lue comme balance
    wb = openpyxl.load_workbook(io.BytesIO(me.modele_balance()))
    wb.move_sheet("Instructions", offset=-1)
    o = io.BytesIO()
    wb.save(o)
    assert len(charger(_fic(o.getvalue(), "ordre.xlsx"))) == 19
    # fichier sans colonnes de balance : erreur comme avant (aucune balance inventée)
    b = io.BytesIO()
    pd.DataFrame({"a": [1], "b": [2]}).to_excel(b, index=False)
    try:
        charger(_fic(b.getvalue(), "bad.xlsx"))
        raise AssertionError("un fichier sans balance doit être refusé")
    except ValueError:
        pass


def test_rapports_pdf_garde_pagination_sommaire():
    st, g, err = hs.run({"uploads": {"main": [hs.FakeFile("balance.xlsx", _xlsx(_equilibrer(BALANCE_TVA)))]}})
    assert err is None, err
    syn = g["rapport_pdf"](g["df"], g["tous"], g["etat"], g["res_statuts"], g["conf_n"], g["conf_v"], None, None, None, "balance.xlsx", None)
    d = rc.rapport_dict("balance.xlsx", [{"Document": "Balance", "Lecture": "lue", "Traitement": "CGNC"}], g["df"][["compte", "libelle", "solde"]], g["cgnc_df"], g["tous"])
    det = rc.construire_pdf(d)
    for octets, titre, sommaire in ((syn, "synthétique", False), (det, "détaillé", True)):
        with pdfplumber.open(io.BytesIO(octets)) as p:
            n = len(p.pages)
            garde = p.pages[0].extract_text()
            assert titre in garde and "MaroIFRS" in garde and "Page 1 sur" not in garde        # page de garde sans pied de page
            assert (("Sommaire" in p.pages[1].extract_text()) is sommaire)
            for i in range(1, n):
                t = p.pages[i].extract_text()
                assert f"Page {i + 1} sur {n}" in t and "MaroIFRS" in t, i
    with pdfplumber.open(io.BytesIO(det)) as p:      # le rapport détaillé garde toutes ses sections
        texte = "\n".join(pg.extract_text() or "" for pg in p.pages)
    for mot in ("1. Documents identifiés", "2. Données extraites", "3. Contrôles de conformité CGNC", "4. Normes IAS/IFRS applicables", "5. Comptes consolidés",
                "6. Comparaison avec les comptes consolidés publiés", "Statut final"):
        assert mot in texte, mot


def test_exports_excel_presentation_sans_changer_les_donnees():
    st, g, err = hs.run({"uploads": {"main": [hs.FakeFile("balance.xlsx", _xlsx(_equilibrer(BALANCE_TVA)))]}})
    assert err is None, err
    x = g["excel_formate"](g["df"], g["tous"], g["etat"], g["res_statuts"], g["conf_n"], g["conf_v"], None, None, None, "balance.xlsx", None)
    wb = openpyxl.load_workbook(io.BytesIO(x))
    ws = wb["Statut des comptes"]
    assert ws["A4"].fill.fgColor.rgb.endswith("4CAF50") and ws.freeze_panes == "A5" and ws.auto_filter.ref
    valeurs = [ws.cell(row=r, column=3).value for r in range(5, 5 + len(g["res_statuts"]))]
    assert valeurs == [float(v) for v in g["res_statuts"]["solde"]]               # mêmes soldes que les résultats calculés


def test_logo_symbole_et_nom_sur_le_meme_axe():
    # géométrie de la maquette : centre du symbole (y = 22) = centre des capitales du nom (sans slogan) ou du bloc nom + slogan (avec slogan)
    assert abs(ui._LIGNE_NOM_SEUL - ui._CAPS * ui._CORPS_NOM / 2 - 22) < 1e-9
    haut_nom = ui._LIGNE_NOM_DUO - ui._CAPS * ui._CORPS_NOM
    assert abs((haut_nom + ui._LIGNE_SLOGAN) / 2 - 22) < 1e-9
    svg = ui.logo_complet_svg(44, True)
    assert svg.count("<svg") == 2 and "MaroIFRS" in svg and "#4CAF50" in svg
    assert "lg-full" in ui.logo_barre() and "lg-compact" in ui.logo_barre()


def test_animations_courtes_et_survol_immediat():
    css = ui.CSS
    anim = set(re.findall(r"@keyframes (\w+)", css))
    assert {"fadeIn", "riseIn", "slideRight", "growX", "growY"} <= anim                          # animations d'apparition rétablies (légères)
    for k in anim:                                                                              # uniquement opacité / transformation / tracé : rien qui force la mise en page
        corps = re.search(r"@keyframes %s \{(.*?)\}\s*(?:\n|$)" % k, css, re.S).group(1)
        assert not re.search(r"\b(width|height|top|left|margin|padding)\s*:", corps), k
    decoratives = ("pulseDot", "drawLine", "tkScroll")                                          # petits points, courbe de l'illustration et bandeau défilant (boucle continue volontairement lente)
    durees = []
    for m in re.finditer(r"(?:animation|transition)\s*:\s*([^;}]*)", css):
        if any(d in m.group(1) for d in decoratives):
            continue
        durees += [float(d) for d in re.findall(r"(?<![\w.])(\d*\.\d+|\d+)s\b", m.group(1))]
    assert durees and max(durees) <= 0.55, max(durees)                                          # apparition : 0,55 s au plus (barres) ; cartes 0,32 s
    assert "backdrop-filter" not in css                                                        # coûteux à l'affichage
    for regle in re.findall(r"[^}]*:hover[^{]*\{[^}]*\}", css):
        for d in re.findall(r"transition:[^;]*?(\d*\.?\d+)s", regle):
            assert float(d) <= 0.20, regle[:80]


def test_navigation_ne_relance_que_la_barre():
    src = (RACINE / "app.py").read_text(encoding="utf-8")
    corps = src[src.index("def barre_navigation"):src.index("barre_navigation()          #")]
    assert "ui.css_pages(actif)" in corps                                                      # le masquage des pages inactives est dans le fragment
    assert ui.NAV_FRAGMENT_SEUL is True
    assert "_nav_complet" in src and "if not ui.NAV_FRAGMENT_SEUL" in src                      # repli documenté
    assert set(ui.PAGES_BARRE) == {"accueil", "transformation", "resultats", "dashboard"}
    assert set(ui.PAGES_MENU) == {"normes", "fiscal", "calc", "conso", "historique", "apropos", "contact"}
