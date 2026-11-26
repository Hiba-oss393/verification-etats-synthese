"""Lance tous les tests sans dépendre de pytest : python tests\\run_tests.py
Chaque test est signalé REUSSI, ECHEC ou IGNORE (non exécutable ici : jamais compté comme réussi). Code de sortie 1 s'il y a un échec."""
import importlib
import sys
import time
import traceback
from pathlib import Path

ICI = Path(__file__).resolve().parent
sys.path.insert(0, str(ICI))
sys.path.insert(0, str(ICI.parent))
from _chemins import Ignore  # noqa: E402


def main() -> int:
    reussis, echecs, ignores = [], [], []
    for f in sorted(ICI.glob("test_*.py")):
        mod = importlib.import_module(f.stem)
        for nom in [n for n in dir(mod) if n.startswith("test_") and callable(getattr(mod, n))]:
            t0 = time.time()
            try:
                getattr(mod, nom)()
            except Ignore as e:
                ignores.append((f.stem, nom, str(e)))
                print(f"IGNORE   {f.stem}.{nom} : {e}")
            except Exception:
                echecs.append((f.stem, nom))
                print(f"ECHEC    {f.stem}.{nom}")
                print("         " + traceback.format_exc(limit=3).replace("\n", "\n         "))
            else:
                reussis.append((f.stem, nom))
                print(f"REUSSI   {f.stem}.{nom}  ({time.time() - t0:.1f} s)")
    print(f"\nRésultat : {len(reussis)} réussi(s), {len(echecs)} échec(s), {len(ignores)} ignoré(s).")
    if ignores:
        print("Les tests ignorés n'ont PAS été exécutés : ils ne comptent pas comme réussis.")
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())
