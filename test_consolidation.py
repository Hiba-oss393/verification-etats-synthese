import pandas as pd

import _chemins  # noqa: F401
from _donnees import K, bal, entites_base, filiale, mere
from consolidation_groupe import consolider, valider_perimetre


def _poste(tab, nom):
    return float(tab.loc[tab["Poste"] == nom, "Montant"].iloc[0])


def test_cas_main_integration_globale():
    """Valeurs attendues calculées à la main : goodwill 200, minoritaires 130 (bilan) et 20 (résultat), résultat part du groupe 180."""
    r = consolider(entites_base(), None, "IFRS", 0.20)
    assert abs(r["ecart_bilan"]) < 1
    assert abs(r["total_actif"] - 1450 * K) < 1 and abs(r["total_passif"] - 1450 * K) < 1
    p = r["perimetre"].iloc[0]
    assert abs(p["Goodwill"] - (200 * K)) < 1  # 600 - 80 % x (450 + 50)
    assert abs(p["Intérêts ne donnant pas le contrôle"] - (130 * K)) < 1  # 20 % x (600 + 50)
    assert abs(r["nci"] - (130 * K)) < 1
    assert abs(r["resultat_ensemble"] - 200 * K) < 1 and abs(r["resultat_groupe"] - 180 * K) < 1
    assert abs(r["ca"] - (550 * K)) < 1
    assert _poste(r["passif"], "Réserves consolidées") == 40  # 80 % x (600 - 450 - 100 de résultat)
    assert _poste(r["actif"], "Goodwill (écarts d'acquisition)") == 200


def test_mise_en_equivalence():
    """Valeur MEE = 30 % x 340 + (120 - 30 % x 300) = 132 ; quote-part de résultat = 12 ; résultat part du groupe = 192."""
    r = consolider(entites_base(avec_associee=True), None, "IFRS", 0.20)
    assert abs(r["ecart_bilan"]) < 1
    assert abs(r["total_actif"] - (1462 * K)) < 1
    assert _poste(r["actif"], "Titres mis en équivalence") == 132
    assert _poste(r["cpc"], "Quote-part dans les résultats des entreprises mises en équivalence") == 12
    assert abs(r["resultat_groupe"] - (192 * K)) < 1


def test_elimination_ventes_achats_et_creance():
    intra = pd.DataFrame([{"Type": "Vente / achat", "Entité A": "Mère SA", "Entité B": "Filiale SARL", "Montant": 100 * K},
                          {"Type": "Créance / dette", "Entité A": "Mère SA", "Entité B": "Filiale SARL", "Montant": 30 * K}])
    r = consolider(entites_base(), intra, "IFRS", 0.20)
    assert abs(r["ca"] - (450 * K)) < 1  # 550 - 100
    assert _poste(r["cpc"], "Achats consommés") == -250               # -(200 + 150 - 100)
    assert abs(r["resultat_groupe"] - (180 * K)) < 1  # l'élimination des ventes/achats est neutre sur le résultat
    assert abs(r["total_actif"] - 1420 * K) < 1 and abs(r["total_passif"] - 1420 * K) < 1   # créance et dette de 30 retirées des deux côtés
    assert _poste(r["actif"], "Clients et comptes rattachés") == 20 and _poste(r["passif"], "Fournisseurs et comptes rattachés") == 70
    assert not r["verifications"], r["verifications"]


def test_marge_en_stock_equilibre_et_resultat():
    f = bal([("1111", "Capital", 0, 400), ("1152", "Réserves", 0, 100), ("2321", "Constructions", 400, 0), ("3111", "Stocks", 100, 0),
             ("5141", "Banque", 200, 0), ("4411", "Fournisseurs", 0, 100), ("7111", "Ventes", 0, 250), ("6111", "Achats", 150, 0)])
    ent = entites_base()
    ent[1]["df"] = f
    intra = pd.DataFrame([{"Type": "Marge en stock", "Entité A": "Mère SA", "Entité B": "Filiale SARL", "Montant": 40 * K}])
    r = consolider(ent, intra, "IFRS", 0.20)
    assert abs(r["ecart_bilan"]) < 1
    assert abs(r["resultat_groupe"] - ((200 - 32) * K - 20 * K)) < 1  # marge nette d'impôt 32 ; minoritaires 20 inchangés (le vendeur est la mère)
    assert _poste(r["actif"], "Impôt différé actif") == 8


def test_frais_preliminaires_ifrs_vs_cgnc_et_minoritaires():
    f = bal([("1111", "Capital", 0, 400), ("1152", "Réserves", 0, 100), ("2111", "Frais préliminaires", 50, 0), ("2321", "Constructions", 400, 0),
             ("5141", "Banque", 250, 0), ("4411", "Fournisseurs", 0, 100), ("7111", "Ventes", 0, 250), ("6111", "Achats", 150, 0)])
    ent = entites_base()
    ent[1]["df"] = f
    ri, rc = consolider(ent, None, "IFRS", 0.20), consolider(ent, None, "CGNC", 0.20)
    assert abs(ri["ecart_bilan"]) < 1 and abs(rc["ecart_bilan"]) < 1
    assert abs(rc["total_actif"] - ri["total_actif"] - 40 * K) < 1            # IFRS : 50 éliminés, impôt différé actif 10
    assert abs(ri["nci"] - (130 * K - int(0.2 * 50 * 0.8 * K))) < 1  # minoritaires supportent 20 % de 50 x (1 - 20 %)


