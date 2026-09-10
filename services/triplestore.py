"""Triplestore registry — resolves a dataset's `platform` field to a backend.

Every backend exposes the same surface:

    sparql_query(query)            -> (ok, results_dict)
    sparql_update(query)           -> (ok, message)
    load_rdf_file(graph, path)     -> (ok, message)   # append
    replace_graph(graph, path)     -> (ok, message)   # atomic where supported
    drop_graph(graph)              -> (ok, message)
    count_triples(graph)           -> int             # -1 on failure
    is_available()                 -> bool

Standards-compliant stores are all instances of SparqlHttpStore, configured by
URL layout and auth (see services/sparql_http.py). Adding another open-source
store means adding one entry to _BUILDERS below.

QLever needs its own class: it has no Graph Store Protocol, authenticates with a
bearer token, and loads bulk data by building an index offline.
"""
from pathlib import Path

from requests.auth import HTTPBasicAuth, HTTPDigestAuth

import config
from services import qlever
from services.comunica import ComunicaStore, parse_sources
from services.sparql_http import SparqlHttpStore, build_scope

DEFAULT_BACKEND = "qlever"

# The graphs a dataset owns. `/data` is the default graph so a plain
# `?s ?p ?o` reads the user's triples; the rest are reachable only via an
# explicit GRAPH, and nothing outside this list is reachable at all.
GRAPH_SUFFIXES = ("/data", "/examples", "/shapes")


def dataset_scope(ds):
    """Graph restriction confining a query to one dataset's own graphs.

    Pass to any backend's sparql_query(graphs=...). Without it, a query reaches
    every tenant's data in the shared store.
    """
    base = ds["graph_base"]
    return build_scope(
        default=base + "/data",
        named=[base + suffix for suffix in GRAPH_SUFFIXES],
    )


class QLeverStore:
    """QLever. Writes go to its delta layer and require --persist-updates on the
    server to survive a restart (see the platform Qleverfile)."""

    name = "qlever"

    def sparql_query(self, query, graphs=None, **kw):
        return qlever.sparql_query(query, graphs=graphs)

    def sparql_update(self, query, **kw):
        return qlever.sparql_update(query)

    def load_rdf_file(self, graph_uri, file_path, **kw):
        return qlever.load_rdf_file(graph_uri, file_path)

    def replace_graph(self, graph_uri, file_path, **kw):
        """QLever has no Graph Store Protocol, so this is drop-then-load and is
        NOT atomic: a failed load leaves the graph empty. Callers that need
        atomicity should prefer a backend that implements GSP PUT."""
        ok, msg = self.drop_graph(graph_uri)
        if not ok:
            return False, f"drop failed: {msg}"
        return self.load_rdf_file(graph_uri, file_path)

    def drop_graph(self, graph_uri, **kw):
        return qlever.drop_graph(graph_uri)

    def count_triples(self, graph_uri, **kw):
        return qlever.count_triples(graph_uri)

    def is_available(self):
        ok, _ = qlever.sparql_query("ASK { ?s ?p ?o }",
                                    timeout=config.BACKEND_PROBE_TIMEOUT)
        return ok


def _virtuoso_auth():
    if config.VIRTUOSO_USER:
        return HTTPDigestAuth(config.VIRTUOSO_USER, config.VIRTUOSO_PASSWORD)
    return None


def _fuseki_auth():
    if config.FUSEKI_USER:
        return HTTPBasicAuth(config.FUSEKI_USER, config.FUSEKI_PASSWORD)
    return None


