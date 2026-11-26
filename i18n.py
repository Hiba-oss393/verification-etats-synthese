"""MaroIFRS - traduction centralisée de l'interface (français / anglais).

PRINCIPE : ce module ne contient AUCUN traitement métier. Il traduit uniquement, au moment de l'AFFICHAGE, les textes que app.py envoie à Streamlit
(titres, étiquettes, boutons, messages, en-têtes de tableaux, textes HTML, titres de graphiques). Les données, les calculs, les formules, les valeurs
renvoyées par les widgets (selectbox, radio, multiselect...) et les rapports PDF / Excel ne passent JAMAIS par ici : ils restent identiques dans les deux langues.

Fonctionnement :
  - le dictionnaire français -> anglais est dans traductions_en.txt (une ligne « français ::: anglais » ; \\n = saut de ligne) ;
  - tr(texte) traduit un texte entier (correspondance exacte) ou, pour un texte composé (f-string avec des nombres, du HTML), chaque fragment connu ;
  - tout texte absent du dictionnaire est affiché tel quel (français) : jamais d'erreur, jamais de texte perdu ;
  - installer(st) enveloppe les fonctions d'affichage de Streamlit ; en français les enveloppes laissent passer les appels sans aucune modification.
La langue est stockée dans st.session_state["lang"] et dans le paramètre d'adresse ?lang=fr|en (la langue survit à l'actualisation de la page et se partage par lien).
"""
import functools
import html as _html
import re
from pathlib import Path

LANGUES = {"fr": "🇫🇷 Français", "en": "🇬🇧 English"}
LANGUE_PAR_DEFAUT = "fr"
FICHIER_EN = Path(__file__).with_name("traductions_en.txt")
SEPARATEUR = " ::: "

# ------------------------------ dictionnaire ------------------------------


def _charger(chemin):
    """Lit le fichier « français ::: anglais ». Les lignes vides ou commençant par # sont ignorées."""
    d = {}
    try:
        with open(chemin, encoding="utf-8") as f:
            for ligne in f:
                ligne = ligne.rstrip("\n")
                if not ligne.strip() or ligne.lstrip().startswith("#") or SEPARATEUR not in ligne:
                    continue
                fr, en = ligne.split(SEPARATEUR, 1)
                fr = fr.replace("\\n", "\n").strip()
                en = en.replace("\\n", "\n").strip()
                if fr and en:
                    d[fr] = en
    except OSError:
        pass
    return d


DICT_EN = _charger(FICHIER_EN)
_MOT = re.compile(r"\w+", re.UNICODE)


def _indexer(dico):
    """Index des fragments par premier mot (ou premier caractère) : balayage rapide, plus longs fragments d'abord."""
    idx = {}
    for fr in dico:
        cle = _MOT.match(fr)
        k = cle.group(0).lower() if cle else fr[0]
        idx.setdefault(k, []).append(fr)
    for k in idx:
        idx[k].sort(key=len, reverse=True)
    return idx


_INDEX_EN = _indexer(DICT_EN)


def _balayer(texte, dico, index):
    """Remplace dans `texte` chaque fragment connu (le plus long d'abord, sur des limites de mots)."""
    sortie, i, n = [], 0, len(texte)
    while i < n:
        c = texte[i]
        debut_mot = c.isalnum() or c == "_"
        if debut_mot and i > 0 and (texte[i - 1].isalnum() or texte[i - 1] == "_"):
            # au milieu d'un mot : on ne cherche pas de fragment ici
            sortie.append(c)
            i += 1
            continue
        if debut_mot:
            m = _MOT.match(texte, i)
            candidats = index.get(m.group(0).lower(), ())
        else:
            candidats = index.get(c, ())
        for cand in candidats:
            if texte.startswith(cand, i):
                fin = i + len(cand)
                derniere = cand[-1]
                if (derniere.isalnum() or derniere == "_") and fin < n and (texte[fin].isalnum() or texte[fin] == "_"):
                    continue
                sortie.append(dico[cand])
                i = fin
                break
        else:
            sortie.append(c)
            i += 1
    return "".join(sortie)


