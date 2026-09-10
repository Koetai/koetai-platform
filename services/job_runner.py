"""
Background upload/reasoning job runner.

Jobs are stored in the upload_jobs SQLite table.
A single daemon thread picks up queued jobs and processes them one at a time.
"""
import shutil
import threading
import sqlite3
import time
import uuid
from pathlib import Path

import config
from services import owl_service
from services import web_scraper_service

_lock   = threading.Lock()
_thread = None


# ── Job creation ──────────────────────────────────────────────────────────────

def submit(dataset_id: int, user_id: int, file_path: Path, graph_uri: str,
           apply_owl: bool, owl_regime: str, replace_data: bool,
           source_url: str = None, source_label: str = None,
           web_source_file_id: int = None) -> str:
    """Insert a new upload job and return its ID.

    With `source_url`, the job downloads the file itself and `file_path` is
    where it will land rather than a file that already exists. That keeps a
    multi-gigabyte fetch off the request thread, where it would outlive
    gunicorn's timeout long before it finished.
    """
    job_id = str(uuid.uuid4())
    conn = _raw_conn()
    with conn:
        conn.execute(
            "INSERT INTO upload_jobs "
            "(id, dataset_id, user_id, file_path, graph_uri, apply_owl, owl_regime, "
            " replace_data, source_url, source_label, web_source_file_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, dataset_id, user_id, str(file_path),
             graph_uri, int(apply_owl), owl_regime, int(replace_data),
             source_url, source_label, web_source_file_id)
        )
    conn.close()
    _ensure_runner()
    return job_id


