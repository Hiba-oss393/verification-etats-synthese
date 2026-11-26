"""SIMULATEUR de Streamlit : exécute app.py de bout en bout avec un faux module `streamlit` (aucun navigateur, aucun rendu).
Il vérifie que le code de l'interface s'exécute sans erreur Python et que les bons messages / boutons sont produits ; il ne prouve rien
sur l'affichage réel. Le test avec le vrai Streamlit (AppTest) se trouve dans test_interface.py et n'est exécuté que si Streamlit est installé."""
import io, os, sys, types, json, traceback, tempfile
import pandas as pd
import _chemins

class Univ:
    def __getattr__(self, n):
        if n.startswith("__"): raise AttributeError(n)
        return lambda *a, **k: Univ()
    def __call__(self, *a, **k): return Univ()
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __iter__(self): return iter([Univ(), Univ()])

class FakeFile(io.BytesIO):
    def __init__(self, name, data):
        super().__init__(data); self.name = name; self.size = len(data)

class Deleg:
    def __init__(self, st): self._st = st
    def __getattr__(self, n): return getattr(self._st, n)
    def __enter__(self): return self
    def __exit__(self, *a): return False

class FakeSt(types.ModuleType):
    def __init__(self, scenario):
        super().__init__("streamlit")
        self.scn = scenario; self.session_state = {}; self.log = {"error": [], "warning": [], "success": [], "download": [], "markdown": 0}
        self.column_config = Univ(); self.query_params = {}
    def cache_data(self, *a, **k):
        if a and callable(a[0]): return a[0]
        return lambda f: f
    cache_resource = cache_data
    def set_page_config(self,*a,**k): pass
    def markdown(self, texte="", *a, **k):
        self.log["markdown"] += 1
        self.log.setdefault("textes", []).append(str(texte))
    def error(self,t,*a,**k): self.log["error"].append(str(t))
    def warning(self,t,*a,**k): self.log["warning"].append(str(t))
    def success(self,t,*a,**k): self.log["success"].append(str(t))
    def info(self,*a,**k): pass
    def caption(self,*a,**k): pass
    def write(self,*a,**k): pass
    def spinner(self,*a,**k): return Deleg(self)
    def empty(self,*a,**k): return Deleg(self)
    def expander(self,*a,**k): return Deleg(self)
    def columns(self, spec, *a, **k):
        n = spec if isinstance(spec, int) else len(spec); return [Deleg(self) for _ in range(n)]
    def tabs(self, names): return [Deleg(self) for _ in names]
    def file_uploader(self, label, *a, **k):
        return self.scn.get("uploads", {}).get(k.get("key", "main"), [] if k.get("accept_multiple_files") else None)
    def data_editor(self, df, *a, **k):
        key = k.get("key", "")
        for pref, fn in self.scn.get("editors", {}).items():
            if key.startswith(pref): return fn(df)
        return df
    def dataframe(self, data=None, *a, **k):
        # rend réellement les tableaux stylés (Styler) pour détecter une erreur de mise en forme que Streamlit afficherait
        if hasattr(data, "to_html"):
            data.to_html()
        self.log["dataframes"] = self.log.get("dataframes", 0) + 1
    def download_button(self, label, data, *a, **k): self.log["download"].append((label, len(data) if data else 0))
    def radio(self, label, options, *a, **k): return self.scn.get("radio", {}).get(k.get("key"), options[0])
    def selectbox(self, label, options, *a, **k):
        o = list(options)
        choix = self.scn.get("select", {}).get(k.get("key"))
        if choix is not None:
            assert choix in o, f"choix {choix!r} absent des options {o} (clé {k.get('key')})"
            return choix
        return o[k.get("index", 0) or 0] if o else None
    def multiselect(self, label, options, default=None, *a, **k): return default if default is not None else []
    def number_input(self, label, *a, **k):
        if k.get("key") in self.scn.get("number", {}): return self.scn["number"][k["key"]]
        if "value" in k: return k["value"]
        return a[2] if len(a) >= 3 else 0
    def text_input(self, label, *a, **k): return self.scn.get("text", {}).get(k.get("key"), "")
    def checkbox(self, *a, **k): return k.get("value", False)
    def button(self, *a, **k): return False
    def slider(self, label, *a, **k): return k.get("value", a[2] if len(a) > 2 else 0)
    def text_area(self,*a,**k): return ""
    def rerun(self): pass
    def fragment(self, f=None, **k):
        return f if f is not None else (lambda g: g)
    def container(self, *a, **k): return Deleg(self)
    def stop(self): pass
    def __getattr__(self, n):
        if n.startswith("__"): raise AttributeError(n)
        return lambda *a, **k: Univ()

def run(scenario):
    # les vrais modules sont remis en place après l'exécution : sinon le test avec le vrai Streamlit (AppTest) serait toujours « ignoré »
    sauve = {k: sys.modules.get(k) for k in ("streamlit", "altair")}
    st = FakeSt(scenario); sys.modules["streamlit"] = st
    m = types.ModuleType("altair"); m.__getattr__ = lambda n: (lambda *a, **k: Univ()); sys.modules["altair"] = m
    os.chdir(_chemins.RACINE)
    # isolation : l'historique de test n'est JAMAIS écrit dans le vrai historique.json du projet
    os.environ["MAROIFRS_HISTORIQUE"] = scenario.get("historique") or os.path.join(tempfile.mkdtemp(prefix="maroifrs_histo_"), "historique.json")
    src = open("app.py", encoding="utf-8").read()
    g = {"__name__": "__main__", "__file__": str(_chemins.RACINE / "app.py")}
    try:
        exec(compile(src, "app.py", "exec"), g)
        return st, g, None
    except Exception:
        return st, g, traceback.format_exc()
    finally:
        for k, v in sauve.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def statuts_affiches(st):
    """Statuts de conformité affichés à l'écran (page Transformation et page Résultats) : liste des libellés, dans l'ordre d'affichage."""
    import re as _re
    sortie = []
    for t in st.log.get("textes", []):
        if 'class="statut-final"' in t:
            m = _re.search(r"Statut de conformité : ([^<]+)</div>", t)
            if m:
                sortie.append(m.group(1).strip())
    return sortie
