"""MaroIFRS - habillage visuel (identité, feuille de style, logo, illustration, photographies, carrousel, bandeau défilant, définition des pages).

Ce module ne contient AUCUNE logique comptable et n'importe pas Streamlit : il ne fournit que des textes (CSS, SVG) et des constantes.
app.py l'utilise pour afficher la navigation et le style ; modifier ce fichier ne change aucun calcul, aucun résultat, aucun export.

Charte demandée : VERT #4CAF50 (identité, éléments positifs) et ORANGE #E65100 (actions, accents), complétés par le blanc.
  Teintes dérivées : vert foncé #2E7D32 / #388E3C (texte et en-têtes, pour la lisibilité), vert pâle #C8E6C9 / #E8F5E9, orange foncé #BF360C (texte),
  orange pâle #FFE3D1 ; rouge d'erreur #B94A48 (anomalies uniquement) ; encre #26322B ; fond général #EDF6EE ; cartes blanches. Aucun dégradé vert-orange.
"""

import base64
from html import escape
from pathlib import Path

# ------------------------------ pages ------------------------------
# identifiant : (libellé, icône Material). L'identifiant sert de clé de conteneur (page_<id>) et de valeur de st.session_state["page"].
PAGES = {
    "accueil": ("Accueil", ":material/home:"),
    "transformation": ("Transformation", ":material/upload_file:"),
    "resultats": ("Résultats", ":material/fact_check:"),
    "dashboard": ("Tableau de bord", ":material/dashboard:"),
    "normes": ("Normes IAS/IFRS", ":material/menu_book:"),
    "fiscal": ("Contrôle fiscal", ":material/receipt_long:"),
    "calc": ("Calculateurs", ":material/calculate:"),
    "conso": ("Consolidation", ":material/account_tree:"),
    "historique": ("Historique", ":material/history:"),
    "apropos": ("À propos", ":material/info:"),
    "contact": ("Contact", ":material/mail:"),
}
PAGES_BARRE = ["accueil", "transformation", "resultats", "dashboard"]          # rangée horizontale toujours visible
PAGES_MENU = ["normes", "fiscal", "calc", "conso", "historique", "apropos", "contact"]   # menu ☰
GROUPES_MENU = [("Référentiels et outils", ["normes", "fiscal", "calc", "conso"]),
                ("Suivi", ["historique"]),
                ("Informations", ["apropos", "contact"])]
PAGE_PAR_DEFAUT = "accueil"
NAV_FRAGMENT_SEUL = True     # True : changer de page ne relance que la barre de navigation (rapide). False : repli, exécution complète à chaque changement.
STREAMLIT_MIN = (1, 40)     # st.container(key=...), st.fragment, icônes Material des boutons


def css_pages(actif):
    """Masque tous les conteneurs de page sauf la page active.
    Les pages restent calculées à chaque exécution (comme les anciens onglets) : fichiers importés, résultats et historique ne sont jamais perdus
    en changeant de page ; seul l'affichage change."""
    regles = []
    for p in PAGES:
        if p != actif:
            regles.append('[data-testid="stLayoutWrapper"]:has(> .st-key-page_%s){display:none !important;}' % p)
            regles.append('.st-key-page_%s{display:none !important;}' % p)
    return "<style>" + "\n".join(regles) + "</style>"


# ------------------------------ logo et illustration ------------------------------
def logo_svg(taille=40):
    """Symbole : trois barres croissantes (graphique financier) dans un anneau (normes internationales), point orange = résultat."""
    return ('<svg viewBox="0 0 48 48" width="%d" height="%d" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="MaroIFRS">'
            '<rect x="1.5" y="1.5" width="45" height="45" rx="13" fill="#4CAF50"/>'
            '<circle cx="24" cy="24" r="16.5" fill="none" stroke="#FFFFFF" stroke-opacity=".55" stroke-width="1.6"/>'
            '<rect x="12.5" y="26" width="6" height="10" rx="2" fill="#FFFFFF"/>'
            '<rect x="21" y="20" width="6" height="16" rx="2" fill="#FFFFFF"/>'
            '<rect x="29.5" y="13" width="6" height="23" rx="2" fill="#FFFFFF"/>'
            '<circle cx="32.5" cy="9.2" r="3.6" fill="#E65100"/></svg>') % (taille, taille)


def _symbole(x, y, taille):
    """Symbole seul, placé dans le cadre (x, y, taille x taille). Anneau, barres croissantes, point orange."""
    return ('<svg x="%s" y="%s" width="%s" height="%s" viewBox="0 0 48 48">'
            '<rect x="1.5" y="1.5" width="45" height="45" rx="13" fill="#4CAF50"/>'
            '<circle cx="24" cy="24" r="16.5" fill="none" stroke="#FFFFFF" stroke-opacity=".55" stroke-width="1.6"/>'
            '<rect x="12.5" y="26" width="6" height="10" rx="2" fill="#FFFFFF"/>'
            '<rect x="21" y="20" width="6" height="16" rx="2" fill="#FFFFFF"/>'
            '<rect x="29.5" y="13" width="6" height="23" rx="2" fill="#FFFFFF"/>'
            '<circle cx="32.5" cy="9.2" r="3.6" fill="#E65100"/></svg>') % (x, y, taille, taille)


# Géométrie du logo complet (unités du viewBox). Le symbole occupe 40 x 40 et son centre vertical est à y = 22.
# Le nom est centré sur ce même axe : hauteur des capitales = 0,72 x corps ; sans slogan, ligne de base = 22 + hauteur/2 ;
# avec slogan, le bloc nom + slogan (écart de 4) est centré sur y = 22.
_CORPS_NOM, _CORPS_SLOGAN, _CAPS = 23.0, 10.5, 0.72
_X_TEXTE = 52                                                         # 40 de symbole + 12 d'espace
_LIGNE_NOM_SEUL = 22 + _CAPS * _CORPS_NOM / 2                          # 30,28
_BLOC = _CAPS * _CORPS_NOM + 4 + _CAPS * _CORPS_SLOGAN                 # 28,12
_LIGNE_NOM_DUO = 22 - _BLOC / 2 + _CAPS * _CORPS_NOM                   # 24,50
_LIGNE_SLOGAN = _LIGNE_NOM_DUO + 4 + _CAPS * _CORPS_SLOGAN             # 36,06
POLICE_LOGO = "Plus Jakarta Sans,Segoe UI,Arial,sans-serif"


def logo_complet_svg(hauteur=44, slogan=True, classe=""):
    """Logo MaroIFRS complet (symbole + nom, avec ou sans slogan) dans un seul SVG : symbole et texte partagent le même axe horizontal."""
    largeur = 214 if slogan else 140
    ligne_nom = _LIGNE_NOM_DUO if slogan else _LIGNE_NOM_SEUL
    txt = ('<text x="%d" y="%.2f" font-family="%s" font-size="%s" font-weight="800" letter-spacing="-0.4" fill="#26322B">Maro<tspan fill="#BF360C">IFRS</tspan></text>'
           % (_X_TEXTE, ligne_nom, POLICE_LOGO, _CORPS_NOM))
    if slogan:
        txt += ('<text x="%d" y="%.2f" font-family="%s" font-size="%s" font-weight="500" fill="#5E6C63">Conversion CGNC · IAS · IFRS</text>'
                % (_X_TEXTE, _LIGNE_SLOGAN, POLICE_LOGO, _CORPS_SLOGAN))
    return ('<svg class="%s" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d 44" width="%.1f" height="%d" role="img" aria-label="MaroIFRS%s">%s%s</svg>'
            % (classe, largeur, hauteur * largeur / 44, hauteur, " : conversion CGNC, IAS, IFRS" if slogan else "", _symbole(0, 2, 40), txt))


def logo_barre():
    """Logo de la barre du haut : version complète (avec slogan) ou compacte (symbole + nom) selon la largeur de l'écran (voir CSS)."""
    return logo_complet_svg(44, True, "lg-full") + logo_complet_svg(40, False, "lg-compact")