_BALISE = re.compile(r"(<[^>]*>)")


@functools.lru_cache(maxsize=30000)
def _tr_noeud(texte):
    """Traduit un texte brut (sans balise) : correspondance exacte, sinon fragments connus. Espaces de début et de fin conservés."""
    nu = texte.strip()
    if not nu:
        return texte
    debut = texte[: len(texte) - len(texte.lstrip())]
    fin = texte[len(texte.rstrip()):]
    echappe = "&" in nu and re.search(r"&(#\w+|\w+);", nu) is not None
    if echappe:                                   # le texte vient d'un HTML échappé (&#x27; &amp; ...) : on traduit le texte réel
        nu = _html.unescape(nu)
    if nu in DICT_EN:
        res = DICT_EN[nu]
    else:
        res = _balayer(nu, DICT_EN, _INDEX_EN)
    if echappe and res == nu:
        return texte                                  # rien à traduire : le texte d'origine (avec ses entités) est conservé tel quel
    if echappe:
        res = res.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return debut + res + fin


def tr(texte, lang="en"):
    """Traduit un texte (brut, Markdown ou HTML) vers `lang`. Pour « fr » ou un texte non textuel, le texte est rendu inchangé."""
    if lang != "en" or not isinstance(texte, str) or not texte:
        return texte
    if texte.lstrip().startswith("<style") or texte.lstrip().startswith("<script"):
        return texte
    if "<" in texte and ">" in texte:
        morceaux = _BALISE.split(texte)
        return "".join(m if (m.startswith("<") and m.endswith(">")) else _tr_noeud(m) for m in morceaux)
    return _tr_noeud(texte)


# ------------------------------ langue courante ------------------------------


def normaliser(code):
    code = str(code or "").strip().lower()[:2]
    return code if code in LANGUES else LANGUE_PAR_DEFAUT


def langue_depuis_adresse(st):
    """Langue demandée dans l'adresse (?lang=en), sinon la langue par défaut."""
    try:
        v = st.query_params.get("lang")
        if isinstance(v, (list, tuple)):
            v = v[0] if v else None
        return normaliser(v) if v else LANGUE_PAR_DEFAUT
    except Exception:
        return LANGUE_PAR_DEFAUT


def initialiser_langue(st):
    """À appeler au début de chaque exécution : la langue de la session prime ; au premier passage, celle de l'adresse."""
    if st.session_state.get("lang") not in LANGUES:
        st.session_state["lang"] = langue_depuis_adresse(st)
    return st.session_state["lang"]


def changer_langue(st, code):
    """Enregistre la langue choisie (session + adresse). Ne touche à aucune donnée."""
    code = normaliser(code)
    st.session_state["lang"] = code
    try:
        st.query_params["lang"] = code
    except Exception:
        pass
    return code


# ------------------------------ enveloppes autour des fonctions d'affichage de Streamlit ------------------------------
# nom de la fonction -> (positions des arguments texte, noms des arguments texte). Les positions ne comptent pas « self ».
_TEXTES = {
    "markdown": ((0,), ("body", "help")),
    "title": ((0,), ("body", "help")),
    "header": ((0,), ("body", "help")),
    "subheader": ((0,), ("body", "help")),
    "caption": ((0,), ("body", "help")),
    "info": ((0,), ("body",)),
    "warning": ((0,), ("body",)),
    "error": ((0,), ("body",)),
    "success": ((0,), ("body",)),
    "toast": ((0,), ("body",)),
    "button": ((0,), ("label", "help")),
    "download_button": ((0,), ("label", "help")),
    "link_button": ((0,), ("label", "help")),
    "text_input": ((0,), ("label", "placeholder", "help")),
    "text_area": ((0,), ("label", "placeholder", "help")),
    "number_input": ((0,), ("label", "help")),
    "checkbox": ((0,), ("label", "help")),
    "toggle": ((0,), ("label", "help")),
    "radio": ((0,), ("label", "help")),
    "selectbox": ((0,), ("label", "placeholder", "help")),
    "multiselect": ((0,), ("label", "placeholder", "help")),
    "select_slider": ((0,), ("label", "help")),
    "slider": ((0,), ("label", "help")),
    "date_input": ((0,), ("label", "help")),
    "file_uploader": ((0,), ("label", "help")),
    "pills": ((0,), ("label", "help")),
    "segmented_control": ((0,), ("label", "help")),
    "expander": ((0,), ("label",)),
    "popover": ((0,), ("label", "help")),
    "status": ((0,), ("label",)),
    "spinner": ((0,), ("text",)),
    "metric": ((0, 1, 2), ("label", "value", "delta", "help")),
    "progress": ((), ("text",)),
}
_AVEC_FORMAT = ("radio", "selectbox", "multiselect", "select_slider", "pills", "segmented_control")
_TABLEAUX = ("dataframe", "data_editor")


