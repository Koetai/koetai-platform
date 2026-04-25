"""Apache Jena Fuseki named-graph management for the Koetai platform."""
from pathlib import Path
import requests
import config


def _query_url(dataset: str = "koetai") -> str:
    return f"{config.FUSEKI_BASE_URL}/{dataset}/sparql"


def _update_url(dataset: str = "koetai") -> str:
    return f"{config.FUSEKI_BASE_URL}/{dataset}/update"


def _gsp_url(dataset: str = "koetai") -> str:
    """Graph Store Protocol URL for direct graph upload."""
    return f"{config.FUSEKI_BASE_URL}/{dataset}/data"


def sparql_update(query: str, fuseki_dataset: str = "koetai") -> tuple[bool, str]:
    url = _update_url(fuseki_dataset)
    try:
        r = requests.post(
            url,
            data=query.encode(),
            headers={"Content-Type": "application/sparql-update"},
            timeout=120,
        )
        return r.status_code < 400, r.text
    except Exception as e:
        return False, str(e)


def sparql_query(query: str, fuseki_dataset: str = "koetai") -> tuple[bool, dict]:
    url = _query_url(fuseki_dataset)
    try:
        r = requests.post(
            url,
            data={"query": query},
            headers={"Accept": "application/sparql-results+json"},
            timeout=120,
        )
        if r.status_code < 400:
            return True, r.json()
        return False, {"error": r.text}
    except Exception as e:
        return False, {"error": str(e)}


def load_rdf_file(graph_uri: str, file_path: Path, fuseki_dataset: str = "koetai") -> tuple[bool, str]:
    """Upload RDF file into a named graph via Graph Store Protocol (POST appends to graph)."""
    ext = file_path.suffix.lower()
    content_type = _rdf_content_type(ext)

    url = _gsp_url(fuseki_dataset)
    try:
        with open(file_path, "rb") as f:
            r = requests.post(
                url,
                params={"graph": graph_uri},
                data=f,
                headers={"Content-Type": content_type},
                timeout=300,
            )
        if r.status_code < 400:
            return True, f"Loaded into <{graph_uri}>"
        return False, r.text
    except Exception as e:
        return False, str(e)


def replace_graph(graph_uri: str, file_path: Path, fuseki_dataset: str = "koetai") -> tuple[bool, str]:
    """Replace a named graph entirely via Graph Store Protocol (PUT)."""
    ext = file_path.suffix.lower()
    content_type = _rdf_content_type(ext)

    url = _gsp_url(fuseki_dataset)
    try:
        with open(file_path, "rb") as f:
            r = requests.put(
                url,
                params={"graph": graph_uri},
                data=f,
                headers={"Content-Type": content_type},
                timeout=300,
            )
        if r.status_code < 400:
            return True, f"Replaced <{graph_uri}>"
        return False, r.text
    except Exception as e:
        return False, str(e)


def drop_graph(graph_uri: str, fuseki_dataset: str = "koetai") -> tuple[bool, str]:
    url = _gsp_url(fuseki_dataset)
    try:
        r = requests.delete(url, params={"graph": graph_uri}, timeout=30)
        return r.status_code < 400, r.text
    except Exception as e:
        return False, str(e)


def count_triples(graph_uri: str, fuseki_dataset: str = "koetai") -> int:
    ok, result = sparql_query(
        f"SELECT (COUNT(*) AS ?c) WHERE {{ GRAPH <{graph_uri}> {{ ?s ?p ?o }} }}",
        fuseki_dataset,
    )
    if ok:
        try:
            return int(result["results"]["bindings"][0]["c"]["value"])
        except (KeyError, IndexError, ValueError):
            return -1
    return -1


def _rdf_content_type(ext: str) -> str:
    return {
        ".ttl":    "text/turtle",
        ".n3":     "text/n3",
        ".nt":     "application/n-triples",
        ".nq":     "application/n-quads",
        ".trig":   "application/trig",
        ".rdf":    "application/rdf+xml",
        ".owl":    "application/rdf+xml",
        ".jsonld": "application/ld+json",
    }.get(ext, "text/turtle")
