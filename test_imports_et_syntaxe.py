import importlib
import py_compile
import re

import _chemins  # noqa: F401
from _chemins import RACINE

MODULES = ["consolidation_groupe", "comparaison_officielle", "grand_livre", "rapport_conformite", "verifier_dependances", "parametres_fiscaux", "couverture_normes", "interface_maroifrs", "charte_pdf", "modeles_excel"]


def test_import_des_modules():
    for m in MODULES:
        importlib.import_module(m)


def test_compilation_de_tous_les_fichiers_python():
    for f in list(RACINE.glob("*.py")) + list((RACINE / "tests").glob("*.py")):
        py_compile.compile(str(f), doraise=True)


def test_syntaxe_compatible_python_39():
    """Python 3.9 est la version minimale annoncée : pas de f-string avec barre oblique inverse ni de guillemets identiques dans une expression."""
    for f in RACINE.glob("*.py"):
        for i, l in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            assert not re.search(r"""f"[^"]*\{[^}"]*\\[^}"]*\}""", l), f"{f.name}:{i} barre oblique inverse dans une f-string"
            assert not re.search(r"""f"[^"]*\{[^}"]*"[^}"]*\}""", l), f"{f.name}:{i} guillemets identiques dans une f-string"
            assert not re.match(r"\s*match\s+\w+.*:\s*$", l), f"{f.name}:{i} instruction match (Python 3.10+)"


def test_fichiers_du_projet_presents_et_json_valides():
    import json
    for f in ("app.py", "lancer.bat", "lancer_tests.bat", "requirements.txt", "config.toml", "referentiel_cgnc.json", "referentiel_ifrs.json",
              "verifier_dependances.py", "parametres_fiscaux.py", "couverture_normes.py", "interface_maroifrs.py", "charte_pdf.py", "modeles_excel.py", ".streamlit/config.toml", "LISEZ-MOI.txt", "CHANGELOG.txt"):
        assert (RACINE / f).exists(), f
    for f in ("referentiel_cgnc.json", "referentiel_ifrs.json"):
        json.loads((RACINE / f).read_text(encoding="utf-8"))


def test_lancer_bat_verifie_toutes_les_dependances():
    bat = (RACINE / "lancer.bat").read_bytes()
    assert b"\r\n" in bat and b"\n" not in bat.replace(b"\r\n", b"")              # fins de ligne Windows
    texte = bat.decode("utf-8")
    assert "verifier_dependances.py" in texte and "pip show streamlit" not in texte    # plus de contrôle limité à Streamlit
    assert "pip install" in texte and "requirements.txt" in texte
    import verifier_dependances as v
    pip_noms = {p for p, _, _ in v.COMPOSANTS}
    requis = {re.split(r"[<>=\s]", l.strip())[0] for l in (RACINE / "requirements.txt").read_text().splitlines() if l.strip()}
    assert requis == pip_noms, (requis ^ pip_noms)                                  # le vérificateur couvre exactement requirements.txt
    for f in v.FICHIERS:
        assert (RACINE / f).exists(), f


def test_verificateur_detecte_un_composant_absent():
    import sys
    import verifier_dependances as v
    sauve = v.COMPOSANTS
    try:
        v.COMPOSANTS = [("paquet-inexistant", "paquet_inexistant_xyz", True)]
        assert v.verifier() == 1
        v.COMPOSANTS = [("facultatif-inexistant", "facultatif_inexistant_xyz", False)]
        assert v.verifier() == 1 and v.verifier(apres_installation=True) == 0
        v.COMPOSANTS = [("sys", "sys", True)]
        assert v.verifier() == 0
    finally:
        v.COMPOSANTS = sauve
