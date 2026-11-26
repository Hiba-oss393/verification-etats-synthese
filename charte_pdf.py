"""MaroIFRS - charte graphique commune aux rapports PDF (reportlab uniquement, aucune dépendance à Streamlit, aucune règle comptable).

Fournit : couleurs, logo dessiné, page de garde, en-têtes et pieds de page (« Page X sur Y »), styles de titres, tableaux (en-têtes pistache, lignes alternées,
montants alignés à droite), sommaire automatique. Les deux rapports (synthétique et détaillé) s'en servent : même identité visuelle que l'application.
"""
from __future__ import annotations

import re
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.platypus.tableofcontents import TableOfContents

# ------------------------------ palette (identique à l'application) ------------------------------
PISTACHE, ACCENT, ORANGE, ROUGE = "#4CAF50", "#388E3C", "#E65100", "#B94A48"
ENCRE, GRIS, FOND, LIGNE = "#26322B", "#5E6C63", "#EDF6EE", "#DCE3D5"
VERT_TEXTE, ORANGE_TEXTE, ZEBRE, VERT_CLAIR = "#2E7D32", "#BF360C", "#F1F8F2", "#E8F5E9"
NIVEAUX_COULEUR = {"Erreur": ROUGE, "À vérifier": ORANGE_TEXTE, "Retraitement": VERT_TEXTE, "Info": GRIS}
STATUTS_COULEUR = {"CONFORME": VERT_TEXTE, "CONFORME AVEC RÉSERVES": ORANGE_TEXTE, "NON CONFORME": ROUGE, "NON DÉTERMINÉ": GRIS, "ANALYSE PARTIELLE": "#8A3000"}

STYLE_TITRE_SECTION = "TitreSectionMaroIFRS"          # les paragraphes de ce style alimentent le sommaire
_RE_NOMBRE = re.compile(r"^[+\-\u2212(]?\d[\d\s\u202f\u00a0.,]*\)?(\s?%|\s?MAD)?$")


def hex_(c):
    return colors.HexColor(c)


def net(texte) -> str:
    """Les polices standard du PDF n'ont pas ces symboles : remplacés par leur équivalent en lettres."""
    return (str(texte).replace("→", "vers").replace("≠", "différent de").replace("≥", ">=").replace("≤", "<=").replace("\u2212", "-")
            .replace("\u202f", " ").replace("\u00a0", " ").replace("−", "-"))


def est_nombre(texte) -> bool:
    t = str(texte).strip()
    return bool(t) and bool(_RE_NOMBRE.match(t))


# ------------------------------ logo ------------------------------
def dessiner_logo(c, x, y, h, nom=True, slogan=False):
    """Dessine le logo MaroIFRS (symbole puis nom) : x, y = coin inférieur gauche du symbole, h = hauteur du symbole en points.
    Le nom est centré sur l'axe horizontal du symbole (hauteur des capitales de Helvetica-Bold = 0,718 x corps)."""
    k = h / 48.0
    c.saveState()
    c.setFillColor(hex_(PISTACHE))
    c.roundRect(x + 1.5 * k, y + 1.5 * k, 45 * k, 45 * k, 13 * k, stroke=0, fill=1)
    c.setStrokeColor(colors.white)
    c.setLineWidth(1.6 * k)
    c.setStrokeAlpha(.55)
    c.circle(x + 24 * k, y + 24 * k, 16.5 * k, stroke=1, fill=0)
    c.setStrokeAlpha(1)
    c.setFillColor(colors.white)
    for bx, bh in ((12.5, 10), (21, 16), (29.5, 23)):
        c.roundRect(x + bx * k, y + 12 * k, 6 * k, bh * k, 2 * k, stroke=0, fill=1)
    c.setFillColor(hex_(ORANGE))
    c.circle(x + 32.5 * k, y + 38.8 * k, 3.6 * k, stroke=0, fill=1)
    if nom:
        corps = 0.575 * h
        cap = 0.718 * corps
        gap = 0.16 * h
        if slogan:
            corps_s = 0.2625 * h
            bloc = cap + gap + 0.718 * corps_s
            base_nom = y + h / 2 + bloc / 2 - cap
            base_slogan = base_nom - gap - 0.718 * corps_s
        else:
            base_nom = y + h / 2 - cap / 2
        tx = x + 1.3 * h
        c.setFont("Helvetica-Bold", corps)
        c.setFillColor(hex_(ENCRE))
        c.drawString(tx, base_nom, "Maro")
        c.setFillColor(hex_(ORANGE_TEXTE))
        c.drawString(tx + stringWidth("Maro", "Helvetica-Bold", corps), base_nom, "IFRS")
        if slogan:
            c.setFont("Helvetica", corps_s)
            c.setFillColor(hex_(GRIS))
            c.drawString(tx, base_slogan, "Conversion CGNC · IAS · IFRS")
    c.restoreState()


