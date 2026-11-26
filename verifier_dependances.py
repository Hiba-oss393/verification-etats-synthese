"""Vérifie que l'environnement permet de lancer MaroIFRS (appelé par lancer.bat, utilisable seul : python verifier_dependances.py).

Codes de sortie :  0 = tout est prêt ;  1 = composants à installer ;  2 = fichiers du projet manquants ;  3 = Python trop ancien.
Option --apres-installation : un composant facultatif (OCR) toujours absent n'empêche pas le lancement (code 0, avertissement).
"""
import importlib
import sys
from pathlib import Path

ICI = Path(__file__).resolve().parent
# nom pip -> nom d'import ; True = indispensable, False = facultatif (l'application démarre sans, avec une fonction en moins)
COMPOSANTS = [("streamlit", "streamlit", True), ("pandas", "pandas", True), ("altair", "altair", True), ("openpyxl", "openpyxl", True),
              ("xlrd", "xlrd", True), ("pdfplumber", "pdfplumber", True), ("reportlab", "reportlab", True),
              ("rapidocr-onnxruntime", "rapidocr_onnxruntime", False)]
FICHIERS = ["app.py", "consolidation_groupe.py", "comparaison_officielle.py", "grand_livre.py", "rapport_conformite.py",
            "parametres_fiscaux.py", "couverture_normes.py", "interface_maroifrs.py", "synthese_conformite.py", "transformation_comptes.py", "charte_pdf.py", "modeles_excel.py", "i18n.py", "traductions_en.txt",
            "referentiel_cgnc.json", "referentiel_ifrs.json", "config.toml", "requirements.txt"]
PYTHON_MIN = (3, 9)
STREAMLIT_MIN = (1, 40)


def verifier(apres_installation: bool = False) -> int:
    if sys.version_info < PYTHON_MIN:
        print(f"Python {sys.version_info.major}.{sys.version_info.minor} est trop ancien : version {PYTHON_MIN[0]}.{PYTHON_MIN[1]} ou plus récente requise.")
        return 3
    manquants_f = [f for f in FICHIERS if not (ICI / f).exists()]
    if manquants_f:
        print("Fichiers du projet introuvables dans le dossier : " + ", ".join(manquants_f))
        print("Recopiez TOUS les fichiers du dossier MaroIFRS au même endroit que lancer.bat.")
        return 2
    absents_obl, absents_fac = [], []
    for pip_nom, import_nom, obligatoire in COMPOSANTS:
        try:
            importlib.import_module(import_nom)
        except Exception as e:  # ImportError, mais aussi bibliothèque binaire défectueuse
            (absents_obl if obligatoire else absents_fac).append((pip_nom, str(e)[:80]))
    # la navigation (menu ☰, pages) demande Streamlit 1.40 ou plus récent
    if not any(p == "streamlit" for p, _ in absents_obl):
        try:
            import streamlit
            import re
            m = re.match(r"(\d+)\.(\d+)", str(streamlit.__version__))
            if m and (int(m.group(1)), int(m.group(2))) < STREAMLIT_MIN:
                absents_obl.append(("streamlit>=%d.%d" % STREAMLIT_MIN, "version %s trop ancienne" % streamlit.__version__))
        except Exception:
            pass
    if sys.version_info >= (3, 13):
        print("Avertissement : Python 3.13 ou plus récent ; la reconnaissance de texte (OCR) peut ne pas s'installer. Python 3.10 à 3.12 est recommandé.")
    for pip_nom, err in absents_fac:
        print(f"Composant facultatif absent : {pip_nom} (l'import de documents scannés par OCR sera indisponible).")
    for pip_nom, err in absents_obl:
        print(f"Composant indispensable absent : {pip_nom}")
    if absents_obl or (absents_fac and not apres_installation):
        return 1
    print("Environnement prêt : tous les composants indispensables sont installés.")
    return 0


if __name__ == "__main__":
    sys.exit(verifier("--apres-installation" in sys.argv))