def _langue_session(st):
    try:
        return st.session_state.get("lang", LANGUE_PAR_DEFAUT)
    except Exception:
        return LANGUE_PAR_DEFAUT


def _traduire_liste(liste, lang):
    return [tr(x, lang) if isinstance(x, str) else x for x in liste]


def _etiquettes_colonnes(donnees, config, lang):
    """Étiquettes anglaises des colonnes d'un tableau, ajoutées à column_config (affichage seul : aucune colonne n'est renommée)."""
    colonnes = getattr(getattr(donnees, "data", donnees), "columns", None)
    if colonnes is None:
        return config
    cfg = dict(config) if isinstance(config, dict) else {}
    for c in colonnes:
        if not isinstance(c, str):
            continue
        lib = tr(c, lang)
        if lib == c:
            continue
        entree = cfg.get(c)
        if entree is None:
            cfg[c] = {"label": lib}
        elif isinstance(entree, dict) and not entree.get("label"):
            e = dict(entree)
            e["label"] = lib
            cfg[c] = e
    return cfg


def _enveloppe_texte(fn, nom, decalage):
    positions, noms = _TEXTES[nom]

    def enveloppe(*a, **k):
        st_ = _etat["st"]
        lang = _langue_session(st_) if st_ is not None else LANGUE_PAR_DEFAUT
        if lang != "en":
            return fn(*a, **k)
        try:
            a = list(a)
            for p in positions:
                q = p + decalage
                if q < len(a) and isinstance(a[q], str):
                    a[q] = tr(a[q], lang)
            for n_ in noms:
                if isinstance(k.get(n_), str):
                    k[n_] = tr(k[n_], lang)
            if nom in _AVEC_FORMAT:
                f0 = k.get("format_func")

                def fmt(x, _f0=f0):
                    v = _f0(x) if _f0 is not None else x
                    return tr(v, lang) if isinstance(v, str) else (str(v) if _f0 is None else v)
                k["format_func"] = fmt
        except Exception:
            pass
        return fn(*a, **k)
    enveloppe._i18n_orig = fn
    enveloppe.__name__ = getattr(fn, "__name__", nom)
    return enveloppe


def _enveloppe_onglets(fn, decalage):
    def enveloppe(*a, **k):
        st_ = _etat["st"]
        lang = _langue_session(st_) if st_ is not None else LANGUE_PAR_DEFAUT
        if lang == "en":
            try:
                a = list(a)
                q = decalage
                if q < len(a) and isinstance(a[q], (list, tuple)):
                    a[q] = _traduire_liste(a[q], lang)
                elif isinstance(k.get("tabs"), (list, tuple)):
                    k["tabs"] = _traduire_liste(k["tabs"], lang)
            except Exception:
                pass
        return fn(*a, **k)
    enveloppe._i18n_orig = fn
    return enveloppe


def _enveloppe_tableau(fn, nom, decalage):
    def enveloppe(*a, **k):
        st_ = _etat["st"]
        lang = _langue_session(st_) if st_ is not None else LANGUE_PAR_DEFAUT
        if lang == "en":
            try:
                donnees = a[decalage] if len(a) > decalage else k.get("data")
                k["column_config"] = _etiquettes_colonnes(donnees, k.get("column_config"), lang)
            except Exception:
                pass
        return fn(*a, **k)
    enveloppe._i18n_orig = fn
    return enveloppe


