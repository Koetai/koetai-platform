"""Web download page sources — add, scan, import, update-check."""
import re
import uuid
from pathlib import Path
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, jsonify)
from flask_login import login_required, current_user
import config
from services.db import get_db
from services import job_runner, triplestore, owl_service
from services import web_scraper_service
from services.web_scraper_service import RDF_EXTENSIONS

# Archive handling moved to web_scraper_service so the job runner can use it
# too, and so members are streamed to disk rather than read into memory.
_ARCHIVE_EXTS = web_scraper_service.ARCHIVE_EXTENSIONS
_extract_rdf_files = web_scraper_service.extract_rdf_files


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(filename: str) -> str:
    """A filename from a scraped page, reduced to something safe to write.

    Path().name drops any directory part; the rest guards against a remote page
    naming a file in a way that escapes the upload directory.
    """
    name = _SAFE_NAME.sub("_", Path(filename).name).lstrip(".")
    return name[:120] or "download"


bp = Blueprint("web_sources", __name__, url_prefix="/u")


def _get_dataset_or_403(owner_orcid, slug):
    db = get_db()
    ds = db.execute(
        "SELECT d.*, u.orcid_id FROM datasets d JOIN users u ON u.id = d.user_id "
        "WHERE u.orcid_id = ? AND d.slug = ?",
        (owner_orcid, slug)
    ).fetchone()
    if not ds or ds["user_id"] != current_user.id:
        return None
    return ds


@bp.route("/<owner_orcid>/<slug>/web")
@login_required
def view(owner_orcid, slug):
    ds = _get_dataset_or_403(owner_orcid, slug)
    if not ds:
        flash("Not found or not authorized.", "error")
        return redirect(url_for("dashboard.index"))
    db = get_db()
    sources = db.execute(
        "SELECT * FROM web_sources WHERE dataset_id = ? ORDER BY created_at DESC",
        (ds["id"],)
    ).fetchall()
    # attach files per source
    sources_with_files = []
    for src in sources:
        files = db.execute(
            "SELECT * FROM web_source_files WHERE source_id = ? ORDER BY filename",
            (src["id"],)
        ).fetchall()
        sources_with_files.append({"src": src, "files": files})
    return render_template("web_sources.html", ds=ds, sources=sources_with_files)


@bp.route("/<owner_orcid>/<slug>/web/add", methods=["POST"])
@login_required
def add_source(owner_orcid, slug):
    ds = _get_dataset_or_403(owner_orcid, slug)
    if not ds:
        return jsonify({"error": "Not authorized"}), 403
    page_url = request.form.get("page_url", "").strip()
    label    = request.form.get("label", "").strip()
    if not page_url or not page_url.startswith("http"):
        flash("Enter a valid URL.", "error")
        return redirect(url_for("web_sources.view", owner_orcid=owner_orcid, slug=slug))
    db = get_db()
    try:
        db.execute(
            "INSERT INTO web_sources (dataset_id, page_url, label) VALUES (?,?,?)",
            (ds["id"], page_url, label or page_url)
        )
        db.commit()
    except Exception as e:
        flash(f"Could not add source: {e}", "error")
    return redirect(url_for("web_sources.view", owner_orcid=owner_orcid, slug=slug))


@bp.route("/<owner_orcid>/<slug>/web/<int:source_id>/scan")
@login_required
def scan_files(owner_orcid, slug, source_id):
    ds = _get_dataset_or_403(owner_orcid, slug)
    if not ds:
        return jsonify({"error": "Not authorized"}), 403
    db = get_db()
    src = db.execute("SELECT * FROM web_sources WHERE id = ? AND dataset_id = ?",
                     (source_id, ds["id"])).fetchone()
    if not src:
        return jsonify({"error": "Source not found"}), 404

    ok, result = web_scraper_service.scrape_page(src["page_url"])
    if not ok:
        return jsonify({"error": result}), 500

    # Upsert discovered files
    for f in result:
        db.execute("""
            INSERT INTO web_source_files (source_id, filename, url, etag, last_modified, content_length)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(source_id, url) DO UPDATE SET
              filename=excluded.filename,
              etag=excluded.etag,
              last_modified=excluded.last_modified,
              content_length=excluded.content_length
        """, (source_id, f["filename"], f["url"],
              f.get("etag"), f.get("last_modified"), f.get("content_length")))
    db.execute("UPDATE web_sources SET last_checked_at = datetime('now') WHERE id = ?",
               (source_id,))
    db.commit()

    files = db.execute(
        "SELECT * FROM web_source_files WHERE source_id = ? ORDER BY filename",
        (source_id,)
    ).fetchall()
    return jsonify({"files": [dict(f) for f in files]})


