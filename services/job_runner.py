"""
Background upload/reasoning job runner.

Jobs are stored in the upload_jobs SQLite table.
A single daemon thread picks up queued jobs and processes them one at a time.
"""
import threading
import sqlite3
import time
import uuid
from pathlib import Path

import config
from services import owl_service

_lock   = threading.Lock()
_thread = None


# ── Job creation ──────────────────────────────────────────────────────────────

def submit(dataset_id: int, user_id: int, file_path: Path, graph_uri: str,
           apply_owl: bool, owl_regime: str, replace_data: bool) -> str:
    """Insert a new upload job and return its ID."""
    job_id = str(uuid.uuid4())
    conn = _raw_conn()
    with conn:
        conn.execute(
            "INSERT INTO upload_jobs "
            "(id, dataset_id, user_id, file_path, graph_uri, apply_owl, owl_regime, replace_data) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (job_id, dataset_id, user_id, str(file_path),
             graph_uri, int(apply_owl), owl_regime, int(replace_data))
        )
    conn.close()
    _ensure_runner()
    return job_id


def get_status(job_id: str) -> dict | None:
    """Return job status dict or None if not found."""
    conn = _raw_conn()
    row = conn.execute(
        "SELECT id, status, phase, message, created_at, finished_at FROM upload_jobs WHERE id=?",
        (job_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


# ── Runner ────────────────────────────────────────────────────────────────────

def _ensure_runner():
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_run_loop, daemon=True, name="job-runner")
            _thread.start()


def _run_loop():
    while True:
        job = _next_queued()
        if job:
            _process(job)
        else:
            time.sleep(3)


def _next_queued() -> dict | None:
    conn = _raw_conn()
    row = conn.execute(
        "SELECT * FROM upload_jobs WHERE status='queued' ORDER BY created_at LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _process(job: dict):
    job_id    = job["id"]
    file_path = Path(job["file_path"])
    graph_uri = job["graph_uri"]
    apply_owl = bool(job["apply_owl"])
    regime    = job["owl_regime"]
    replace   = bool(job["replace_data"])

    def update(status, phase, message):
        conn = _raw_conn()
        finished = "datetime('now')" if status in ("done", "error") else "NULL"
        with conn:
            conn.execute(
                f"UPDATE upload_jobs SET status=?, phase=?, message=?, "
                f"finished_at={'datetime('+chr(39)+'now'+chr(39)+')' if status in ('done','error') else 'finished_at'} "
                f"WHERE id=?",
                (status, phase, message, job_id)
            )
        conn.close()

    def _set(status, phase, message):
        conn = _raw_conn()
        with conn:
            if status in ("done", "error"):
                conn.execute(
                    "UPDATE upload_jobs SET status=?, phase=?, message=?, finished_at=datetime('now') WHERE id=?",
                    (status, phase, message, job_id)
                )
            else:
                conn.execute(
                    "UPDATE upload_jobs SET status=?, phase=?, message=? WHERE id=?",
                    (status, phase, message, job_id)
                )
        conn.close()

    _set("running", "parsing", "Parsing RDF file…")

    try:
        load_path = file_path

        # Step 1: normalize OWL/XML → N-Triples via Jena riot (robust parser)
        if file_path.suffix.lower() in (".owl", ".rdf"):
            ok, nt_path, msg = owl_service.normalize_to_nt(file_path)
            if ok:
                load_path = nt_path
            # If riot fails, fall through with original file

        # Step 2: OWL reasoning
        if apply_owl:
            if regime == "RDFS":
                _set("running", "reasoning", "Applying RDFS closure (Jena)…")
                ok, reasoned, msg = owl_service.materialize_rdfs(load_path)
            else:
                mb = owl_service.size_mb(file_path)
                _set("running", "reasoning",
                     f"Applying {regime} reasoning (owlrl) — {mb:.0f} MB file, this may take a while…")
                ok, reasoned, msg = owl_service.materialize_owlrl(load_path, regime, timeout=7200)

            if load_path != file_path:
                load_path.unlink(missing_ok=True)  # remove intermediate NT

            if not ok:
                _set("error", "reasoning", f"Reasoning failed: {msg}")
                return
            load_path = reasoned

        # Step 3: load into triplestore
        _set("running", "loading", "Loading triples into triplestore…")

        # Import here to avoid circular imports at module load
        from services import triplestore
        conn = _raw_conn()
        ds_row = conn.execute(
            "SELECT * FROM datasets WHERE id=?", (job["dataset_id"],)
        ).fetchone()
        conn.close()

        if ds_row is None:
            _set("error", "loading", "Dataset not found")
            return

        ts = triplestore.get(dict(ds_row))

        # replace_graph is a single atomic PUT on backends with a Graph Store
        # Protocol; on QLever it degrades to drop-then-load, as before.
        if replace:
            ok, msg = ts.replace_graph(graph_uri, load_path)
        else:
            ok, msg = ts.load_rdf_file(graph_uri, load_path)

        # Clean up temp file if different from original
        if load_path != file_path:
            load_path.unlink(missing_ok=True)

        if not ok:
            _set("error", "loading", f"Loading failed: {msg}")
            return

        _set("done", "done", msg or "Upload complete")

    except Exception as e:
        _set("error", "error", str(e))


def _raw_conn():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
