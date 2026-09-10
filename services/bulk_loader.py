"""Asking the loader agent to import a file with the store's own bulk loader.

Loading through the SPARQL Graph Store Protocol is about 30,000 statements a
second here; the store's own loader does 600,000. On an 11 GB dump that is the
difference between most of an hour and a couple of minutes. The loader needs the
store stopped, though, so something privileged has to do it.

That something is deliberately not this app. The app writes a request file into
a directory the agent watches, and reads a result file back. It holds no Docker
access of its own, and the agent will only load a file that is already inside
the uploads directory, into the container and volume it was configured with —
so a request cannot name a different container, image or volume. See
deploy/loader-agent/agent.sh, which is short enough to read in one go.
"""
import json
import os
import time
import uuid
from pathlib import Path

import config

REQUEST_DIR = Path(config.BULK_LOADER_DIR) / "requests"
RESULT_DIR = Path(config.BULK_LOADER_DIR) / "results"
HEARTBEAT = RESULT_DIR / ".agent-alive"

# Formats the store's loader will take. Turtle is included because the loader
# parses it itself — unlike our own batching, which cannot split it.
FORMATS = {".nt": "nt", ".nq": "nq", ".ttl": "ttl"}

# The agent is wired to one store's container and volume, so it serves datasets
# on that backend and no other. Offering it elsewhere would load the triples
# into the wrong store under the right graph URI, which is worse than refusing.
STORE_PLATFORM = os.environ.get("BULK_LOADER_PLATFORM", "oxigraph")


def is_available(max_age_s: int = 30) -> bool:
    """Whether an agent is running and recently alive.

    Presence of the directory is not enough: the volume outlives the container,
    so a stale directory would advertise a loader that is not there.
    """
    try:
        return (time.time() - float(HEARTBEAT.read_text().strip())) < max_age_s
    except (OSError, ValueError):
        return False


def rdf_format(path: Path) -> str | None:
    """The loader's format name for a file, seeing through one compression layer.

    Only .gz and .bz2, because the agent streams those straight into the loader.
    A .zip or .tgz would have to be unpacked to disk first, which for the files
    this path exists for means writing out another several gigabytes.
    """
    name = Path(path).name.lower()
    for suffix in (".gz", ".bz2"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    for suffix, fmt in FORMATS.items():
        if name.endswith(suffix):
            return fmt
    return None


def submit(file_path: Path, graph_uri: str, optimise: bool = False) -> tuple[bool, str]:
    """Queue a bulk load. Returns (ok, request_id_or_error)."""
    if not is_available():
        return False, "The bulk loader is not running."
    fmt = rdf_format(file_path)
    if not fmt:
        return False, ("The bulk loader reads N-Triples, N-Quads and Turtle, "
                       "optionally gzipped or bzip2ed; this file is none of those.")
    request_id = uuid.uuid4().hex
    REQUEST_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"id": request_id, "file": str(file_path), "graph": graph_uri,
               "format": fmt, "optimise": bool(optimise)}
    # Write beside the target and rename, so the agent never reads a half-written
    # request — it polls the directory and would otherwise catch one mid-write.
    tmp = REQUEST_DIR / f".{request_id}.tmp"
    tmp.write_text(json.dumps(payload))
    tmp.rename(REQUEST_DIR / f"{request_id}.json")
    return True, request_id


def status(request_id: str) -> dict | None:
    """The agent's result for a request, or None while it is still queued."""
    try:
        return json.loads((RESULT_DIR / f"{request_id}.json").read_text())
    except (OSError, ValueError):
        return None
