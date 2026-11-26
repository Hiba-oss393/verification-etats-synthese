import json
import os
import tempfile

import pandas as pd

import _chemins  # noqa: F401
import harness_streamlit as hs
from _chemins import RACINE
from test_couverture_normes import balance_complete
from test_interface import _xlsx


_OCTETS = {}


def _fichier(nom, delta=0):
    """Octets générés une seule fois par (nom, delta) : deux exports Excel successifs n'ont pas toujours la même taille (horodatage interne)."""
    if (nom, delta) not in _OCTETS:
        d = balance_complete()[["compte", "libelle", "debit", "credit"]].copy()
        d.loc[d["compte"] == "5141", ["debit"]] += delta
        d.loc[d["compte"] == "1161", ["credit"]] += delta
        _OCTETS[(nom, delta)] = _xlsx(d)
    return hs.FakeFile(nom, _OCTETS[(nom, delta)])


def _histo(chemin):
    return json.load(open(chemin, encoding="utf-8"))


def test_historique_enregistre_conserve_et_ne_duplique_pas():
    chemin = os.path.join(tempfile.mkdtemp(), "historique.json")
    hs.run({"uploads": {"main": [_fichier("a.xlsx")]}, "historique": chemin})
    h = _histo(chemin)
    assert len(h) == 1 and h[0]["fichier"] == "a.xlsx" and h[0]["comptes"] == 14 and 0 <= h[0]["conformite"] <= 100 and h[0]["constats"]
    hs.run({"uploads": {"main": [_fichier("a.xlsx")]}, "historique": chemin})            # même fichier : la ligne est mise à jour, pas dupliquée
    assert len(_histo(chemin)) == 1
    hs.run({"uploads": {"main": [_fichier("b.xlsx", delta=10)]}, "historique": chemin})
    assert [e["fichier"] for e in _histo(chemin)] == ["a.xlsx", "b.xlsx"]                 # l'entrée précédente est conservée


def test_historique_existant_est_conserve_et_fichier_corrompu_ne_plante_pas():
    chemin = os.path.join(tempfile.mkdtemp(), "historique.json")
    ancien = [{"cle": "x|1", "fichier": "ancien.xlsx", "date": "01/01/2026 10:00:00", "comptes": 5, "conformite": 80.0, "valeur": 70.0,
               "statuts": {"éliminé": 0, "à revoir": 1, "inchangé": 4, "reclassé": 0, "ajusté": 0}, "constats": [], "conformite_cgnc": None}]
    json.dump(ancien, open(chemin, "w", encoding="utf-8"), ensure_ascii=False)
    st, g, err = hs.run({"uploads": {"main": [_fichier("nouveau.xlsx")]}, "historique": chemin})
    assert err is None, err
    h = _histo(chemin)
    assert h[0] == ancien[0] and h[1]["fichier"] == "nouveau.xlsx"                         # l'ancien enregistrement n'est ni modifié ni perdu
    open(chemin, "w", encoding="utf-8").write("{ceci n'est pas du json")
    st, g, err = hs.run({"uploads": {"main": [_fichier("apres.xlsx")]}, "historique": chemin})
    assert err is None, err                                                                  # un fichier corrompu n'empêche pas l'application de démarrer


def test_les_tests_n_ecrivent_jamais_dans_le_vrai_historique():
    hs.run({"uploads": {"main": [_fichier("z.xlsx")]}})                                      # sans chemin imposé : le simulateur utilise un fichier temporaire
    assert not (RACINE / "historique.json").exists(), "un test a écrit dans l'historique réel du projet"