def test_integration_proportionnelle_cgnc_et_conversion_ifrs():
    ent = entites_base()
    ent[1].update(methode="IP", pct_controle=50, pct_interet=50, cout_titres=300 * K, fp_acquisition=500 * K)
    ent[0]["df"] = mere(banque=750, titres=300)
    rc = consolider(ent, None, "CGNC", 0.20)
    assert abs(rc["ecart_bilan"]) < 1 and abs(rc["nci"]) < 1
    assert abs(consolider(ent, None, "IFRS", 0.20)["ecart_bilan"]) < 1
    ri = consolider(ent, None, "IFRS", 0.20)
    assert "Mise en équivalence" in ri["perimetre"].iloc[0]["Méthode"]            # IFRS 11 : l'IP est remplacée par la MEE
    assert any("IFRS 11" in n for n in ri["notes"])


def test_refus_si_donnees_insuffisantes():
    base = entites_base()
    for ent, motif in ((base[:1], "Au moins deux sociétés"),
                       ([base[0], dict(base[1], df=None)], "aucune balance"),
                       ([base[0], dict(base[1], cout_titres=None)], "coût d'acquisition"),
                       ([base[0], dict(base[1], fp_acquisition=None)], "capitaux propres à la date d'acquisition"),
                       ([base[0], dict(base[1], pct_controle=0, pct_interet=0)], "pourcentages"),
                       ([base[0], dict(base[1], pct_controle=40, pct_interet=40)], "IFRS 10"),
                       ([base[0], dict(base[1], df=bal([("1111", "Capital", 0, 100), ("5141", "Banque", 50, 0), ("7111", "V", 0, 10)]))], "déséquilibrée")):
        try:
            consolider(ent, None, "IFRS", 0.20)
        except ValueError as e:
            assert motif in str(e), (motif, str(e))
        else:
            raise AssertionError(f"la consolidation aurait dû être refusée ({motif})")
    deux_meres = [dict(base[0]), dict(base[1], role="mere")]
    assert any("exactement une société mère" in b for b in valider_perimetre(deux_meres)[0])
    sans_cpc = dict(base[1], df=bal([("1111", "Capital", 0, 100), ("5141", "Banque", 100, 0)]))
    assert any("classes 6 et 7" in b for b in valider_perimetre([base[0], sans_cpc])[0])


def test_hypotheses_et_donnees_manquantes_signalees():
    r = consolider(entites_base(), None, "IFRS", 0.20)
    assert any("IAS 21" in h for h in r["hypotheses"]) and any("IFRS 16" in h for h in r["hypotheses"])
    manque = " ".join(r["donnees_manquantes"])
    assert "Aucune opération intragroupe" in manque and "Aucun écart d'évaluation" not in manque   # un écart a été saisi dans ce cas
    ent = entites_base()
    ent[1]["ecart_evaluation"] = 0
    assert any("Aucun écart d'évaluation" in m for m in consolider(ent, None, "IFRS", 0.20)["donnees_manquantes"])
    # dividendes comptabilisés par la mère mais non déclarés
    ent = entites_base()
    ent[0]["df"] = mere(avec_div=True)
    r = consolider(ent, None, "IFRS", 0.20)
    assert any("aucun dividende intragroupe n'est déclaré" in m for m in r["donnees_manquantes"])
    r = consolider(ent, None, "IFRS", 0.20, {"Filiale SARL": 40 * K})
    assert not any("aucun dividende" in m for m in r["donnees_manquantes"])


def test_verifications_de_coherence():
    ent = entites_base()
    intra = pd.DataFrame([{"Type": "Vente / achat", "Entité A": "Mère SA", "Entité B": "Filiale SARL", "Montant": 5000 * K}])
    r = consolider(ent, intra, "IFRS", 0.20)
    assert any("supérieure au poste correspondant" in v for v in r["verifications"])
    ent[0]["df"] = mere(banque=350, titres=700)                      # compte 251 de la mère ≠ coût déclaré
    r = consolider(ent, None, "IFRS", 0.20)
    assert any("compte 251" in v for v in r["verifications"])
    try:
        consolider(entites_base(), pd.DataFrame([{"Type": "Vente / achat", "Entité A": "Inconnue", "Entité B": "Filiale SARL", "Montant": 10}]), "IFRS", 0.2)
    except ValueError as e:
        assert "société inconnue" in str(e)
    else:
        raise AssertionError("société inconnue non refusée")
