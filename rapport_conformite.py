"""Rapport final de conformité : PDF (reportlab) et Excel (openpyxl), sans dépendance Streamlit.

Contenu (une section par exigence) : documents identifiés, données extraites, contrôles CGNC (dont TVA), anomalies,
normes IAS/IFRS applicables, retraitements proposés, consolidation, écarts avec les comptes officiels, statut final.
"""
from __future__ import annotations

import io
from datetime import datetime
from xml.sax.saxutils import escape

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.utils import get_column_letter
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm
from reportlab.platypus import PageBreak, Paragraph, Spacer

import charte_pdf as cp
import synthese_conformite as sc
import transformation_comptes as tc

# Charte MaroIFRS (identique à l'application) : pistache #4CAF50, accent #388E3C, orange #E65100, rouge #B94A48
VERT, ORANGE, MENTHE = "#4CAF50", "#E65100", "#E8F5E9"
ENCRE = "26322B"


def statut_final(cgnc_df: pd.DataFrame | None, constats_df: pd.DataFrame | None, comparaison: dict | None = None,
                 pieces_ko: bool = False, ecritures_df: pd.DataFrame | None = None, nature: dict | None = None,
                 non_executes: list | None = None, manquantes: list | None = None) -> tuple[str, list[str]]:
    """Statut explicite et ses motifs. Règles :
       Non conforme  : au moins une erreur CGNC (équilibre, plan comptable, TVA...) ou pièces déséquilibrées ;
       Analyse partielle : le document est incomplet (factures seules, bilan sans résultat...) : aucune conclusion de conformité n'est possible ;
       Conforme avec réserves : points « à vérifier », retraitements IFRS à passer, contrôles non exécutés, informations manquantes ou écarts avec les comptes officiels ;
       Conforme : document complet, tous les contrôles applicables exécutés, aucune anomalie ni information manquante."""
    motifs = []
    if (cgnc_df is None) and (constats_df is None) and not pieces_ko and not comparaison and not nature:
        return "NON DÉTERMINÉ", ["aucun contrôle n'a pu être appliqué (aucune donnée comptable exploitable)"]
    n_err = int((cgnc_df["Niveau"] == "Erreur").sum()) if cgnc_df is not None and not cgnc_df.empty else 0
    n_ver = int((cgnc_df["Niveau"] == "À vérifier").sum()) if cgnc_df is not None and not cgnc_df.empty else 0
    n_err_ifrs = int((constats_df["Niveau"] == "Erreur").sum()) if constats_df is not None and not constats_df.empty else 0
    n_retr = int((constats_df["Niveau"] == "Retraitement").sum()) if constats_df is not None and not constats_df.empty else 0
    n_ver_ifrs = int((constats_df["Niveau"] == "À vérifier").sum()) if constats_df is not None and not constats_df.empty else 0
    if n_err:
        motifs.append(f"{n_err} erreur(s) de conformité CGNC")
    if n_err_ifrs:
        motifs.append(f"{n_err_ifrs} erreur(s) relevée(s) lors du contrôle IAS/IFRS")
    if pieces_ko:
        motifs.append("écritures déséquilibrées dans le grand livre / journal")
    if n_ver:
        motifs.append(f"{n_ver} point(s) CGNC à vérifier")
    if n_retr:
        motifs.append(f"{n_retr} retraitement(s) IFRS à passer")
    if n_ver_ifrs:
        motifs.append(f"{n_ver_ifrs} point(s) IFRS à vérifier")
    if ecritures_df is not None and not ecritures_df.empty:
        n_ecr = int((ecritures_df["Niveau"] == "À vérifier").sum())
        if n_ecr:
            motifs.append(f"{n_ecr} point(s) à vérifier sur les écritures ou les factures (dont contrôles de TVA impossibles ou incomplets)")
    if comparaison:
        if comparaison.get("statut") == "Informations à compléter":
            motifs.append("comparaison avec les comptes officiels non réalisée : informations à compléter")
        elif comparaison.get("statut") in ("Écarts significatifs", "Écarts à expliquer"):
            motifs.append("écarts avec les comptes consolidés publiés : " + comparaison["statut"].lower())
        elif comparaison.get("statut") == "Comparaison non valable":
            motifs.append("comparaison avec les comptes officiels non valable (exercice, référentiel, devise ou périmètre différents)")
    partiel = bool(nature) and nature.get("complets") is False
    if manquantes:
        motifs.append(f"{len(manquantes)} norme(s) dont le chiffrage exige des informations complémentaires")
    if non_executes:
        motifs.append(f"{len(non_executes)} contrôle(s) non exécuté(s) ou non applicable(s)")
    if n_err or n_err_ifrs or pieces_ko:
        return "NON CONFORME", motifs
    if partiel:
        return "ANALYSE PARTIELLE", ["document incomplet : " + " ".join(nature.get("raisons", []))[:220]] + motifs
    if motifs:
        return "CONFORME AVEC RÉSERVES", motifs
    return "CONFORME", ["document complet, aucune anomalie, aucune information manquante, tous les contrôles applicables exécutés"]


