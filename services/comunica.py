"""Comunica federation backend — a *virtual* dataset over external sources.

Unlike the storage backends (Fuseki, QLever, ...), a Comunica dataset holds no
data of its own. It is defined by a list of sources — SPARQL endpoints, RDF
files, TPF interfaces — and every query is federated across them at request time
by the Comunica engine (https://comunica.dev). Nothing is uploaded, so the
load/replace/drop half of the backend interface is unsupported here.

Comunica is a Node CLI; we shell out to it per query, the same way the shape and
reasoning services shell out. It is optional: an install without it simply has no
'comunica' backend available (see is_available()).
"""
import json
import shutil
import subprocess

import config


class ComunicaStore:
    name = "comunica"

    def __init__(self, sources):
        # A list of source URLs/paths this virtual dataset federates over.
        self.sources = [s for s in (sources or []) if s]

    # ── read ──────────────────────────────────────────────────────────────────
    def sparql_query(self, query, graphs=None, **kw):
        """Federate the query across this dataset's sources.

        `graphs` is ignored: a federation dataset has no local named graphs to
        scope to — its sources *are* its data.
        """
        if not self.sources:
            return False, {"error": "This federation dataset has no sources configured."}
        cmd = [config.COMUNICA_BIN, *self.sources, query,
               "-t", "application/sparql-results+json"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=config.COMUNICA_TIMEOUT)
        except FileNotFoundError:
            return False, {"error": "Comunica is not installed on this server."}
        except subprocess.TimeoutExpired:
            return False, {"error": "Federated query timed out."}
        if r.returncode != 0:
            # Comunica logs warnings to stderr even on success, so only surface it
            # on a non-zero exit.
            return False, {"error": (r.stderr or r.stdout or "Comunica query failed").strip()[:800]}
        try:
            return True, json.loads(r.stdout)
        except json.JSONDecodeError:
            return False, {"error": "Comunica returned no parseable result."}

    def count_triples(self, graph_uri=None, **kw):
        # Counting every triple across remote sources is unbounded and slow, so
        # report "unknown" (the UI already handles -1) rather than hammer them.
        return -1

    def is_available(self):
        # Just check the binary resolves. `comunica-sparql --version` exits
        # non-zero (it expects a query), so an exit-code probe would be wrong;
        # and running a real query to probe would be far too heavy.
        return shutil.which(config.COMUNICA_BIN) is not None

    # ── write: unsupported (virtual dataset) ───────────────────────────────────
    def _readonly(self):
        return False, ("Federation datasets are virtual — they query external "
                       "sources and cannot be written to. Edit the source list "
                       "instead of uploading.")

    def sparql_update(self, query, **kw):
        return self._readonly()

    def load_rdf_file(self, graph_uri, file_path, **kw):
        return self._readonly()

    def replace_graph(self, graph_uri, file_path, **kw):
        return self._readonly()

    def drop_graph(self, graph_uri, **kw):
        # Deleting a federation dataset drops only its DB row; there is no stored
        # graph. A harmless success keeps the delete path uniform across backends.
        return True, "Federation dataset has no stored graph to drop."


def parse_sources(raw):
    """Split a stored source blob (one URL per line) into a clean list."""
    if not raw:
        return []
    return [line.strip() for line in raw.splitlines() if line.strip()]
