"""Mission « photographies, langue, menu » : tests de la couche visuelle et de la traduction.
Ils vérifient (1) que les modules comptables sont OCTET POUR OCTET ceux de la version de départ, (2) les photographies, le carrousel et le bandeau défilant,
(3) la traduction centralisée (aucune donnée ni valeur de widget modifiée), (4) le correctif de menu, (5) que l'application s'exécute dans les deux langues
avec des résultats identiques. Ils s'exécutent dans le simulateur de Streamlit : aucun d'eux ne remplace un essai dans le navigateur."""
import hashlib
import re

import _chemins  # noqa: F401
import harness_streamlit as hs
from _chemins import RACINE
from _donnees import K  # noqa: F401
import interface_maroifrs as ui
import i18n

# Empreintes SHA-256 des modules de la version de départ (MaroIFRS_VERSION_FINALE.zip) : calculs, rapports, modèles Excel, charte PDF.
EMPREINTES = {
    "grand_livre.py": "9210998f39825b940fb0cf592771bdf86b528f78a016e28065f4c419eacdb5b0",
    "consolidation_groupe.py": "ff6cceccc9af110c19c85886b061c0e9e3cee9c13eb67d26c1649ae98b8c5f7f",
    "comparaison_officielle.py": "f9e1415afc66a9f7abc90c1991d13962d4d99622f4e389959ae56e9d22c80ff8",
    "parametres_fiscaux.py": "af16aa0e2a28061153234abd548b3cdcadd8044620899f9ee4577612bc056504",
    "couverture_normes.py": "8c4231b7db41d97a522cea4daaa2104e2ec5873ac88c55662461c556d60876c8",
    "rapport_conformite.py": "c97cc54d9aa893a9835c894eeab23892cbc5de2846fe96991c97d53fd45cae63",
    "synthese_conformite.py": "533c57e134bfe1f4fc8d3deed49278ce6ab9c26768080f0f5f59045f05566b30",
    "transformation_comptes.py": "6c713c30dddf87f579273585c202c3f76931ac5ee800c5aedd1a0351f47954aa",
    "charte_pdf.py": "d23c3388ace61d14671f1ebbff1d6338fa1c84dcf4f5189aaf00524e3cde6894",
    "modeles_excel.py": "26d0f45d16f5c6e7542a920d3f1381393943c5899680a4a3e0aa18f1dead2b00",
}


def test_modules_comptables_identiques_a_la_version_de_depart():
    for nom, h in EMPREINTES.items():
        assert hashlib.sha256((RACINE / nom).read_bytes()).hexdigest() == h, nom + " a été modifié"


def test_huit_photographies_integrees_sans_deformation():
    from PIL import Image
    dossier = RACINE / "assets" / "images"
    attendus = ["image_01_accueil", "image_02_marche_financier", "image_03_reunion_financiere", "image_04_transformation", "image_05_resultats",
                "image_06_dashboard", "image_07_consolidation", "image_08_controle_fiscal"]
    for nom in attendus:
        f = dossier / (nom + ".jpg")
        assert f.exists(), nom
        w, h = Image.open(f).size
        assert abs(w / h - 1672 / 941) < 0.005, (nom, w, h)               # proportions de l'original conservées (pas d'étirement)
        assert f.stat().st_size < 260_000, nom                             # images allégées : pas de ralentissement inutile
        assert ui.image_uri(nom).startswith("data:image/jpeg;base64,")
    for nom in attendus[3:]:
        v = dossier / (nom + "_vignette.jpg")
        w, h = Image.open(v).size
        assert abs(w / h - 1672 / 941) < 0.01 and v.stat().st_size < 80_000, nom
    assert ui.image_uri("image_inexistante") == ""                          # fichier absent : repli sans erreur


def test_photos_des_pages_et_repli():
    assert set(ui.PHOTOS_PAGES) == {"trans", "res", "dash", "conso", "fisc"}
    for cle in ui.PHOTOS_PAGES:
        h = ui.photo_bandeau(cle)
        assert 'class="bd-ph"' in h and "<img" in h and 'alt="' in h and "loading=\"lazy\"" in h, cle
    assert ui.photo_bandeau("norm") == ""                                  # page sans photo : motif d'origine conservé par app.py
    src = (RACINE / "app.py").read_text(encoding="utf-8")
    assert 'class="bd-m"' in src and "ui.photo_bandeau(cle" in src          # le motif d'origine reste le repli


