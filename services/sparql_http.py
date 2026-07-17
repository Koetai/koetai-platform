"""Generic SPARQL 1.1 Protocol + Graph Store Protocol client.

Every standards-compliant triplestore — Fuseki, Virtuoso, Oxigraph, Blazegraph,
RDF4J, GraphDB — speaks SPARQL 1.1 Query/Update plus the Graph Store Protocol.
They differ only in URL layout, the name of the Graph Store query parameter, and
authentication. Those are constructor arguments here rather than subclasses, so
adding a new open-source store is a line in the registry, not a new module.

QLever is deliberately not built on this: it has no Graph Store Protocol and
loads bulk data by building an index offline. See services/qlever.py.
"""
from pathlib import Path
from typing import NamedTuple, Optional

import requests


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
        load_timeout: int = 300,
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
        self.load_timeout = load_timeout

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def sparql_query(self, query: str, graphs: "Scope" = None, **kw) -> tuple[bool, dict]:
        """Run a query, optionally confined to `graphs` (see build_scope).

        Scoping is enforced by the store via the SPARQL 1.1 Protocol, not by
        inspecting the query text — so GRAPH ?g cannot enumerate beyond the
        permitted graphs and an explicit GRAPH <other> simply matches nothing.
        """
        try:
            r = requests.post(
                self._url(self.query_path),
                data=scoped_body(query, graphs),
                headers={"Accept": "application/sparql-results+json"},
                auth=self.auth,
                timeout=self.query_timeout,
            )
            if r.status_code < 400:
                return True, r.json()
            return False, {"error": r.text}
        except Exception as e:
            return False, {"error": str(e)}

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

    def load_rdf_file(self, graph_uri: str, file_path: Path, **kw) -> tuple[bool, str]:
        """Append a file into a named graph (Graph Store Protocol POST)."""
        return self._gsp_write(requests.post, graph_uri, file_path, "Loaded into")

    def replace_graph(self, graph_uri: str, file_path: Path, **kw) -> tuple[bool, str]:
        """Replace a named graph atomically (Graph Store Protocol PUT)."""
        return self._gsp_write(requests.put, graph_uri, file_path, "Replaced")

    def _gsp_write(self, method, graph_uri: str, file_path: Path, verb: str) -> tuple[bool, str]:
        file_path = Path(file_path)
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

    def drop_graph(self, graph_uri: str, **kw) -> tuple[bool, str]:
        try:
            r = requests.delete(
                self._url(self.gsp_path),
                params={self.gsp_param: graph_uri},
                auth=self.auth,
                timeout=30,
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
        ok, _ = self.sparql_query("ASK { ?s ?p ?o }")
        return ok
