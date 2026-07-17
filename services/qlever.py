"""QLever named-graph management for the Koetai platform."""
import subprocess
import tempfile
import shutil
from pathlib import Path
import requests
import config
from services.sparql_http import scoped_body


def sparql_update(query: str, endpoint_url: str = None) -> tuple[bool, str]:
    """Run a SPARQL UPDATE against the platform QLever instance."""
    url = (endpoint_url or config.QLEVER_PLATFORM_URL) + "/update"
    try:
        r = requests.post(url, data={"update": query}, timeout=120)
        return r.status_code < 400, r.text
    except Exception as e:
        return False, str(e)


def sparql_query(query: str, endpoint_url: str = None, graphs=None) -> tuple[bool, dict]:
    """Run a SPARQL SELECT/CONSTRUCT against the platform QLever instance.

    `graphs` (a sparql_http.Scope) confines the query server-side. This matters
    more on QLever than elsewhere: its default graph is the union of every named
    graph, so an unscoped `?s ?p ?o` would read all tenants' data.
    """
    url = (endpoint_url or config.QLEVER_PLATFORM_URL)
    try:
        r = requests.post(url, data=scoped_body(query, graphs),
                          headers={"Accept": "application/sparql-results+json"}, timeout=120)
        if r.status_code < 400:
            return True, r.json()
        return False, {"error": r.text}
    except Exception as e:
        return False, {"error": str(e)}


def load_rdf_file(graph_uri: str, file_path: Path, endpoint_url: str = None) -> tuple[bool, str]:
    """
    Load an RDF file into a named graph.
    Uses SPARQL LOAD or INSERT DATA depending on file size.
    For large files, triggers a QLever index rebuild via the CLI.
    """
    size_mb = file_path.stat().st_size / (1024 * 1024)

    if size_mb < 50:
        return _load_via_insert(graph_uri, file_path, endpoint_url)
    else:
        return _load_via_index_rebuild(graph_uri, file_path)


def _load_via_insert(graph_uri: str, file_path: Path, endpoint_url: str = None) -> tuple[bool, str]:
    """Load small RDF files via SPARQL INSERT DATA into named graph."""
    import re

    # Read and strip comments for INSERT DATA (QLever limitation)
    content = file_path.read_text(encoding="utf-8", errors="replace")

    # For Turtle: wrap in INSERT DATA { GRAPH <uri> { ... } }
    # Strip @prefix directives and convert to SPARQL-compatible prefixes
    prefixes = []
    triples = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("@prefix") or stripped.startswith("@base"):
            sparql_prefix = stripped.rstrip(".").replace("@prefix", "PREFIX").replace("@base", "BASE")
            prefixes.append(sparql_prefix)
        else:
            triples.append(line)

    triple_block = "\n".join(triples)
    prefix_block = "\n".join(prefixes)

    update = f"""{prefix_block}

INSERT DATA {{
  GRAPH <{graph_uri}> {{
{triple_block}
  }}
}}"""
    return sparql_update(update, endpoint_url)


def _load_via_index_rebuild(graph_uri: str, file_path: Path) -> tuple[bool, str]:
    """For large files: copy to dataset dir and rebuild QLever index."""
    # Find the platform dataset directory
    platform_dir = config.DEPLOY_DIR / "platform"
    platform_dir.mkdir(exist_ok=True)

    dest = platform_dir / file_path.name
    shutil.copy2(file_path, dest)

    ok, stdout, stderr = _run(f"qlever index", cwd=str(platform_dir))
    if not ok:
        return False, stderr
    ok, stdout, stderr = _run("qlever start", cwd=str(platform_dir))
    return ok, stderr if not ok else stdout


def drop_graph(graph_uri: str, endpoint_url: str = None) -> tuple[bool, str]:
    """Drop a named graph from the triplestore."""
    return sparql_update(f"DROP GRAPH <{graph_uri}>", endpoint_url)


def count_triples(graph_uri: str, endpoint_url: str = None) -> int:
    """Return triple count for a named graph, or -1 on error."""
    ok, result = sparql_query(
        f"SELECT (COUNT(*) AS ?c) WHERE {{ GRAPH <{graph_uri}> {{ ?s ?p ?o }} }}",
        endpoint_url
    )
    if ok:
        try:
            return int(result["results"]["bindings"][0]["c"]["value"])
        except (KeyError, IndexError, ValueError):
            return -1
    return -1


def _run(cmd: str, cwd: str = None, timeout: int = 300) -> tuple[bool, str, str]:
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd,
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Command timed out"
    except Exception as e:
        return False, "", str(e)