def _fmt(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, (int, float)):
        return f"{v:,.2f}".replace(",", " ") if abs(v) < 100 and v != int(v) else f"{v:,.0f}".replace(",", " ")
    return str(v)


def _tableau(df: pd.DataFrame, largeurs=None, taille=7.8):
    """Tableau PDF d'un DataFrame : en-têtes pistache, lignes alternées, colonnes numériques alignées à droite, niveaux colorés."""
    if df is None or df.empty:
        return None
    droite = [j for j, c in enumerate(df.columns)
              if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]
    coul = {j: cp.NIVEAUX_COULEUR for j, c in enumerate(df.columns) if str(c) == "Niveau"}
    lignes = [[_fmt(v) for v in r] for r in df.itertuples(index=False, name=None)]
    return cp.tableau_pdf([str(c) for c in df.columns], lignes, largeurs, taille, droite=droite, couleurs_colonnes=coul)


def construire_pdf(d: dict) -> bytes:
    """Rapport détaillé de conformité (PDF, A4 paysage) : page de garde, sommaire, en-têtes et pieds de page, toutes les sections. d : voir rapport_dict()."""
    buf = io.BytesIO()
    pagesize = landscape(A4)
    titre_rapport = "Rapport détaillé de conformité"
    fichier = d.get("fichier") or ""
    doc = cp.DocumentMaro(buf, pagesize=pagesize, leftMargin=1.4 * cm, rightMargin=1.4 * cm, topMargin=2.0 * cm, bottomMargin=1.6 * cm,
                          title="Rapport détaillé de conformité CGNC / IAS-IFRS", author="MaroIFRS")
    st = cp.styles(9)
    p, petit = st["p"], st["petit"]
    W = pagesize[0] - 2.8 * cm
    stat, motifs = d["statut"]
    coul = cp.STATUTS_COULEUR[stat]
    el = [Spacer(1, 1), PageBreak()] + cp.sommaire(st)          # la page de garde est dessinée par `garde` ; page 2 = sommaire
    el += [Paragraph(f'<font color="{coul}" size="15"><b>Statut final : {stat}</b></font>', p),
           Spacer(1, 3), Paragraph("Motifs : " + "; ".join(motifs) + ".", p)]
    if d.get("indicateurs"):
        el += [Spacer(1, 5), _tableau(pd.DataFrame(d["indicateurs"], columns=["Indicateur", "Valeur", "Définition"]), [W * .2, W * .1, W * .7]),
               Paragraph("Ces indicateurs sont ceux affichés dans l'application. Le verdict de conformité est le statut final ci-dessus ; "
                         "les pourcentages ne mesurent que la part des comptes sans retraitement.", petit)]

    def section(titre, df=None, texte=None, largeurs=None, vide=None, max_lignes=None):
        el.extend(cp.titre_section(titre, st))
        if texte:
            el.append(Paragraph(cp.net(texte), p))
            el.append(Spacer(1, 3))
        if df is not None and max_lignes and len(df) > max_lignes:
            el.append(Paragraph(f"Seules les {max_lignes} premières lignes sur {len(df)} sont reproduites ici ; le détail complet figure dans le fichier Excel.", petit))
            df = df.head(max_lignes)
        t = _tableau(df, largeurs)
        if t is not None:
            el.append(t)
        elif vide:
            el.append(Paragraph(vide, petit))
    section("1. Documents identifiés", d.get("documents"), vide="Aucun document reconnu.", largeurs=[W * .25, W * .3, W * .45])
    section("2. Données extraites", d.get("donnees"),
            texte=d.get("texte_donnees"), vide="Aucune donnée chiffrée extraite.", largeurs=None, max_lignes=300)
    if d.get("comptes_retraites") is not None and len(d["comptes_retraites"]):
        ct = d["comptes_retraites"]
        rp = tc.rapprochement(ct)
        section("2 bis. Comptes d'origine (CGNC) et comptes après traitement IAS/IFRS", ct[["Compte", "Libellé", tc.COLONNES[2], "Statut", "Traitement appliqué",
                                                                                          tc.COLONNES[5], "Écart (MAD)", "Norme", "Chiffré dans le comparatif"]],
                texte=(f"{rp['comptes']} compte(s). Total des soldes d'origine {rp['avant']:,.0f} MAD ; après traitement {rp['apres']:,.0f} MAD ; "
                       f"écart chiffré {rp['ecart']:+,.0f} MAD. Répartition : " + ", ".join(f"{k} {v}" for k, v in rp["par_statut"].items())
                       + ". Seuls les comptes « éliminés » voient leur montant modifié ; les autres statuts signalent un traitement à passer "
                         "(la justification de chaque compte figure dans le fichier Excel).").replace(",", " "),
                largeurs=[W * .06, W * .17, W * .1, W * .07, W * .24, W * .1, W * .08, W * .06, W * .12], max_lignes=400)
    section("3. Contrôles de conformité CGNC (dont TVA)", d.get("cgnc"), texte=d.get("texte_cgnc"),
            vide="Aucune anomalie CGNC relevée.", largeurs=[W * .1, W * .09, W * .08, W * .5, W * .23])
    if d.get("ecritures") is not None and len(d["ecritures"]):
        section("3 bis. Contrôle des écritures (grand livre / journaux)", d["ecritures"], largeurs=[W * .2, W * .1, W * .7])
    el.append(PageBreak())
    section("4. Normes IAS/IFRS applicables, anomalies et retraitements proposés", d.get("ifrs"), texte=d.get("texte_ifrs"),
            vide="Aucun constat IAS/IFRS.", largeurs=[W * .13, W * .1, W * .4, W * .37])
    if d.get("normes_non_appliquees"):
        el.append(Paragraph("Normes non appliquées (le document ne contient pas les éléments concernés) : " + escape(cp.net(d["normes_non_appliquees"])), petit))
    q = d.get("qualite")
    if q:
        section("4 bis. Nature des résultats et qualité des données", q["composantes"],
                texte=((("RÉSULTATS PARTIELS. " + " ".join(d["nature"]["raisons"]) + " ") if d.get("nature") and not d["nature"].get("complets") else "Résultats issus d'un document complet. ")
                       + f"Indicateur de qualité des données : {q['score']}/100 ({q['niveau']}). " + q["avertissement"]),
                largeurs=[W * .24, W * .07, W * .09, W * .6])
    if d.get("traitements") is not None:
        tr = d["traitements"]
        section("4 ter. Traitement par norme (état réel de chaque contrôle)", tr[["Norme", "Statut du contrôle", "Poste ou compte concerné", "Valeur comptable initiale (MAD)",
                                                                              "Ajustement proposé", "Règle appliquée", "Justification du retraitement", "Référence de la norme",
                                                                              "Informations nécessaires"]],
                texte="Chaque ligne indique si le contrôle a réellement été exécuté, la règle appliquée et sa justification. « Détection sur les comptes » signifie que la règle repère le sujet : "
                      "le retraitement chiffré exige les informations indiquées. Les normes du catalogue sans règle automatique ne sont pas listées ici.",
                largeurs=[W * .06, W * .12, W * .07, W * .08, W * .13, W * .17, W * .16, W * .08, W * .13], max_lignes=60)
    if d.get("comparatif") is not None:
        section("4 quater. Comparatif des états avant (CGNC) et après (IFRS) retraitement", d["comparatif"], largeurs=[W * .07, W * .26, W * .11, W * .11, W * .1, W * .35],
                texte="Seuls les retraitements que l'application chiffre réellement (non-valeurs, provisions réglementées, impôts différés associés) figurent ici.")
    if d.get("manquantes") or d.get("non_executes"):
        el.extend(cp.titre_section("4 quinquies. Informations manquantes et contrôles non exécutés", st))
        for x in d.get("manquantes") or []:
            el.append(Paragraph("- Information manquante : " + escape(cp.net(x)), petit))
        for x in d.get("non_executes") or []:
            el.append(Paragraph("- Contrôle non exécuté ou non applicable : " + escape(cp.net(x)), petit))
    if d.get("fiscal_parametres") is not None:
        section("4 sexies. Contrôle fiscal : paramètres appliqués", d["fiscal_parametres"], largeurs=[W * .27, W * .1, W * .26, W * .17, W * .2],
                texte=d.get("texte_fiscal"))
    section("5. Comptes consolidés", None, texte=d.get("texte_conso") or "Aucun passage aux comptes consolidés n'a été demandé : "
            "il exige les comptes de la société mère et de chaque filiale, le périmètre et les éliminations.")
    for nom in ("actif", "passif", "cpc"):
        if d.get(f"conso_{nom}") is not None:
            el.append(Paragraph({"actif": "Bilan consolidé : actif", "passif": "Bilan consolidé : passif", "cpc": "Compte de résultat consolidé"}[nom]
                                + " (milliers de MAD)", st["h2"]))
            el.append(_tableau(d[f"conso_{nom}"][["Poste", "Montant"]], [W * .6, W * .15]))
    if d.get("conso_perimetre") is not None and len(d["conso_perimetre"]):
        el.append(Paragraph("Périmètre de consolidation", st["h2"]))
        el.append(_tableau(d["conso_perimetre"]))
    if d.get("conso_notes"):
        for n in d["conso_notes"]:
            el.append(Paragraph("- " + escape(cp.net(n)), petit))
    section("6. Comparaison avec les comptes consolidés publiés", d.get("comparaison_tableau"), texte=d.get("texte_comparaison")
            or "Aucune comparaison avec des comptes officiels n'a été effectuée.", largeurs=[W * .14, W * .11, W * .11, W * .1, W * .07, W * .12, W * .35])
    el.append(Spacer(1, 10))
    el.append(Paragraph("Limites : ce rapport résulte de règles explicites appliquées aux données importées ; il ne remplace ni l'audit des comptes ni "
                        "l'avis d'un expert-comptable. Les résumés des normes IFRS sont pédagogiques ; le texte officiel est publié par l'IFRS Foundation. "
                        "Aucun résultat n'a été modifié pour correspondre à des chiffres officiels.", petit))

    def garde(c, _d):
        cp.page_de_garde(c, pagesize[0], pagesize[1], ["Rapport détaillé", "de conformité"], "Conversion du CGNC vers les normes IAS / IFRS",
                         [("Fichier analysé", fichier or "—"), ("Date d'édition", cp.date_edition()), ("Contenu", "Contrôles CGNC, normes IAS/IFRS, retraitements, fiscalité")],
                         ["Document généré par MaroIFRS. Outil d'aide à la décision : il ne remplace ni l'audit des comptes ni l'avis d'un expert-comptable."],
                         statut=(stat, coul))
    doc.multiBuild(el, canvasmaker=cp.fabrique_canvas(titre_rapport, fichier, avec_garde=True), onFirstPage=garde, onLaterPages=lambda c, _d: None)
    return buf.getvalue()