HERO_SVG = (
    '<svg class="hero-svg" viewBox="0 0 520 340" width="100%" xmlns="http://www.w3.org/2000/svg" role="img" '
    'aria-label="Illustration : graphiques financiers et conversion CGNC vers IFRS">'
    # carte 1 : histogramme
    '<rect x="8" y="14" width="250" height="176" rx="18" fill="#FFFFFF" stroke="#B2DBB4"/>'
    '<rect x="28" y="34" width="70" height="8" rx="4" fill="#C8E6C9"/>'
    '<rect x="28" y="50" width="44" height="6" rx="3" fill="#D7EDD8"/>'
    '<g class="hb"><rect x="34" y="124" width="22" height="42" rx="5" fill="#81C784"/><rect x="66" y="108" width="22" height="58" rx="5" fill="#66BB6A"/>'
    '<rect x="98" y="116" width="22" height="50" rx="5" fill="#81C784"/><rect x="130" y="92" width="22" height="74" rx="5" fill="#4CAF50"/>'
    '<rect x="162" y="80" width="22" height="86" rx="5" fill="#4CAF50"/><rect x="194" y="62" width="22" height="104" rx="5" fill="#E65100"/></g>'
    '<line x1="28" y1="168" x2="238" y2="168" stroke="#E2E9DD"/>'
    # carte 2 : courbe
    '<rect x="276" y="0" width="236" height="124" rx="18" fill="#FFFFFF" stroke="#B2DBB4"/>'
    '<rect x="296" y="20" width="60" height="8" rx="4" fill="#C8E6C9"/>'
    '<g stroke="#D7EDD8"><line x1="296" y1="56" x2="492" y2="56"/><line x1="296" y1="80" x2="492" y2="80"/><line x1="296" y1="104" x2="492" y2="104"/></g>'
    '<polyline class="hl" points="298,100 328,86 358,92 392,66 426,72 470,38" fill="none" stroke="#388E3C" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>'
    '<circle cx="470" cy="38" r="6" fill="#E65100"/><circle cx="470" cy="38" r="11" fill="#E65100" fill-opacity=".18"/>'
    # carte 3 : jauge de conformité
    '<rect x="276" y="140" width="236" height="104" rx="18" fill="#FFFFFF" stroke="#B2DBB4"/>'
    '<circle cx="334" cy="192" r="30" fill="none" stroke="#C8E6C9" stroke-width="12"/>'
    '<circle class="hd" cx="334" cy="192" r="30" fill="none" stroke="#388E3C" stroke-width="12" stroke-linecap="round" stroke-dasharray="140 189" transform="rotate(-90 334 192)"/>'
    '<path d="M322 192 l8 8 l15 -16" fill="none" stroke="#388E3C" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>'
    '<rect x="384" y="168" width="104" height="9" rx="4.5" fill="#C8E6C9"/><rect x="384" y="188" width="82" height="9" rx="4.5" fill="#D7EDD8"/>'
    '<rect x="384" y="208" width="94" height="9" rx="4.5" fill="#D7EDD8"/>'
    # carte 4 : liste de contrôles
    '<rect x="8" y="206" width="250" height="124" rx="18" fill="#FFFFFF" stroke="#B2DBB4"/>'
    '<g fill="#C8E6C9"><circle cx="38" cy="236" r="10"/><circle cx="38" cy="264" r="10"/><circle cx="38" cy="292" r="10"/></g>'
    '<g fill="none" stroke="#388E3C" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M33 236 l3.4 3.4 l6 -6.6"/><path d="M33 264 l3.4 3.4 l6 -6.6"/></g>'
    '<circle cx="38" cy="292" r="4.5" fill="#E65100"/>'
    '<rect x="60" y="231" width="120" height="9" rx="4.5" fill="#C8E6C9"/><rect x="60" y="259" width="96" height="9" rx="4.5" fill="#C8E6C9"/>'
    '<rect x="60" y="287" width="108" height="9" rx="4.5" fill="#FFE3D1"/>'
    '<rect x="196" y="228" width="46" height="16" rx="8" fill="#E8F5E9"/><rect x="196" y="256" width="46" height="16" rx="8" fill="#E8F5E9"/>'
    '<rect x="196" y="284" width="46" height="16" rx="8" fill="#FFE3D1"/>'
    # carte 5 : conversion CGNC -> IFRS
    '<rect x="276" y="260" width="236" height="70" rx="18" fill="#FFFFFF" stroke="#B2DBB4"/>'
    '<circle cx="324" cy="295" r="17" fill="#C8E6C9" stroke="#4CAF50" stroke-width="2"/>'
    '<text x="324" y="299" text-anchor="middle" font-size="10" font-weight="700" fill="#26322B" font-family="Plus Jakarta Sans,Segoe UI,Arial,sans-serif">CGNC</text>'
    '<path d="M350 295 H398" stroke="#4CAF50" stroke-width="3" stroke-linecap="round"/><path d="M390 287 l9 8 l-9 8" fill="none" stroke="#4CAF50" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
    '<circle cx="436" cy="295" r="17" fill="#FFE3D1" stroke="#E65100" stroke-width="2"/>'
    '<text x="436" y="299" text-anchor="middle" font-size="10" font-weight="700" fill="#26322B" font-family="Plus Jakarta Sans,Segoe UI,Arial,sans-serif">IFRS</text>'
    '</svg>')


# ------------------------------ feuille de style ------------------------------
CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
/* ============ IDENTITÉ MAROIFRS : pistache · orange · blanc · gris très clair ============ */
:root {
  --pi: #4CAF50; --pi-d: #388E3C; --pi-t: #2E7D32; --pi-l: #C8E6C9; --pi-xl: #E8F5E9;
  --or: #E65100; --or-h: #BF4400; --or-t: #BF360C; --or-l: #FFE3D1; --or-xl: #FFF1E6;
  --ink: #26322B; --muted: #5E6C63; --faint: #8A978F; --line: #DCE3D5; --line-d: #C5D1BB;
  --bg: #EDF6EE; --blanc: #FFFFFF; --rouge: #B94A48; --pi-dd: #1B5E20;
  --ombre: 0 1px 2px rgba(38,50,43,.05), 0 4px 14px rgba(38,50,43,.05);
  --ombre-h: 0 2px 4px rgba(38,50,43,.06), 0 12px 28px rgba(38,50,43,.10);
  --sans: 'Plus Jakarta Sans','Segoe UI',system-ui,-apple-system,'Helvetica Neue',Arial,sans-serif;
  --serif: var(--sans);
}
html, body, .stApp, .stApp p, .stApp label, .stApp li, .stApp button, .stApp input, .stApp textarea {font-family: var(--sans);}
.stApp {background: var(--bg); color: var(--ink);}
.stApp [data-testid="stMarkdownContainer"] p, .stApp li {color: var(--ink);}
#MainMenu, footer, header {visibility: hidden;}
[data-testid="stHeader"], [data-testid="stAppHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"] {display: none !important; height: 0 !important;}
/* blocs qui ne portent que du style : aucun espace réservé */
[data-testid="stElementContainer"]:has(> [data-testid="stMarkdown"] style), [data-testid="stElementContainer"]:has(> [data-testid="stMarkdownContainer"] style) {display: none !important;}
[data-testid="stSidebar"], [data-testid="collapsedControl"], [data-testid="stSidebarCollapsedControl"], [data-testid="stExpandSidebarButton"] {display: none !important;}
[data-testid="stApp"], [data-testid="stAppViewContainer"], [data-testid="stMain"], [data-testid="stMainBlockContainer"], .block-container, [data-testid="stVerticalBlock"],
[data-testid="stTabs"], [data-testid="stTabPanel"], [data-baseweb="tab-panel"], div[role="tabpanel"] {outline: none !important; box-shadow: none; border: 0;}
::selection {background: var(--pi); color: var(--ink);}
.block-container {padding: .55rem 1.6rem 2rem 1.6rem !important; max-width: 100% !important;}
.stApp [data-testid="stMarkdownContainer"] h4 {font-family: var(--sans) !important; color: var(--ink) !important; font-weight: 700 !important; font-size: 1.25rem !important; letter-spacing: -.01em; margin: 1.5rem 0 .4rem 0; padding-left: .7rem; border-left: 4px solid var(--or); line-height: 1.25;}
:focus-visible {outline: 2px solid var(--or) !important; outline-offset: 2px;}

/* ============ Animations : légères et rapides (≤ 0,35 s), uniquement opacité / déplacement (calculées par la carte graphique) ============
   - fondu + légère montée à l'affichage d'une page ; cartes, indicateurs et tableaux apparaissent en cascade (décalage ≤ 0,2 s) ;
   - survol : les cartes se soulèvent, les boutons se soulèvent aussi et se compriment au clic ;
   - désactivées si le système demande de réduire les animations. */
@keyframes fadeIn {from {opacity: 0;} to {opacity: 1;}}
@keyframes riseIn {from {opacity: 0; transform: translateY(10px);} to {opacity: 1; transform: none;}}
@keyframes slideRight {from {opacity: 0; transform: translateX(-14px);} to {opacity: 1; transform: none;}}
@keyframes growX {from {transform: scaleX(0);} to {transform: scaleX(1);}}
@keyframes growY {from {transform: scaleY(0);} to {transform: scaleY(1);}}
@keyframes drawLine {from {stroke-dashoffset: 400;} to {stroke-dashoffset: 0;}}
@keyframes pulseDot {0%, 100% {transform: scale(1);} 50% {transform: scale(1.18);}}
[class*="st-key-page_"] {animation: fadeIn .22s ease-out both;}
.step, .nc, .panel, .kpi, .stat, .bd, .tw, .note, [class*="st-key-fc_"], .st-key-contact_carte {animation: riseIn .32s ease-out both;}
[data-testid="stColumn"]:nth-child(2) :is(.step, .nc, .panel, .kpi, .stat), [class*="st-key-fc_"]:nth-child(2) {animation-delay: .05s;}
[data-testid="stColumn"]:nth-child(3) :is(.step, .nc, .panel, .kpi, .stat) {animation-delay: .10s;}
[data-testid="stColumn"]:nth-child(4) :is(.step, .nc, .panel, .kpi, .stat) {animation-delay: .15s;}
[data-testid="stColumn"]:nth-child(5) :is(.step, .nc, .panel, .kpi, .stat) {animation-delay: .20s;}
.stats .stat:nth-child(2) {animation-delay: .05s;} .stats .stat:nth-child(3) {animation-delay: .10s;} .stats .stat:nth-child(4) {animation-delay: .15s;}
.bar i {transform-origin: left center; animation: growX .5s ease-out both;}
.hero-svg .hb rect {transform-box: fill-box; transform-origin: bottom; animation: growY .55s ease-out both;}
.hero-svg .hb rect:nth-child(2) {animation-delay: .05s;} .hero-svg .hb rect:nth-child(3) {animation-delay: .10s;} .hero-svg .hb rect:nth-child(4) {animation-delay: .15s;}
.hero-svg .hb rect:nth-child(5) {animation-delay: .20s;} .hero-svg .hb rect:nth-child(6) {animation-delay: .25s;}
.hero-svg .hl {stroke-dasharray: 400; animation: drawLine .9s ease-out both;}
.chip::before, .tags span::before {animation: pulseDot 2.4s ease-in-out infinite;}
@media (prefers-reduced-motion: reduce) {*, *::before, *::after {animation: none !important; transition: none !important;}}

