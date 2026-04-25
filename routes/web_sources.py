"""Web download page sources — add, scan, import, update-check."""
import bz2
import gzip
import tarfile
import uuid
import zipfile
from pathlib import Path
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, jsonify)
from flask_login import login_required, current_user
import config
from services.db import get_db
from services import triplestore, owl_service
from services import web_scraper_service
from services.web_scraper_service import RDF_EXTENSIONS

_ARCHIVE_EXTS = {".zip", ".gz", ".bz2", ".tgz"}


def _extract_rdf_files(archive_path: Path, dest_dir: Path) -> list[Path]:
    """Extract RDF files from an archive. Returns list of paths to extracted files."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix   = archive_path.suffix.lower()
    suffixes = [s.lower() for s in archive_path.suffixes]
    extracted = []

    if suffix == ".zip":
        with zipfile.ZipFile(archive_path) as zf:
            for name in zf.namelist():
                if Path(name).suffix.lower() in RDF_EXTENSIONS:
                    out = dest_dir / Path(name).name
                    out.write_bytes(zf.read(name))
                    extracted.append(out)

    elif suffix == ".tgz" or (suffix == ".gz" and ".tar" in suffixes):
        with tarfile.open(archive_path) as tf:
            for member in tf.getmembers():
                if member.isfile() and Path(member.name).suffix.lower() in RDF_EXTENSIONS:
                    f = tf.extractfile(member)
                    if f:
                        out = dest_dir / Path(member.name).name
                        out.write_bytes(f.read())
                        extracted.append(out)

    elif suffix == ".gz":
        inner_name = archive_path.stem          # e.g. "data.ttl" from "data.ttl.gz"
        inner_ext  = Path(inner_name).suffix.lower()
        out_name   = inner_name if inner_ext in RDF_EXTENSIONS else inner_name + ".ttl"
        out = dest_dir / out_name
        with gzip.open(archive_path, "rb") as f_in:
            out.write_bytes(f_in.read())
        extracted.append(out)

    elif suffix == ".bz2":
        inner_name = archive_path.stem
        inner_ext  = Path(inner_name).suffix.lower()
        out_name   = inner_name if inner_ext in RDF_EXTENSIONS else inner_name + ".ttl"
        out = dest_dir / out_name
        with bz2.open(archive_path, "rb") as f_in:
            out.write_bytes(f_in.read())
        extracted.append(out)

    return extracted

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
    ts         = triplestore.get(ds)
    upload_dir = config.UPLOAD_DIR / str(current_user.id) / slug / "web"
    OWL_EXTS   = {".owl", ".rdf"}

    # Read all file rows up front, then release the read transaction before
    # the long network/triplestore operations to avoid holding the DB lock.
    rows = {}
    for fid in file_ids:
        row = db.execute(
            "SELECT * FROM web_source_files WHERE id = ? AND source_id = ?",
            (fid, source_id)
        ).fetchone()
        if row:
            rows[fid] = dict(row)
    db.commit()  # release any implicit read transaction

    imported = []
    errors   = []

    try:
        for fid in file_ids:
            if fid not in rows:
                errors.append(f"File id {fid} not found")
                continue
            row = rows[fid]

            ext  = Path(row["filename"]).suffix.lower()
            dest = upload_dir / f"{uuid.uuid4().hex}{ext}"
            ok, msg = web_scraper_service.download_file(row["url"], dest)
            if not ok:
                errors.append(f"{row['filename']}: {msg}")
                continue

            # ── Archive: extract RDF files then load each ──────────────────
            if ext in _ARCHIVE_EXTS:
                extract_dir = upload_dir / f"x_{uuid.uuid4().hex}"
                try:
                    rdf_paths = _extract_rdf_files(dest, extract_dir)
                except Exception as e:
                    errors.append(f"{row['filename']}: extraction failed — {e}")
                    dest.unlink(missing_ok=True)
                    continue
                finally:
                    dest.unlink(missing_ok=True)

                if not rdf_paths:
                    errors.append(f"{row['filename']}: no RDF files found in archive")
                    extract_dir.rmdir()
                    continue

                loaded_any = False
                for rdf_path in rdf_paths:
                    load_path = rdf_path
                    if apply_owl and rdf_path.suffix.lower() in OWL_EXTS:
                        ok_r, reasoned, owl_msg = owl_service.materialize(rdf_path, regime=owl_regime)
                        if ok_r:
                            load_path = reasoned
                        else:
                            errors.append(f"{row['filename']}/{rdf_path.name} (OWL): {owl_msg}")
                            rdf_path.unlink(missing_ok=True)
                            continue
                    ok, msg = ts.load_rdf_file(graph_uri, load_path)
                    if load_path != rdf_path:
                        load_path.unlink(missing_ok=True)
                    rdf_path.unlink(missing_ok=True)
                    if not ok:
                        errors.append(f"{row['filename']}/{rdf_path.name}: {msg}")
                    else:
                        loaded_any = True
                try:
                    extract_dir.rmdir()
                except OSError:
                    pass
                if loaded_any:
                    meta = web_scraper_service._head_file(row["url"])
                    db.execute(
                        "UPDATE web_source_files SET imported_at=datetime('now'), etag=?, last_modified=? WHERE id=?",
                        (meta.get("etag") or row["etag"], meta.get("last_modified") or row["last_modified"], fid)
                    )
                    db.commit()
                    imported.append(row["filename"])
                continue

            # ── Plain RDF file ─────────────────────────────────────────────
            load_path = dest
            if apply_owl and ext in OWL_EXTS:
                ok_r, reasoned, owl_msg = owl_service.materialize(dest, regime=owl_regime)
                if ok_r:
                    load_path = reasoned
                else:
                    errors.append(f"{row['filename']} (OWL reasoning): {owl_msg}")
                    continue

            ok, msg = ts.load_rdf_file(graph_uri, load_path)
            if load_path != dest:
                load_path.unlink(missing_ok=True)
            if not ok:
                errors.append(f"{row['filename']}: {msg}")
            else:
                meta = web_scraper_service._head_file(row["url"])
                db.execute(
                    "UPDATE web_source_files SET imported_at=datetime('now'), etag=?, last_modified=? WHERE id=?",
                    (meta.get("etag") or row["etag"], meta.get("last_modified") or row["last_modified"], fid)
                )
                db.commit()
                imported.append(row["filename"])

        if imported:
            db.execute("UPDATE web_sources SET last_imported_at=datetime('now') WHERE id=?",
                       (source_id,))
            db.commit()

    except Exception as e:
        return jsonify({"imported": imported, "errors": errors + [f"Unexpected error: {e}"]}), 500

    return jsonify({"imported": imported, "errors": errors})


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