# Each builder is called lazily so an unconfigured store costs nothing and a
# broken one cannot break import of this module.
_BUILDERS = {
    "qlever": QLeverStore,

    "fuseki": lambda: SparqlHttpStore(
        name="fuseki",
        base_url=f"{config.FUSEKI_BASE_URL}/{config.FUSEKI_DATASET}",
        query_path="/sparql",
        update_path="/update",
        gsp_path="/data",
        auth=_fuseki_auth(),
    ),

    # Virtuoso's writable endpoints are the -auth variants and expect digest auth.
    "virtuoso": lambda: SparqlHttpStore(
        name="virtuoso",
        base_url=config.VIRTUOSO_URL,
        query_path="/sparql",
        update_path="/sparql-auth",
        gsp_path="/sparql-graph-crud-auth",
        auth=_virtuoso_auth(),
    ),

    "oxigraph": lambda: SparqlHttpStore(
        name="oxigraph",
        base_url=config.OXIGRAPH_URL,
        query_path="/query",
        update_path="/update",
        gsp_path="/store",
    ),

    # Blazegraph serves query, update and GSP from one path, and calls the
    # Graph Store parameter "context-uri" rather than "graph".
    "blazegraph": lambda: SparqlHttpStore(
        name="blazegraph",
        base_url=config.BLAZEGRAPH_URL,
        query_path="/sparql",
        update_path="/sparql",
        gsp_path="/sparql",
        gsp_param="context-uri",
    ),

    "rdf4j": lambda: SparqlHttpStore(
        name="rdf4j",
        base_url=f"{config.RDF4J_URL}/repositories/{config.RDF4J_REPO}",
        query_path="",
        update_path="/statements",
        gsp_path="/rdf-graphs/service",
    ),

    # Comunica is a federation engine, not a store. Built with no sources here so
    # available() can probe it; get() below supplies each dataset's real sources.
    "comunica": lambda: ComunicaStore(sources=[]),
}

SUPPORTED = tuple(_BUILDERS)


def get(ds_row):
    """Return the backend for a dataset DB row (needs a 'platform' field).

    An unrecognised platform raises rather than silently falling back: a dataset
    pointed at the wrong store returns plausible-looking empty results, which is
    far harder to notice than an error.
    """
    try:
        platform = ds_row["platform"]
    except (KeyError, IndexError, TypeError):
        platform = None

    # Comunica is the one backend configured per-dataset: its "sources" column is
    # the federation target list, so it can't be built from global config alone.
    if platform == "comunica":
        try:
            raw_sources = ds_row["sources"]
        except (KeyError, IndexError, TypeError):
            raw_sources = None
        return ComunicaStore(parse_sources(raw_sources))

    return get_by_name(platform or DEFAULT_BACKEND)


