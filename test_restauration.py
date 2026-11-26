"""Restauration et corrections : cohérence écran / PDF, rapports avec les résultats réels, comptes avant-après, charte de couleurs.
Les contrôles portent sur des valeurs calculées à la main, pas seulement sur l'absence d'erreur."""
import io
import re
import subprocess
import tempfile
from pathlib import Path

import openpyxl
import pandas as pd

import _chemins  # noqa: F401
import charte_pdf as cp
import harness_streamlit as hs
import modeles_excel as me
import rapport_conformite as rc
import synthese_conformite as sc
import transformation_comptes as tcomp
from test_interface import BALANCE_TVA, _equilibrer, _xlsx


def _texte_pdf(octets):
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(octets)
    try:
        out = subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, text=True, check=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        import pdfplumber
        with pdfplumber.open(f.name) as pdf:
            out = "\n".join(p.extract_text() or "" for p in pdf.pages)
    return re.sub(r"\s+", " ", out)


# Balance dont TOUS les comptes sont « inchangés » (aucune règle de norme) mais déséquilibrée de 100 000 MAD : erreur CGNC certaine.
BALANCE_DESEQUILIBREE = pd.DataFrame([("1111", "Capital social", 0, 1_000_000), ("5141", "Banque", 900_000, 0)],
                                     columns=["compte", "libelle", "debit", "credit"])


def test_plus_de_100_pour_cent_conforme_a_l_ecran_quand_le_pdf_dit_non_conforme():
    """Le défaut signalé : 100 % affiché « conformité IFRS » à l'écran, NON CONFORME dans le PDF."""
    st, g, err = hs.run({"uploads": {"main": [hs.FakeFile("desequilibree.xlsx", _xlsx(BALANCE_DESEQUILIBREE))]}})
    assert err is None, err
    assert g["conf_n"] == 100.0                                                   # tous les comptes sont « inchangés » : le piège existe bien
    stat_pdf, motifs = g["rap"]["statut"]
    assert stat_pdf == "NON CONFORME" and any("erreur" in m for m in motifs)
    affiches = hs.statuts_affiches(st)
    assert affiches and set(affiches) == {"NON CONFORME"}, affiches               # l'écran affiche le même statut que le PDF, sur chaque page
    textes = " ".join(st.log["textes"])
    assert "Conformité IFRS" not in textes                                        # l'ancien intitulé trompeur a disparu
    assert sc.ETIQUETTE_COMPTES in textes
    pdf = _texte_pdf(rc.construire_pdf(g["rap"]))
    assert "Statut final : NON CONFORME" in pdf
    assert sc.ETIQUETTE_COMPTES in pdf and "100 %" in pdf                         # le PDF présente le même indicateur, sous le même nom, avec sa définition
    assert "pas un taux de conformité" in pdf or "ne mesurent que la part des comptes" in pdf


def test_statut_identique_ecran_pdf_synthetique_et_excel():
    st, g, err = hs.run({"uploads": {"main": [hs.FakeFile("balance.xlsx", _xlsx(_equilibrer(BALANCE_TVA)))]}})
    assert err is None, err
    stat, motifs = g["rap"]["statut"]
    assert set(hs.statuts_affiches(st)) == {stat}
    syn = _texte_pdf(g["rapport_pdf"](g["df"], g["tous"], g["etat"], g["res_statuts"], g["conf_n"], g["conf_v"], None, None, None,
                                      "balance.xlsx", None, g["rap"]["statut"]))
    assert f"Statut final : {stat}" in syn                                        # rapport synthétique
    det = _texte_pdf(rc.construire_pdf(g["rap"]))
    assert f"Statut final : {stat}" in det                                        # rapport détaillé
    assert all(x in det for x in (f"{g['conf_n']:.0f} %", f"{g['conf_v']:.0f} %"))
    x = g["excel_formate"](g["df"], g["tous"], g["etat"], g["res_statuts"], g["conf_n"], g["conf_v"], None, None, None, "balance.xlsx", None, g["rap"]["statut"])
    ws = openpyxl.load_workbook(io.BytesIO(x))["Synthèse"]
    assert any(f"Statut final : {stat}" in str(c.value) for row in ws.iter_rows() for c in row if c.value)
    libelles = [str(c.value) for row in ws.iter_rows() for c in row if c.value]
    assert sc.ETIQUETTE_COMPTES in libelles and "Conformité (comptes)" not in libelles