def test_carrousel_trois_photos_fondu_commande_manuelle():
    h = ui.carrousel_html()
    assert h.count('class="slide') == 3 and h.count("<img") == 3
    assert 5000 <= ui.CARROUSEL_DELAI_MS <= 7000                            # consigne : 5 à 7 secondes
    assert "transition:opacity" in h                                        # fondu
    assert 'class="arr pv"' in h and 'class="arr nx"' in h and h.count("<button type=\"button\" aria-label") == 3   # flèches + 3 indicateurs
    assert "ArrowLeft" in h and "mouseenter" in h and "visibilitychange" in h
    assert {d["pos"] for d in ui.CARROUSEL_DIAPOS} == {"left", "bottom", "right"}   # position du texte adaptée à chaque photographie
    src = (RACINE / "app.py").read_text(encoding="utf-8")
    for d in ui.CARROUSEL_DIAPOS:                                           # le bouton d'action vise un bouton Streamlit qui existe déjà
        cle = d["cible"]
        assert ('key="%s"' % cle in src) or ('"ouvrir_" + page_f' in src and cle.startswith("ouvrir_")), cle
        assert "<button class=\"cta\"" in h and 'data-cible="%s"' % cle in h
    assert "st.components" not in h and "http" not in re.sub(r"www\.w3\.org", "", h)   # aucune ressource externe ni rechargement


def test_bandeau_defilant_donnees_reelles_et_demo_signalees():
    ind = [{"libelle": "Réel", "valeur": "43", "variation": None, "serie": None},
           {"libelle": "Démo", "valeur": "1", "variation": 0.84, "serie": [1, 2, 3], "demo": True},
           {"libelle": "Baisse", "valeur": "2", "variation": -1.5, "serie": [3, 2, 1]}]
    h = ui.ticker_html(ind)
    assert h.count('class="demo"') == 2                                     # 1 indicateur fictif, présent dans la copie défilante aussi
    assert "▲" in h and "▼" in h and "<polyline" in h
    assert "-1,50" in h or "−1,50" in h
    for i in ui.indicateurs_demo():
        assert i["demo"] is True                                            # toute valeur d'exemple porte la pastille DÉMO
    assert "tkScroll" in ui.CSS and "animation-play-state: paused" in ui.CSS
    src = (RACINE / "app.py").read_text(encoding="utf-8")
    assert "def indicateurs_bandeau" in src and "valeurs fictives" in src


def test_menu_correctif_espacement_premier_bouton():
    css = ui.CSS
    assert ".tiroir-g" in css and "has(.tiroir-g) + [data-testid=\"stElementContainer\"]" in css
    assert ".st-key-tiroir {padding-top: 1.35rem" in css
    assert ":where(.stDownloadButton) button:active {transform: scale(.98)" in css  # animation d'origine conservée
    src = (RACINE / "app.py").read_text(encoding="utf-8")
    for p in ui.PAGES_MENU:                                                 # toutes les entrées du menu et leurs actions sont inchangées
        assert 'key="menu_" + p' in src
    assert "on_click=aller_depuis_barre, args=(p,)" in src


def test_traduction_exacte_fragments_et_balises():
    assert i18n.tr("Résultats", "fr") == "Résultats"
    assert i18n.tr("Résultats", "en") == "Results"
    assert i18n.tr("Télécharger un modèle de balance", "en") == "Download a trial balance template"
    assert i18n.tr("<div class=\"x\">Qualité des données importées</div>", "en") == "<div class=\"x\">Quality of imported data</div>"
    assert i18n.tr("20 normes contrôlées · 43 au référentiel", "en") == "20 standards checked · 43 in the reference"
    assert i18n.tr("Texte inconnu du dictionnaire xyz", "en") == "Texte inconnu du dictionnaire xyz"     # jamais d'erreur ni de perte
    assert i18n.tr("Compte 3421 : Clients", "en").startswith("Account 3421")                               # numéros conservés
    assert i18n.tr("L&#x27;exercice", "en") == "L&#x27;exercice"                                              # texte inconnu avec entité : conservé tel quel
    assert i18n.tr("Aucune règle applicable : repris tel quel", "en") == "No applicable rule: carried over as is"
    assert i18n.tr("<style>.a{}</style>", "en") == "<style>.a{}</style>"
    assert i18n.tr(123, "en") == 123


def test_dictionnaire_bien_forme():
    assert len(i18n.DICT_EN) > 900
    for fr, en in i18n.DICT_EN.items():
        assert fr.strip() and en.strip()
    src = (RACINE / "i18n.py").read_text(encoding="utf-8")
    for interdit in ("grand_livre", "consolidation_groupe", "rapport_conformite", "couverture_normes", "parametres_fiscaux", "pandas"):
        assert "import " + interdit not in src                              # la traduction ne touche à aucun module métier


