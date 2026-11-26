"""MaroIFRS - modèle Excel de balance comptable (aucune dépendance à Streamlit, aucune règle comptable).

Le fichier produit est lu par l'importateur de l'application (app.charger_balance) :
  * feuille 1 « Balance comptable » : titre, informations de l'entreprise, totaux de contrôle (au-dessus du tableau), puis le tableau
    dont la ligne d'en-têtes est « Code compte | Intitulé du compte | Débit | Crédit » ;
  * feuille 2 « Instructions » (ignorée par l'importateur).
La zone de données commence juste sous la ligne d'en-têtes : aucune ligne décorative, aucune ligne de total, aucune cellule fusionnée dans le tableau.
Les lignes d'exemple sont celles de la balance d'exemple de l'application (équilibrée : total débit = total crédit).
"""
from __future__ import annotations

import io
from datetime import date

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.properties import PageSetupProperties

# Charte MaroIFRS
PISTACHE, ACCENT, ORANGE, ROUGE = "4CAF50", "388E3C", "E65100", "B94A48"
FOND, BLANC, ENCRE, GRIS = "EDF6EE", "FFFFFF", "26322B", "5E6C63"
VERT_CLAIR, ORANGE_CLAIR, LIGNE = "E8F5E9", "FFE3D1", "C5D1BB"

ENTETES = ("Code compte", "Intitulé du compte", "Débit", "Crédit")
NOM_FEUILLE_BALANCE = "Balance comptable"
NOM_FEUILLE_INSTRUCTIONS = "Instructions"
LIGNE_ENTETES = 14                 # ligne des en-têtes du tableau (tout ce qui est au-dessus est informatif)
NB_LIGNES_FORMATEES = 200          # lignes prêtes à remplir (mise en forme, bordures, format monétaire)
FORMAT_MONTANT = '#,##0.00;[Red]-#,##0.00;;@'      # milliers séparés, 2 décimales, zéro masqué


def _bord(couleur=LIGNE, style="thin"):
    c = Side(style=style, color=couleur)
    return Border(left=c, right=c, top=c, bottom=c)


def _remplir(couleur):
    return PatternFill("solid", fgColor=couleur)


def lignes_exemple():
    """Exemples réalistes de comptes CGNC : ceux de la balance d'exemple de l'application (importée à la demande pour éviter un import circulaire)."""
    return [
        ("1111", "Capital social", 0, 1_000_000), ("1481", "Emprunts auprès des établissements de crédit", 0, 600_000),
        ("2111", "Frais préliminaires", 50_000, 0), ("2230", "Fonds commercial", 150_000, 0), ("2310", "Terrains", 300_000, 0),
        ("2321", "Constructions", 800_000, 0), ("2340", "Matériel de transport", 200_000, 0),
        ("2832", "Amortissements des immobilisations corporelles", 0, 950_000), ("3111", "Marchandises", 400_000, 0),
        ("3421", "Clients", 1_250_000, 0), ("4411", "Fournisseurs", 0, 700_000), ("5141", "Banque", 500_000, 0),
        ("6111", "Achats revendus de marchandises", 1_800_000, 0), ("6171", "Rémunérations du personnel", 600_000, 0),
        ("6513", "Charges non courantes", 30_000, 0), ("6701", "Impôts sur les résultats", 120_000, 0),
        ("7111", "Ventes de marchandises au Maroc", 0, 3_000_000), ("7119", "Rabais, remises et ristournes accordés", 100_000, 0),
        ("7585", "Produits non courants", 0, 50_000),
    ]