@bp.route("/<owner_orcid>/<slug>/web/<int:source_id>/check")
@login_required
def check_update(owner_orcid, slug, source_id):
    ds = _get_dataset_or_403(owner_orcid, slug)
    if not ds:
        return jsonify({"error": "Not authorized"}), 403
    db = get_db()
    src = db.execute("SELECT * FROM web_sources WHERE id = ? AND dataset_id = ?",
                     (source_id, ds["id"])).fetchone()
    if not src:
        return jsonify({"error": "Source not found"}), 404
    files = db.execute(
        "SELECT * FROM web_source_files WHERE source_id = ?", (source_id,)
    ).fetchall()
    if not files:
        # Re-scan first
        ok, result = web_scraper_service.scrape_page(src["page_url"])
        if not ok:
            return jsonify({"error": result}), 500
        for f in result:
            db.execute("""
                INSERT INTO web_source_files (source_id, filename, url, etag, last_modified, content_length)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(source_id, url) DO NOTHING
            """, (source_id, f["filename"], f["url"],
                  f.get("etag"), f.get("last_modified"), f.get("content_length")))
        db.commit()
        files = db.execute(
            "SELECT * FROM web_source_files WHERE source_id = ?", (source_id,)
        ).fetchall()

    updates = []
    for f in files:
        result = web_scraper_service.check_file_update(f["url"], f["etag"], f["last_modified"])
        if result.get("has_update"):
            updates.append(f["filename"])
        # Update stored metadata
        if result.get("etag") or result.get("last_modified"):
            db.execute(
                "UPDATE web_source_files SET etag=?, last_modified=?, content_length=? WHERE id=?",
                (result.get("etag"), result.get("last_modified"),
                 result.get("content_length"), f["id"])
            )
    db.execute("UPDATE web_sources SET last_checked_at = datetime('now') WHERE id = ?",
               (source_id,))
    db.commit()
    return jsonify({"has_update": bool(updates), "updated_files": updates})


@bp.route("/<owner_orcid>/<slug>/web/<int:source_id>/import", methods=["POST"])
@login_required
def import_files(owner_orcid, slug, source_id):
    ds = _get_dataset_or_403(owner_orcid, slug)
    if not ds:
        return jsonify({"error": "Not authorized"}), 403
    db = get_db()
    src = db.execute("SELECT * FROM web_sources WHERE id = ? AND dataset_id = ?",
                     (source_id, ds["id"])).fetchone()
    if not src:
        return jsonify({"error": "Source not found"}), 404

    file_ids  = request.json.get("file_ids", [])
    apply_owl = request.json.get("apply_owl", False)
    owl_regime = request.json.get("owl_regime", "OWL_RL")
    if not file_ids:
        return jsonify({"error": "No files selected"}), 400

    graph_uri  = ds["graph_base"] + "/data"
    upload_dir = config.UPLOAD_DIR / str(current_user.id) / slug / "web"

    # Queue one job per file and return immediately. Downloading and loading
    # used to happen here, inside the request: fine for a few megabytes, fatal
    # for the gigabyte-scale dumps this feature exists to fetch, because
    # gunicorn kills the worker long before the load finishes.
    jobs, errors = [], []
    for fid in file_ids:
        row = db.execute(
            "SELECT * FROM web_source_files WHERE id = ? AND source_id = ?",
            (fid, source_id)
        ).fetchone()
        if not row:
            errors.append(f"File id {fid} not found")
            continue

        # Keep the published name, not just its last suffix. "species.nt.gz"
        # saved as "<uuid>.gz" loses the .nt, and the extractor then has to
        # guess the member's syntax — it guesses Turtle, which happens to parse
        # N-Triples but would be wrong for JSON-LD or RDF/XML.
        dest = upload_dir / f"{uuid.uuid4().hex}_{_safe_name(row['filename'])}"
        job_id = job_runner.submit(
            dataset_id=ds["id"],
            user_id=current_user.id,
            file_path=dest,
            graph_uri=graph_uri,
            apply_owl=apply_owl,
            owl_regime=owl_regime,
            replace_data=False,
            source_url=row["url"],
            source_label=row["filename"],
            web_source_file_id=fid,
        )
        jobs.append({"job_id": job_id, "filename": row["filename"]})

    return jsonify({"jobs": jobs, "errors": errors})


@bp.route("/<owner_orcid>/<slug>/web/<int:source_id>/delete", methods=["POST"])
@login_required
def delete_source(owner_orcid, slug, source_id):
    ds = _get_dataset_or_403(owner_orcid, slug)
    if not ds:
        flash("Not authorized.", "error")
        return redirect(url_for("dashboard.index"))
    db = get_db()
    db.execute("DELETE FROM web_sources WHERE id = ? AND dataset_id = ?",
               (source_id, ds["id"]))
    db.commit()
    flash("Web source removed.", "success")
    return redirect(url_for("web_sources.view", owner_orcid=owner_orcid, slug=slug))
