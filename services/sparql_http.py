"""Generic SPARQL 1.1 Protocol + Graph Store Protocol client.

Every standards-compliant triplestore — Fuseki, Virtuoso, Oxigraph, Blazegraph,
RDF4J, GraphDB — speaks SPARQL 1.1 Query/Update plus the Graph Store Protocol.
They differ only in URL layout, the name of the Graph Store query parameter, and
authentication. Those are constructor arguments here rather than subclasses, so
adding a new open-source store is a line in the registry, not a new module.

QLever is deliberately not built on this: it has no Graph Store Protocol and
loads bulk data by building an index offline. See services/qlever.py.
"""
import re
from pathlib import Path
from typing import NamedTuple, Optional

import requests

import config


class Scope(NamedTuple):
    """The set of graphs a query is allowed to see.

    `default` becomes the query's default graph, so a plain `?s ?p ?o` reads it.
    `named` is what GRAPH can range over. Both map onto the SPARQL 1.1 Protocol
    `default-graph-uri` / `named-graph-uri` parameters, which every compliant
    store enforces server-side.
    """
    default: Optional[str] = None
    named: tuple = ()


def build_scope(default: str = None, named=()) -> Scope:
    return Scope(default=default, named=tuple(named))


def scoped_body(query: str, graphs: Optional[Scope]) -> dict:
    """Form body for a query, adding protocol-level graph restrictions."""
    body = {"query": query}
    if not graphs:
        return body
    if graphs.default:
        body["default-graph-uri"] = graphs.default
    if graphs.named:
        body["named-graph-uri"] = list(graphs.named)
    return body


def scoped_params(graphs: Optional[Scope]) -> dict:
    """Graph restrictions as URL query parameters.

    The companion to scoped_body for stores queried with "query via POST
    directly" (SPARQL 1.1 Protocol 2.1.3): the query travels as the raw body, so
    the graph parameters have to go in the URL. Oxigraph 0.5 additionally
    rejects a repeated named-graph-uri in a form-encoded body — it reports
    "'named-graph-uri' cannot be set both in the body and the URL query" — so
    the URL is also the only encoding it accepts for a multi-graph scope.
    """
    if not graphs:
        return {}
    params = {}
    if graphs.default:
        params["default-graph-uri"] = graphs.default
    if graphs.named:
        params["named-graph-uri"] = list(graphs.named)
    return params


RDF_CONTENT_TYPES = {
    ".ttl":    "text/turtle",
    ".n3":     "text/n3",
    ".nt":     "application/n-triples",
    ".nq":     "application/n-quads",
    ".trig":   "application/trig",
    ".rdf":    "application/rdf+xml",
    ".owl":    "application/rdf+xml",
    ".jsonld": "application/ld+json",
}


def rdf_content_type(ext: str) -> str:
    return RDF_CONTENT_TYPES.get(ext.lower(), "text/turtle")


# ── query form, and what to ask the store for ──────────────────────────────
#
# SELECT and ASK return a table of bindings; CONSTRUCT and DESCRIBE return a
# graph. A store will not convert between the two — it answers 406 — so asking
# for the wrong one is not a formatting detail, it is a failed query.
GRAPH_FORMS = {"CONSTRUCT", "DESCRIBE"}
RESULTS_ACCEPT = "application/sparql-results+json"
GRAPH_ACCEPT   = "text/turtle"

_FORM_RE = re.compile(r"\b(SELECT|ASK|CONSTRUCT|DESCRIBE)\b", re.I)


def query_form(query: str) -> str:
    """Which of the four SPARQL query forms `query` uses.

    IRIs are removed before comments, not after: an IRI may contain a '#', so
    stripping comments first truncates `<...skos/core#Concept>` and takes the
    rest of the line with it. Strings go too, so that a literal mentioning one
    of the keywords does not decide the answer.

    Defaults to SELECT, which is the safe guess: it only chooses a default
    Accept header, and a client that named a format is never overridden.
    """
    t = re.sub(r"<[^<>\s]*>", " ", query)          # IRIs first — they contain '#'
    t = re.sub(r"#[^\n]*", " ", t)                  # then comments
    t = re.sub(r'"""(?:.|\n)*?"""', " ", t)
    t = re.sub(r"\'\'\'(?:.|\n)*?\'\'\'", " ", t)
    t = re.sub(r'"(?:[^"\\\n]|\\.)*"', " ", t)
    t = re.sub(r"\'(?:[^\'\\\n]|\\.)*\'", " ", t)
    m = _FORM_RE.search(t)
    return m.group(1).upper() if m else "SELECT"


