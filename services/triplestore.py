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
BACKEND_INFO = {
    "fuseki":     ("Fuseki / Jena", "Apache Jena TDB2 — queryable as soon as an upload finishes. The safe default.", True),
    "oxigraph":   ("Oxigraph",      "Lighter than Fuseki and just as durable. Needs Oxigraph 0.5.11 or later.", True),
    "qlever":     ("QLever",        "Very fast over large read-mostly data, but Koetai cannot upload to it — querying an existing index only.", True),
    "comunica":   ("Federation (Comunica)", "Not a store: holds no data and queries a list of external sources live.", True),
    "virtuoso":   ("Virtuoso",      "Implemented but never tested against Koetai.", False),
    "blazegraph": ("Blazegraph",    "Implemented but never tested against Koetai.", False),
    "rdf4j":      ("RDF4J",         "Implemented but never tested against Koetai.", False),
}


def backend_choices() -> list[dict]:
    """The backends to offer on the dataset form, reachable ones first.

    The form used to hardcode its options, so it offered QLever by default with
    no QLever running, and had no entry at all for stores that were.
    """
    status = available()
    choices = []
    for name, (label, blurb, tested) in BACKEND_INFO.items():
        choices.append({
            "name": name, "label": label, "description": blurb,
            "tested": tested, "available": status.get(name, False),
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
