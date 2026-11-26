"""Chemins communs : tous les tests se repèrent par rapport au dossier du projet, jamais par un chemin absolu."""
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))


class Ignore(Exception):
    """Test non exécutable dans cet environnement (par exemple Streamlit absent) : signalé comme IGNORÉ, jamais comme réussi."""