def _feuille_balance(ws, exemples):
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = PISTACHE
    for col, largeur in zip("ABCD", (18, 58, 20, 20)):
        ws.column_dimensions[col].width = largeur
    prem = LIGNE_ENTETES + 1
    der = LIGNE_ENTETES + NB_LIGNES_FORMATEES

    # ---- titre ----
    ws.merge_cells("A1:D1")
    ws["A1"] = "Balance comptable"
    ws["A1"].font = Font(name="Calibri", size=22, bold=True, color=ENCRE)
    ws["A1"].alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 38
    ws.merge_cells("A2:D2")
    ws["A2"] = "Modèle MaroIFRS · plan comptable CGNC · prêt pour l'importation dans MaroIFRS"
    ws["A2"].font = Font(name="Calibri", size=11, color=GRIS)
    for col in "ABCD":
        ws[f"{col}2"].border = Border(bottom=Side(style="medium", color=ORANGE))
    ws.row_dimensions[2].height = 22

    # ---- informations de l'entreprise (au-dessus du tableau : non lues par l'importateur) ----
    infos = [("Entreprise", "Nom ou raison sociale de l'entreprise"), ("Exercice comptable", "Exercice clos le 31/12/…"),
             ("Date d'édition", date.today()), ("Devise", "MAD (dirham marocain)")]
    for i, (lib, val) in enumerate(infos):
        r = 4 + i
        ws[f"A{r}"] = lib
        ws[f"A{r}"].font = Font(name="Calibri", size=11, bold=True, color=ENCRE)
        ws[f"A{r}"].fill = _remplir(VERT_CLAIR)
        ws[f"A{r}"].border = _bord()
        ws.merge_cells(f"B{r}:D{r}")
        c = ws[f"B{r}"]
        c.value = val
        c.font = Font(name="Calibri", size=11, color=GRIS if i != 2 else ENCRE, italic=(i != 2))
        c.alignment = Alignment(horizontal="left", vertical="center")
        for col in "BCD":
            ws[f"{col}{r}"].border = _bord()
        ws.row_dimensions[r].height = 20
    ws["B6"].number_format = "dd/mm/yyyy"

    # ---- totaux de contrôle (formules, au-dessus du tableau) ----
    ws.merge_cells("A9:B9")
    ws["A9"] = "Total (calculé automatiquement)"
    ws["A9"].font = Font(name="Calibri", size=11, bold=True, color=ENCRE)
    ws["C9"] = f"=SUM(C{prem}:C{der})"
    ws["D9"] = f"=SUM(D{prem}:D{der})"
    ws.merge_cells("A10:B10")
    ws["A10"] = "Écart débit − crédit (doit être égal à 0)"
    ws["A10"].font = Font(name="Calibri", size=11, bold=True, color=ENCRE)
    ws.merge_cells("C10:D10")
    ws["C10"] = "=ROUND(C9-D9,2)"
    for ref in ("C9", "D9", "C10"):
        ws[ref].number_format = '#,##0.00'
        ws[ref].font = Font(name="Calibri", size=11, bold=True, color=ENCRE)
        ws[ref].alignment = Alignment(horizontal="right")
    for r in (9, 10):
        for col in "ABCD":
            ws[f"{col}{r}"].fill = _remplir(FOND)
            ws[f"{col}{r}"].border = _bord()
    ws.conditional_formatting.add("C10", CellIsRule(operator="equal", formula=["0"], fill=_remplir("DCEBCF"), font=Font(bold=True, color="2E7D32")))
    ws.conditional_formatting.add("C10", CellIsRule(operator="notEqual", formula=["0"], fill=_remplir("F3DAD9"), font=Font(bold=True, color=ROUGE)))

    # ---- consigne (au-dessus du tableau) ----
    ws.merge_cells("A12:D12")
    ws["A12"] = ("Les lignes ci-dessous sont des EXEMPLES (balance équilibrée) : remplacez-les par vos comptes, un compte par ligne, "
                 "sans toucher à la ligne d'en-têtes. Voir la feuille « Instructions ».")
    ws["A12"].font = Font(name="Calibri", size=10, italic=True, color="8A3000")
    ws["A12"].fill = _remplir(ORANGE_CLAIR)
    ws["A12"].alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[12].height = 32

    # ---- en-têtes du tableau (colonnes lues par l'importateur) ----
    for j, titre in enumerate(ENTETES, 1):
        c = ws.cell(row=LIGNE_ENTETES, column=j, value=titre)
        c.font = Font(name="Calibri", size=11, bold=True, color=ENCRE)
        c.fill = _remplir(PISTACHE)
        c.alignment = Alignment(horizontal="right" if j >= 3 else "left", vertical="center", wrap_text=True)
        c.border = Border(left=Side(style="thin", color=ACCENT), right=Side(style="thin", color=ACCENT),
                          top=Side(style="thin", color=ACCENT), bottom=Side(style="medium", color=ACCENT))
    ws.row_dimensions[LIGNE_ENTETES].height = 26

    # ---- zone de données : exemples, puis lignes vides déjà mises en forme ----
    for k in range(NB_LIGNES_FORMATEES):
        r = prem + k
        zebre = _remplir("F1F8F2") if k % 2 else _remplir(BLANC)
        a, b, c, d = (ws.cell(row=r, column=j) for j in (1, 2, 3, 4))
        if k < len(exemples):
            code, lib, deb, cre = exemples[k]
            a.value, b.value = str(code), lib
            c.value = deb if deb else None
            d.value = cre if cre else None
        a.number_format = "@"                                   # code compte en texte : aucun zéro ou format perdu
        c.number_format = d.number_format = FORMAT_MONTANT
        a.alignment = Alignment(horizontal="left")
        b.alignment = Alignment(horizontal="left", wrap_text=False)
        c.alignment = d.alignment = Alignment(horizontal="right")
        for cell in (a, b, c, d):
            cell.fill = zebre
            cell.border = _bord("DCE3D5")
            cell.font = Font(name="Calibri", size=11, color=ENCRE)

    ws.auto_filter.ref = f"A{LIGNE_ENTETES}:D{der}"
    ws.freeze_panes = f"A{prem}"
    # contrôle de saisie : montants numériques positifs ou nuls
    dv = DataValidation(type="decimal", operator="greaterThanOrEqual", formula1="0", allow_blank=True, showErrorMessage=True,
                        errorTitle="Montant invalide", error="Saisissez un nombre positif ou nul (sans texte, sans symbole de devise).",
                        showInputMessage=True, promptTitle="Montant", prompt="Nombre positif. Une seule des colonnes Débit / Crédit par compte.")
    dv.add(f"C{prem}:D{der}")
    ws.add_data_validation(dv)
    # mise en évidence d'une ligne qui a un montant mais pas de code compte (elle serait ignorée à l'importation)
    ws.conditional_formatting.add(f"A{prem}:D{der}", FormulaRule(
        formula=[f'AND($A{prem}="",OR($C{prem}<>"",$D{prem}<>""))'], fill=_remplir("F3DAD9"), font=Font(color=ROUGE)))
    # impression
    ws.print_title_rows = f"{LIGNE_ENTETES}:{LIGNE_ENTETES}"
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.oddFooter.center.text = "MaroIFRS · Balance comptable · Page &P sur &N"