def get_status(job_id: str) -> dict | None:
    """Return job status dict or None if not found."""
    conn = _raw_conn()
    row = conn.execute(
        "SELECT id, status, phase, message, source_label, created_at, finished_at "
        "FROM upload_jobs WHERE id=?",
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


def reclaim_orphaned() -> int:
    """Fail any job left 'running' by a process that is no longer here.

    One process runs one job at a time, so a job still marked 'running' when the
    app starts cannot be: its runner went with the process — a restart, a crash,
    a container rebuild mid-load. _next_queued only ever claims 'queued', so
    nothing would pick it up again and the page polling it would report it as
    loading for ever.

    Failed rather than requeued deliberately: an interrupted load may have
    written all, some or none of its triples, and repeating it blindly would
    duplicate them. The message says to check before retrying, because checking
    is the only way to know.
    """
    conn = _raw_conn()
    # Its working files are orphaned too: the cleanup that normally follows a
    # job lives in the process that died. The source is re-uploadable and the
    # extracted members are reproducible, so both go, as they would after any
    # other failure.
    stranded = conn.execute(
        "SELECT id, file_path FROM upload_jobs WHERE status='running'"
    ).fetchall()
    for row in stranded:
        try:
            path = Path(row["file_path"])
            path.unlink(missing_ok=True)
            shutil.rmtree(path.parent / f"x_{row['id']}", ignore_errors=True)
        except OSError:
            pass

    with conn:
        cur = conn.execute(
            "UPDATE upload_jobs SET status='error', phase='interrupted', "
            "message='Interrupted — the server restarted while this job was running. "
            "Some or all of the data may have loaded: check the dataset\u2019s triple "
            "count before uploading again, or the same triples may be added twice.', "
            "finished_at=datetime('now') WHERE status='running'"
        )
        n = cur.rowcount
    conn.close()
    return n


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

    source_url = job.get("source_url")

    try:
        # Step 0a: fetch, when the job owns its own download rather than being
        # handed an upload. Keeps a multi-gigabyte transfer off the request
        # thread, which would not survive it.
        if source_url:
            _set("running", "downloading", f"Downloading {job.get('source_label') or source_url}…")
            file_path.parent.mkdir(parents=True, exist_ok=True)
            ok, msg = web_scraper_service.download_file(source_url, file_path)
            if not ok:
                _release_source(file_path, False)
                _set("error", "downloading", f"Download failed: {msg}")
                return

        # Step 0b: unpack, however the archive arrived. Compressed RDF is the
        # normal shape at this size — for an upload it is also what keeps the
        # file under the request cap — and one archive may hold several RDF
        # files, so it fans out into several loads inside this one job.
        if file_path.suffix.lower() in web_scraper_service.ARCHIVE_EXTENSIONS:
            _set("running", "extracting", f"Extracting {file_path.name}…")
            extract_dir = file_path.parent / f"x_{job_id}"
            try:
                members = web_scraper_service.extract_rdf_files(file_path, extract_dir)
            except Exception as e:
                _release_source(file_path, False)
                shutil.rmtree(extract_dir, ignore_errors=True)
                _set("error", "extracting", f"Extraction failed: {e}")
                return

            if not members:
                _release_source(file_path, False)
                shutil.rmtree(extract_dir, ignore_errors=True)
                _set("error", "extracting", "No RDF files found in archive")
                return

            ok, msg = _load_members(job, members, graph_uri, apply_owl, regime,
                                    replace, _set)
            shutil.rmtree(extract_dir, ignore_errors=True)   # members are derived
            _release_source(file_path, ok)
            if not ok:
                _set("error", "loading", msg)
                return
            _mark_web_source_imported(job)
            _set("done", "done", msg)
            return

    except Exception as e:
        _set("error", "error", str(e))
        return

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
                _release_source(file_path, False)
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
            _release_source(file_path, False)
            _set("error", "loading", "Dataset not found")
            return

        ts = triplestore.get(dict(ds_row))

        # replace_graph is a single atomic PUT on backends with a Graph Store
        # Protocol; on QLever it degrades to drop-then-load, as before.
        # Report as it goes. A line-based load is sent in batches, so the count
        # is real progress rather than a guess, and a large import stops looking
        # like a hang.
        def report(n):
            _set("running", "loading", f"Loaded {n:,} statements so far…")

        if replace:
            ok, msg = ts.replace_graph(graph_uri, load_path, progress=report)
        else:
            ok, msg = ts.load_rdf_file(graph_uri, load_path, progress=report)

        # Clean up temp file if different from original
        if load_path != file_path:
            load_path.unlink(missing_ok=True)

        if not ok:
            _release_source(file_path, False)
            _set("error", "loading", f"Loading failed: {msg}")
            return

        _release_source(file_path, True)
        _mark_web_source_imported(job)
        _set("done", "done", msg or "Upload complete")

    except Exception as e:
        _release_source(file_path, False)
        _set("error", "error", str(e))




def _release_source(path: Path, loaded: bool):
    """Drop the source file unless it is being kept.

    A file whose load failed is never kept, whatever the setting says: nothing
    refers to it, no retry reads it back, and leaving it behind was how a
    rejected upload used to sit in the uploads directory for ever.
    """
    if loaded and config.KEEP_UPLOADED_SOURCES:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _load_members(job, members, graph_uri, apply_owl, regime, replace, _set):
    """Load each RDF file extracted from one archive into the dataset's graph.

    Only the first load may replace the graph; the rest must append, or each
    member would wipe the one before it.
    """
    from services import triplestore

    conn = _raw_conn()
    ds_row = conn.execute("SELECT * FROM datasets WHERE id=?", (job["dataset_id"],)).fetchone()
    conn.close()
    if ds_row is None:
        return False, "Dataset not found"
    ts = triplestore.get(dict(ds_row))

    loaded, errors = 0, []
    for i, member in enumerate(members, 1):
        _set("running", "loading", f"Loading {member.name} ({i} of {len(members)})…")
        load_path = member
        if apply_owl and member.suffix.lower() in (".owl", ".rdf"):
            ok_r, reasoned, owl_msg = owl_service.materialize(member, regime=regime)
            if not ok_r:
                errors.append(f"{member.name} (OWL): {owl_msg}")
                continue
            load_path = reasoned

        if replace and i == 1:
            ok, msg = ts.replace_graph(graph_uri, load_path)
        else:
            ok, msg = ts.load_rdf_file(graph_uri, load_path)

        if load_path != member:
            load_path.unlink(missing_ok=True)
        if ok:
            loaded += 1
        else:
            errors.append(f"{member.name}: {msg}")

    if not loaded:
        return False, "; ".join(errors) or "Nothing loaded"
    summary = f"Loaded {loaded} of {len(members)} file(s) into <{graph_uri}>"
    if errors:
        summary += f" — {len(errors)} failed: " + "; ".join(errors)
    return True, summary


def _mark_web_source_imported(job):
    """Record a successful import against the web source row, if this job has one.

    The route used to do this inline; with the load asynchronous, only the job
    knows whether it actually succeeded.
    """
    fid = job.get("web_source_file_id")
    if not fid:
        return
    try:
        conn = _raw_conn()
        row = conn.execute("SELECT * FROM web_source_files WHERE id=?", (fid,)).fetchone()
        if row is None:
            conn.close()
            return
        meta = web_scraper_service._head_file(row["url"])
        with conn:
            conn.execute(
                "UPDATE web_source_files SET imported_at=datetime('now'), etag=?, "
                "last_modified=? WHERE id=?",
                (meta.get("etag") or row["etag"],
                 meta.get("last_modified") or row["last_modified"], fid)
            )
            conn.execute(
                "UPDATE web_sources SET last_imported_at=datetime('now') WHERE id=?",
                (row["source_id"],)
            )
        conn.close()
    except Exception:
        pass          # bookkeeping must never fail a load that already succeeded


def _raw_conn():
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
