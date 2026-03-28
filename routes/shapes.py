"""Shape inference (ShExer + RUDOF) and validation routes."""
from pathlib import Path
from flask import (Blueprint, render_template, request, jsonify,
                   redirect, url_for, flash, send_file)
from flask_login import login_required, current_user
import io
import config
from services.db import get_db
from services import shexer_service, rudof_service

bp = Blueprint("shapes", __name__, url_prefix="/u")


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


def _latest_rdf_file(user_id, slug) -> Path | None:
    upload_dir = config.UPLOAD_DIR / str(user_id) / slug
    if not upload_dir.exists():
        return None
    files = sorted(upload_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
    return files[0] if files else None


@bp.route("/<owner_orcid>/<slug>/shapes")
@login_required
def view(owner_orcid, slug):
    ds = _get_dataset_or_403(owner_orcid, slug)
    if not ds:
        flash("Not found or not authorized.", "error")
        return redirect(url_for("dashboard.index"))

    db = get_db()
    shapes = db.execute(
        "SELECT * FROM shapes WHERE dataset_id = ? ORDER BY created_at DESC",
        (ds["id"],)
    ).fetchall()
    return render_template("shapes.html", ds=ds, shapes=shapes)


@bp.route("/<owner_orcid>/<slug>/shapes/infer", methods=["POST"])
@login_required
def infer(owner_orcid, slug):
    ds = _get_dataset_or_403(owner_orcid, slug)
    if not ds:
        return jsonify({"error": "Not found or not authorized"}), 403

    tool   = request.json.get("tool", "shexer")  # shexer | rudof_shex | rudof_shacl
    rdf_file = _latest_rdf_file(current_user.id, slug)
    if not rdf_file:
        return jsonify({"error": "No RDF data uploaded yet"}), 400

    if tool == "shexer":
        ok, schema = shexer_service.infer_shex(rdf_file, ds["graph_base"] + "/data")
        fmt = "shex"
        mermaid = shexer_service.shex_to_mermaid(schema) if ok else None
    elif tool == "rudof_shex":
        ok, schema = rudof_service.infer_shex_rudof(rdf_file)
        fmt = "shex"
        mermaid = shexer_service.shex_to_mermaid(schema) if ok else None
    elif tool == "rudof_shacl":
        ok, schema = rudof_service.infer_shacl(rdf_file)
        fmt = "shacl"
        mermaid = rudof_service.shacl_to_mermaid(schema) if ok else None
    else:
        return jsonify({"error": "Unknown tool"}), 400

    if not ok:
        return jsonify({"error": schema}), 500

    db = get_db()
    db.execute(
        "INSERT INTO shapes (dataset_id, format, source, content, mermaid) VALUES (?,?,?,?,?)",
        (ds["id"], fmt, "inferred", schema, mermaid)
    )
    db.commit()
    shape_id = db.execute("SELECT last_insert_rowid() as id").fetchone()["id"]
    return jsonify({"id": shape_id, "format": fmt, "mermaid": mermaid, "schema": schema})


@bp.route("/<owner_orcid>/<slug>/shapes/validate", methods=["POST"])
@login_required
def validate(owner_orcid, slug):
    ds = _get_dataset_or_403(owner_orcid, slug)
    if not ds:
        return jsonify({"error": "Not found or not authorized"}), 403

    shape_id = request.json.get("shape_id")
    rdf_file = _latest_rdf_file(current_user.id, slug)
    if not rdf_file:
        return jsonify({"error": "No RDF data uploaded yet"}), 400

    db = get_db()
    shape = db.execute("SELECT * FROM shapes WHERE id = ? AND dataset_id = ?",
                       (shape_id, ds["id"])).fetchone()
    if not shape:
        return jsonify({"error": "Shape not found"}), 404

    if shape["format"] == "shex":
        ok, report = rudof_service.validate_shex(rdf_file, shape["content"])
    else:
        ok, report = rudof_service.validate_shacl(rdf_file, shape["content"])

    return jsonify({"valid": ok, "report": report})


@bp.route("/<owner_orcid>/<slug>/shapes/<int:shape_id>/download/<fmt>")
def download(owner_orcid, slug, shape_id, fmt):
    """Download shape as .shex/.ttl or .mmd (Mermaid)."""
    db = get_db()
    ds = db.execute(
        "SELECT d.* FROM datasets d JOIN users u ON u.id = d.user_id "
        "WHERE u.orcid_id = ? AND d.slug = ?", (owner_orcid, slug)
    ).fetchone()
    if not ds:
        return "Not found", 404

    shape = db.execute("SELECT * FROM shapes WHERE id = ? AND dataset_id = ?",
                       (shape_id, ds["id"])).fetchone()
    if not shape:
        return "Not found", 404

    if fmt == "mermaid":
        content  = shape["mermaid"] or "# No Mermaid diagram available"
        filename = f"{slug}-schema.mmd"
        mimetype = "text/plain"
    elif fmt in ("shex", "shacl", "ttl"):
        content  = shape["content"]
        filename = f"{slug}-shapes.{'shex' if shape['format'] == 'shex' else 'ttl'}"
        mimetype = "text/plain"
    else:
        return "Unknown format", 400

    return send_file(
        io.BytesIO(content.encode()),
        mimetype=mimetype,
        as_attachment=True,
        download_name=filename,
    )
