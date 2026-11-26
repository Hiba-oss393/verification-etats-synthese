"""Consolidation d'un groupe : société mère, filiales, périmètre, éliminations, intérêts ne donnant pas le contrôle.

Module autonome (aucune dépendance à Streamlit). Les règles sont explicites :
  - IG  : intégration globale (contrôle, IFRS 10 / CGNC)            -> 100 % des comptes, intérêts minoritaires (NCI)
  - IP  : intégration proportionnelle (CGNC uniquement)              -> pourcentage d'intérêt des comptes, sans NCI
  - MEE : mise en équivalence (influence notable / co-entreprise)    -> IAS 28 / IFRS 11
Hypothèses simplificatrices (affichées dans le résultat) :
  - NCI évalués à la quote-part de l'actif net identifiable (IFRS 3.19, option « goodwill partiel »).
  - Écart d'évaluation des actifs identifiables porté en immobilisations corporelles, non amorti.
  - Un seul exercice, une seule devise (MAD) : aucune conversion de devises (IAS 21 non traitée).
Le module REFUSE de produire des comptes consolidés si les données sont insuffisantes (voir valider_perimetre).
"""
from __future__ import annotations

import pandas as pd

TOL = 1.0  # tolérance d'arrondi en MAD

TYPES_ELIMINATION = ["Créance / dette", "Compte courant", "Prêt / emprunt", "Vente / achat", "Marge en stock", "Marge en immobilisation"]
METHODES = {"IG": "Intégration globale", "IP": "Intégration proportionnelle (CGNC)", "MEE": "Mise en équivalence"}


def _solde(df: pd.DataFrame, *prefixes: str) -> float:
    masque = df["compte"].astype(str).str.startswith(tuple(prefixes))
    return float(df.loc[masque, "solde"].sum())


