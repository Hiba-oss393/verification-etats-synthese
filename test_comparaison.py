import io

import pandas as pd

import _chemins  # noqa: F401
import comparaison_officielle as co

CALC = {"total_actif": 6_393_000.0, "total_passif": 6_393_000.0, "ca": 4_200_000.0, "resultat_net": 1_030_000.0}
META_C = {"Exercice": "2025", "Référentiel": "IFRS", "Périmètre": "Consolidé (groupe)", "Unité": "MAD", "Devise": "MAD"}
META_O = dict(META_C, Unité="Milliers de MAD")
OFF = {"total_actif": 6393.0, "total_passif": 6401.0, "ca": 4200.0, "resultat_net": 1118.0}     # JEU DE TEST fictif


def test_comparaison_valide_ecarts_et_unites():
    calc_avant = dict(CALC)
    r = co.comparer(CALC, OFF, META_C, META_O, {"nci_resultat": 88_000})
    assert r["comparable"] and r["ajustement"] == "aucun" and CALC == calc_avant         # aucun résultat calculé n'est modifié
    t = r["tableau"].set_index("Indicateur")
    assert t.loc["Total actif", "Écart (MAD)"] == 0 and t.loc["Total actif", "Statut"] == "Conforme (arrondis)"
    assert t.loc["Total passif", "Écart (MAD)"] == -8000
    assert abs(t.loc["Résultat net part du groupe", "Écart (MAD)"] + 88_000) < 1
    assert t.loc["Résultat net part du groupe", "Statut"] == "Écart significatif"
    assert "intérêts ne donnant pas le contrôle" in t.loc["Résultat net part du groupe", "Origines possibles de l'écart"]
    assert r["statut"] == "Écarts significatifs"


def test_refus_si_informations_absentes_aucune_valeur_par_defaut():
    for champ in ("Référentiel", "Périmètre", "Unité", "Devise", "Exercice"):
        for cote in ("calc", "off"):
            mc, mo = dict(META_C), dict(META_O)
            (mc if cote == "calc" else mo)[champ] = None
            r = co.comparer(CALC, OFF, mc, mo)
            assert not r["comparable"] and r["statut"] == "Informations à compléter", (champ, cote)
            assert r["tableau"].empty and r["manquants"], (champ, cote)
            assert any(champ.lower().replace("é", "é") in m.lower() or champ[:-1].lower() in m.lower() for m in r["manquants"])
    mo = dict(META_O, Unité=co.A_CHOISIR)           # la valeur d'attente de la liste n'est jamais acceptée
    assert co.comparer(CALC, OFF, META_C, mo)["statut"] == "Informations à compléter"


def test_comparaison_non_valable_si_exercice_referentiel_perimetre_devise_differents():
    for champ, valeur in (("Exercice", "2024"), ("Référentiel", "CGNC (normes marocaines)"), ("Périmètre", "Social (société seule)"), ("Devise", "Autre devise")):
        r = co.comparer(CALC, OFF, META_C, dict(META_O, **{champ: valeur}))
        assert not r["comparable"] and r["statut"] == "Comparaison non valable" and r["problemes"], champ
        assert (r["tableau"]["Statut"] == "Non comparable").all()


def test_import_fichier_pas_de_valeurs_par_defaut():
    brut = pd.DataFrame([{"Société": "Test", "Exercice": 2025, "Total actif": 1, "Total passif": 1, "Chiffre d'affaires": 2,
                          "Résultat net part du groupe": 3}])             # référentiel, périmètre, unité, devise absents
    l = co.lire_officiel(brut)[0]
    assert l["Référentiel"] is None and l["Périmètre"] is None and l["Unité"] is None and l["Devise"] is None
    assert l["Exercice"] == "2025" and l["resultat_net"] == 3.0
    brut["Unité"] = "euros"                                                  # valeur hors liste : refusée, non corrigée
    assert co.lire_officiel(brut)[0]["Unité"] is None
    brut["Unité"] = "Milliers de MAD"
    assert co.lire_officiel(brut)[0]["Unité"] == "Milliers de MAD"
    b = io.BytesIO()
    co.modele_officiel().to_excel(b, index=False)                            # le modèle ne contient aucun chiffre ni valeur préremplie
    m = co.modele_officiel().iloc[0]
    assert all(pd.isna(m[c]) for c in ("Exercice", "Référentiel", "Périmètre", "Unité", "Devise", "Total actif", "Résultat net part du groupe"))
    try:
        co.lire_officiel(pd.DataFrame([{"Société": "x"}]))
    except ValueError as e:
        assert "Colonnes manquantes" in str(e)
    else:
        raise AssertionError("fichier incomplet accepté")
