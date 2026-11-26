"""Grand livre, journaux comptables et contrôle de TVA (CGNC / CGI).

Lecture de listes d'écritures (Excel / CSV) : une ligne = une écriture avec au minimum compte, débit, crédit ;
colonnes reconnues en plus : date, journal, pièce (n° d'écriture), libellé.
Les écritures sont contrôlées (équilibre par pièce, comptes, dates) puis cumulées en balance pour alimenter la suite du traitement.
"""
from __future__ import annotations

import re

import pandas as pd

TOL = 0.5
TAUX_TVA = (0.20, 0.14, 0.10, 0.07)   # taux normal et taux réduits (CGI, art. 98 et 99)

_ALIAS = {
    "date": ("date", "date ecriture", "date piece", "date comptable"),
    "journal": ("journal", "code journal", "jnl", "jrn"),
    "piece": ("piece", "n piece", "no piece", "numero piece", "n ecriture", "numero ecriture", "ecriture", "num piece", "n de piece"),
    "compte": ("compte", "n compte", "numero compte", "no compte", "code compte", "compte general", "general"),
    "libelle": ("libelle", "libelle ecriture", "designation", "intitule", "description"),
    "debit": ("debit", "montant debit", "mouvement debit", "debits"),
    "credit": ("credit", "montant credit", "mouvement credit", "credits"),
}


def _n(t) -> str:
    t = str(t).lower().strip()
    for a, b in zip("éèêëàâîïôûùç", "eeeeaaiioouc"):
        t = t.replace(a, b)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _colonnes(df: pd.DataFrame) -> dict:
    index = {_n(c): c for c in df.columns}
    res = {}
    for cle, alias in _ALIAS.items():
        for a in alias:
            if a in index:
                res[cle] = index[a]
                break
    return res


def est_liste_ecritures(df: pd.DataFrame) -> str | None:
    """'journal' / 'grand_livre' si le tableau est une liste d'écritures (et non une balance), sinon None.
    Une balance a un compte par ligne ; une liste d'écritures a une date, un journal ou un numéro de pièce."""
    c = _colonnes(df)
    if not {"compte", "debit", "credit"} <= set(c):
        return None
    if not ({"date", "journal", "piece"} & set(c)):
        return None
    return "journal" if ({"journal", "piece"} & set(c)) else "grand_livre"