def preparer(df: pd.DataFrame) -> pd.DataFrame:
    """Garantit les colonnes compte / libelle / debit / credit / solde."""
    d = df.copy()
    d.columns = [str(c).strip().lower() for c in d.columns]
    for c in ("debit", "credit"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0)
    d["compte"] = d["compte"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    if "libelle" not in d.columns:
        d["libelle"] = ""
    d["solde"] = d["debit"] - d["credit"]
    return d


def agregats(df: pd.DataFrame) -> dict:
    """Postes d'une société (MAD), mêmes regroupements que les états consolidés de l'application."""
    s = lambda *p: _solde(df, *p)
    a_cpc = df["compte"].astype(str).str.startswith(("6", "7")).any()
    tres_passif = max(-s("55"), 0.0)
    incorp, corp = s("22", "282", "292"), s("23", "283", "293")
    nonval = max(s("21", "281"), 0.0)
    financ = s("2") - nonval - incorp - corp
    stocks, clients, vmp = s("31", "391"), s("342", "3942"), s("35", "395")
    autres_creances = s("3") - stocks - clients - vmp
    dispo = s("5") + tres_passif
    ca = -s("711", "712")
    r_net = -s("6", "7") if a_cpc else -s("119")
    capital, primes = -s("111"), -s("112")
    fp = -s("11", "13") + (-s("6", "7") if a_cpc else 0.0)
    fourn = -s("441")
    emprunts = -s("14")
    prov = -s("15")
    autres_dettes = -s("4") - fourn - s("16", "17")
    lignes = (ca + (-s("71") - ca) + (-s("611", "612", "613", "614", "615")) + (-s("617")) + (-s("618")) + (-s("616")) + (-s("619"))
              + (-s("63", "73")) + (-s("65", "75")) + (-s("67")))
    residu = (r_net - lignes) if a_cpc else 0.0   # comptes de résultat hors rubriques ci-dessus : rattachés au non courant
    return {
        "nonval": nonval, "incorp": incorp, "corp": corp, "financ": financ, "stocks": stocks, "clients": clients,
        "autres_creances": autres_creances, "vmp": vmp, "dispo": dispo, "tres_passif": tres_passif,
        "ca": ca, "autres_prod": -s("71") - ca, "achats": -s("611", "612", "613", "614", "615"), "perso": -s("617"),
        "autres_ch": -s("618"), "impt_tx": -s("616"), "dot": -s("619"), "fin": -s("63", "73"), "non_cour": -s("65", "75") + residu,
        "impots": -s("67"), "r_net": r_net, "a_cpc": bool(a_cpc),
        "capital": capital, "primes": primes, "fp": fp, "fourn": fourn, "emprunts": emprunts, "prov": prov,
        "autres_dettes": autres_dettes, "titres_participation": s("251"),
    }


# --------------------------------------------------------------------------------------- validation
def valider_perimetre(entites: list[dict], referentiel: str = "IFRS") -> tuple[list[str], list[str]]:
    """Retourne (bloquants, avertissements). Aucun état consolidé n'est produit s'il y a des bloquants."""
    bloc, warn = [], []
    meres = [e for e in entites if e.get("role") == "mere"]
    filiales = [e for e in entites if e.get("role") != "mere" and e.get("methode") != "hors"]
    if len(entites) < 2:
        bloc.append("Au moins deux sociétés sont nécessaires (une société mère et une filiale) : un document isolé ne permet pas de consolider.")
    if len(meres) != 1:
        bloc.append("Il faut désigner exactement une société mère.")
    if meres and not filiales:
        bloc.append("Aucune filiale ou entreprise associée n'est comprise dans le périmètre.")
    for e in entites:
        nom = e.get("nom") or "(sans nom)"
        df = e.get("df")
        if df is None or df.empty:
            bloc.append(f"{nom} : aucune balance importée.")
            continue
        ecart = float(df["debit"].sum() - df["credit"].sum())
        if abs(ecart) > TOL:
            bloc.append(f"{nom} : balance déséquilibrée (écart {ecart:,.0f} MAD) : corriger avant de consolider.".replace(",", " "))
        if not df["compte"].astype(str).str.startswith(("6", "7")).any():
            bloc.append(f"{nom} : aucun compte de charges ou de produits (classes 6 et 7) : le résultat ne peut pas être consolidé.")
        if e.get("role") == "mere":
            continue
        pc, pi_ = float(e.get("pct_controle", 0)), float(e.get("pct_interet", 0))
        met = e.get("methode", "IG")
        if not (0 < pi_ <= 100) or not (0 < pc <= 100):
            bloc.append(f"{nom} : pourcentages de contrôle et d'intérêt à renseigner (entre 0 et 100).")
            continue
        if met == "IG" and pc <= 50 and not e.get("controle_de_fait"):
            bloc.append(f"{nom} : intégration globale avec un contrôle de {pc:.0f} % ≤ 50 % : IFRS 10 exige de démontrer le pouvoir, "
                        "l'exposition aux rendements et le lien entre les deux (cochez « contrôle de fait » si c'est justifié).")
        if met == "MEE" and not (20 <= pc <= 50) and not e.get("controle_de_fait"):
            warn.append(f"{nom} : mise en équivalence avec {pc:.0f} % de droits de vote : l'influence notable est présumée entre 20 % et 50 % (IAS 28.5).")
        if met == "IP" and referentiel == "IFRS":
            warn.append(f"{nom} : l'intégration proportionnelle n'est pas admise par IFRS 11 pour une co-entreprise : "
                        "elle est traitée en mise en équivalence dans le référentiel IFRS.")
        if met in ("IG", "IP") and e.get("cout_titres") is None:
            bloc.append(f"{nom} : coût d'acquisition des titres à renseigner.")
        if e.get("fp_acquisition") is None:
            bloc.append(f"{nom} : capitaux propres à la date d'acquisition à renseigner (calcul du goodwill et des réserves consolidées).")
    return bloc, warn


# --------------------------------------------------------------------------------------- consolidation
def consolider(entites: list[dict], intragroupe: pd.DataFrame | None = None, referentiel: str = "IFRS",
               taux_id: float = 0.20, dividendes: dict | None = None) -> dict:
    """entites : [{nom, role ('mere'|'filiale'), methode, pct_controle, pct_interet, df, cout_titres, fp_acquisition,
                   ecart_evaluation (opt.), controle_de_fait (opt.)}]
    intragroupe : DataFrame colonnes Type, Entité A, Entité B, Montant. Types admis :
        'Créance / dette' (clients / fournisseurs), 'Compte courant' (autres créances / dettes), 'Prêt / emprunt', 'Vente / achat' (A vend à B, produit et charge),
        'Marge en stock' (A vend à B, stock chez B non encore revendu, marge = Montant), 'Marge en immobilisation'.
    dividendes : {nom de la filiale : dividendes versés à la mère au cours de l'exercice (MAD)}.
    Lève ValueError si les données sont insuffisantes."""
    bloc, warn = valider_perimetre(entites, referentiel)
    if bloc:
        raise ValueError("Consolidation impossible : " + " | ".join(bloc))
    dividendes = dividendes or {}
    ent = []
    for e in entites:
        e = dict(e)
        e["df"] = preparer(e["df"])
        if e.get("role") != "mere" and e.get("methode") == "IP" and referentiel == "IFRS":
            e["methode"] = "MEE"
        e["ag"] = agregats(e["df"])
        ent.append(e)
    mere = next(e for e in ent if e["role"] == "mere")
    subs = [e for e in ent if e is not mere and e.get("methode") != "hors"]
    noms = {e["nom"] for e in ent}

    champs_actif = ["incorp", "corp", "financ", "stocks", "clients", "autres_creances", "vmp", "dispo"]
    champs_cpc = ["ca", "autres_prod", "achats", "perso", "autres_ch", "impt_tx", "dot", "fin", "non_cour", "impots"]
    champs_pass = ["emprunts", "fourn", "autres_dettes", "tres_passif", "prov"]
    tot = {k: 0.0 for k in champs_actif + champs_cpc + champs_pass}
    tot.update({"mee_valeur": 0.0, "mee_resultat": 0.0, "goodwill": 0.0, "ecart_eval": 0.0, "nci_fp": 0.0, "nci_resultat": 0.0})

    def ajouter(e, coef):
        for k in champs_actif + champs_cpc + champs_pass:
            tot[k] += coef * e["ag"][k]

    ajouter(mere, 1.0)
    capitaux_propres_cumules = mere["ag"]["fp"]        # capitaux propres (hors non-valeur) des entités intégrées, avant éliminations
    resultat_cumule = mere["ag"]["r_net"]
    titres_elimines = 0.0
    detail_sub, details_notes = [], []
    # non-valeurs (frais préliminaires) : éliminées en IFRS, avec impôt différé ; effet porté en capitaux propres
    nonval_total = mere["ag"]["nonval"]
    for e in subs:
        met, pi_, pc = e["methode"], float(e["pct_interet"]) / 100, float(e["pct_controle"]) / 100
        ag, fp_c, r = e["ag"], e["ag"]["fp"], e["ag"]["r_net"]
        fp_acq = float(e["fp_acquisition"])
        ecart_eval = float(e.get("ecart_evaluation") or 0.0)
        cout = float(e.get("cout_titres") or 0.0)
        if met == "IG":
            ajouter(e, 1.0)
            capitaux_propres_cumules += fp_c
            resultat_cumule += r
            nonval_total += ag["nonval"]
            gw = cout - pi_ * (fp_acq + ecart_eval)
            nci_fp = (1 - pi_) * (fp_c + ecart_eval)
            if referentiel == "IFRS":   # les minoritaires supportent leur part de l'élimination des frais préliminaires de la filiale (nette d'impôt différé)
                nci_fp -= (1 - pi_) * ag["nonval"] * (1 - taux_id)
            nci_r = (1 - pi_) * r
            tot["goodwill"] += gw
            tot["ecart_eval"] += ecart_eval
            tot["nci_fp"] += nci_fp
            tot["nci_resultat"] += nci_r
            titres_elimines += cout
            detail_sub.append({"Société": e["nom"], "Méthode": METHODES["IG"], "% contrôle": pc * 100, "% intérêt": pi_ * 100,
                               "Coût des titres": cout, "Quote-part de l'actif net acquis": pi_ * (fp_acq + ecart_eval),
                               "Goodwill": gw, "Intérêts ne donnant pas le contrôle": nci_fp, "Résultat NCI": nci_r})
        elif met == "IP":
            ajouter(e, pi_)
            capitaux_propres_cumules += pi_ * fp_c
            resultat_cumule += pi_ * r
            nonval_total += pi_ * ag["nonval"]
            gw = cout - pi_ * (fp_acq + ecart_eval)
            tot["goodwill"] += gw
            tot["ecart_eval"] += pi_ * ecart_eval
            titres_elimines += cout
            detail_sub.append({"Société": e["nom"], "Méthode": METHODES["IP"], "% contrôle": pc * 100, "% intérêt": pi_ * 100,
                               "Coût des titres": cout, "Quote-part de l'actif net acquis": pi_ * (fp_acq + ecart_eval),
                               "Goodwill": gw, "Intérêts ne donnant pas le contrôle": 0.0, "Résultat NCI": 0.0})
        else:  # MEE : valeur = quote-part des capitaux propres de clôture + goodwill implicite
            cout_mee = float(e.get("cout_titres") or 0.0)
            gw_impl = cout_mee - pi_ * fp_acq
            valeur = pi_ * fp_c + gw_impl
            tot["mee_valeur"] += valeur
            tot["mee_resultat"] += pi_ * r
            titres_elimines += cout_mee
            resultat_cumule += pi_ * r
            capitaux_propres_cumules += valeur - cout_mee  # la valeur MEE remplace le coût des titres inclus dans les capitaux propres de la mère
            detail_sub.append({"Société": e["nom"], "Méthode": METHODES["MEE"], "% contrôle": pc * 100, "% intérêt": pi_ * 100,
                               "Coût des titres": cout_mee, "Quote-part de l'actif net acquis": pi_ * fp_acq,
                               "Goodwill": gw_impl, "Intérêts ne donnant pas le contrôle": 0.0, "Résultat NCI": 0.0})
    # les titres éliminés sortent des immobilisations financières de la mère
    tot["financ"] -= titres_elimines
    # ---- dividendes intragroupe : produit de participation éliminé (le résultat consolidé ne les contient pas) ----
    div_total = 0.0
    for nom, v in dividendes.items():
        if nom in noms and v:
            div_total += float(v)
    if div_total > TOL:
        tot["fin"] -= div_total
        resultat_cumule -= div_total
        details_notes.append(f"Dividendes intragroupe éliminés du résultat : {div_total:,.0f} MAD (reclassés en réserves consolidées).".replace(",", " "))
    # ---- éliminations intragroupe ----
    elim_lignes, marge_total = [], 0.0
    if intragroupe is not None and not intragroupe.empty:
        for _, l in intragroupe.iterrows():
            typ, a, b = str(l.get("Type", "")), l.get("Entité A"), l.get("Entité B")
            mt = pd.to_numeric(l.get("Montant"), errors="coerce")
            if pd.isna(mt) or abs(mt) < TOL:
                continue
            if a not in noms or b not in noms:
                raise ValueError(f"Élimination intragroupe : société inconnue ({a} / {b}). Utilisez les noms du périmètre.")
            if a == b:
                raise ValueError("Élimination intragroupe : les deux sociétés doivent être différentes.")
            mt = float(mt)
            if typ == "Créance / dette":          # créance client (3421) en face d'une dette fournisseur (4411)
                tot["clients"] -= mt
                tot["fourn"] -= mt
            elif typ == "Compte courant":         # autre créance (345, 3488...) en face d'une autre dette (4488...)
                tot["autres_creances"] -= mt
                tot["autres_dettes"] -= mt
            elif typ == "Prêt / emprunt":
                tot["financ"] -= mt
                tot["emprunts"] -= mt
            elif typ == "Vente / achat":
                tot["ca"] -= mt
                tot["achats"] += mt   # achats exprimés en négatif dans les charges : l'élimination en réduit la valeur absolue
            elif typ in ("Marge en stock", "Marge en immobilisation"):
                net = mt * (1 - taux_id)
                marge_total += net
                if typ == "Marge en stock":
                    tot["stocks"] -= mt
                else:
                    tot["corp"] -= mt
                resultat_cumule -= net
                capitaux_propres_cumules -= net
                tot["id_marge"] = tot.get("id_marge", 0.0) + mt * taux_id   # impôt différé actif
                vendeur = next(e for e in subs if e["nom"] == a) if any(e["nom"] == a for e in subs) else None
                if vendeur and vendeur["methode"] == "IG":
                    tot["nci_resultat"] -= (1 - float(vendeur["pct_interet"]) / 100) * net
                    tot["nci_fp"] -= (1 - float(vendeur["pct_interet"]) / 100) * net
            else:
                raise ValueError(f"Type d'élimination inconnu : {typ}")
            elim_lignes.append({"Type": typ, "Entité A": a, "Entité B": b, "Montant (MAD)": mt})
    # ---- actifs en non-valeur : éliminés (pas d'actif en IFRS), avec impôt différé ----
    if referentiel == "IFRS" and nonval_total > TOL:
        id_nv = nonval_total * taux_id
        capitaux_propres_cumules -= nonval_total * (1 - taux_id)
        tot["id_actif_nonval"] = id_nv
        details_notes.append(f"Frais préliminaires / charges à répartir éliminés : {nonval_total:,.0f} MAD (impôt différé actif {id_nv:,.0f} MAD).".replace(",", " "))
    else:
        tot["id_actif_nonval"] = 0.0
        if nonval_total > TOL:  # CGNC : on les conserve en immobilisations en non-valeur
            tot["incorp"] += nonval_total
    # ---- construction des états (milliers de MAD) ----
    k = lambda x: round(x / 1000.0)
    dta = tot["id_actif_nonval"] + tot.get("id_marge", 0.0)
    immo = tot["incorp"] + tot["corp"] + tot["ecart_eval"] + tot["financ"] + tot["goodwill"] + tot["mee_valeur"] + dta
    circ = tot["stocks"] + tot["clients"] + tot["autres_creances"] + tot["vmp"] + tot["dispo"]
    total_actif = immo + circ
    r_groupe = resultat_cumule - tot["nci_resultat"]
    # Capitaux propres consolidés (y compris NCI) = capitaux propres cumulés
    #   - titres éliminés + goodwill + écart d'évaluation  (les valeurs MEE sont déjà incluses dans le cumul)
    cp_total = capitaux_propres_cumules - sum(float(e.get("cout_titres") or 0.0) for e in subs if e["methode"] in ("IG", "IP")) \
        + tot["goodwill"] + tot["ecart_eval"]
    cp_groupe = cp_total - tot["nci_fp"]
    capital, primes = mere["ag"]["capital"], mere["ag"]["primes"]
    reserves = cp_groupe - capital - primes - r_groupe
    dettes = tot["emprunts"] + tot["fourn"] + tot["autres_dettes"] + tot["tres_passif"]
    total_passif = cp_groupe + tot["nci_fp"] + tot["prov"] + dettes
    ecart_bilan = total_actif - total_passif

    r_expl = sum(tot[c] for c in ("ca", "autres_prod", "achats", "perso", "autres_ch", "impt_tx", "dot"))
    r_courant = r_expl + tot["fin"]
    r_net_int = r_courant + tot["non_cour"] + tot["impots"]
    r_net_ens = r_net_int + tot["mee_resultat"] - marge_total
    L, T, G = "ligne", "sous-total", "total"
    actif = pd.DataFrame([
        ("Actif immobilisé", k(immo), T), ("Goodwill (écarts d'acquisition)", k(tot["goodwill"]), L),
        ("Immobilisations incorporelles", k(tot["incorp"]), L), ("Immobilisations corporelles (y c. écarts d'évaluation)", k(tot["corp"] + tot["ecart_eval"]), L),
        ("Immobilisations financières", k(tot["financ"]), L), ("Impôt différé actif", k(dta), L), ("Titres mis en équivalence", k(tot["mee_valeur"]), L),
        ("Actif circulant", k(circ), T), ("Stocks et en-cours", k(tot["stocks"]), L), ("Clients et comptes rattachés", k(tot["clients"]), L),
        ("Autres créances et comptes de régularisation", k(tot["autres_creances"]), L), ("Valeurs mobilières de placement", k(tot["vmp"]), L),
        ("Disponibilités", k(tot["dispo"]), L), ("TOTAL ACTIF", k(total_actif), G)], columns=["Poste", "Montant", "Type"])
    passif = pd.DataFrame([
        ("Capitaux propres (part du groupe)", k(cp_groupe), T), ("Capital", k(capital), L), ("Primes d'émission et de fusion", k(primes), L),
        ("Réserves consolidées", k(reserves), L), ("Résultat net part du groupe", k(r_groupe), L),
        ("Intérêts ne donnant pas le contrôle", k(tot["nci_fp"]), T), ("Provisions pour risques et charges", k(tot["prov"]), T), ("Dettes", k(dettes), T),
        ("Emprunts et dettes financières", k(tot["emprunts"]), L), ("Fournisseurs et comptes rattachés", k(tot["fourn"]), L),
        ("Autres dettes et comptes de régularisation", k(tot["autres_dettes"]), L), ("Trésorerie - Passif", k(tot["tres_passif"]), L),
        ("TOTAL PASSIF", k(total_passif), G)], columns=["Poste", "Montant", "Type"])
    cpc = pd.DataFrame([
        ("Chiffre d'affaires", k(tot["ca"]), L), ("Autres produits d'exploitation", k(tot["autres_prod"]), L), ("Achats consommés", k(tot["achats"]), L),
        ("Charges de personnel", k(tot["perso"]), L), ("Autres charges d'exploitation", k(tot["autres_ch"]), L), ("Impôts et taxes", k(tot["impt_tx"]), L),
        ("Dotations d'exploitation", k(tot["dot"]), L), ("Résultat d'exploitation", k(r_expl), T), ("Charges et produits financiers", k(tot["fin"]), L),
        ("Résultat courant", k(r_courant), T), ("Charges et produits non-courants", k(tot["non_cour"]), L), ("Impôts sur les résultats", k(tot["impots"]), L),
        ("Élimination de la marge interne (nette d'impôt)", k(-marge_total), L),
        ("Quote-part dans les résultats des entreprises mises en équivalence", k(tot["mee_resultat"]), L),
        ("Résultat net de l'ensemble consolidé", k(r_net_ens), T), ("Résultat des intérêts ne donnant pas le contrôle", k(tot["nci_resultat"]), L),
        ("Résultat net part du groupe", k(r_groupe), G)], columns=["Poste", "Montant", "Type"])
    verif, manquantes = [], []
    fmt = lambda x: f"{x:,.0f}".replace(",", " ")
    # --- réciprocité des éliminations déclarées ---
    par_nom = {e["nom"]: e for e in ent}
    for l in elim_lignes:
        A, B, mt, typ = par_nom[l["Entité A"]]["ag"], par_nom[l["Entité B"]]["ag"], l["Montant (MAD)"], l["Type"]
        regles = {"Créance / dette": (A["clients"], "clients de A", B["fourn"], "fournisseurs de B"),
                  "Compte courant": (A["autres_creances"], "autres créances de A", B["autres_dettes"], "autres dettes de B"),
                  "Prêt / emprunt": (A["financ"], "immobilisations financières de A", B["emprunts"], "emprunts de B"),
                  "Vente / achat": (A["ca"], "chiffre d'affaires de A", -B["achats"], "achats de B"),
                  "Marge en stock": (B["stocks"], "stocks de B", None, ""),
                  "Marge en immobilisation": (B["corp"], "immobilisations corporelles de B", None, "")}[typ]
        for val, nom_poste in ((regles[0], regles[1]), (regles[2], regles[3])):
            if nom_poste and mt > val + TOL:
                verif.append(f"Élimination « {typ} » ({l['Entité A']} / {l['Entité B']}) de {fmt(mt)} MAD supérieure au poste correspondant "
                             f"({nom_poste} : {fmt(val)} MAD) : vérifier le montant ou le sens A / B.")
    # --- postes devenus négatifs après éliminations ---
    for cle, lib, signe in (("clients", "Clients", 1), ("fourn", "Fournisseurs", 1), ("stocks", "Stocks", 1), ("autres_creances", "Autres créances", 1),
                            ("autres_dettes", "Autres dettes", 1), ("emprunts", "Emprunts", 1), ("financ", "Immobilisations financières", 1), ("ca", "Chiffre d'affaires", 1)):
        if signe * tot[cle] < -TOL:
            verif.append(f"{lib} négatif après éliminations ({fmt(tot[cle])} MAD) : éliminations excessives ou mal affectées.")
    # --- titres déclarés et compte 251 de la mère ---
    declare = sum(float(e.get("cout_titres") or 0.0) for e in subs)
    if abs(declare - mere["ag"]["titres_participation"]) > max(TOL, 0.01 * abs(mere["ag"]["titres_participation"])):
        verif.append(f"Coût des titres déclaré ({fmt(declare)} MAD) différent du compte 251 de la mère ({fmt(mere['ag']['titres_participation'])} MAD) : "
                     "participations indirectes, titres non consolidés ou erreur de saisie ?")
    # --- dividendes ---
    prod_part = -_solde(mere["df"], "732")
    if div_total > prod_part + TOL:
        verif.append(f"Dividendes intragroupe déclarés ({fmt(div_total)} MAD) supérieurs aux produits de participation de la mère (compte 732 : {fmt(prod_part)} MAD).")
    if div_total <= TOL and prod_part > TOL:
        manquantes.append(f"La mère comptabilise {fmt(prod_part)} MAD de produits de participation (compte 732) mais aucun dividende intragroupe n'est déclaré : "
                          "le résultat consolidé peut contenir des dividendes internes non éliminés.")
    # --- données absentes ---
    if not elim_lignes:
        manquantes.append("Aucune opération intragroupe déclarée : créances, dettes, ventes, achats, marges et prêts entre sociétés du groupe restent dans les comptes consolidés.")
    if all(abs(float(e.get("ecart_evaluation") or 0.0)) < TOL for e in subs if e["methode"] in ("IG", "IP")):
        manquantes.append("Aucun écart d'évaluation saisi : les actifs et passifs des filiales sont repris à leur valeur comptable, sans juste valeur à la date d'acquisition (IFRS 3).")
    if marge_total == 0 and not any(l["Type"].startswith("Marge") for l in elim_lignes):
        manquantes.append("Aucune marge interne en stock ou en immobilisation déclarée : les profits internes non réalisés ne sont pas éliminés.")
    if abs(ecart_bilan) > 1000:
        verif.append(f"Écart actif / passif de {ecart_bilan:,.0f} MAD : vérifier les capitaux propres à l'acquisition, les titres et les éliminations.".replace(",", " "))
    if abs(r_net_ens - resultat_cumule) > 1000:
        verif.append(f"Le résultat consolidé ({r_net_ens:,.0f} MAD) diffère du cumul attendu ({resultat_cumule:,.0f} MAD) : vérifier les comptes de résultat.".replace(",", " "))
    hypotheses = [
        "Montants en milliers de dirhams (MAD).",
        "Intérêts ne donnant pas le contrôle évalués à la quote-part de l'actif net identifiable (goodwill partiel, IFRS 3.19) ; pas de goodwill complet.",
        "Écart d'évaluation porté en immobilisations corporelles, sans amortissement ni impôt différé ; aucune conversion de devises (IAS 21 non appliquée).",
        "Les comptes de chaque société sont repris tels que saisis : aucun retraitement IFRS propre à une filiale (IFRS 16, IAS 19, IFRS 9...) n'est appliqué, "
        "seuls les frais préliminaires sont traités (éliminés par les capitaux propres, sans retraitement du résultat de l'exercice).",
        "Homogénéité des méthodes comptables, dates de clôture et exercice des balances non vérifiés : à contrôler par l'utilisateur.",
        "Le goodwill n'est pas testé en dépréciation (IAS 36) et ne subit aucune dépréciation ni amortissement.",
        "Pas de participations indirectes ni de variations de pourcentage en cours d'exercice : chaque pourcentage est supposé constant sur l'exercice.",
        "Les éliminations se limitent à celles déclarées ; les ventes/achats supposent que les achats figurent en comptes 611 à 615 et les ventes en 711 à 712.",
        "Dividendes supposés enregistrés en produits financiers (compte 732) chez la mère.",
        f"Taux d'impôt différé retenu : {taux_id:.0%} (saisi par l'utilisateur)."]
    notes = hypotheses + details_notes + warn
    return {"actif": actif, "passif": passif, "cpc": cpc, "perimetre": pd.DataFrame(detail_sub),
            "eliminations": pd.DataFrame(elim_lignes), "notes": notes, "verifications": verif,
            "ecart_bilan": ecart_bilan, "total_actif": total_actif, "total_passif": total_passif,
            "hypotheses": hypotheses, "donnees_manquantes": manquantes,
            "ca": tot["ca"], "resultat_groupe": r_groupe, "resultat_ensemble": r_net_ens, "nci": tot["nci_fp"],
            "referentiel": referentiel}