def test_enveloppes_streamlit_valeurs_des_widgets_inchangees():
    class Faux:
        def __init__(self):
            self.session_state = {"lang": "en"}
            self.appels = []
        def selectbox(self, label, options, **k):
            self.appels.append((label, [k["format_func"](o) for o in options]))
            return options[0]
        def button(self, label, **k):
            self.appels.append((label, None))
            return False
        def markdown(self, body, **k):
            self.appels.append((body, None))
    st = Faux()
    i18n.installer(st)
    i18n.installer(st)                                                      # idempotent : pas de double enveloppe
    valeur = st.selectbox("Choisir une norme", ["IAS 12", "éliminé"])
    assert valeur == "IAS 12"                                               # la valeur renvoyée reste celle d'origine (jamais traduite)
    assert st.appels[0] == ("Choose a standard", ["IAS 12", "eliminated"])
    st.button("Importer mes documents")
    assert st.appels[1][0] == "Import my documents"
    st.session_state["lang"] = "fr"
    st.button("Importer mes documents")
    assert st.appels[2][0] == "Importer mes documents"                      # en français : appel strictement inchangé


def _balance():
    import test_interface as ti
    return {"uploads": {"main": [hs.FakeFile("balance.xlsx", ti._xlsx(ti._equilibrer(ti.BALANCE_TVA)))]}}


def _executer(lang):
    ancien = hs.FakeSt.__init__

    def init(self, scn):
        ancien(self, scn)
        self.query_params = {"lang": lang}
    hs.FakeSt.__init__ = init
    try:
        return hs.run(_balance())
    finally:
        hs.FakeSt.__init__ = ancien


def test_application_identique_en_francais_et_en_anglais():
    st_fr, g_fr, err_fr = _executer("fr")
    st_en, g_en, err_en = _executer("en")
    assert err_fr is None and err_en is None, (err_fr or err_en)
    assert st_fr.session_state["lang"] == "fr" and st_en.session_state["lang"] == "en"
    # mêmes données, mêmes calculs, mêmes constats, même statut final dans les deux langues
    assert g_fr["df"].equals(g_en["df"])
    assert g_fr["constats_df"].equals(g_en["constats_df"])
    assert g_fr["rap"]["statut"] == g_en["rap"]["statut"]                    # même statut final de conformité (calculé une seule fois, jamais traduit)
    assert g_fr["rap"]["indicateurs"] == g_en["rap"]["indicateurs"]
    assert g_fr["rap"]["traitements"].equals(g_en["rap"]["traitements"])
    assert g_fr["rap"]["comparatif"].equals(g_en["rap"]["comparatif"])
    d_fr, d_en = st_fr.log["download"], st_en.log["download"]               # mêmes exports (PDF / Excel) : mêmes tailles (libellés traduits, 1-2 octets d'horodatage tolérés)
    assert len(d_fr) == len(d_en) and all(abs(a[1] - b[1]) <= 64 for a, b in zip(d_fr, d_en))
    texte_fr, texte_en = " ".join(st_fr.log["textes"]), " ".join(st_en.log["textes"])
    assert "Du plan comptable marocain" in texte_fr or "Quartier" in texte_fr or "CGNC → IAS / IFRS" in texte_fr
    assert "Your Moroccan financial statements" in texte_en and "Vos états de synthèse marocains" not in texte_en   # l'accueil est traduit en anglais


def test_selecteur_de_langue_dans_l_en_tete():
    src = (RACINE / "app.py").read_text(encoding="utf-8")
    assert 'key="langue_ui"' in src and "changer_langue_ui" in src and "i18n.initialiser_langue(st)" in src
    assert i18n.LANGUES == {"fr": "🇫🇷 Français", "en": "🇬🇧 English"}
    class S:
        session_state = {}
        query_params = {"lang": "en"}
    assert i18n.initialiser_langue(S) == "en"                               # langue conservée via l'adresse (?lang=en)
    S.session_state["lang"] = "fr"
    assert i18n.initialiser_langue(S) == "fr"                               # puis par la session pendant la navigation
    i18n.changer_langue(S, "en")
    assert S.session_state["lang"] == "en" and S.query_params["lang"] == "en"


def test_widgets_non_cles_ont_desormais_une_cle_stable():
    import ast
    t = ast.parse((RACINE / "app.py").read_text(encoding="utf-8"))
    sensibles = {"text_input", "number_input", "selectbox", "radio", "checkbox", "multiselect", "file_uploader", "slider", "text_area", "toggle", "date_input"}
    for n in ast.walk(t):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in sensibles:
            assert any(k.arg == "key" for k in n.keywords), "widget sans clé à la ligne %d" % n.lineno