# ------------------------------ pages : garde, en-têtes, pieds de page ------------------------------
def fabrique_canvas(titre_rapport, fichier, avec_garde=True):
    """Classe de canevas qui numérote « Page X sur Y » et dessine l'en-tête et le pied de page de chaque page (sauf la page de garde)."""
    class CanvasMaro(rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._etats = []

        def showPage(self):
            self._etats.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._etats)
            for i, etat in enumerate(self._etats, 1):
                self.__dict__.update(etat)
                if not (avec_garde and i == 1):
                    self._cadre(i, total)
                super().showPage()
            super().save()

        def _cadre(self, num, total):
            w, h = self._pagesize
            m = 1.4 * cm
            self.saveState()
            # en-tête : bandeau pistache fin, logo réduit, titre du rapport, filet orange
            self.setFillColor(hex_(PISTACHE))
            self.rect(0, h - 0.32 * cm, w, 0.32 * cm, stroke=0, fill=1)
            dessiner_logo(self, m, h - 1.18 * cm, 0.62 * cm, nom=True)
            self.setFont("Helvetica", 8)
            self.setFillColor(hex_(GRIS))
            self.drawRightString(w - m, h - 0.92 * cm, net(titre_rapport))
            self.setStrokeColor(hex_(ORANGE))
            self.setLineWidth(1.1)
            self.line(m, h - 1.38 * cm, m + 2.2 * cm, h - 1.38 * cm)
            self.setStrokeColor(hex_(LIGNE))
            self.setLineWidth(.6)
            self.line(m + 2.2 * cm, h - 1.38 * cm, w - m, h - 1.38 * cm)
            # pied de page
            self.line(m, 1.0 * cm, w - m, 1.0 * cm)
            self.setFont("Helvetica", 7.5)
            self.setFillColor(hex_(GRIS))
            gauche = "MaroIFRS · conversion CGNC / IAS / IFRS" + (f" · {net(fichier)}" if fichier else "")
            self.drawString(m, 0.62 * cm, gauche[:120])
            self.drawRightString(w - m, 0.62 * cm, f"Page {num} sur {total}")
            self.restoreState()

    return CanvasMaro