def _feuille_instructions(ws):
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = ORANGE
    for col, largeur in zip("ABCDE", (24, 46, 16, 30, 34)):
        ws.column_dimensions[col].width = largeur
    ws.merge_cells("A1:E1")
    ws["A1"] = "Instructions de remplissage"
    ws["A1"].font = Font(name="Calibri", size=20, bold=True, color=ENCRE)
    ws.row_dimensions[1].height = 34
    ws.merge_cells("A2:E2")
    ws["A2"] = "Comment remplir la feuille « Balance comptable » pour que MaroIFRS l'importe sans erreur."
    ws["A2"].font = Font(name="Calibri", size=11, color=GRIS)
    for col in "ABCDE":
        ws[f"{col}2"].border = Border(bottom=Side(style="medium", color=ORANGE))

    r = 4

    def titre(texte):
        nonlocal r
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
        c = ws.cell(row=r, column=1, value=texte)
        c.font = Font(name="Calibri", size=13, bold=True, color=ENCRE)
        c.fill = _remplir(VERT_CLAIR)
        c.border = Border(left=Side(style="thick", color=ORANGE))
        c.alignment = Alignment(vertical="center", indent=1)
        ws.row_dimensions[r].height = 24
        r += 1

    def tableau(entetes, lignes, hauteurs=None):
        nonlocal r
        for j, t in enumerate(entetes, 1):
            c = ws.cell(row=r, column=j, value=t)
            c.font, c.fill = Font(name="Calibri", size=11, bold=True, color=ENCRE), _remplir(PISTACHE)
            c.border, c.alignment = _bord(ACCENT), Alignment(vertical="center", wrap_text=True, horizontal="right" if t in ("Débit", "Crédit") else "left")
        r += 1
        for i, lg in enumerate(lignes):
            for j, v in enumerate(lg, 1):
                c = ws.cell(row=r, column=j, value=v)
                c.font = Font(name="Calibri", size=10.5, color=ENCRE, bold=(j == 1))
                c.fill = _remplir("F1F8F2" if i % 2 else BLANC)
                c.border = _bord("DCE3D5")
                c.alignment = Alignment(vertical="top", wrap_text=True)
                if isinstance(v, (int, float)):
                    c.number_format, c.alignment = '#,##0.00;;;@', Alignment(horizontal="right", vertical="top")
            r += 1
        r += 1

    def puces(textes):
        nonlocal r
        for t in textes:
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
            c = ws.cell(row=r, column=1, value="•  " + t)
            c.font = Font(name="Calibri", size=10.5, color=ENCRE)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            ws.row_dimensions[r].height = 14.5 * (1 + (len(t) + 3) // 125) + 3
            r += 1
        r += 1

    titre("1. Les colonnes")
    tableau(("Colonne", "Contenu attendu", "Obligatoire", "Format", "Exemple"), [
        ("Code compte", "Numéro du compte du plan comptable CGNC (classes 1 à 7). Un compte par ligne.", "Oui", "Texte ou nombre entier, sans espace", "3421"),
        ("Intitulé du compte", "Libellé du compte (texte libre).", "Oui (peut rester court)", "Texte", "Clients"),
        ("Débit", "Solde débiteur du compte à la clôture de l'exercice, en dirhams (MAD).", "Un des deux montants", "Nombre positif, 2 décimales", "1 250 000,00"),
        ("Crédit", "Solde créditeur du compte à la clôture de l'exercice, en dirhams (MAD).", "Un des deux montants", "Nombre positif, 2 décimales", "700 000,00"),
    ])
    titre("2. Règles de saisie")
    puces([
        "Une ligne = un compte. Saisissez le solde de clôture : en principe le montant va dans UNE seule des colonnes Débit ou Crédit (l'autre reste vide ou à 0). "
        "Si les deux sont remplies, MaroIFRS utilise la différence Débit − Crédit.",
        "La balance doit être équilibrée : total des débits = total des crédits. L'écart est calculé en haut de la feuille « Balance comptable » (cellule verte quand il vaut 0).",
        "Écrivez les montants comme des NOMBRES (1250000 ou 1 250 000,00 avec le format de la cellule), jamais comme du texte, sans symbole de devise ni « MAD ». "
        "Un montant écrit en texte serait lu comme 0.",
        "Ne laissez pas de montant sans code compte : la ligne serait ignorée à l'importation (elle est surlignée en rouge dans la feuille).",
        "Ne modifiez pas, ne déplacez pas et ne fusionnez pas la ligne d'en-têtes (Code compte, Intitulé du compte, Débit, Crédit). "
        "Ne placez aucune ligne de titre, de sous-total ou de total au milieu du tableau : les totaux se trouvent au-dessus.",
        "Vous pouvez insérer des lignes dans le tableau, supprimer les lignes d'exemple et trier ou filtrer grâce aux flèches des en-têtes. "
        "Les informations de l'entreprise (en haut) sont facultatives et ne sont pas lues par l'importateur.",
        "Gardez la feuille « Balance comptable » en première position du classeur (l'importateur la recherche en priorité).",
    ])
    titre("3. Exemple de remplissage")
    tableau(("Code compte", "Intitulé du compte", "Débit", "Crédit", "Commentaire"), [
        ("3421", "Clients", 1_250_000, None, "Solde débiteur : colonne Débit"),
        ("4411", "Fournisseurs", None, 700_000, "Solde créditeur : colonne Crédit"),
        ("2832", "Amortissements des immobilisations corporelles", None, 950_000, "Compte d'amortissement : colonne Crédit"),
        ("6111", "Achats revendus de marchandises", 1_800_000, None, "Charge : colonne Débit"),
        ("7111", "Ventes de marchandises au Maroc", None, 3_000_000, "Produit : colonne Crédit"),
    ])
    titre("4. Formats acceptés à l'importation")
    puces([
        "Fichiers Excel (.xlsx, .xls) et CSV. Ce modèle est un classeur .xlsx : enregistrez-le au même format, puis importez-le dans la page « Transformation ».",
        "Noms d'en-têtes reconnus (majuscules, accents et ponctuation sans importance) : Code compte / Compte / N° compte ; Intitulé du compte / Libellé / Désignation ; Débit ; Crédit.",
        "Le tableau peut commencer plus bas que la première ligne (titre et informations de l'entreprise au-dessus, comme dans ce modèle).",
        "Les contrôles de conformité (CGNC, puis IAS/IFRS) sont lancés automatiquement après l'importation ; les résultats se lisent dans la page « Résultats ».",
    ])
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)


def modele_balance() -> bytes:
    """Classeur Excel du modèle de balance (octets)."""
    wb = Workbook()
    ws = wb.active
    ws.title = NOM_FEUILLE_BALANCE
    _feuille_balance(ws, lignes_exemple())
    _feuille_instructions(wb.create_sheet(NOM_FEUILLE_INSTRUCTIONS))
    wb.active = 0
    wb.properties.title = "Modèle de balance comptable MaroIFRS"
    wb.properties.creator = "MaroIFRS"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
