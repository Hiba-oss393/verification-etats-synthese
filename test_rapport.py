import io

import openpyxl
import pandas as pd
import pdfplumber

import _chemins  # noqa: F401
import comparaison_officielle as co
import rapport_conformite as rc
from _donnees import entites_base
from consolidation_groupe import consolider

CG = pd.DataFrame([{"Contrôle": "TVA", "Niveau": "À vérifier", "Compte": "4455", "Constat": "x", "Référence CGNC": "y"}])
IF = pd.DataFrame([{"Norme": "IAS 1", "Niveau": "Retraitement", "Constat": "non-valeurs", "Retraitement suggéré": "charges"}])


def test_regles_du_statut_final():
    assert rc.statut_final(None, None)[0] == "NON DÉTERMINÉ"                                       # rien d'analysé : jamais « conforme »
    vide = pd.DataFrame(columns=["Niveau"])
    assert rc.statut_final(vide, vide)[0] == "CONFORME"
    assert rc.statut_final(pd.DataFrame([{"Niveau": "Erreur"}]), vide)[0] == "NON CONFORME"
    assert rc.statut_final(vide, vide, pieces_ko=True)[0] == "NON CONFORME"
    assert rc.statut_final(pd.DataFrame([{"Niveau": "À vérifier"}]), vide)[0] == "CONFORME AVEC RÉSERVES"
    assert rc.statut_final(vide, pd.DataFrame([{"Niveau": "Retraitement"}]))[0] == "CONFORME AVEC RÉSERVES"
    ecr = pd.DataFrame([{"Contrôle": "TVA", "Niveau": "À vérifier", "Détail": "Contrôle impossible"}])
    s, motifs = rc.statut_final(vide, vide, ecritures_df=ecr)
    assert s == "CONFORME AVEC RÉSERVES" and any("TVA" in m for m in motifs)
    incomplet = {"statut": "Informations à compléter", "problemes": [], "tableau": pd.DataFrame()}
    s, motifs = rc.statut_final(vide, vide, incomplet)
    assert s == "CONFORME AVEC RÉSERVES" and any("informations à compléter" in m for m in motifs)


def test_exports_pdf_et_excel_complets():
    conso = consolider(entites_base(), None, "IFRS", 0.20)
    meta = {"Exercice": "2025", "Référentiel": "IFRS", "Périmètre": "Consolidé (groupe)", "Unité": "MAD", "Devise": "MAD"}
    cmp_ = co.comparer({"total_actif": conso["total_actif"], "total_passif": conso["total_passif"], "ca": conso["ca"], "resultat_net": conso["resultat_groupe"]},
                       {"total_actif": 1450, "total_passif": 1450, "ca": 550, "resultat_net": 180}, meta, dict(meta, Unité="Milliers de MAD"))
    assert cmp_["statut"] == "Concordance avec les comptes officiels"
    d = rc.rapport_dict("balance.xlsx", [{"Document": "Balance", "Lecture": "lue", "Traitement": "CGNC"}],
                        pd.DataFrame([{"Compte": "1111", "Libellé": "Capital", "Solde (MAD)": -1000}]), CG, IF, cmp_, conso,
                        ecritures=pd.DataFrame([{"Contrôle": "TVA", "Niveau": "À vérifier", "Détail": "Contrôle impossible"}]),
                        normes_non_appliquees="IAS 2, IFRS 15")
    pdf, xls = rc.construire_pdf(d), rc.construire_excel(d)
    assert pdf[:5] == b"%PDF-" and xls[:2] == b"PK"
    with pdfplumber.open(io.BytesIO(pdf)) as p:
        texte = "\n".join(pg.extract_text() or "" for pg in p.pages)
        assert len(p.pages) >= 3
    for mot in ("Statut final : CONFORME AVEC RÉSERVES", "Documents identifiés", "Contrôles de conformité CGNC", "Normes IAS/IFRS applicables",
                "Comptes consolidés", "Périmètre de consolidation", "Comparaison avec les comptes consolidés publiés", "IAS 2, IFRS 15", "Aucun résultat"):
        assert mot in texte, mot
    wb = openpyxl.load_workbook(io.BytesIO(xls))
    for feuille in ("Synthèse", "Documents", "Données", "CGNC", "Écritures", "IFRS", "Conso actif", "Conso passif", "Conso CPC", "Périmètre", "Éliminations", "Comparaison"):
        assert feuille in wb.sheetnames, feuille
    assert wb["Synthèse"]["B5"].value == "CONFORME AVEC RÉSERVES"
    cp = wb["Conso CPC"]
    valeurs = {cp.cell(row=i, column=1).value: cp.cell(row=i, column=2).value for i in range(1, cp.max_row + 1)}
    assert valeurs["Résultat net part du groupe"] == 180
    assert any("DONNÉES MANQUANTES" in str(c.value) for c in wb["Périmètre"][1])        # données manquantes reprises dans l'export


def test_exports_sans_consolidation_ni_comparaison():
    d = rc.rapport_dict("t.xlsx", None, None, None, None)
    assert d["statut"][0] == "NON DÉTERMINÉ"
    assert rc.construire_pdf(d)[:5] == b"%PDF-" and rc.construire_excel(d)[:2] == b"PK"