def page_de_garde(c, w, h, titre, sous_titre, meta, mention, statut=None):
    """Dessine la page de garde. meta = liste de (libellé, valeur) ; statut = (texte, couleur) facultatif, affiché en pastille."""
    m = 2.0 * cm
    c.saveState()
    c.setFillColor(hex_(PISTACHE))
    c.rect(0, h - 0.7 * cm, w, 0.7 * cm, stroke=0, fill=1)
    c.setFillColor(hex_(ORANGE))
    c.rect(0, h - 0.82 * cm, 4.2 * cm, 0.12 * cm, stroke=0, fill=1)
    dessiner_logo(c, m, h - 4.6 * cm, 1.9 * cm, nom=True, slogan=True)
    # titre
    c.setFillColor(hex_(ENCRE))
    c.setFont("Helvetica-Bold", 30)
    y = h * 0.665
    for ligne in titre:
        c.drawString(m, y, net(ligne))
        y -= 36
    c.setFillColor(hex_(ORANGE))
    c.rect(m, y + 12, 3.2 * cm, 0.14 * cm, stroke=0, fill=1)
    c.setFillColor(hex_(GRIS))
    c.setFont("Helvetica", 13)
    c.drawString(m, y - 14, net(sous_titre))
    # fiche d'identité du rapport
    nb = len(meta) + (1 if statut else 0)
    hauteur_carte = 1.0 * cm + nb * 0.95 * cm
    yc = y - 2.0 * cm - hauteur_carte
    c.setFillColor(colors.white)
    c.setStrokeColor(hex_(LIGNE))
    c.setLineWidth(.8)
    c.roundRect(m, yc, w - 2 * m, hauteur_carte, 10, stroke=1, fill=1)
    c.setFillColor(hex_(ORANGE))
    c.rect(m, yc + 0.5 * cm, 0.1 * cm, hauteur_carte - 1.0 * cm, stroke=0, fill=1)
    ligne_y = yc + hauteur_carte - 1.0 * cm
    for lib, val in meta:
        c.setFont("Helvetica", 8.5)
        c.setFillColor(hex_(GRIS))
        c.drawString(m + 0.7 * cm, ligne_y + 0.28 * cm, net(lib).upper())
        c.setFont("Helvetica-Bold", 11)
        c.setFillColor(hex_(ENCRE))
        c.drawString(m + 6.2 * cm, ligne_y + 0.26 * cm, net(val)[:95])
        ligne_y -= 0.95 * cm
    if statut:
        c.setFont("Helvetica", 8.5)
        c.setFillColor(hex_(GRIS))
        c.drawString(m + 0.7 * cm, ligne_y + 0.28 * cm, "STATUT FINAL")
        txt, coul = statut
        c.setFont("Helvetica-Bold", 11)
        lg = stringWidth(net(txt), "Helvetica-Bold", 11) + 0.6 * cm
        c.setFillColor(hex_(coul))
        c.roundRect(m + 6.2 * cm, ligne_y - 0.02 * cm, lg, 0.62 * cm, 6, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.drawString(m + 6.5 * cm, ligne_y + 0.2 * cm, net(txt))
    # mention de bas de page
    c.setFont("Helvetica", 8)
    c.setFillColor(hex_(GRIS))
    for i, ligne in enumerate(mention):
        c.drawString(m, 1.45 * cm - i * 0.38 * cm, net(ligne))
    c.setStrokeColor(hex_(LIGNE))
    c.line(m, 2.0 * cm, w - m, 2.0 * cm)
    c.restoreState()


# ------------------------------ styles de texte et de tableaux ------------------------------
def styles(taille_base=9):
    t = {}
    t["h1"] = ParagraphStyle(STYLE_TITRE_SECTION, fontName="Helvetica-Bold", fontSize=14, leading=18, textColor=hex_(ENCRE), spaceBefore=14, spaceAfter=2, keepWithNext=1)
    t["h2"] = ParagraphStyle("SousTitreMaroIFRS", fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=hex_(VERT_TEXTE), spaceBefore=8, spaceAfter=3, keepWithNext=1)
    t["p"] = ParagraphStyle("TexteMaroIFRS", fontName="Helvetica", fontSize=taille_base, leading=taille_base + 3.4, textColor=hex_(ENCRE))
    t["petit"] = ParagraphStyle("PetitMaroIFRS", parent=t["p"], fontSize=7.8, leading=10.2, textColor=hex_(GRIS))
    t["gras"] = ParagraphStyle("GrasMaroIFRS", parent=t["p"], fontName="Helvetica-Bold")
    t["sommaire"] = ParagraphStyle("SommaireTitre", fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=hex_(ENCRE), spaceAfter=4)
    return t


def titre_section(texte, st):
    """Titre de section : texte + filet pistache et accent orange (une section = une entrée du sommaire)."""
    return [Paragraph(net(texte), st["h1"]), HRFlowable(width="100%", thickness=1.2, color=hex_(PISTACHE), spaceBefore=1, spaceAfter=6)]


def tableau_pdf(entetes, lignes, largeurs=None, taille=7.8, droite=(), couleurs_colonnes=None, premiere_ligne_en_gras=False, lignes_gras=()):
    """Tableau de rapport. `lignes` : liste de listes de textes. Colonnes dans `droite` (indices) alignées à droite (montants).
    `couleurs_colonnes` : {indice de colonne: {texte: couleur}} pour colorer un niveau ou un statut."""
    cel = ParagraphStyle("c", fontName="Helvetica", fontSize=taille, leading=taille + 2.2, textColor=hex_(ENCRE))
    cel_d = ParagraphStyle("cd", parent=cel, alignment=2)
    tete = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=taille, leading=taille + 2.2, textColor=hex_(ENCRE))
    tete_d = ParagraphStyle("td", parent=tete, alignment=2)
    from xml.sax.saxutils import escape
    data = [[Paragraph(escape(net(e)), tete_d if j in droite else tete) for j, e in enumerate(entetes)]]
    for i_lg, lg in enumerate(lignes):
        ligne = []
        for j, v in enumerate(lg):
            txt = escape(net(v))
            if i_lg in lignes_gras:
                txt = f"<b>{txt}</b>"
            coul = (couleurs_colonnes or {}).get(j, {}).get(str(v))
            if coul:
                txt = f'<font color="{coul}"><b>{txt}</b></font>'
            elif premiere_ligne_en_gras and j == 0:
                txt = f"<b>{txt}</b>"
            ligne.append(Paragraph(txt, cel_d if j in droite else cel))
        data.append(ligne)
    t = Table(data, colWidths=largeurs, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), hex_(PISTACHE)), ("LINEBELOW", (0, 0), (-1, 0), 1.1, hex_(ACCENT)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, hex_(ZEBRE)]),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, hex_(LIGNE)), ("BOX", (0, 0), (-1, -1), 0.5, hex_(LIGNE)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2)]))
    return t