_CLES_TEXTE_GRAPHIQUE = ("title", "text", "subtitle")


def _traduire_spec(obj, lang, cle=None, dans_donnees=False):
    """Traduit les titres, domaines de couleurs et valeurs textuelles d'une spécification Vega-Lite ; les noms de champs ne sont jamais modifiés."""
    if isinstance(obj, dict):
        res = {}
        for k, v in obj.items():
            if k in ("datasets",) or (k == "data" and isinstance(v, dict)):
                res[k] = _traduire_spec(v, lang, k, True)
            elif dans_donnees:
                res[k] = _traduire_spec(v, lang, k, True)       # enregistrements : on traduit les VALEURS, jamais les clés
            elif k in _CLES_TEXTE_GRAPHIQUE or k in ("domain", "sort", "values"):
                res[k] = _traduire_spec(v, lang, k, False)
            else:
                res[k] = _traduire_spec(v, lang, None, False)
        return res
    if isinstance(obj, list):
        return [_traduire_spec(x, lang, cle, dans_donnees) for x in obj]
    if isinstance(obj, str) and (dans_donnees or cle in _CLES_TEXTE_GRAPHIQUE or cle in ("domain", "sort", "values")):
        return tr(obj, lang)
    return obj


def _enveloppe_altair(fn, decalage):
    def enveloppe(*a, **k):
        st_ = _etat["st"]
        lang = _langue_session(st_) if st_ is not None else LANGUE_PAR_DEFAUT
        if lang == "en":
            try:
                import altair as alt
                graphique = a[decalage] if len(a) > decalage else k.get("chart")
                spec = _traduire_spec(graphique.to_dict(), lang)
                nouveau = alt.Chart.from_dict(spec)
                if len(a) > decalage:
                    a = list(a)
                    a[decalage] = nouveau
                else:
                    k["chart"] = nouveau
            except Exception:
                pass
        return fn(*a, **k)
    enveloppe._i18n_orig = fn
    return enveloppe


def _ecrire_traduit(fn, decalage):
    def enveloppe(*a, **k):
        st_ = _etat["st"]
        lang = _langue_session(st_) if st_ is not None else LANGUE_PAR_DEFAUT
        if lang == "en":
            try:
                a = list(a)
                for i in range(decalage, len(a)):
                    if isinstance(a[i], str):
                        a[i] = tr(a[i], lang)
            except Exception:
                pass
        return fn(*a, **k)
    enveloppe._i18n_orig = fn
    return enveloppe


_etat = {"st": None}


def _poser(cible, nom, fabrique):
    """Remplace cible.nom par son enveloppe, une seule fois (l'original est toujours celui qu'on enveloppe)."""
    try:
        courant = getattr(cible, nom)
    except AttributeError:
        return
    if getattr(courant, "_i18n_orig", None) is not None:
        return
    try:
        setattr(cible, nom, fabrique(courant))
    except Exception:
        pass


def installer(st):
    """Enveloppe les fonctions d'affichage de Streamlit (module `st` et classe DeltaGenerator, qui sert aux colonnes, onglets, conteneurs).
    Idempotent : peut être appelé à chaque exécution du script. En français, les enveloppes ne modifient aucun appel."""
    _etat["st"] = st
    cibles = [(st, 0)]
    try:
        from streamlit.delta_generator import DeltaGenerator
        cibles.append((DeltaGenerator, 1))
    except Exception:
        pass
    for cible, decalage in cibles:
        for nom in _TEXTES:
            _poser(cible, nom, lambda f, n=nom, d=decalage: _enveloppe_texte(f, n, d))
        _poser(cible, "tabs", lambda f, d=decalage: _enveloppe_onglets(f, d))
        for nom in _TABLEAUX:
            _poser(cible, nom, lambda f, n=nom, d=decalage: _enveloppe_tableau(f, n, d))
        _poser(cible, "altair_chart", lambda f, d=decalage: _enveloppe_altair(f, d))
        _poser(cible, "write", lambda f, d=decalage: _ecrire_traduit(f, d))
    return st
