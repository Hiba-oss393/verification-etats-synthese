"""Bandeau des services, apparitions au défilement, sélecteur de langue : couche visuelle seulement."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import interface_maroifrs as ui
import i18n

ATTENDUS = ["Transformation IAS/IFRS", "Conformité comptable", "Référentiel CGNC", "Analyse financière",
            "Contrôle fiscal", "Consolidation", "Rapports professionnels", "Comparaison des exercices"]


def test_bandeau_services_huit_elements_sans_indice_boursier():
    h = ui.services_html()
    for nom in ATTENDUS:
        assert nom in h
    assert "démo" not in h.lower() and "DÉMO" not in h and "Indice" not in h
    assert h.count('class="sv-i"') == 16            # 8 éléments x 2 (boucle continue)


def test_bandeau_services_traduit_en_anglais():
    h = ui.services_html(lambda t: i18n.tr(t, "en"), "en")
    assert "IAS/IFRS transformation" in h and "Professional reports" in h and "Comparison of financial years" in h


def test_ancien_ticker_et_donnees_demo_toujours_disponibles():
    assert callable(ui.ticker_html) and callable(ui.indicateurs_demo)


def test_script_apparition_et_css():
    h = ui.reveal_html()
    assert "IntersectionObserver" in h and "rv-on" in h
    for regle in ("html.rv-on .rv-t:not(.rv-in)", "@keyframes pageIn", "prefers-reduced-motion", "st-key-langue_ui", ".sv-i"):
        assert regle in ui.CSS


def test_selecteur_langue_toujours_branche_dans_app():
    src = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
    assert 'key="langue_ui"' in src and "on_change=changer_langue_ui" in src and "ui.services_html(" in src and "ui.reveal_html()" in src