def test_comptes_avant_apres_exemple_chiffre_a_la_main():
    """211 Frais préliminaires 50 000 (débit), 2811 amortissement 20 000 (crédit) : non-valeur nette 30 000 éliminée en IFRS (IAS 38)."""
    bal = pd.DataFrame([("1111", "Capital social", 0, 100_000), ("2111", "Frais préliminaires", 50_000, 0), ("2811", "Amortissement des frais préliminaires", 0, 20_000),
                        ("5141", "Banque", 70_000, 0)], columns=["compte", "libelle", "debit", "credit"])
    st, g, err = hs.run({"uploads": {"main": [hs.FakeFile("b.xlsx", _xlsx(bal))]}})
    assert err is None, err
    tab = tcomp.comptes_retraites(g["res_statuts"]).set_index("Compte")
    assert tab.loc["2111", "Statut"] == "éliminé" and tab.loc["2811", "Statut"] == "éliminé"
    assert tab.loc["2111", "Solde d'origine CGNC (MAD)"] == 50_000 and tab.loc["2111", "Solde après traitement IFRS (MAD)"] == 0
    assert tab.loc["2811", "Écart (MAD)"] == 20_000                               # le crédit de 20 000 est éliminé (solde -20 000 -> 0)
    assert tab.loc["5141", "Statut"] == "inchangé" and tab.loc["5141", "Écart (MAD)"] == 0
    rp = tcomp.rapprochement(tab.reset_index())
    assert rp["ecart"] == -30_000                                                  # = non-valeur nette
    cmp_ = g["comparatif"].set_index("Poste")
    assert cmp_.loc["Immobilisations en non-valeur", "Écart (MAD)"] == -30_000      # même montant que le comparatif agrégé
    assert tab.loc["2111", "Chiffré dans le comparatif"] == "Oui"


def test_rapport_detaille_contient_comptes_origine_ajustements_ecarts_normes():
    bal = pd.DataFrame([("1111", "Capital social", 0, 100_000), ("2111", "Frais préliminaires", 50_000, 0), ("2811", "Amortissement frais préliminaires", 0, 20_000),
                        ("5141", "Banque", 70_000, 0)], columns=["compte", "libelle", "debit", "credit"])
    st, g, err = hs.run({"uploads": {"main": [hs.FakeFile("b.xlsx", _xlsx(bal))]}})
    assert err is None, err
    rap = g["rap"]
    pdf = _texte_pdf(rc.construire_pdf(rap))
    for attendu in ("Comptes d'origine (CGNC) et comptes après traitement IAS/IFRS", "Frais préliminaires", "50 000", "Éliminé", "IAS 38",
                    "Règle appliquée", "Justification du retraitement", "Comparatif des états avant (CGNC) et après (IFRS)", "Immobilisations en non-valeur",
                    "Contrôles de conformité CGNC"):
        assert attendu in pdf, attendu
    wb = openpyxl.load_workbook(io.BytesIO(rc.construire_excel(rap)))
    assert "Comptes avant-après" in wb.sheetnames and "Comparatif CGNC-IFRS" in wb.sheetnames and "Traitement par norme" in wb.sheetnames
    ws = wb["Comptes avant-après"]
    lignes = {str(r[0]): r for r in ws.iter_rows(min_row=1, values_only=True) if r[0]}
    assert lignes["2111"][2] == 50_000 and lignes["2111"][5] == 0 and lignes["2111"][6] == -50_000


def test_charte_de_couleurs_demandee_partout():
    assert cp.PISTACHE.lower() == "#4caf50" and cp.ORANGE.lower() == "#e65100"
    assert me.PISTACHE == "4CAF50" and me.ORANGE == "E65100"
    assert rc.VERT.lower() == "#4caf50" and rc.ORANGE.lower() == "#e65100"
    racine = Path(__file__).resolve().parent.parent
    for f in ("config.toml", ".streamlit/config.toml"):
        t = (racine / f).read_text(encoding="utf-8").lower()
        assert 'primarycolor = "#e65100"' in t
    for f in ("app.py", "interface_maroifrs.py", "charte_pdf.py", "modeles_excel.py", "rapport_conformite.py"):
        t = (racine / f).read_text(encoding="utf-8").lower()
        assert "91bd70" not in t and "e8892b" not in t, f                         # plus aucune trace de l'ancienne charte


def test_indicateurs_partages_ecran_rapports():
    ind = sc.indicateurs(87.5, 91.2, 40)
    assert ind[1][0] == sc.ETIQUETTE_COMPTES and ind[1][1] == "88 %" and ind[2][1] == "91 %"
    h = sc.bandeau_statut_html("NON CONFORME", ["1 erreur(s) de conformité CGNC"])
    assert "NON CONFORME" in h and 'class="statut-final"' in h