def _page(ws, ligne_entete=None):
    """Mise en page d'impression commune : paysage, une page de large, en-têtes répétés, pied de page numéroté."""
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.oddFooter.center.text = "MaroIFRS · Page &P sur &N"
    if ligne_entete:
        ws.print_title_rows = f"{ligne_entete}:{ligne_entete}"


def _feuille(wb, titre, df, largeurs=None, note=None):
    ws = wb.create_sheet(titre[:31])
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = VERT.lstrip("#")
    fin = Side(style="thin", color="DCE3D5")
    cadre = Border(left=fin, right=fin, top=fin, bottom=fin)
    ligne = 1
    if note:
        ws.cell(row=1, column=1, value=note).alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(row=1, column=1).font = Font(name="Calibri", size=10, italic=True, color="5E6C63")
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(4, len(df.columns) if df is not None else 4))
        ws.row_dimensions[1].height = 45
        ligne = 3
    if df is None or df.empty:
        ws.cell(row=ligne, column=1, value="Aucune donnée")
        _page(ws)
        return
    for j, c in enumerate(df.columns, 1):
        x = ws.cell(row=ligne, column=j, value=str(c))
        x.font = Font(name="Calibri", size=11, bold=True, color=ENCRE)
        x.fill = PatternFill("solid", fgColor=VERT.lstrip("#"))
        x.border = Border(left=fin, right=fin, top=fin, bottom=Side(style="medium", color="388E3C"))
        x.alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[ligne].height = 24
    for i, (_, r) in enumerate(df.iterrows(), ligne + 1):
        for j, v in enumerate(r, 1):
            if isinstance(v, float) and pd.isna(v):
                v = None
            if hasattr(v, "item"):
                v = v.item()
            x = ws.cell(row=i, column=j, value=v)
            x.font = Font(name="Calibri", size=10.5, color=ENCRE)
            x.border = cadre
            if (i - ligne) % 2 == 0:
                x.fill = PatternFill("solid", fgColor="F1F8F2")
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                x.number_format = "#,##0.00" if isinstance(v, float) and abs(v) < 100 else "#,##0"
                x.alignment = Alignment(horizontal="right", vertical="top")
            else:
                x.alignment = Alignment(wrap_text=True, vertical="top")
    for j, c in enumerate(df.columns, 1):
        long = max([len(str(c))] + [len(str(v)) for v in df.iloc[:, j - 1].head(200)])
        ws.column_dimensions[get_column_letter(j)].width = min(max(12, long + 2), 70)
    ws.freeze_panes = ws.cell(row=ligne + 1, column=1)
    ws.auto_filter.ref = f"A{ligne}:{get_column_letter(len(df.columns))}{ligne + len(df)}"
    _page(ws, ligne)


