"""Shape inference (ShExer + RUDOF) and validation routes."""
from pathlib import Path
from flask import (Blueprint, render_template, request, jsonify,
                   redirect, url_for, flash, send_file)
from flask_login import login_required, current_user
import io
import config
import tempfile, shutil
from services.db import get_db
from services import shexer_service, rudof_service, jena_service, rdfconfig_service

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
    """
    Return the most recently uploaded source RDF file for this dataset.
    Excludes sub-directories (e.g. github/, web/) and temp files.
    Prefers files in the top-level upload dir; falls back to sub-dirs.
    """
    upload_dir = config.UPLOAD_DIR / str(user_id) / slug
    if not upload_dir.exists():
        return None
    # Only direct children that are files with known RDF extensions
    rdf_exts = config.ALLOWED_RDF_EXTENSIONS
    files = sorted(
        [f for f in upload_dir.iterdir() if f.is_file() and f.suffix.lower() in rdf_exts],
        key=lambda f: f.stat().st_mtime, reverse=True
    )
    if files:
        return files[0]
    # Fall back: search recursively
    files = sorted(
        [f for f in upload_dir.rglob("*") if f.is_file() and f.suffix.lower() in rdf_exts],
        key=lambda f: f.stat().st_mtime, reverse=True
    )
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

    tool     = request.json.get("tool", "shexer")  # shexer | rudof_shex | rudof_shacl
    rdf_file = _latest_rdf_file(current_user.id, slug)
    if not rdf_file:
        return jsonify({"error": "No RDF data uploaded yet"}), 400

    rdfcfg_model  = None
    rdfcfg_prefix = None
    rdfcfg_svg    = None
    rdfcfg_sparql = None

    if tool == "shexer":
        # Run ShExer with rdf-config output in a temp dir
        tmpdir = tempfile.mkdtemp(prefix="koetai_rdfcfg_")
        try:
            ok, schema = shexer_service.infer_shex(
                rdf_file,
                graph_uri=ds["graph_base"] + "/data",
                rdfconfig_dir=Path(tmpdir),
            )
            fmt = "shex"
            mermaid = shexer_service.shex_to_mermaid(schema) if ok else None

            if ok:
                # Generate rdf-config outputs (SVG, SPARQL) from the YAML files
                endpoint_url = f"{config.BASE_URL}/u/{ds['orcid_id']}/{ds['slug']}/sparql"
                rc = rdfconfig_service.generate_from_shex_output(
                    Path(tmpdir), endpoint_url=endpoint_url
                )
                rdfcfg_model  = rc.get("model_yaml")
                rdfcfg_prefix = rc.get("prefix_yaml")
                rdfcfg_svg    = rc.get("svg")
                rdfcfg_sparql = rc.get("sparql")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

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
        "INSERT INTO shapes (dataset_id, format, source, content, mermaid, "
        "rdfconfig_model, rdfconfig_prefix, rdfconfig_svg, rdfconfig_sparql) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (ds["id"], fmt, "inferred", schema, mermaid,
         rdfcfg_model, rdfcfg_prefix, rdfcfg_svg, rdfcfg_sparql)
    )
    db.commit()
    shape_id = db.execute("SELECT last_insert_rowid() as id").fetchone()["id"]
    return jsonify({
        "id":            shape_id,
        "format":        fmt,
        "mermaid":       mermaid,
        "schema":        schema,
        "has_rdfconfig": bool(rdfcfg_svg or rdfcfg_model),
    })


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

    validator = request.json.get("validator", "rudof")  # rudof | jena

    if shape["format"] == "shex":
        if validator == "jena":
            ok, report = jena_service.validate_shex(rdf_file, shape["content"])
        else:
            ok, report = rudof_service.validate_shex(rdf_file, shape["content"])
    else:
        ok, report = rudof_service.validate_shacl(rdf_file, shape["content"])

    return jsonify({"valid": ok, "report": report, "validator": validator})


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
    elif fmt == "rdfconfig_model":
        content  = shape["rdfconfig_model"] or "# model.yaml not available"
        filename = "model.yaml"
        mimetype = "text/yaml"
    elif fmt == "rdfconfig_prefix":
        content  = shape["rdfconfig_prefix"] or "# prefix.yaml not available"
        filename = "prefix.yaml"
        mimetype = "text/yaml"
    elif fmt == "rdfconfig_svg":
        content  = shape["rdfconfig_svg"] or "<!-- SVG not available -->"
        filename = f"{slug}-schema.svg"
        mimetype = "image/svg+xml"
        return send_file(
            io.BytesIO(content.encode()),
            mimetype=mimetype,
            as_attachment=False,  # display inline
            download_name=filename,
        )
    elif fmt == "rdfconfig_sparql":
        content  = shape["rdfconfig_sparql"] or "# SPARQL not available"
        filename = f"{slug}-queries.rq"
        mimetype = "text/plain"
    else:
        return "Unknown format", 400

    return send_file(
        io.BytesIO(content.encode()),
        mimetype=mimetype,
        as_attachment=True,
        download_name=filename,
    )


@bp.route("/<owner_orcid>/<slug>/shapes/<int:shape_id>/delete", methods=["POST"])
@login_required
def delete_shape(owner_orcid, slug, shape_id):
    db = get_db()
    ds = db.execute(
        "SELECT d.* FROM datasets d JOIN users u ON u.id = d.user_id "
        "WHERE u.orcid_id = ? AND d.slug = ? AND d.user_id = ?",
        (owner_orcid, slug, current_user.id)
    ).fetchone()
    if not ds:
        return jsonify({"error": "Not authorized"}), 403
    db.execute("DELETE FROM shapes WHERE id = ? AND dataset_id = ?", (shape_id, ds["id"]))
    db.commit()
    return jsonify({"success": True})