def lire_ecritures(df: pd.DataFrame) -> pd.DataFrame:
    c = _colonnes(df)
    if not {"compte", "debit", "credit"} <= set(c):
        raise ValueError("Colonnes compte / débit / crédit introuvables dans la liste d'écritures.")
    e = pd.DataFrame({k: df[v] for k, v in c.items()})
    for k in ("date", "journal", "piece", "libelle"):
        if k not in e:
            e[k] = ""
    e["compte"] = e["compte"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    for k in ("debit", "credit"):
        e[k] = pd.to_numeric(e[k].astype(str).str.replace(r"[\s\u00a0]", "", regex=True).str.replace(",", ".", regex=False), errors="coerce").fillna(0.0)
    e = e[(e["compte"] != "") & (e["compte"].str.lower() != "nan")]
    e = e[(e["debit"] != 0) | (e["credit"] != 0)].reset_index(drop=True)
    brut = e["date"]
    iso = brut.astype(str).str.match(r"^\d{4}-\d{2}-\d{2}")   # 2025-04-10 : année d'abord, jamais jour d'abord
    e["date"] = pd.to_datetime(brut.where(~iso), errors="coerce", dayfirst=True, format="mixed")
    e.loc[iso, "date"] = pd.to_datetime(brut[iso].astype(str).str[:10], errors="coerce", format="%Y-%m-%d")
    return e[["date", "journal", "piece", "compte", "libelle", "debit", "credit"]]


def controles_ecritures(e: pd.DataFrame, comptes_cgnc: dict | None = None) -> list[dict]:
    """Constats {Contrôle, Niveau, Détail}."""
    r = []

    def add(ctrl, niv, det):
        r.append({"Contrôle": ctrl, "Niveau": niv, "Détail": det})
    if e.empty:
        add("Lecture", "Erreur", "Aucune écriture exploitable")
        return r
    ecart = e["debit"].sum() - e["credit"].sum()
    if abs(ecart) > TOL:
        add("Équilibre général", "Erreur", f"Total débit ≠ total crédit (écart {ecart:,.2f} MAD)".replace(",", " "))
    if (e["debit"] < 0).any() or (e["credit"] < 0).any():
        add("Montants", "À vérifier", f"{int(((e['debit'] < 0) | (e['credit'] < 0)).sum())} écriture(s) avec un montant négatif (extourne ou erreur de saisie)")
    both = e[(e["debit"] != 0) & (e["credit"] != 0)]
    if len(both):
        add("Montants", "Info", f"{len(both)} ligne(s) avec débit et crédit renseignés sur la même ligne")
    pieces = e[e["piece"].astype(str).str.strip().ne("") & e["piece"].astype(str).str.lower().ne("nan")]
    if len(pieces):
        g = pieces.groupby([pieces["journal"].astype(str), pieces["piece"].astype(str)])[["debit", "credit"]].sum()
        mal = g[(g["debit"] - g["credit"]).abs() > TOL]
        if len(mal):
            ex = ", ".join(f"{j or '—'}/{p}" for (j, p) in list(mal.index)[:6])
            add("Équilibre des pièces", "Erreur", f"{len(mal)} pièce(s) déséquilibrée(s) (partie double non respectée) : {ex}")
        else:
            add("Équilibre des pièces", "Info", f"{g.shape[0]} pièce(s) équilibrée(s)")
    else:
        add("Équilibre des pièces", "Info", "Aucun numéro de pièce : l'équilibre n'est contrôlé qu'au total")
    non_num = e.loc[~e["compte"].str.fullmatch(r"\d+"), "compte"].unique()
    if len(non_num):
        add("Comptes", "Erreur", f"{len(non_num)} compte(s) non numérique(s) : {', '.join(map(str, non_num[:6]))}")
    hors_classe = e.loc[e["compte"].str.fullmatch(r"\d+") & ~e["compte"].str[0].isin(list("12345678")), "compte"].unique()
    if len(hors_classe):
        add("Comptes", "Erreur", f"{len(hors_classe)} compte(s) hors des classes 1 à 8 : {', '.join(map(str, hors_classe[:6]))}")
    if comptes_cgnc:
        rub = {c[:2] for c in comptes_cgnc}
        inconnus = sorted({c for c in e["compte"].unique() if c.isdigit() and c[:1] in "12345678" and c[:2] not in rub})
        if inconnus:
            add("Plan comptable", "Erreur", f"Rubrique(s) inexistante(s) au CGNC : {', '.join(inconnus[:8])}")
    dates = e["date"].dropna()
    if len(dates):
        annees = sorted(dates.dt.year.unique())
        if len(annees) > 1:
            add("Exercice", "À vérifier", f"Écritures datées de plusieurs années ({', '.join(map(str, annees))}) : vérifier que l'exercice est unique")
        else:
            add("Exercice", "Info", f"Exercice {annees[0]} ({dates.min():%d/%m/%Y} au {dates.max():%d/%m/%Y})")
        if e["date"].isna().any():
            add("Dates", "À vérifier", f"{int(e['date'].isna().sum())} écriture(s) sans date lisible")
    return r


def balance_depuis_ecritures(e: pd.DataFrame) -> pd.DataFrame:
    """Cumule les écritures par compte : colonnes compte, libelle, debit, credit, solde."""
    lib = e.groupby("compte")["libelle"].agg(lambda s: next((str(x) for x in s if str(x).strip() and str(x).lower() != "nan"), ""))
    b = e.groupby("compte", as_index=False)[["debit", "credit"]].sum()
    b["libelle"] = b["compte"].map(lib)
    b = b[["compte", "libelle", "debit", "credit"]].sort_values("compte").reset_index(drop=True)
    b["solde"] = b["debit"] - b["credit"]
    return b


# ------------------------------------------------------------------------------------------------- TVA
# Principe : un taux de TVA ne se contrôle que sur des opérations détaillées (écritures regroupées par pièce, ou factures).
# Le solde de clôture d'un compte de TVA cumule plusieurs taux, plusieurs périodes, des régularisations et des paiements :
# le rapporter au chiffre d'affaires donnerait un taux sans signification. Sans détail, le contrôle est déclaré impossible.
TOL_TAUX = 0.6   # points de pourcentage
TOL_MONTANT = 1.0  # MAD (arrondis)


def _vrai(v) -> bool:
    """Booléen d'une cellule de tableau : une cellule vide (ligne ajoutée à la main) n'est PAS « vrai »."""
    return False if v is None or (not isinstance(v, str) and pd.isna(v)) else bool(v)


def _taux_conforme(tva: float, base: float) -> bool:
    if abs(base) < TOL_MONTANT:
        return False
    if abs(tva) <= TOL_MONTANT and abs(base) <= 100:
        return True
    return any(abs(tva - base * r) <= max(TOL_MONTANT, abs(base) * TOL_TAUX / 100) for r in TAUX_TVA)


def _s(df, *p):
    return float(df.loc[df["compte"].astype(str).str.startswith(tuple(p)), "solde"].sum())


def controle_tva(df: pd.DataFrame, detail_disponible: bool = False) -> list[dict]:
    """Contrôles de TVA sur une BALANCE : uniquement le sens des soldes et la cohérence des positions de TVA.
    Aucun taux n'est calculé : si une TVA ou des ventes existent et qu'aucun détail (écritures ou factures) n'a été fourni,
    le contrôle de taux est déclaré impossible."""
    r = []

    def add(niv, texte, ref=""):
        r.append({"Contrôle": "TVA", "Niveau": niv, "Constat": texte, "Référence": ref})
    tva_f, tva_r = -_s(df, "4455"), _s(df, "3455")
    ventes = -_s(df, "711", "712")
    if (abs(tva_f) > 1 or abs(tva_r) > 1 or ventes > 0) and not detail_disponible:
        add("À vérifier", "Contrôle impossible : les taux de TVA ne se vérifient pas à partir d'une balance (les soldes de clôture cumulent plusieurs taux, "
            "périodes et régularisations). Importez le journal ou le grand livre, ou les factures, pour contrôler les taux pièce par pièce.",
            "CGI art. 98 et 99 (taux)")
    if tva_f < -1:
        add("À vérifier", f"Compte 4455 à solde débiteur ({-tva_f:,.0f} MAD) : TVA facturée négative (avoirs excédentaires, erreur de sens ?)".replace(",", " "), "4455")
    if tva_r < -1:
        add("À vérifier", "TVA récupérable (3455) à solde créditeur : erreur de sens ou compensation à reclasser", "3455")
    d4456, c3456 = -_s(df, "4456"), _s(df, "3456")
    if d4456 > 1 and c3456 > 1:
        add("À vérifier", "TVA due (4456) et crédit de TVA (3456) présents simultanément : une seule des deux positions peut exister à la clôture", "CGNC 3456 / 4456")
    return r


def controle_tva_ecritures(e: pd.DataFrame) -> list[dict]:
    """Contrôle des taux de TVA sur les écritures détaillées : pour chaque pièce, TVA (4455 / 34551 / 34552) rapportée à sa base HT
    (produits 711-712, charges 61, immobilisations 21-24) et comparée aux taux du CGI (20 %, 14 %, 10 %, 7 %). Lignes {Contrôle, Niveau, Détail}."""
    lignes = []

    def add(niv, texte):
        lignes.append({"Contrôle": "TVA (taux par pièce)", "Niveau": niv, "Détail": texte})
    tva_presente = e["compte"].str.startswith(("4455", "3455")).any()
    if not tva_presente:
        add("Info", "Aucune écriture de TVA (4455 / 3455) dans la liste : contrôle de taux sans objet")
        return lignes
    p = e["piece"].astype(str).str.strip()
    util = e[(p != "") & (p.str.lower() != "nan")]
    if util.empty:
        add("À vérifier", "Contrôle impossible : les écritures n'ont pas de numéro de pièce, la TVA ne peut pas être rapprochée de sa base HT")
        return lignes
    util = util.assign(net=util["credit"] - util["debit"], cle=util["journal"].astype(str) + "/" + util["piece"].astype(str))
    ok, atypiques, sans_base, exo = 0, [], 0, []
    for cle, g in util.groupby("cle"):
        c = g["compte"]
        sommes = {
            "vente": (g.loc[c.str.startswith("4455"), "net"].sum(), g.loc[c.str.startswith(("711", "712")), "net"].sum()),
            "achat": (-g.loc[c.str.startswith("34552"), "net"].sum(), -g.loc[c.str.startswith(("611", "612", "613", "614", "615", "616", "617", "618")), "net"].sum()),
            "immobilisation": (-g.loc[c.str.startswith("34551"), "net"].sum(), -g.loc[c.str.startswith(("21", "22", "23", "24")), "net"].sum()),
        }
        for nature, (tva, base) in sommes.items():
            if abs(tva) <= TOL_MONTANT and abs(base) <= TOL_MONTANT:
                continue
            if abs(base) <= TOL_MONTANT:
                sans_base += 1
                continue
            if abs(tva) <= TOL_MONTANT:
                if nature == "vente":
                    exo.append(cle)
                continue
            if _taux_conforme(tva, base):
                ok += 1
            else:
                atypiques.append(f"{cle} ({nature}, taux observé {tva / base:.1%})")
    if ok:
        add("Info", f"{ok} pièce(s) dont le taux de TVA correspond à un taux légal (20 %, 14 %, 10 % ou 7 %)")
    if atypiques:
        add("À vérifier", f"{len(atypiques)} pièce(s) avec un taux de TVA ne correspondant à aucun taux légal unique (taux mixtes, base partielle ou erreur) : "
            + "; ".join(atypiques[:8]) + (" ..." if len(atypiques) > 8 else ""))
    if exo:
        add("À vérifier", f"{len(exo)} pièce(s) de vente sans TVA facturée (exonération, exportation ou oubli) : " + ", ".join(exo[:8]) + (" ..." if len(exo) > 8 else ""))
    if sans_base:
        add("À vérifier", f"{sans_base} pièce(s) portent de la TVA sans base HT dans la même pièce : contrôle impossible pour ces pièces")
    if not ok and not atypiques and not exo:
        add("À vérifier", "Contrôle impossible : aucune pièce ne regroupe à la fois la TVA et sa base HT")
    return lignes


def controle_tva_factures(tab: pd.DataFrame) -> list[dict]:
    """Contrôle de taux sur des factures (colonnes HT, TVA, TTC ; colonne facultative _tva_deduite = TVA non lue sur le document)."""
    lignes = []

    def add(niv, texte):
        lignes.append({"Contrôle": "TVA (factures)", "Niveau": niv, "Détail": texte})
    if tab is None or len(tab) == 0 or not {"HT", "TVA"} <= set(tab.columns):
        return lignes
    ok, atyp, deduites, incoh = 0, [], [], []
    for i, r in tab.iterrows():
        ht, tva = pd.to_numeric(r["HT"], errors="coerce"), pd.to_numeric(r["TVA"], errors="coerce")
        nom = str(r.get("N° facture", "")).strip() or f"ligne {i + 1}"
        if pd.isna(ht) or pd.isna(tva):
            deduites.append(nom)
            continue
        if _vrai(r.get("_tva_deduite", False)):
            deduites.append(nom)
            continue
        ttc = pd.to_numeric(r.get("TTC"), errors="coerce")
        if not pd.isna(ttc) and abs(ttc - (ht + tva)) > TOL_MONTANT:
            incoh.append(nom)
        if abs(ht) <= TOL_MONTANT:
            continue
        if _taux_conforme(float(tva), float(ht)):
            ok += 1
        else:
            atyp.append(f"{nom} (taux observé {tva / ht:.1%})")
    if ok:
        add("Info", f"{ok} facture(s) dont le taux de TVA correspond à un taux légal (20 %, 14 %, 10 %, 7 % ou exonération)")
    if atyp:
        add("À vérifier", f"{len(atyp)} facture(s) avec un taux ne correspondant à aucun taux légal unique : " + "; ".join(atyp[:8]) + (" ..." if len(atyp) > 8 else ""))
    if deduites:
        add("À vérifier", f"Contrôle impossible pour {len(deduites)} facture(s) : TVA absente du document ou déduite par l'application (taux supposé), "
            "donc non vérifiable : " + ", ".join(deduites[:8]) + (" ..." if len(deduites) > 8 else ""))
    if incoh:
        add("À vérifier", f"{len(incoh)} facture(s) dont le TTC diffère de HT + TVA : " + ", ".join(incoh[:8]))
    return lignes


# ------------------------------------------------------------------------------------------------- écritures proposées pour les factures
def ecritures_depuis_factures(tab: pd.DataFrame, libelle_fn=None) -> pd.DataFrame:
    """Écritures comptables CGNC PROPOSÉES pour chaque facture (à valider par le comptable) :
    achat  : débit charge (ou immobilisation) HT, débit 34552 / 34551 TVA récupérable, crédit 4411 fournisseurs TTC ;
    vente  : débit 3421 clients TTC, crédit produit HT, crédit 4455 TVA facturée. Les avoirs (montants négatifs) inversent les sens.
    Colonnes : Facture, Journal, Compte, Libellé, Débit, Crédit, Observation."""
    libelle_fn = libelle_fn or (lambda c: "")
    lignes = []

    def num(x):
        v = pd.to_numeric(x, errors="coerce")
        return 0.0 if pd.isna(v) else float(v)

    def ecr(fact, jnl, code, lib, debit, obs=""):
        lignes.append({"Facture": fact, "Journal": jnl, "Compte": code, "Libellé": lib, "Débit": debit if debit >= 0 else 0.0,
                       "Crédit": -debit if debit < 0 else 0.0, "Observation": obs})
    for i, r in tab.iterrows():
        ht, tva = num(r.get("HT")), num(r.get("TVA"))
        sens = r.get("Sens") if r.get("Sens") in ("Achat", "Vente") else "Achat"
        code = str(r.get("Compte", "")).strip()
        nom = str(r.get("N° facture", "")).strip() or f"ligne {i + 1}"
        tiers = str(r.get("Tiers", r.get("Fournisseur / Client", ""))).strip()
        obs = ""
        if not code or code.lower() == "nan":
            obs = "Compte à renseigner : imputation non déterminée"
            code = "????"
        lib = libelle_fn(code) or f"Compte {code}"
        if _vrai(r.get("_tva_deduite", False)):
            obs = (obs + " ; " if obs else "") + "TVA non lue sur le document : montant reconstitué, à vérifier"
        if sens == "Vente":
            ecr(nom, "VT", "3421", "Clients" + (f" – {tiers}" if tiers else ""), ht + tva, obs)
            ecr(nom, "VT", code, lib, -ht)
            ecr(nom, "VT", "4455", "État – TVA facturée", -tva)
        else:
            immo = code.startswith("2")
            ecr(nom, "AC", code, lib, ht, obs)
            ecr(nom, "AC", "34551" if immo else "34552", "État – TVA récupérable sur " + ("immobilisations" if immo else "charges"), tva)
            ecr(nom, "AC", "4411", "Fournisseurs" + (f" – {tiers}" if tiers else ""), -(ht + tva))
    return pd.DataFrame(lignes, columns=["Facture", "Journal", "Compte", "Libellé", "Débit", "Crédit", "Observation"])