def construire_excel(d: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Synthèse"
    stat, motifs = d["statut"]
    ws["A1"], ws["A1"].font = "MaroIFRS · Rapport détaillé de conformité : CGNC → IAS / IFRS", Font(name="Calibri", bold=True, size=18, color=ENCRE)
    ws.row_dimensions[1].height = 30
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = VERT.lstrip("#")
    for col in "ABCDE":
        ws[f"{col}2"].border = Border(bottom=Side(style="medium", color="E65100"))
    ws["A3"], ws["B3"] = "Fichier", d.get("fichier") or "—"
    ws["A4"], ws["B4"] = "Date d'édition", datetime.now().strftime("%d/%m/%Y %H:%M")
    ws["A5"], ws["B5"] = "Statut final", stat
    ws["B5"].font = Font(bold=True, size=12, color={"CONFORME": "2E7D32", "CONFORME AVEC RÉSERVES": "BF360C", "NON CONFORME": "B94A48", "NON DÉTERMINÉ": "5E6C63", "ANALYSE PARTIELLE": "8A3000"}[stat])
    ws["A6"], ws["B6"] = "Motifs", "; ".join(motifs)
    for k, (lib, val, defi) in enumerate(d.get("indicateurs") or [], 9):
        ws.cell(row=k, column=1, value=lib).font = Font(bold=True, color=ENCRE)
        ws.cell(row=k, column=2, value=f"{val} : {defi}").alignment = Alignment(wrap_text=True, vertical="top")
    ws["A8"] = "Sommaire des feuilles : Documents, Données, Comptes avant-après, CGNC, Écritures, IFRS, Conso (actif, passif, CPC, périmètre, éliminations), Comparaison."
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 22, 110
    for c in ("A3", "A4", "A5", "A6"):
        ws[c].font = Font(bold=True, color=ENCRE)
        ws[c].fill = PatternFill("solid", fgColor="E8F5E9")
    _page(ws)
    ws["B6"].alignment = Alignment(wrap_text=True)
    _feuille(wb, "Documents", d.get("documents"))
    _feuille(wb, "Données", d.get("donnees"), note=d.get("texte_donnees"))
    if d.get("comptes_retraites") is not None:
        _feuille(wb, "Comptes avant-après", d["comptes_retraites"], note="Pour chaque compte : montant d'origine (CGNC), traitement IAS/IFRS, montant après traitement, écart, norme et justification. "
                 "Seuls les comptes « éliminés » voient leur montant modifié ; les autres statuts signalent un traitement à passer.")
    _feuille(wb, "CGNC", d.get("cgnc"), note=d.get("texte_cgnc"))
    if d.get("ecritures") is not None:
        _feuille(wb, "Écritures", d["ecritures"])
    _feuille(wb, "IFRS", d.get("ifrs"), note=d.get("texte_ifrs"))
    if d.get("traitements") is not None:
        _feuille(wb, "Traitement par norme", d["traitements"], note="État réel de chaque contrôle : exécuté, sans objet, non sélectionné, rappel d'information ou informations complémentaires requises.")
    if d.get("comparatif") is not None:
        _feuille(wb, "Comparatif CGNC-IFRS", d["comparatif"], note="États avant (CGNC) et après (IFRS) retraitement, limités aux retraitements chiffrés par l'application.")
    if d.get("qualite"):
        _feuille(wb, "Qualité des données", d["qualite"]["composantes"],
                 note=f"Score {d['qualite']['score']}/100 ({d['qualite']['niveau']}). " + d["qualite"]["avertissement"])
    if d.get("manquantes") or d.get("non_executes"):
        lignes = [{"Type": "Information manquante", "Détail": x} for x in d.get("manquantes") or []] + [{"Type": "Contrôle non exécuté / non applicable", "Détail": x} for x in d.get("non_executes") or []]
        _feuille(wb, "Manquants et non exécutés", pd.DataFrame(lignes))
    if d.get("fiscal_parametres") is not None:
        _feuille(wb, "Paramètres fiscaux", d["fiscal_parametres"], note=d.get("texte_fiscal"))
    if d.get("conso_actif") is not None:
        _feuille(wb, "Conso actif", d["conso_actif"])
        _feuille(wb, "Conso passif", d["conso_passif"])
        _feuille(wb, "Conso CPC", d["conso_cpc"])
        _feuille(wb, "Périmètre", d.get("conso_perimetre"), note=" ".join(d.get("conso_notes") or []))
        _feuille(wb, "Éliminations", d.get("conso_eliminations"))
    else:
        _feuille(wb, "Conso", None, note=d.get("texte_conso") or "Aucun passage aux comptes consolidés demandé.")
    _feuille(wb, "Comparaison", d.get("comparaison_tableau"), note=d.get("texte_comparaison")
             or "Aucune comparaison avec des comptes officiels n'a été effectuée.")
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def rapport_dict(fichier, documents, donnees, cgnc_df, constats_df, comparaison=None, conso=None, ecritures=None,
                 texte_donnees=None, normes_non_appliquees="", pieces_ko=False, traitements=None, comparatif=None, qualite=None,
                 nature=None, manquantes=None, non_executes=None, fiscal_parametres=None, texte_fiscal=None,
                 comptes_retraites=None, indicateurs=None) -> dict:
    """Assemble le dictionnaire commun au PDF et à l'Excel."""
    d = {"fichier": fichier, "documents": pd.DataFrame(documents) if documents else None, "donnees": donnees,
         "texte_donnees": texte_donnees, "cgnc": cgnc_df if cgnc_df is not None else None, "ifrs": constats_df,
         "ecritures": ecritures, "normes_non_appliquees": normes_non_appliquees}
    d.update({"traitements": traitements, "comparatif": comparatif, "qualite": qualite, "nature": nature, "manquantes": manquantes or [],
              "non_executes": non_executes or [], "fiscal_parametres": fiscal_parametres, "texte_fiscal": texte_fiscal,
              "comptes_retraites": comptes_retraites, "indicateurs": indicateurs})
    d["statut"] = statut_final(cgnc_df, constats_df, comparaison, pieces_ko, ecritures, nature, non_executes, manquantes)
    if cgnc_df is not None and not cgnc_df.empty:
        d["texte_cgnc"] = (f"{int((cgnc_df['Niveau'] == 'Erreur').sum())} erreur(s), {int((cgnc_df['Niveau'] == 'À vérifier').sum())} point(s) à vérifier, "
                           f"{int((cgnc_df['Niveau'] == 'Info').sum())} information(s).")
    if constats_df is not None and not constats_df.empty:
        d["texte_ifrs"] = (f"{int((constats_df['Niveau'] == 'Retraitement').sum())} retraitement(s) proposé(s), "
                           f"{int((constats_df['Niveau'] == 'Erreur').sum())} erreur(s), {int((constats_df['Niveau'] == 'À vérifier').sum())} point(s) à vérifier.")
    if conso:
        d.update({"conso_actif": conso["actif"], "conso_passif": conso["passif"], "conso_cpc": conso["cpc"],
                  "conso_perimetre": conso.get("perimetre"), "conso_eliminations": conso.get("eliminations"),
                  "conso_notes": (["DONNÉES MANQUANTES : " + x for x in conso.get("donnees_manquantes", [])]
                                  + ["POINT À VÉRIFIER : " + x for x in conso.get("verifications", [])] + list(conso.get("notes") or [])),
                  "texte_conso": f"Référentiel : {conso.get('referentiel', 'IFRS')}. Total actif {conso['total_actif'] / 1000:,.0f} k MAD, "
                                 f"total passif {conso['total_passif'] / 1000:,.0f} k MAD, résultat net part du groupe {conso['resultat_groupe'] / 1000:,.0f} k MAD.".replace(",", " ")})
    if comparaison:
        d["comparaison_tableau"] = comparaison["tableau"]
        d["texte_comparaison"] = "Statut : " + comparaison["statut"] + (". " + " ".join(comparaison["problemes"]) if comparaison["problemes"] else "") \
            + ". Aucun résultat calculé n'a été ajusté."
    return d