def export_sample(ds, max_subjects: int = 5000, timeout: int = 300):
    """Write a sample of a dataset's triples to a temporary N-Triples file.

    Shape inference and validation read a file, which used to be the uploaded
    source. That file is not guaranteed to exist — it is removed after loading
    unless KEEP_UPLOADED_SOURCES says otherwise, an interrupted job clears it,
    and a dataset filled from several uploads never had one file holding all of
    it. The store is the thing that actually knows what the dataset contains.

    Samples whole subjects rather than the first N triples: a bare LIMIT would
    cut descriptions in half, and a shape inferred from half a description is
    wrong in a way that is hard to see.

    Returns (path, count) with path None when the dataset holds nothing.
    """
    import tempfile

    store = get(ds)
    scope = dataset_scope(ds)

    # Sample per rdf:type, not off the top of the dataset. A flat LIMIT takes
    # whatever subjects the store returns first — one contiguous run — so a type
    # holding a small share of the data gets few representatives or none, and its
    # optional properties disappear from the inferred shape entirely. Shapes are
    # per type, so sampling per type is what the result is actually made of.
    ok, types_result = store.sparql_query(
        "SELECT DISTINCT ?t WHERE { ?s a ?t } LIMIT 64", graphs=scope, timeout=timeout)
    types = ([b["t"]["value"] for b in types_result.get("results", {}).get("bindings", [])
              if b.get("t", {}).get("type") == "uri"] if ok else [])

    rows = []
    if types:
        per_type = max(50, int(max_subjects) // len(types))
        for t in types:
            ok, res = store.sparql_query(
                "SELECT ?s ?p ?o WHERE { "
                f"{{ SELECT ?s WHERE {{ ?s a <{t}> }} LIMIT {per_type} }} "
                "?s ?p ?o }", graphs=scope, timeout=timeout)
            if ok:
                rows.extend(res.get("results", {}).get("bindings", []))

    if not rows:
        # Untyped data, or a store that could not answer the type query.
        ok, result = store.sparql_query(
            "SELECT ?s ?p ?o WHERE { "
            f"{{ SELECT DISTINCT ?s WHERE {{ ?s ?p ?o }} LIMIT {int(max_subjects)} }} "
            "?s ?p ?o }", graphs=scope, timeout=timeout)
        if not ok:
            return None, 0
        rows = result.get("results", {}).get("bindings", [])
    if not rows:
        return None, 0

    def term(t):
        v = t.get("value", "")
        kind = t.get("type")
        if kind == "uri":
            return f"<{v}>"
        if kind == "bnode":
            return f"_:{v}"
        lit = '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'
        if t.get("xml:lang"):
            return f'{lit}@{t["xml:lang"]}'
        if t.get("datatype"):
            return f'{lit}^^<{t["datatype"]}>'
        return lit

    fd, name = tempfile.mkstemp(suffix=".nt", prefix="koetai-sample-")
    written = 0
    with open(fd, "w", encoding="utf-8") as f:
        for b in rows:
            try:
                f.write(f"{term(b['s'])} {term(b['p'])} {term(b['o'])} .\n")
                written += 1
            except (KeyError, TypeError):
                continue
    return Path(name), written


def get_by_name(platform: str):
    builder = _BUILDERS.get(platform)
    if builder is None:
        raise ValueError(
            f"Unknown triplestore backend {platform!r}. Supported: {', '.join(SUPPORTED)}"
        )
    return builder()


# What each backend is, for the dataset form. Kept beside the registry so a new
# backend is described where it is added rather than in a template that nobody
# remembers to update — which is how the form came to offer three of seven.
# `tested` marks the ones Koetai has actually been run against; the rest use the
# same SPARQL 1.1 + Graph Store client and are configured the same way, but are
# unproven, and saying so is more useful than listing them as equals.
# label, description, tested, kind. `kind` decides how absence is described:
# a server that is not up is "not running" and starting it is the fix, whereas
# Comunica is a command-line tool that is either installed or not — calling that
# "not running" sends you looking for a service that was never meant to exist.
BACKEND_INFO = {
    "fuseki":     ("Fuseki / Jena", "Apache Jena TDB2 — queryable as soon as an upload finishes. The safe default.", True, "server"),
    "oxigraph":   ("Oxigraph",      "Lighter than Fuseki and just as durable. Needs Oxigraph 0.5.11 or later.", True, "server"),
    "qlever":     ("QLever",        "Very fast over large read-mostly data, but Koetai cannot upload to it — querying an existing index only.", True, "server"),
    "comunica":   ("Federation (Comunica)", "Not a store: holds no data and queries a list of external SPARQL endpoints and RDF files live. Needs Node and @comunica/query-sparql on the server.", True, "tool"),
    "virtuoso":   ("Virtuoso",      "Implemented but never tested against Koetai.", False, "server"),
    "blazegraph": ("Blazegraph",    "Implemented but never tested against Koetai.", False, "server"),
    "rdf4j":      ("RDF4J",         "Implemented but never tested against Koetai.", False, "server"),
}


def backend_choices() -> list[dict]:
    """The backends to offer on the dataset form, reachable ones first.

    The form used to hardcode its options, so it offered QLever by default with
    no QLever running, and had no entry at all for stores that were.
    """
    status = available()
    choices = []
    for name, (label, blurb, tested, kind) in BACKEND_INFO.items():
        choices.append({
            "name": name, "label": label, "description": blurb,
            "tested": tested, "available": status.get(name, False),
            "unavailable_label": "not installed" if kind == "tool" else "not running",
        })
    # Reachable first, then the ones that have been proven, then by definition
    # order — so the top option is always something that will actually work.
    choices.sort(key=lambda c: (not c["available"], not c["tested"]))
    return choices


def available() -> dict[str, bool]:
    """Map each supported backend to whether it is reachable right now.

    Used by a local install to show which stores the host actually has running.
    """
    status = {}
    for name in SUPPORTED:
        try:
            status[name] = get_by_name(name).is_available()
        except Exception:
            status[name] = False
    return status
