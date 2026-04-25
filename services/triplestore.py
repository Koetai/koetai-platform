"""Triplestore factory — returns the right backend based on dataset platform field."""
from pathlib import Path
from services import qlever, fuseki


class _QLeverBackend:
    def sparql_query(self, query, **kw):
        return qlever.sparql_query(query)

    def sparql_update(self, query, **kw):
        return qlever.sparql_update(query)

    def load_rdf_file(self, graph_uri, file_path, **kw):
        return qlever.load_rdf_file(graph_uri, file_path)

    def drop_graph(self, graph_uri, **kw):
        return qlever.drop_graph(graph_uri)

    def count_triples(self, graph_uri, **kw):
        return qlever.count_triples(graph_uri)


class _FusekiBackend:
    def __init__(self, dataset: str):
        self._ds = dataset

    def sparql_query(self, query, **kw):
        return fuseki.sparql_query(query, self._ds)

    def sparql_update(self, query, **kw):
        return fuseki.sparql_update(query, self._ds)

    def load_rdf_file(self, graph_uri, file_path, **kw):
        return fuseki.load_rdf_file(graph_uri, file_path, self._ds)

    def drop_graph(self, graph_uri, **kw):
        return fuseki.drop_graph(graph_uri, self._ds)

    def count_triples(self, graph_uri, **kw):
        return fuseki.count_triples(graph_uri, self._ds)


def get(ds_row) -> "_QLeverBackend | _FusekiBackend":
    """
    Return the appropriate triplestore backend for a dataset DB row.
    ds_row must have 'platform' and (for fuseki) 'slug' fields.
    """
    platform = ds_row["platform"] if "platform" in ds_row.keys() else "qlever"
    if platform == "fuseki":
        return _FusekiBackend("koetai")
    return _QLeverBackend()