def accept_for(query: str, client_accept: str = "") -> str:
    """The Accept header to send to the store.

    A client that asked for a specific format gets it — that is what makes the
    endpoint usable for CSV, XML or N-Triples. A client that asked for nothing
    in particular (curl sends */*, fetch sends nothing, a browser sends a list
    ending in */*) gets a default chosen by the query form.
    """
    a = (client_accept or "").strip()
    if a and "*/*" not in a and "text/html" not in a:
        return a
    return GRAPH_ACCEPT if query_form(query) in GRAPH_FORMS else RESULTS_ACCEPT


class SparqlHttpStore:
    """A triplestore reachable over SPARQL 1.1 + Graph Store Protocol."""

    def __init__(
        self,
        name: str,
        base_url: str,
        query_path: str = "/sparql",
        update_path: str = "/update",
        gsp_path: str = "/data",
        gsp_param: str = "graph",
        auth=None,
        query_timeout: int = 120,
        load_timeout: int = None,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.query_path = query_path
        self.update_path = update_path
        self.gsp_path = gsp_path
        # Blazegraph calls this "context-uri"; the spec and everyone else say "graph".
        self.gsp_param = gsp_param
        self.auth = auth
        self.query_timeout = query_timeout
        self.load_timeout = load_timeout if load_timeout is not None else config.RDF_LOAD_TIMEOUT

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def sparql_query(self, query: str, graphs: "Scope" = None, timeout: int = None, **kw) -> tuple[bool, dict]:
        """Run a query, optionally confined to `graphs` (see build_scope).

        Scoping is enforced by the store via the SPARQL 1.1 Protocol, not by
        inspecting the query text — so GRAPH ?g cannot enumerate beyond the
        permitted graphs and an explicit GRAPH <other> simply matches nothing.
        """
        try:
            # "Query via POST directly": the query is the request body and the
            # graph restrictions are URL parameters. Preferred over posting
            # everything form-encoded because Oxigraph rejects a repeated
            # named-graph-uri in the body — see scoped_params.
            r = requests.post(
                self._url(self.query_path),
                params=scoped_params(graphs),
                data=query.encode(),
                headers={"Content-Type": "application/sparql-query",
                         "Accept": "application/sparql-results+json"},
                auth=self.auth,
                timeout=timeout or self.query_timeout,
            )
            if r.status_code < 400:
                return True, r.json()
            return False, {"error": r.text}
        except Exception as e:
            return False, {"error": str(e)}

    def sparql_query_raw(self, query: str, graphs: "Scope" = None,
                         accept: str = None, timeout: int = None):
        """Run a query and return the store's own response untouched.

        sparql_query() parses JSON because that is what the rest of the app
        wants. A public SPARQL endpoint has a different job: hand back what the
        client asked for, in the format the store produced it. That is the only
        way CONSTRUCT and DESCRIBE can work at all — they return a graph, and
        there is no JSON result object to parse.

        Returns (ok, body: bytes, content_type: str, status: int).
        """
        accept = accept or RESULTS_ACCEPT
        try:
            r = requests.post(
                self._url(self.query_path),
                params=scoped_params(graphs),
                data=query.encode(),
                headers={"Content-Type": "application/sparql-query",
                         "Accept": accept},
                auth=self.auth,
                timeout=timeout or self.query_timeout,
            )
            return (r.status_code < 400, r.content,
                    r.headers.get("Content-Type", "application/octet-stream"),
                    r.status_code)
        except Exception as e:
            return False, str(e).encode(), "text/plain; charset=utf-8", 502

    def sparql_update(self, query: str, **kw) -> tuple[bool, str]:
        try:
            r = requests.post(
                self._url(self.update_path),
                data=query.encode(),
                headers={"Content-Type": "application/sparql-update"},
                auth=self.auth,
                timeout=self.query_timeout,
            )
            return r.status_code < 400, r.text
        except Exception as e:
            return False, str(e)

    def load_rdf_file(self, graph_uri: str, file_path: Path, progress=None, **kw) -> tuple[bool, str]:
        """Append a file into a named graph (Graph Store Protocol POST)."""
        return self._gsp_write(requests.post, graph_uri, file_path, "Loaded into", progress)

    def replace_graph(self, graph_uri: str, file_path: Path, progress=None, **kw) -> tuple[bool, str]:
        """Replace a named graph atomically (Graph Store Protocol PUT)."""
        return self._gsp_write(requests.put, graph_uri, file_path, "Replaced", progress)

    # Formats where one line is one statement, so the file can be split without
    # parsing it. Turtle and RDF/XML cannot: prefixes and nesting carry across
    # lines, and half a Turtle file is not Turtle.
    LINE_BASED = {".nt", ".nq"}

    def _gsp_write(self, method, graph_uri: str, file_path: Path, verb: str,
                   progress=None) -> tuple[bool, str]:
        file_path = Path(file_path)
        if (file_path.suffix.lower() in self.LINE_BASED
                and config.RDF_LOAD_BATCH_LINES > 0):
            return self._gsp_write_batched(method, graph_uri, file_path, verb, progress)
        try:
            with open(file_path, "rb") as f:
                r = method(
                    self._url(self.gsp_path),
                    params={self.gsp_param: graph_uri},
                    data=f,
                    headers={"Content-Type": rdf_content_type(file_path.suffix)},
                    auth=self.auth,
                    timeout=self.load_timeout,
                )
            if r.status_code < 400:
                return True, f"{verb} <{graph_uri}>"
            return False, r.text
        except Exception as e:
            return False, str(e)

    def _gsp_write_batched(self, method, graph_uri: str, file_path: Path, verb: str,
                           progress=None) -> tuple[bool, str]:
        """Send a line-based file to the store in batches.

        One Graph Store Protocol write is one transaction, which the store keeps
        in memory until it commits, so the peak cost of a load scales with the
        file rather than with anything bounded. That is what killed two real
        imports here: the kernel's OOM killer took Oxigraph at ~10 GB, and the
        app saw only "Remote end closed connection without response".

        Batching trades atomicity for a bounded footprint. A failure part-way
        therefore leaves the graph partly written, which the caller is told
        about — silently keeping half a dataset would be worse than saying so.
        """
        total = 0
        batch, batch_lines = [], 0
        ctype = rdf_content_type(file_path.suffix)
        first = True

        def send(lines):
            nonlocal first
            body = b"".join(lines)
            # Only the first batch may replace; the rest must append, or each
            # batch would wipe the one before it.
            verb_fn = method if first else requests.post
            r = verb_fn(
                self._url(self.gsp_path),
                params={self.gsp_param: graph_uri},
                data=body,
                headers={"Content-Type": ctype},
                auth=self.auth,
                timeout=self.load_timeout,
            )
            first = False
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")

        try:
            with open(file_path, "rb") as f:
                for line in f:
                    if not line.strip():
                        continue
                    batch.append(line)
                    batch_lines += 1
                    if batch_lines >= config.RDF_LOAD_BATCH_LINES:
                        send(batch)
                        total += batch_lines
                        if progress:
                            progress(total)
                        batch, batch_lines = [], 0
                if batch:
                    send(batch)
                    total += batch_lines
                    if progress:
                        progress(total)
        except Exception as e:
            if total:
                return False, (f"{e} — after {total:,} statements. The graph now holds "
                               f"a partial load; replace it rather than appending again.")
            return False, str(e)
        return True, f"{verb} <{graph_uri}> ({total:,} statements)"

    def drop_graph(self, graph_uri: str, **kw) -> tuple[bool, str]:
        # Dropping is a write across as many triples as loading them was, so it
        # gets the load timeout rather than a short one. At 30s a multi-million
        # triple graph timed out client-side while the store went on to complete
        # it, leaving the caller believing a delete had failed that had not.
        try:
            r = requests.delete(
                self._url(self.gsp_path),
                params={self.gsp_param: graph_uri},
                auth=self.auth,
                timeout=self.load_timeout,
            )
            # Deleting a graph that was never created is not an error for our callers.
            if r.status_code == 404:
                return True, f"<{graph_uri}> did not exist"
            return r.status_code < 400, r.text
        except Exception as e:
            return False, str(e)

    def count_triples(self, graph_uri: str, **kw) -> int:
        ok, result = self.sparql_query(
            f"SELECT (COUNT(*) AS ?c) WHERE {{ GRAPH <{graph_uri}> {{ ?s ?p ?o }} }}"
        )
        if not ok:
            return -1
        try:
            return int(result["results"]["bindings"][0]["c"]["value"])
        except (KeyError, IndexError, ValueError, TypeError):
            return -1

    def is_available(self) -> bool:
        """Cheap reachability probe, used to show which stores a local install has."""
        ok, _ = self.sparql_query("ASK { ?s ?p ?o }",
                                  timeout=config.BACKEND_PROBE_TIMEOUT)
        return ok