def bloc_indicateurs(cases, largeur_totale):
    """Rangée d'indicateurs (valeur en gros, légende dessous) : cases = liste de (valeur, légende)."""
    v_st = ParagraphStyle("kv", fontName="Helvetica-Bold", fontSize=17, leading=20, textColor=hex_(ENCRE), alignment=1)
    l_st = ParagraphStyle("kl", fontName="Helvetica", fontSize=8, leading=10, textColor=hex_(GRIS), alignment=1)
    n = len(cases)
    t = Table([[Paragraph(net(v), v_st) for v, _ in cases], [Paragraph(net(l), l_st) for _, l in cases]], colWidths=[largeur_totale / n] * n)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), hex_(VERT_CLAIR)), ("BOX", (0, 0), (-1, -1), 0.6, hex_(PISTACHE)),
                           ("LINEBEFORE", (1, 0), (-1, -1), 0.6, colors.white), ("LINEABOVE", (0, 0), (-1, 0), 2.2, hex_(ORANGE)),
                           ("TOPPADDING", (0, 0), (-1, 0), 9), ("BOTTOMPADDING", (0, -1), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return t


# ------------------------------ document avec sommaire ------------------------------
class DocumentMaro(SimpleDocTemplate):
    """SimpleDocTemplate qui alimente le sommaire avec chaque titre de section."""
    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name == STYLE_TITRE_SECTION:
            self.notify("TOCEntry", (0, flowable.getPlainText(), self.page))


def sommaire(st):
    """Éléments de la page de sommaire (titre + table des matières + saut de page)."""
    toc = TableOfContents()
    toc.dotsMinLevel = 0
    toc.levelStyles = [ParagraphStyle("TOC0", fontName="Helvetica", fontSize=10.5, leading=17, leftIndent=0, firstLineIndent=0, textColor=hex_(ENCRE))]
    return [Paragraph("Sommaire", st["sommaire"]), HRFlowable(width="100%", thickness=1.2, color=hex_(PISTACHE), spaceAfter=10), toc, PageBreak()]


def date_edition() -> str:
    return datetime.now().strftime("%d/%m/%Y à %H:%M")