/* ============ Barre de navigation (toujours visible) ============ */
[data-testid="stLayoutWrapper"]:has(> .st-key-barre) {position: sticky; top: .5rem; z-index: 90; background: #FFFFFF; border: 1px solid var(--line); border-top: 4px solid var(--pi); border-radius: 16px; padding: .4rem .7rem; box-shadow: 0 1px 2px rgba(38,50,43,.05), 0 6px 16px rgba(38,50,43,.08);}
.st-key-barre {padding: 0;}
.st-key-barre [data-testid="stHorizontalBlock"] {flex-wrap: nowrap !important; gap: .35rem !important; align-items: center;}
.st-key-barre [data-testid="stColumn"] {flex: 0 0 auto !important; width: auto !important; min-width: 0 !important;}
.st-key-barre [data-testid="stColumn"]:last-child {margin-left: auto;}
.st-key-barre [data-testid="stMarkdownContainer"], .st-key-barre [data-testid="stMarkdownContainer"] p, .st-key-barre [data-testid="stElementContainer"] {margin: 0 !important; padding-bottom: 0;}
.brand {display: flex; align-items: center; height: 2.75rem; padding: 0 .9rem 0 .4rem; white-space: nowrap; line-height: 0;}
.brand svg {display: block; overflow: visible; flex: none;}
.brand .lg-compact {display: none;}
.chip {display: inline-flex; align-items: center; gap: .45rem; white-space: nowrap; background: var(--pi-xl); border: 1px solid var(--pi-l); color: var(--ink); padding: .32rem .85rem; border-radius: 999px; font-size: .78rem; font-weight: 600; line-height: 1.2;}
.chip::before {content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--or);}
/* boutons de navigation */
.st-key-barre .stButton button, .st-key-tiroir .stButton button {box-shadow: none !important; animation: none !important;}
.st-key-barre .stButton button {height: 2.5rem; min-height: 0; padding: 0 1rem; border-radius: 12px; border: 1px solid transparent; background: transparent; position: relative; transition: background-color .12s ease-out, border-color .12s ease-out;}
.st-key-barre .stButton button p {font-weight: 600; font-size: .92rem; color: var(--muted); white-space: nowrap;}
.st-key-barre .stButton button [data-testid="stIconMaterial"] {color: var(--muted); font-size: 1.3rem;}
.st-key-barre .stButton button:hover {background: var(--pi-l); border-color: var(--pi-l);}
.st-key-barre .stButton button:hover p, .st-key-barre .stButton button:hover [data-testid="stIconMaterial"] {color: var(--ink);}
.st-key-barre .stButton button[kind="primary"], .st-key-barre .stButton [data-testid="stBaseButton-primary"] {background: var(--pi); border-color: var(--pi);}
.st-key-barre .stButton button[kind="primary"]:hover, .st-key-barre .stButton [data-testid="stBaseButton-primary"]:hover {background: var(--pi); border-color: var(--pi-d);}
.st-key-barre .stButton button:disabled {opacity: .45; cursor: not-allowed;}
.st-key-barre .stButton button[kind="primary"] p, .st-key-barre .stButton [data-testid="stBaseButton-primary"] p, .st-key-barre .stButton button[kind="primary"] [data-testid="stIconMaterial"] {color: var(--ink) !important;}
.st-key-barre .stButton button[kind="primary"]::after, .st-key-barre .stButton [data-testid="stBaseButton-primary"]::after {content: ""; position: absolute; left: 22%; right: 22%; bottom: 3px; height: 3px; border-radius: 3px; background: var(--or);}
.st-key-nav_burger .stButton button {border: 1px solid var(--pi); background: #FFFFFF; padding: 0 .95rem 0 .75rem;}
.st-key-nav_burger .stButton button:hover {background: var(--pi-xl); border-color: var(--pi-d);}
.st-key-nav_burger .stButton button p {color: var(--ink);}
.st-key-nav_burger .stButton button [data-testid="stIconMaterial"] {color: var(--ink);}

/* ============ Menu ☰ (tiroir) ============ */
.st-key-nav_scrim {position: fixed !important; inset: 0; z-index: 998; width: 100% !important; height: 100% !important; animation: fadeIn .15s ease-out both;}
.st-key-nav_scrim > div, .st-key-nav_scrim [data-testid="stButton"] {width: 100%; height: 100%;}
.st-key-tiroir [data-testid="stElementContainer"], .st-key-tiroir .stButton {width: 100% !important;}
.st-key-nav_scrim .stButton button {position: absolute; inset: 0; width: 100%; height: 100%; border: 0; border-radius: 0; background: rgba(38,50,43,.30) !important; cursor: default; opacity: 1;}
.st-key-nav_scrim .stButton button p {color: transparent !important; font-size: 0;}
[data-testid="stLayoutWrapper"]:has(> .st-key-tiroir) {position: fixed; top: 0; left: 0; bottom: 0; z-index: 1000; width: min(330px, 88vw);}
.st-key-tiroir {position: absolute; inset: 0; background: #FFFFFF; padding: 1.1rem 1rem 1.4rem 1rem; box-shadow: 8px 0 40px rgba(38,50,43,.18); overflow-y: auto; gap: .3rem !important; animation: slideRight .18s ease-out both;}
.tiroir-t {display: flex; align-items: center; height: 2.6rem; line-height: 0; padding: 0 .4rem; border-bottom: 1px solid var(--line); margin-bottom: .5rem;}
.tiroir-t svg {display: block; overflow: visible;}
.tiroir-g {font-size: .76rem; font-weight: 700; color: var(--faint); letter-spacing: .03em; line-height: 1.3; padding: 1rem .6rem .35rem .6rem;}
.st-key-tiroir .stButton button {width: 100%; justify-content: flex-start; height: 2.7rem; min-height: 0; padding: 0 .8rem; border-radius: 12px; border: 1px solid transparent; background: transparent; position: relative; transition: background-color .12s ease-out;}
.st-key-tiroir .stButton button > div, .st-key-tiroir .stButton button span[data-has-shortcut] {justify-content: flex-start !important; width: 100%;}
.st-key-tiroir .stButton button p {font-weight: 600; font-size: .95rem; color: var(--ink); text-align: left;}
.st-key-tiroir .stButton button [data-testid="stIconMaterial"] {color: var(--pi-t); font-size: 1.35rem;}
.st-key-tiroir .stButton button:hover {background: var(--pi-l);}
.st-key-tiroir .stButton button[kind="primary"], .st-key-tiroir .stButton [data-testid="stBaseButton-primary"] {background: var(--pi);}
.st-key-tiroir .stButton button[kind="primary"] [data-testid="stIconMaterial"], .st-key-tiroir .stButton button[kind="primary"] p {color: var(--ink) !important;}
.st-key-tiroir .stButton button[kind="primary"]::before, .st-key-tiroir .stButton [data-testid="stBaseButton-primary"]::before {content: ""; position: absolute; left: 0; top: 22%; bottom: 22%; width: 4px; border-radius: 4px; background: var(--or);}
.st-key-nav_fermer .stButton button {width: auto !important; margin-left: auto; height: 2.3rem; padding: 0 .7rem; background: #FFFFFF; border: 1px solid var(--line-d);}
.st-key-nav_fermer .stButton button p {font-size: .85rem;}
@media (max-width: 1100px) {.chip {display: none;} .brand .lg-full {display: none;} .brand .lg-compact {display: block;}}
@media (max-width: 760px) {.st-key-barre [data-testid="stHorizontalBlock"] {overflow-x: auto; scrollbar-width: none;} .brand {padding: 0 .3rem;} .st-key-barre .stButton button {padding: 0 .6rem;} .st-key-barre .stButton button p {font-size: .82rem;}
  .st-key-barre [data-testid="stColumn"]:nth-child(n+3) button [data-testid="stIconMaterial"] {display: none;} .block-container {padding: .4rem .8rem 2rem .8rem !important;}
  [data-testid="stLayoutWrapper"]:has(> .st-key-barre) {padding: .3rem .4rem;}}

/* ============ Page d'accueil ============ */
.st-key-hero {position: relative; overflow: hidden; background: var(--pi-d); border: 0; box-shadow: 0 10px 30px rgba(46,125,50,.28); border-radius: 26px; padding: 2.2rem 2.6rem; margin: 1rem 0 1.2rem 0;}
.st-key-hero::before {content: ""; position: absolute; width: 520px; height: 520px; right: -150px; top: -230px; border-radius: 50%; background: rgba(255,255,255,.10); pointer-events: none;}
.st-key-hero > * {position: relative; z-index: 1;}
.pill {display: inline-flex; align-items: center; gap: .5rem; background: #FFFFFF; border: 1px solid #FFFFFF; color: var(--ink); border-radius: 999px; padding: .32rem .9rem; font-size: .8rem; font-weight: 600;}
.pill svg {color: var(--pi-t);}
.hero-t {font-size: clamp(2rem, 3.4vw, 3.15rem); line-height: 1.08; font-weight: 800; letter-spacing: -.03em; color: #FFFFFF; margin: 1.1rem 0 .9rem 0;}
.hero-p {color: #FFFFFF; font-weight: 500; font-size: 1.04rem; line-height: 1.65; max-width: 640px; margin: 0 0 1.3rem 0;}
.tags {margin-top: 1.1rem;}
.tags span {display: inline-flex; align-items: center; gap: .4rem; margin: 0 .5rem .5rem 0; padding: .3rem .8rem; border-radius: 999px; font-weight: 600; font-size: .8rem; background: rgba(255,255,255,.18); border: 1px solid rgba(255,255,255,.55); color: #FFFFFF; transition: background-color .12s ease-out;}
.tags span::before {content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--or);}
.tags span:hover {background: rgba(255,255,255,.30);}
.tags span::before {background: #FFB380;}
.hero-r {background: transparent;}
.stats {display: grid; grid-template-columns: repeat(auto-fit,minmax(190px,1fr)); gap: .9rem; margin: 0 0 1.6rem 0;}
.stat {position: relative; background: #FFFFFF; border: 1px solid var(--line); border-radius: 16px; padding: 1rem 1.2rem; box-shadow: var(--ombre); transition: transform .15s ease-out, box-shadow .15s ease-out;}
.stat::before {content: ""; position: absolute; left: 1.2rem; top: 0; width: 34px; height: 3px; border-radius: 0 0 3px 3px; background: var(--or);}
.stat:hover {transform: translateY(-2px); box-shadow: var(--ombre-h);}
.stat b {display: block; font-size: 1.9rem; font-weight: 800; letter-spacing: -.02em; color: var(--ink); line-height: 1.15; margin-top: .3rem;}
.stat span {color: var(--muted); font-size: .84rem;}
.eyebrow {color: var(--pi-t); font-size: .8rem; font-weight: 700; letter-spacing: .02em; margin-top: 1rem; display: flex; align-items: center; gap: .5rem;}
.eyebrow::before {content: ""; display: inline-block; width: 18px; height: 3px; border-radius: 3px; background: var(--or);}
.h2 {color: var(--ink); font-size: clamp(1.45rem, 2.2vw, 1.95rem); font-weight: 800; letter-spacing: -.025em; line-height: 1.15; margin: .25rem 0 1.1rem 0;}

/* cartes : étapes, fonctionnalités, panneaux */
.step, .nc, .panel, .kpi, .fc {transition: transform .18s ease-out, box-shadow .18s ease-out, border-color .18s ease-out;}
.step:hover, .nc:hover, .kpi:hover {transform: translateY(-2px); box-shadow: var(--ombre-h); border-color: var(--line-d);}
.step {position: relative; background: #FFFFFF; border: 1px solid var(--line); border-radius: 18px; padding: 1.3rem 1.3rem 1.2rem 1.3rem; min-height: 190px; box-shadow: var(--ombre);}
[data-testid="stColumn"]:not(:last-child) .step:has(.n:not(:empty))::after {content: ""; position: absolute; top: 2.55rem; right: -1.15rem; width: 1.15rem; border-top: 2px dashed var(--pi); z-index: 0;}
.step .n {position: absolute; top: 1.1rem; right: 1.2rem; font-weight: 800; font-size: 1.05rem; color: var(--or-t); background: var(--or-xl); border-radius: 999px; min-width: 2rem; height: 2rem; display: flex; align-items: center; justify-content: center;}
.step .n:empty {display: none;}
.tile {width: 46px; height: 46px; border-radius: 14px; background: var(--pi); color: #FFFFFF; display: flex; align-items: center; justify-content: center; margin-bottom: 1rem; transition: transform .15s ease-out, background-color .15s ease-out;}
.step:hover .tile, .fc:hover .tile, .kpi:hover .kico {transform: scale(1.08) rotate(-3deg); background: var(--or); color: #FFFFFF;}
.step h3, .nc h3, .fc h3 {font-weight: 700; color: var(--ink); font-size: 1.1rem; letter-spacing: -.01em; margin: 0 0 .35rem 0; padding: 0;}
.step p, .nc p, .fc p {color: var(--muted); font-size: .88rem; line-height: 1.55; margin: 0;}
[class*="st-key-fc_"] {background: #FFFFFF; border: 1px solid var(--line); border-radius: 18px; padding: 1.25rem 1.25rem .6rem 1.25rem; box-shadow: var(--ombre); transition: transform .18s ease-out, box-shadow .18s ease-out, border-color .18s ease-out; gap: .2rem !important;}
[class*="st-key-fc_"]:hover {transform: translateY(-2px); box-shadow: var(--ombre-h); border-color: var(--pi);}
[class*="st-key-fc_"] .fc {min-height: 176px;}
[class*="st-key-fc_"] .fc p {margin-bottom: .6rem;}
[class*="st-key-fc_"] .stButton button {background: transparent !important; border: 0 !important; box-shadow: none !important; padding: .1rem 0 !important; min-height: 0; height: 2rem; animation: none !important; justify-content: flex-start;}
[class*="st-key-fc_"] .stButton button p {color: var(--pi-t) !important; font-weight: 700; font-size: .88rem;}
[class*="st-key-fc_"] .stButton button [data-testid="stIconMaterial"] {color: var(--or) !important; transition: transform .12s ease-out;}
[class*="st-key-fc_"] .stButton button:hover {transform: none;}
[class*="st-key-fc_"] .stButton button:hover p {color: var(--or-t) !important;}
[class*="st-key-fc_"] .stButton button:hover [data-testid="stIconMaterial"] {transform: translateX(4px);}
.nc {background: #FFFFFF; border: 1px solid var(--line); border-radius: 18px; overflow: hidden; box-shadow: var(--ombre); min-height: 260px;}
.nc .top {height: 104px; position: relative; display: flex; align-items: center; justify-content: center; color: rgba(46,125,50,.30);}
.nc .top .b {position: absolute; top: .85rem; left: .85rem; background: #FFFFFF; color: var(--ink); border-radius: 999px; padding: .26rem .75rem; font-weight: 700; font-size: .84rem; display: inline-flex; gap: .4rem; align-items: center; box-shadow: 0 1px 4px rgba(38,50,43,.10);}
.nc .top .b svg {color: var(--pi-t);}
.nc .top .a {position: absolute; top: .85rem; right: .85rem; border-radius: 999px; padding: .2rem .65rem; font-weight: 700; font-size: .72rem;}
.a.on {background: var(--pi-d); color: #FFFFFF;}
.a.off {background: #FFFFFF; color: var(--muted); border: 1px solid var(--line);}
.nc .body {padding: 1rem 1.2rem 1.2rem 1.2rem;}
.panel {background: #FFFFFF; border: 1px solid var(--line); border-radius: 20px; padding: 1.6rem 1.8rem; min-height: 120px; box-shadow: var(--ombre);}
.panel:hover {box-shadow: var(--ombre-h);}
.panel.g {background: var(--pi-xl); border-color: var(--pi-l);}
.panel h3 {color: var(--ink); font-weight: 800; letter-spacing: -.02em; font-size: 1.25rem; margin: .4rem 0 1rem 0; padding: 0;}
.panel.g h3 {font-size: 1.1rem;}
.panel p {color: var(--muted);}
.li {display: flex; gap: .7rem; align-items: flex-start; color: var(--ink); font-size: .93rem; margin-bottom: .7rem; line-height: 1.5;}
.li svg {flex: none; margin-top: 2px;}
.ok svg {color: var(--pi-d);}
.no svg {color: var(--or);}
.kpi {display: flex; gap: .9rem; align-items: center; background: #FFFFFF; border: 1px solid var(--line); border-radius: 16px; padding: .95rem 1.15rem; box-shadow: var(--ombre); position: relative; overflow: hidden;}
.kpi::before {content: ""; position: absolute; left: 0; top: 24%; bottom: 24%; width: 3px; border-radius: 0 3px 3px 0; background: var(--or);}
.kpi .kico {width: 44px; height: 44px; flex: none; border-radius: 13px; background: var(--pi); color: #FFFFFF; display: flex; align-items: center; justify-content: center; transition: transform .15s ease-out, background-color .15s ease-out;}
.kpi span {display: block; color: var(--muted); font-size: .8rem; line-height: 1.3;}
.kpi b {color: var(--ink); font-size: 1.45rem; font-weight: 800; letter-spacing: -.02em;}
.btn {display: inline-block; background: var(--or); color: #FFFFFF !important; padding: .7rem 1.4rem; border-radius: 12px; text-decoration: none; font-weight: 700; transition: background-color .12s ease-out;}
.btn:hover {background: var(--or-h); transform: translateY(-1px); box-shadow: 0 8px 20px rgba(230,81,0,.30);}
.pied {text-align: center; color: var(--faint); font-size: .84rem; margin: 2.2rem 0 .8rem 0;}
.pied::before {content: ""; display: block; width: 36px; height: 3px; border-radius: 3px; background: var(--or); margin: 0 auto .8rem auto;}

.st-key-contact_carte {background: #FFFFFF; border: 1px solid var(--line); border-radius: 20px; padding: 1.6rem 1.8rem 1.8rem 1.8rem; box-shadow: var(--ombre); gap: .4rem !important;}
.ct-h {font-weight: 800; letter-spacing: -.02em; font-size: 1.3rem; color: var(--ink); margin: 0 0 .4rem 0; padding: 0;}
.ct-l {margin: 1rem 0 .1rem 0; font-size: .8rem; color: var(--faint); font-weight: 700;}
.ct-a {font-size: 1.3rem; font-weight: 800; letter-spacing: -.01em; margin: 0 0 .8rem 0;}
/* bandeau d'en-tête de page (compact) */
.bd {position: relative; overflow: hidden; display: flex; align-items: center; justify-content: space-between; gap: 1.5rem; background: #FFFFFF; border: 1px solid var(--line); border-radius: 20px; border-top: 5px solid var(--pi); padding: 1.2rem 1.7rem; margin: 1rem 0 1.2rem 0; box-shadow: var(--ombre);}
.bd::before {content: ""; position: absolute; left: 0; top: 20%; bottom: 20%; width: 4px; border-radius: 0 4px 4px 0; background: var(--or);}
.bd.inv {flex-direction: row;}
.bd-t {flex: 1 1 380px;}
.bd-h {font-size: clamp(1.55rem, 2.6vw, 2.15rem); font-weight: 800; letter-spacing: -.03em; line-height: 1.12; color: var(--ink); margin: .25rem 0 .45rem 0;}
.bd-s {color: var(--muted); font-size: 1rem; line-height: 1.55; max-width: 680px;}
.bd-m {flex: none; line-height: 0; background: var(--pi); border-radius: 18px; padding: .6rem;}
.bd-m svg {width: 104px; height: 94px;}
@media (max-width: 760px) {.bd-m {display: none;}}

/* ============ Tableaux HTML ============ */
.tw {background: #FFFFFF; border: 1px solid var(--line); border-radius: 16px; overflow: hidden; box-shadow: var(--ombre); margin: .4rem 0 .8rem 0;}
.tw-scroll {max-height: 640px; overflow: auto;}
.tw table {width: 100%; border-collapse: collapse; table-layout: auto;}
.tw thead th {position: sticky; top: 0; z-index: 2; background: var(--pi-t); color: #FFFFFF; text-align: left; font-size: .78rem; font-weight: 700; letter-spacing: .02em; padding: .75rem 1rem; white-space: nowrap; border-bottom: 3px solid var(--or);}
.tw thead th.num, .tw td.num {text-align: right;}
.tw td.num {white-space: nowrap; font-variant-numeric: tabular-nums;}
.tw tbody td {padding: .68rem 1rem; font-size: .9rem; line-height: 1.5; color: var(--ink); vertical-align: top; border-bottom: 1px solid #E8EEE2; white-space: normal; overflow-wrap: break-word;}
.tw tbody tr {transition: background-color .1s ease-out;}
.tw tbody tr:nth-child(even) {background: #F1F8F2;}
.tw tbody tr:hover {background: var(--pi-xl); box-shadow: inset 3px 0 0 var(--or);}
.tw td.c1 {font-weight: 700; color: var(--ink); white-space: nowrap;}
.tw td.nw {white-space: nowrap;}
.tw .pill2 {display: inline-block; padding: .14rem .7rem; border-radius: 999px; font-size: .78rem; font-weight: 700; background: var(--pi-l); color: var(--ink); white-space: nowrap;}
.tw .pill2.x {background: #EEF3EE; color: var(--muted);}
.tw-scroll::-webkit-scrollbar {width: 9px; height: 9px;}
.tw-scroll::-webkit-scrollbar-thumb {background: var(--line-d); border-radius: 99px;}
.tw-scroll::-webkit-scrollbar-thumb:hover {background: var(--pi-d);}

/* ============ Éléments de lecture ============ */
.pipe {display: flex; flex-wrap: wrap; gap: .5rem; margin: .4rem 0 1rem 0;}
.pipe span {background: var(--pi-l); color: var(--ink); border-radius: 999px; padding: .28rem .85rem; font-size: .82rem; font-weight: 600;}
.score {font-weight: 800; letter-spacing: -.03em; font-size: 3.6rem; color: var(--ink); line-height: 1;}
.score span {font-size: 1.5rem; color: var(--faint); margin-left: .2rem;}
.bar {height: 10px; background: var(--pi-l); border-radius: 999px; overflow: hidden; margin: .8rem 0;}
.bar i {display: block; height: 100%; border-radius: 999px;}
.donut {display: flex; flex-wrap: wrap; align-items: center; gap: 1.2rem;}
.legs {flex: 1 1 140px;}
.leg {display: flex; align-items: center; gap: .55rem; font-size: .9rem; color: var(--ink); padding: .22rem 0;}
.leg b {margin-left: auto; color: var(--ink);}
.dot {width: 9px; height: 9px; border-radius: 50%; display: inline-block;}
.def {font-size: .83rem; color: var(--muted); margin: .3rem 0;}
.badge {display: inline-block; padding: .15rem .75rem; border-radius: 999px; font-weight: 700; font-size: .8rem;}
.bv {background: var(--pi-l); color: #1B5E20;}
.bo {background: var(--or-l); color: #8A3000;}
.br {background: #F3DAD9; color: #8C2F2D;}
.titre2 {font-weight: 800; letter-spacing: -.02em; font-size: 1.3rem; color: var(--ink); margin: 1.6rem 0 .6rem 0; padding-left: .7rem; border-left: 4px solid var(--or); line-height: 1.2;}
.hh {font-size: .76rem; letter-spacing: .03em; color: var(--faint); font-weight: 700;}
.note {background: var(--or-xl); border: 1px solid #FFC9A6; color: #8A3000; border-radius: 12px; padding: .7rem 1rem; font-size: .85rem; margin: .6rem 0;}

/* ============ Boutons, champs, alertes ============ */
:where(.stButton) button, :where(.stDownloadButton) button, :where(.stLinkButton) a, a[data-testid^="stBaseLinkButton"] {border-radius: 12px; font-weight: 600; transition: background-color .12s ease-out, border-color .12s ease-out, box-shadow .12s ease-out, transform .12s ease-out;}
:where(.stButton) button p, :where(.stDownloadButton) button p, :where(.stLinkButton) a p {font-weight: 600;}
:where(.stButton) button[kind="secondary"], :where(.stButton) [data-testid="stBaseButton-secondary"], :where(.stDownloadButton) button, a[data-testid="stBaseLinkButton-secondary"] {background: #FFFFFF; color: var(--ink); border: 1px solid var(--line-d);}
:where(.stButton) button[kind="secondary"]:hover, :where(.stButton) [data-testid="stBaseButton-secondary"]:hover, :where(.stDownloadButton) button:hover, a[data-testid="stBaseLinkButton-secondary"]:hover {background: var(--pi-xl); border-color: var(--pi-d); color: var(--ink); transform: translateY(-1px); box-shadow: 0 6px 16px rgba(38,50,43,.08);}
:where(.stDownloadButton) button {background: var(--pi-xl); border-color: var(--pi-d);}
:where(.stButton) button p, :where(.stDownloadButton) button p, a[data-testid^="stBaseLinkButton"] p {color: var(--ink);}
:where(.stButton) button[kind="primary"], :where(.stButton) [data-testid="stBaseButton-primary"], a[data-testid="stBaseLinkButton-primary"] {background: var(--or); border: 1px solid var(--or); color: #FFFFFF;}
:where(.stButton) button[kind="primary"] p, :where(.stButton) [data-testid="stBaseButton-primary"] p, a[data-testid="stBaseLinkButton-primary"] p, :where(.stButton) button[kind="primary"] [data-testid="stIconMaterial"] {color: #FFFFFF !important;}
:where(.stButton) button[kind="primary"]:hover, :where(.stButton) [data-testid="stBaseButton-primary"]:hover, a[data-testid="stBaseLinkButton-primary"]:hover {background: var(--or-h); border-color: var(--or-h); transform: translateY(-1px); box-shadow: 0 8px 20px rgba(230,81,0,.30);}
:where(.stButton) button:active, :where(.stDownloadButton) button:active {transform: scale(.98); box-shadow: none !important;}
:where(.stButton) button:disabled, :where(.stDownloadButton) button:disabled {background: #E6ECE6 !important; border-color: var(--line) !important; color: #9AA59D !important; cursor: not-allowed; transform: none !important; box-shadow: none !important;}
:where(.stButton) button:disabled p, :where(.stDownloadButton) button:disabled p {color: #9AA59D !important;}
[data-testid="stFileUploaderDropzone"] {background: #FFFFFF; border: 1.5px dashed var(--pi-d); border-radius: 16px; transition: background-color .12s ease-out, border-color .12s ease-out;}
[data-testid="stFileUploaderDropzone"]:hover {background: var(--pi-xl); border-color: var(--or);}
[data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] > div, [data-testid="stTextInput"] input, [data-testid="stNumberInput"] input {background: #FFFFFF !important; border-radius: 12px !important; border-color: var(--line-d) !important; color: var(--ink);}
[data-testid="stTextInput"] input:focus, [data-testid="stNumberInput"] input:focus {box-shadow: 0 0 0 3px rgba(76,175,80,.45) !important; border-color: var(--pi-d) !important;}
[data-testid="stAlert"] {border-radius: 14px; background: #FFFFFF !important; border: 1px solid var(--line); border-left: 4px solid var(--pi-d); box-shadow: var(--ombre);}
[data-testid="stAlert"] > div, [data-testid="stAlertContainer"] {background: transparent !important; color: var(--ink) !important;}
[data-testid="stAlert"] p {color: var(--ink);}
[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]) {border-left-color: var(--or);}
[data-testid="stAlert"]:has([data-testid="stAlertContentError"]) {border-left-color: var(--rouge);}
[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]) {border-left-color: var(--pi-d);}
[data-testid="stExpander"] {border: 1px solid var(--line) !important; border-radius: 14px !important; background: #FFFFFF; box-shadow: var(--ombre);}
[data-testid="stExpander"] summary {font-weight: 600; color: var(--ink);}
[data-testid="stExpander"] summary:hover {color: var(--or-t);}
[data-testid="stDataFrame"] {border-radius: 14px; overflow: hidden; border: 1px solid var(--line);}
[data-testid="stSpinner"] {position: fixed !important; top: 74px; left: 50%; transform: translateX(-50%); z-index: 9999; background: #FFFFFF; color: var(--ink); border: 1px solid var(--line); border-left: 4px solid var(--or); border-radius: 999px; padding: .45rem 1.1rem; box-shadow: var(--ombre-h); width: auto !important; max-width: 90vw;}
[data-testid="stSpinner"] * {color: var(--ink) !important;}
[data-testid="stStatusWidget"] {background: #FFFFFF; border-radius: 999px; padding: .2rem .8rem; box-shadow: var(--ombre);}
[data-stale="true"], .stApp [data-stale="true"] {opacity: 1 !important; filter: none !important; transition: none !important;}
input[type="checkbox"], input[type="radio"] {accent-color: var(--or);}

/* ============ Normes sélectionnées (liste « Normes à appliquer » et filtres) : petites cartes claires ============ */
[data-testid="stMultiSelect"] [data-baseweb="select"] > div {padding: .25rem .35rem; min-height: 3rem;}
span[data-baseweb="tag"], [data-testid="stMultiSelect"] [data-tag] {background: var(--pi-xl) !important; color: var(--ink) !important; border: 1px solid var(--pi-l) !important; border-radius: 10px !important;
  padding: .28rem .6rem .28rem .55rem !important; margin: .18rem .22rem !important; font-weight: 600; font-size: .85rem; gap: .35rem; display: inline-flex; align-items: center; box-shadow: none !important;
  transition: background-color .12s ease-out, border-color .12s ease-out, box-shadow .12s ease-out;}
span[data-baseweb="tag"]::before, [data-testid="stMultiSelect"] [data-tag]::before {content: ""; flex: none; width: 6px; height: 6px; border-radius: 50%; background: var(--or);}
span[data-baseweb="tag"]:hover, [data-testid="stMultiSelect"] [data-tag]:hover {background: #FFFFFF !important; border-color: var(--pi) !important; box-shadow: 0 4px 12px rgba(38,50,43,.10) !important;}
span[data-baseweb="tag"] *, [data-testid="stMultiSelect"] [data-tag] span {color: var(--ink) !important;}
span[data-baseweb="tag"] svg, [data-testid="stMultiSelect"] [data-tag] svg {color: var(--faint) !important; fill: var(--faint) !important;}
span[data-baseweb="tag"] svg:hover, [data-testid="stMultiSelect"] [data-tag] button:hover svg {color: var(--or-t) !important; fill: var(--or-t) !important;}
[data-testid="stMultiSelect"] [data-tag] button {background: transparent !important; border: 0 !important; box-shadow: none !important; padding: 0 !important; min-height: 0 !important;}

/* ============ Sous-onglets internes (référentiels, calculateurs, consolidation, états) ============ */
[role="tablist"], [data-baseweb="tab-list"] {gap: .3rem; background: transparent; border-bottom: 1px solid var(--line) !important; padding: 0 0 .35rem 0; margin: .6rem 0 .8rem 0; flex-wrap: wrap; box-shadow: none;}
[role="tab"], [data-baseweb="tab"] {border-radius: 10px; padding: .45rem 1rem; height: auto; background: transparent; position: relative; transition: background-color .12s ease-out;}
[role="tab"]:hover, [data-baseweb="tab"]:hover {background: var(--pi-xl);}
[role="tab"] p, [data-baseweb="tab"] p {font-weight: 600; color: var(--muted) !important; font-size: .93rem;}
[role="tab"][aria-selected="true"], [data-baseweb="tab"][aria-selected="true"] {background: var(--pi-l);}
[role="tab"][aria-selected="true"] p, [data-baseweb="tab"][aria-selected="true"] p {color: var(--ink) !important;}
[role="tab"][aria-selected="true"]::after, [data-baseweb="tab"][aria-selected="true"]::after {content: ""; position: absolute; left: 18%; right: 18%; bottom: 2px; height: 3px; border-radius: 3px; background: var(--or);}
.react-aria-SelectionIndicator, [data-baseweb="tab-highlight"], [data-baseweb="tab-border"] {display: none !important;}
/* ============ Photographies : bandeaux de page ============ */
.bd-ph {flex: none; width: clamp(220px, 28vw, 340px); height: 132px; border-radius: 16px; overflow: hidden; line-height: 0; box-shadow: var(--ombre-h); border: 3px solid #FFFFFF; outline: 1px solid var(--line);}
.bd-ph img {display: block; width: 100%; height: 100%; object-fit: cover; object-position: center; transition: transform .5s ease-out;}
.bd:hover .bd-ph img {transform: scale(1.04);}
@media (max-width: 760px) {.bd {flex-wrap: wrap;} .bd-ph {width: 100%; height: 120px;}}

/* ============ Carrousel d'accueil (composant isolé : aucun rechargement de la page) ============ */
.st-key-carrousel {margin: .9rem 0 .8rem 0;}
.st-key-carrousel iframe {display: block; width: 100%; border: 0; border-radius: 24px; box-shadow: 0 10px 30px rgba(38,50,43,.18);}

/* ============ Bandeau défilant d'indicateurs ============ */
.tk {display: flex; align-items: stretch; background: #FFFFFF; border: 1px solid var(--line); border-radius: 16px; overflow: hidden; box-shadow: var(--ombre); margin: .2rem 0 .35rem 0;}
.tk-tag {flex: none; display: flex; flex-direction: column; justify-content: center; gap: .1rem; padding: .5rem 1rem; background: var(--pi-t); color: #FFFFFF; font-weight: 800; font-size: .78rem; line-height: 1.25; border-right: 4px solid var(--or); max-width: 190px;}
.tk-tag small {font-weight: 500; font-size: .68rem; opacity: .95;}
.tk-view {position: relative; flex: 1 1 auto; min-width: 0; overflow: hidden; -webkit-mask-image: linear-gradient(90deg, transparent 0, #000 28px, #000 calc(100% - 28px), transparent 100%); mask-image: linear-gradient(90deg, transparent 0, #000 28px, #000 calc(100% - 28px), transparent 100%);}
.tk-track {display: flex; width: max-content; animation: tkScroll 80s linear infinite; will-change: transform;}
.tk-view:hover .tk-track {animation-play-state: paused;}
@keyframes tkScroll {from {transform: translateX(0);} to {transform: translateX(-50%);}}
.ti {display: flex; align-items: center; gap: .6rem; padding: .5rem 1.25rem; border-right: 1px solid var(--line); white-space: nowrap;}
.ti-l {color: var(--muted); font-size: .74rem; font-weight: 600;}
.ti-v {color: var(--ink); font-weight: 800; font-size: .96rem; font-variant-numeric: tabular-nums;}
.ti-d {font-size: .78rem; font-weight: 700; font-variant-numeric: tabular-nums;}
.ti-d.up {color: #2E7D32;} .ti-d.dn {color: #BF360C;} .ti-d.eq {color: var(--muted);}
.ti svg {display: block; width: 64px; height: 24px; overflow: visible;}
.ti .demo {font-size: .6rem; font-weight: 800; letter-spacing: .04em; background: var(--or-xl); color: var(--or-t); border: 1px dashed #FFC9A6; border-radius: 6px; padding: .05rem .35rem;}
.tk-note {color: var(--faint); font-size: .74rem; margin: 0 0 1.1rem .2rem;}
@media (max-width: 760px) {.tk-tag small {display: none;} .tk-tag {max-width: 110px; padding: .4rem .6rem;}}
@media (prefers-reduced-motion: reduce) {.tk-view {overflow-x: auto;} .tk-track {animation: none !important;}}

/* ============ Sélecteur de langue (en-tête) ============ */
.st-key-langue_ui {min-width: 9.6rem;}
.st-key-langue_ui [data-baseweb="select"] > div {min-height: 2.5rem !important; border-radius: 12px !important; background: #FFFFFF !important; border: 1px solid var(--pi) !important; font-weight: 600;}
.st-key-langue_ui [data-baseweb="select"] > div:hover {border-color: var(--pi-d) !important; background: var(--pi-xl) !important;}
.st-key-langue_ui [data-testid="stSelectbox"] label {display: none;}
.st-key-barre [data-testid="stColumn"]:nth-last-child(2) {margin-left: auto;}
.st-key-barre [data-testid="stColumn"]:last-child {margin-left: 0;}

/* ============ Correctif du menu ☰ : espacement vertical du premier bouton de chaque groupe ============
   Cause : le titre de groupe (.tiroir-g) était collé au premier bouton, et le bouton agrandi (survol, focus, clic) recouvrait le texte du titre ;
   le premier bouton du menu, au contact du haut du tiroir défilant (overflow-y: auto), pouvait aussi être rogné.
   Correctif : plus de marge sous chaque titre, marge haute du tiroir, titre toujours au-dessus des boutons ; l'animation d'agrandissement est conservée. */
.st-key-tiroir {padding-top: 1.35rem !important; gap: .45rem !important; overflow-x: hidden;}
.st-key-tiroir [data-testid="stElementContainer"] {overflow: visible;}
.st-key-tiroir [data-testid="stElementContainer"]:has(.tiroir-g) {position: relative; z-index: 3; margin-bottom: .2rem;}
.st-key-tiroir [data-testid="stElementContainer"]:has(.tiroir-g) + [data-testid="stElementContainer"] {margin-top: .55rem;}
.st-key-tiroir [data-testid="stElementContainer"]:has(.stButton) {position: relative; z-index: 1;}
.st-key-tiroir .stButton {padding: 2px 0;}
.st-key-tiroir .stButton button {transform-origin: center center;}
.tiroir-g {padding: 1rem .6rem .55rem .6rem !important; margin: 0 !important; position: relative; z-index: 3;}
.st-key-tiroir [data-testid="stHorizontalBlock"] {margin-bottom: .35rem;}
@media (max-height: 640px) {.st-key-tiroir {padding-top: .9rem !important;} .tiroir-g {padding-top: .7rem !important;}}
/* ============ Sélecteur de langue : emplacement réservé, jamais rogné ============ */
.st-key-barre [data-testid="stColumn"]:last-child {flex: 0 0 9.6rem !important; width: 9.6rem !important; min-width: 9.6rem !important; margin-left: 0;}
.st-key-langue_ui, .st-key-langue_ui [data-testid="stSelectbox"] {min-width: 100%; width: 100%;}
.st-key-langue_ui [data-baseweb="select"] > div {padding-left: .35rem; box-shadow: none !important;}
@media (max-width: 1500px) {.chip {display: none;}}
@media (max-width: 1280px) {.st-key-barre .stButton button {padding: 0 .65rem;} .st-key-barre .stButton button p {font-size: .86rem;} .brand .lg-full {display: none;} .brand .lg-compact {display: block;}}
@media (max-width: 760px) {.st-key-barre [data-testid="stColumn"]:last-child {flex-basis: 8.4rem !important; width: 8.4rem !important; min-width: 8.4rem !important;}}

/* ============ Bandeau défilant des services MaroIFRS (remplace les indices) ============ */
.sv-i {display: flex; align-items: center; gap: .7rem; flex: none; padding: .62rem 1.5rem .62rem 1.1rem; border-right: 1px solid var(--line); white-space: nowrap;}
.sv-ic {flex: none; display: grid; place-items: center; width: 36px; height: 36px; border-radius: 10px; background: var(--pi-xl); color: var(--pi-t); transition: background-color .2s ease-out, color .2s ease-out;}
.sv-i:hover .sv-ic {background: var(--or); color: #FFFFFF;}
.sv-t {display: block; font-weight: 700; font-size: .86rem; color: var(--ink); line-height: 1.25;}
.sv-d {display: block; font-size: .74rem; color: var(--muted); line-height: 1.3;}
.tk-track.sv {animation-duration: 70s;}

/* ============ Transitions entre pages et apparitions au défilement ============
   Les éléments ne sont masqués que si le petit script (rv_html) a démarré : s'il échoue, tout reste visible. */
@keyframes pageIn {from {opacity: 0; transform: translateY(12px);} to {opacity: 1; transform: none;}}
[class*="st-key-page_"] {animation: pageIn .38s cubic-bezier(.22,.61,.36,1) both;}
html.rv-on .rv-t {transition: opacity .55s cubic-bezier(.22,.61,.36,1), transform .55s cubic-bezier(.22,.61,.36,1); transition-delay: var(--rvd, 0ms); will-change: opacity, transform;}
html.rv-on .rv-t:not(.rv-in) {opacity: 0; transform: translateY(22px);}
html.rv-on .rv-t.rv-in {opacity: 1; transform: none;}
html.rv-on .rv-t:is(.stat, .step, .nc, .panel, .kpi, .fc, .tw, .bd, .note) {animation: none;}
html.rv-on .rv-t.rv-in:is(.stat, .step, .nc, .fc, .kpi):hover {transform: translateY(-3px);}
.stApp .stButton button {transition: transform .15s ease-out, box-shadow .15s ease-out, background-color .15s ease-out, border-color .15s ease-out;}
.stApp [data-testid="stMain"] .stButton button:hover {transform: translateY(-1px);}
@media (prefers-reduced-motion: reduce) {html.rv-on .rv-t, html.rv-on .rv-t:not(.rv-in) {opacity: 1 !important; transform: none !important; transition: none !important;}}
</style>"""


# ============================== photographies, carrousel d'accueil, bandeau défilant ==============================
# Ces fonctions ne produisent que du texte (HTML). Elles n'importent pas Streamlit et ne lisent aucune donnée comptable.
DOSSIER_IMAGES = Path(__file__).resolve().parent / "assets" / "images"
_URI_IMAGES = {}

# page -> photographie du bandeau de page (clé de bandeau() dans app.py)
PHOTOS_PAGES = {
    "trans": ("transformation", "image_04_transformation"),
    "res": ("resultats", "image_05_resultats"),
    "dash": ("dashboard", "image_06_dashboard"),
    "conso": ("conso", "image_07_consolidation"),
    "fisc": ("fiscal", "image_08_controle_fiscal"),
}
PHOTO_TEXTE_ALT = {
    "trans": "Poste de travail : transformation des états financiers",
    "res": "Poste de travail : résultats et rapport d'audit",
    "dash": "Écran affichant un tableau de bord financier",
    "conso": "Réunion d'analyse : consolidation de groupe",
    "fisc": "Bureau : contrôle fiscal et dossiers d'audit",
}


def image_uri(nom, vignette=False):
    """URI de données d'une photographie de assets/images/ (lue une seule fois par processus). Texte vide si le fichier est absent : la page s'affiche sans photo."""
    cle = (nom, bool(vignette))
    if cle not in _URI_IMAGES:
        fichier = DOSSIER_IMAGES / (nom + ("_vignette" if vignette else "") + ".jpg")
        try:
            _URI_IMAGES[cle] = "data:image/jpeg;base64," + base64.b64encode(fichier.read_bytes()).decode("ascii")
        except OSError:
            _URI_IMAGES[cle] = ""
    return _URI_IMAGES[cle]


def photo_bandeau(cle, page_active=None, tr=lambda s: s):
    """Photographie du bandeau d'une page (page_active : si fourni, la photo n'est rendue que pour cette page).
    NB : la navigation ne relance que la barre du haut ; les photos de toutes les pages doivent donc être présentes dans la page (page_active=None).
    Retourne \"\" si la page n'a pas de photographie ou si le fichier est absent : app.py affiche alors le motif d'origine."""
    if cle not in PHOTOS_PAGES:
        return ""
    page, nom = PHOTOS_PAGES[cle]
    if page_active is not None and page_active != page:
        return ""
    uri = image_uri(nom, vignette=True)
    if not uri:
        return ""
    return '<div class="bd-ph"><img src="%s" alt="%s" loading="lazy" decoding="async"></div>' % (uri, escape(tr(PHOTO_TEXTE_ALT.get(cle, ""))))


# ---- carrousel ----
# image, position du texte (left | bottom | right), bouton d'action (clé du bouton Streamlit existant), textes français (traduits par tr)
CARROUSEL_DIAPOS = [
    {"image": "image_01_accueil", "pos": "left", "cible": "hero_importer", "objet": "center 40%",
     "kicker": "CGNC → IAS / IFRS", "titre": "Du plan comptable marocain aux normes internationales",
     "texte": "Importez votre balance ou vos états de synthèse : MaroIFRS les contrôle au regard du CGNC, puis applique les règles IAS/IFRS.",
     "bouton": "Importer mes documents", "alt": "Quartier d'affaires au coucher du soleil"},
    {"image": "image_02_marche_financier", "pos": "bottom", "cible": "hero_normes", "objet": "center 30%",
     "kicker": "Référentiel", "titre": "Les normes IAS/IFRS et le CGNC à portée de main",
     "texte": "Consultez les normes, les interprétations IFRIC / SIC et le plan comptable CGNC, avec leurs règles de contrôle.",
     "bouton": "Explorer les normes", "alt": "Salle de marché avec écrans boursiers"},
    {"image": "image_03_reunion_financiere", "pos": "right", "cible": "ouvrir_conso", "objet": "center 35%",
     "kicker": "Groupe", "titre": "De la société mère à la consolidation du groupe",
     "texte": "Périmètre, éliminations intragroupe, intérêts ne donnant pas le contrôle et comparaison avec les comptes publiés.",
     "bouton": "Ouvrir la consolidation", "alt": "Réunion financière en salle vitrée"},
]
CARROUSEL_DELAI_MS = 6500          # changement automatique toutes les 6,5 s (consigne : 5 à 7 s)
CARROUSEL_HAUTEUR = 430            # hauteur du composant, en pixels

_CARROUSEL_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:#1B2A20;font-family:'Plus Jakarta Sans','Segoe UI',system-ui,-apple-system,Arial,sans-serif;overflow:hidden}
.car{position:relative;width:100%;height:100%;overflow:hidden;background:#1B2A20}
.slide{position:absolute;inset:0;opacity:0;visibility:hidden;transition:opacity 1.1s ease,visibility 0s linear 1.1s}
.slide.on{opacity:1;visibility:visible;z-index:1;transition:opacity 1.1s ease,visibility 0s}
.slide img{display:block;width:100%;height:100%;object-fit:cover;transform:scale(1.05);transition:transform 8s ease-out}
.slide.on img{transform:scale(1)}
.scrim{position:absolute;inset:0;pointer-events:none}
.p-left .scrim{background:linear-gradient(90deg,rgba(14,26,19,.86) 0%,rgba(14,26,19,.62) 36%,rgba(14,26,19,.12) 70%,rgba(14,26,19,0) 100%)}
.p-right .scrim{background:linear-gradient(270deg,rgba(14,26,19,.88) 0%,rgba(14,26,19,.64) 38%,rgba(14,26,19,.12) 72%,rgba(14,26,19,0) 100%)}
.p-bottom .scrim{background:linear-gradient(0deg,rgba(14,26,19,.90) 0%,rgba(14,26,19,.66) 38%,rgba(14,26,19,.10) 74%,rgba(14,26,19,0) 100%)}
.txt{position:absolute;max-width:min(560px,84%);color:#FFFFFF}
.p-left .txt{left:clamp(22px,5vw,66px);top:50%;transform:translateY(-50%)}
.p-right .txt{right:clamp(22px,5vw,66px);top:50%;transform:translateY(-50%)}
.p-bottom .txt{left:clamp(22px,5vw,66px);bottom:clamp(52px,11vh,76px);max-width:min(640px,86%)}
.kick{display:inline-flex;align-items:center;gap:.45rem;background:#FFFFFF;color:#26322B;border-radius:999px;padding:.3rem .85rem;font-size:.78rem;font-weight:700}
.kick:before{content:"";width:7px;height:7px;border-radius:50%;background:#E65100}
h2{font-size:clamp(1.45rem,3.1vw,2.45rem);font-weight:800;line-height:1.1;letter-spacing:-.025em;margin:.9rem 0 .7rem 0;text-shadow:0 2px 14px rgba(0,0,0,.35)}
p{font-size:clamp(.88rem,1.45vw,1.04rem);line-height:1.6;font-weight:500;color:#FFFFFF;text-shadow:0 1px 8px rgba(0,0,0,.35)}
.cta{display:inline-flex;align-items:center;gap:.5rem;margin-top:1.1rem;background:#E65100;color:#FFFFFF;border:0;border-radius:12px;padding:.78rem 1.4rem;font:700 .95rem 'Plus Jakarta Sans','Segoe UI',Arial,sans-serif;cursor:pointer;transition:background-color .15s ease-out,transform .15s ease-out,box-shadow .15s ease-out}
.cta:hover{background:#BF4400;transform:translateY(-1px);box-shadow:0 8px 20px rgba(230,81,0,.35)}
.cta:active{transform:scale(.98)}
.cta:focus-visible,.arr:focus-visible,.dots button:focus-visible{outline:2px solid #FFFFFF;outline-offset:3px}
.slide.on .txt>*{animation:up .7s ease-out both}
.slide.on .txt>:nth-child(2){animation-delay:.12s}.slide.on .txt>:nth-child(3){animation-delay:.22s}.slide.on .txt>:nth-child(4){animation-delay:.32s}
@keyframes up{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
.arr{position:absolute;top:50%;transform:translateY(-50%);z-index:5;width:42px;height:42px;border-radius:50%;border:1px solid rgba(255,255,255,.7);background:rgba(20,34,26,.45);color:#FFFFFF;font-size:1.25rem;line-height:1;cursor:pointer;transition:background-color .15s ease-out}
.arr:hover{background:rgba(230,81,0,.9);border-color:#E65100}
.arr.pv{left:12px}.arr.nx{right:12px}
.dots{position:absolute;left:0;right:0;bottom:16px;z-index:5;display:flex;justify-content:center;gap:9px}
.dots button{width:10px;height:10px;border-radius:999px;border:0;background:rgba(255,255,255,.6);cursor:pointer;padding:0;transition:width .25s ease-out,background-color .25s ease-out}
.dots button.on{width:28px;background:#E65100}
.bar{position:absolute;left:0;bottom:0;height:4px;width:100%;z-index:5;background:rgba(255,255,255,.18)}
.bar i{display:block;height:100%;width:0;background:#4CAF50}
.bar i.run{animation:fill var(--d) linear forwards}
@keyframes fill{from{width:0}to{width:100%}}
@media (max-width:620px){
 .p-left .txt,.p-right .txt,.p-bottom .txt{left:16px;right:16px;top:auto;bottom:46px;transform:none;max-width:none}
 .p-left .scrim,.p-right .scrim,.p-bottom .scrim{background:linear-gradient(0deg,rgba(14,26,19,.92) 0%,rgba(14,26,19,.7) 48%,rgba(14,26,19,.05) 100%)}
 p{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
 .arr{display:none}.cta{padding:.62rem 1.1rem;margin-top:.8rem}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

_CARROUSEL_JS = """
(function(){
 var D=%(delai)d, slides=[].slice.call(document.querySelectorAll('.slide')), dots=[].slice.call(document.querySelectorAll('.dots button')), bar=document.querySelector('.bar i'), i=0, t=null, pause=false;
 function show(n){
  i=(n+slides.length)%%slides.length;
  slides.forEach(function(s,k){s.classList.toggle('on',k===i);s.setAttribute('aria-hidden',k===i?'false':'true');var b=s.querySelector('.cta');if(b)b.tabIndex=(k===i?0:-1);});
  dots.forEach(function(d,k){d.classList.toggle('on',k===i);d.setAttribute('aria-current',k===i?'true':'false');});
  bar.classList.remove('run');void bar.offsetWidth;if(!pause)bar.classList.add('run');
  restart();
 }
 function restart(){clearTimeout(t);if(!pause&&!document.hidden)t=setTimeout(function(){show(i+1);},D);}
 function go(k){try{var d=window.parent.document,b=d.querySelector('.st-key-'+k+' button');if(b){b.click();return true;}}catch(e){}return false;}
 document.querySelector('.pv').addEventListener('click',function(){show(i-1);});
 document.querySelector('.nx').addEventListener('click',function(){show(i+1);});
 dots.forEach(function(d,k){d.addEventListener('click',function(){show(k);});});
 [].forEach.call(document.querySelectorAll('.cta'),function(b){b.addEventListener('click',function(){go(b.getAttribute('data-cible'));});});
 var car=document.querySelector('.car');
 car.addEventListener('mouseenter',function(){pause=true;clearTimeout(t);bar.classList.remove('run');});
 car.addEventListener('mouseleave',function(){pause=false;show(i);});
 document.addEventListener('visibilitychange',function(){if(document.hidden){clearTimeout(t);}else{show(i);}});
 document.addEventListener('keydown',function(e){if(e.key==='ArrowLeft')show(i-1);if(e.key==='ArrowRight')show(i+1);});
 show(0);
})();
"""


def carrousel_html(tr=lambda s: s, delai_ms=CARROUSEL_DELAI_MS, lang="fr"):
    """Document HTML autonome du carrousel (3 photographies fournies, fondu, flèches, points, bouton d'action par diapositive).
    Tourne dans un composant isolé : le défilement ne relance jamais l'application. Le bouton d'action déclenche le bouton Streamlit existant de la même page."""
    diapos, points = [], []
    for k, d in enumerate(CARROUSEL_DIAPOS):
        uri = image_uri(d["image"])
        img = '<img src="%s" alt="%s" decoding="async"%s>' % (uri, escape(tr(d["alt"])), "" if k == 0 else ' loading="lazy"') if uri else ""
        diapos.append(
            '<section class="slide p-%s%s" aria-roledescription="slide" aria-label="%d / %d">%s<div class="scrim"></div>'
            '<div class="txt"><span class="kick">%s</span><h2>%s</h2><p>%s</p><button class="cta" type="button" data-cible="%s">%s &#8594;</button></div></section>'
            % (d["pos"], " on" if k == 0 else "", k + 1, len(CARROUSEL_DIAPOS), img, escape(tr(d["kicker"])), escape(tr(d["titre"])), escape(tr(d["texte"])),
               d["cible"], escape(tr(d["bouton"]))))
        points.append('<button type="button" aria-label="%s %d"></button>' % (escape(tr("Photographie")), k + 1))
    return ('<!DOCTYPE html><html lang="%s"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>%s</style></head><body>'
            '<div class="car" role="region" aria-roledescription="carousel" aria-label="%s" style="--d:%dms">%s'
            '<button class="arr pv" type="button" aria-label="%s">&#8249;</button><button class="arr nx" type="button" aria-label="%s">&#8250;</button>'
            '<div class="dots">%s</div><div class="bar"><i></i></div></div><script>%s</script></body></html>'
            % (lang, _CARROUSEL_CSS, escape(tr("Présentation de MaroIFRS")), delai_ms, "".join(diapos), escape(tr("Photographie précédente")),
               escape(tr("Photographie suivante")), "".join(points), _CARROUSEL_JS % {"delai": delai_ms}))


# ---- bandeau défilant d'indicateurs ----
def _nombre(v, decimales=1, lang="fr", signe=False):
    """Formate un nombre à la française (espace insécable, virgule) ou à l'anglaise (virgule, point)."""
    txt = ("{:+,.%df}" if signe else "{:,.%df}") % decimales
    txt = txt.format(v)
    if lang == "fr":
        txt = txt.replace(",", "\u202f").replace(".", ",")
    return txt


def _spark(serie, couleur):
    """Petit graphique d'évolution (SVG) ; rien si moins de deux points."""
    if not serie or len(serie) < 2:
        return ""
    lo, hi = min(serie), max(serie)
    pas = (hi - lo) or 1.0
    pts = " ".join("%.1f,%.1f" % (2 + 60.0 * k / (len(serie) - 1), 22 - 20.0 * (v - lo) / pas) for k, v in enumerate(serie))
    fin_x, fin_y = pts.split(" ")[-1].split(",")
    return ('<svg viewBox="0 0 64 24" aria-hidden="true"><polyline points="%s" fill="none" stroke="%s" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
            '<circle cx="%s" cy="%s" r="2.2" fill="%s"/></svg>' % (pts, couleur, fin_x, fin_y, couleur))


def ticker_html(indicateurs, tr=lambda s: s, lang="fr"):
    """Bandeau horizontal d'indicateurs, défilement continu en CSS pur (aucun script, aucun rechargement).
    indicateurs : liste de dictionnaires {libelle, valeur (texte), variation (nombre en % ou None), serie (liste de nombres ou None), demo (bool)}.
    Les indicateurs « demo » sont des valeurs FICTIVES et portent la pastille DÉMO ; les autres sont des données de MaroIFRS."""
    def un(ind):
        var = ind.get("variation")
        if var is None:
            delta = ""
        else:
            cls, fleche = ("up", "\u25b2") if var > 0 else (("dn", "\u25bc") if var < 0 else ("eq", "\u25ac"))
            delta = '<span class="ti-d %s">%s %s %s</span>' % (cls, fleche, _nombre(var, 2, lang, signe=True), ind.get("unite", "%"))
            couleur = {"up": "#2E7D32", "dn": "#E65100", "eq": "#8A978F"}[cls]
        graphique = _spark(ind.get("serie"), couleur if var is not None else "#4CAF50")
        pastille = '<span class="demo" title="%s">%s</span>' % (escape(tr("Valeur fictive de démonstration")), escape(tr("DÉMO"))) if ind.get("demo") else ""
        return ('<div class="ti"><span class="ti-l">%s</span><span class="ti-v">%s</span>%s%s%s</div>'
                % (escape(tr(ind["libelle"])), escape(str(ind["valeur"])), delta, graphique, pastille))
    corps = "".join(un(i) for i in indicateurs)
    return ('<div class="tk" role="region" aria-label="%s"><div class="tk-tag">%s<small>%s</small></div>'
            '<div class="tk-view"><div class="tk-track">%s%s</div></div></div>'
            % (escape(tr("Indicateurs")), escape(tr("Indicateurs")), escape(tr("MaroIFRS et exemples fictifs")), corps, corps.replace('class="ti"', 'class="ti" aria-hidden="true"')))


def indicateurs_demo(lang="fr"):
    """Quatre indicateurs FICTIFS de démonstration (aucun lien avec un marché réel), signalés par la pastille DÉMO."""
    series = [
        ("Indice démo A", 1248.60, 0.84, [1196, 1203, 1199, 1214, 1221, 1217, 1232, 1241, 1248.6], 2),
        ("Indice démo B", 9071.25, -0.32, [9150, 9142, 9120, 9133, 9098, 9104, 9085, 9078, 9071.25], 2),
        ("Taux démo C", 3.25, 0.05, [3.10, 3.12, 3.15, 3.15, 3.18, 3.20, 3.22, 3.20, 3.25], 2),
        ("Cours démo D", 184.70, 1.17, [176, 177.5, 176.9, 179, 180.4, 181, 182.9, 182.5, 184.7], 2),
    ]
    return [{"libelle": n, "valeur": _nombre(v, d, lang) + (" %" if n.startswith("Taux") else ""), "variation": var, "serie": ser, "demo": True} for n, v, var, ser, d in series]



# ---- bandeau défilant des services MaroIFRS (remplace les indices démo à l'accueil ; ticker_html et indicateurs_demo restent disponibles) ----
_SV_ICONES = {
    "transf": '<path d="M7 7h11l-3-3M17 17H6l3 3"/><path d="M18 7v4M6 17v-4"/>',
    "conf": '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4"/>',
    "cgnc": '<path d="M4 5h6a3 3 0 0 1 3 3v11a2 2 0 0 0-2-2H4z"/><path d="M20 5h-4a3 3 0 0 0-3 3"/><path d="M20 5v12h-5"/>',
    "analyse": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "fiscal": '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"/><path d="M9 8h6M9 12h6"/>',
    "conso": '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
    "rapport": '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5M10 13h6M10 17h6"/>',
    "compar": '<path d="M7 4v16M17 4v16"/><path d="M3 8l4-4 4 4M13 16l4 4 4-4"/>',
}
SERVICES_BANDEAU = [   # uniquement des fonctions réellement présentes dans MaroIFRS
    ("transf", "Transformation IAS/IFRS", "Adaptation des comptes aux normes internationales"),
    ("conf", "Conformité comptable", "Vérification et analyse des écarts"),
    ("cgnc", "Référentiel CGNC", "Contrôles et cohérence des comptes marocains"),
    ("analyse", "Analyse financière", "Exploitation et interprétation des données comptables"),
    ("fiscal", "Contrôle fiscal", "Vérifications et suivi des paramètres fiscaux"),
    ("conso", "Consolidation", "Traitement et analyse des données de groupe"),
    ("rapport", "Rapports professionnels", "Génération de rapports détaillés PDF et Excel"),
    ("compar", "Comparaison des exercices", "Analyse des évolutions et des différences entre périodes"),
]


def services_html(tr=lambda s: s, lang="fr"):
    """Bandeau horizontal défilant (CSS pur) des fonctions de MaroIFRS : icône, intitulé, courte description. Texte seulement, aucune donnée comptable."""
    def un(cle, titre, desc):
        return ('<div class="sv-i"><span class="sv-ic"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
                'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">%s</svg></span><span><b class="sv-t">%s</b><span class="sv-d">%s</span></span></div>'
                % (_SV_ICONES[cle], escape(tr(titre)), escape(tr(desc))))
    corps = "".join(un(*e) for e in SERVICES_BANDEAU)
    return ('<div class="tk" role="region" aria-label="%s"><div class="tk-tag">%s<small>%s</small></div><div class="tk-view"><div class="tk-track sv">%s%s</div></div></div>'
            % (escape(tr("Fonctionnalités de MaroIFRS")), escape(tr("Solutions")), escape(tr("Du CGNC aux normes IAS/IFRS")), corps,
               corps.replace('class="sv-i"', 'class="sv-i" aria-hidden="true"')))


# ---- apparition progressive au défilement (script isolé : agit sur la page parente, ne touche aucune donnée) ----
REVEAL_SELECTEURS = (".hero-t, .hero-p, .pill, .tags, .eyebrow, .h2, .bd, .stat, .step, .nc, .fc, .kpi, .panel, .tw, .note, .tk, "
                     "[data-testid=\"stMarkdownContainer\"] h2, [data-testid=\"stMarkdownContainer\"] h3, [data-testid=\"stDataFrame\"], "
                     "[data-testid=\"stPlotlyChart\"], [data-testid=\"stVegaLiteChart\"], [data-testid=\"stExpander\"], [data-testid=\"stMetric\"], [data-testid=\"stAlert\"]")
_REVEAL_JS = """(function(){try{var P=window.parent,D=P.document;if(P.__rvOn){return;}
var io=new P.IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){e.target.classList.add('rv-in');io.unobserve(e.target);}});},{threshold:.06,rootMargin:'0px 0px -5%% 0px'});
function scan(){D.querySelectorAll('%(sel)s').forEach(function(el){if(el.__rv||el.closest('.tk-view')||el.closest('.st-key-barre')||el.closest('.st-key-tiroir')){return;}el.__rv=1;
var p=el.parentElement,k=p?Array.prototype.indexOf.call(p.children,el):0;el.style.setProperty('--rvd',(Math.min(k,5)*70)+'ms');el.classList.add('rv-t');io.observe(el);});}
var t=null;new P.MutationObserver(function(){if(t){return;}t=P.setTimeout(function(){t=null;scan();},60);}).observe(D.body,{childList:true,subtree:true});
scan();P.__rvOn=1;D.documentElement.classList.add('rv-on');
P.setTimeout(function(){D.querySelectorAll('.rv-t:not(.rv-in)').forEach(function(el){var r=el.getBoundingClientRect();if(r.top<P.innerHeight&&r.bottom>0){el.classList.add('rv-in');}});},2500);
}catch(e){}})();"""


def reveal_html():
    """Composant HTML invisible (hauteur 0) qui active les apparitions au défilement. Si le script ne peut pas agir, la page s'affiche normalement."""
    return '<!DOCTYPE html><html><body><script>%s</script></body></html>' % (_REVEAL_JS % {"sel": REVEAL_SELECTEURS.replace("\\", "\\\\").replace("'", "\\'")})
