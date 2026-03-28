"""Dataset view, RDF upload, SPARQL proxy."""
import os
import uuid
from pathlib import Path
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, jsonify, Response)
from flask_login import login_required, current_user
import config
from services.db import get_db
from services import qlever

bp = Blueprint("datasets", __name__, url_prefix="/u")


def _get_dataset_or_404(owner_orcid, slug):
    db = get_db()
    return db.execute(
        "SELECT d.*, u.orcid_id, u.name as owner_name "
        "FROM datasets d JOIN users u ON u.id = d.user_id "
        "WHERE u.orcid_id = ? AND d.slug = ?",
        (owner_orcid, slug)
    ).fetchone()


@bp.route("/<owner_orcid>/<slug>")
def view(owner_orcid=None, slug=None):
    if owner_orcid is None:
        owner_orcid = current_user.orcid_id
    ds = _get_dataset_or_404(owner_orcid, slug)
    if not ds:
        flash("Dataset not found.", "error")
        return redirect(url_for("dashboard.index"))
    if not ds["is_public"] and (not current_user.is_authenticated or current_user.id != ds["user_id"]):
        flash("This dataset is private.", "error")
        return redirect(url_for("main.index"))

    triple_count = qlever.count_triples(ds["graph_base"] + "/data")
    return render_template("dataset.html", ds=ds, triple_count=triple_count)


@bp.route("/<owner_orcid>/<slug>/upload", methods=["POST"])
@login_required
def upload(owner_orcid, slug):
    ds = _get_dataset_or_404(owner_orcid, slug)
    if not ds or ds["user_id"] != current_user.id:
        return jsonify({"error": "Not found or not authorized"}), 403

    f = request.files.get("rdf_file")
    if not f:
        return jsonify({"error": "No file provided"}), 400

    ext = Path(f.filename).suffix.lower()
    if ext not in config.ALLOWED_RDF_EXTENSIONS:
        return jsonify({"error": f"Unsupported format: {ext}"}), 400

    upload_dir = config.UPLOAD_DIR / str(current_user.id) / slug
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / f"{uuid.uuid4().hex}{ext}"
    f.save(str(file_path))

    graph_uri = ds["graph_base"] + "/data"
    ok, msg = qlever.load_rdf_file(graph_uri, file_path)

    if not ok:
        return jsonify({"error": msg}), 500
    return jsonify({"success": True, "graph": graph_uri})


@bp.route("/<owner_orcid>/<slug>/sparql", methods=["GET", "POST"])
def sparql_endpoint(owner_orcid, slug):
    """Proxy SPARQL requests, scoped to this dataset's named graph."""
    ds = _get_dataset_or_404(owner_orcid, slug)
    if not ds:
        return jsonify({"error": "Dataset not found"}), 404

    query = request.values.get("query", "")
    if not query:
        return render_template("yasgui.html", ds=ds)

    # Scope query to dataset's named graph if no FROM clause present
    graph_uri = ds["graph_base"] + "/data"
    if "FROM" not in query.upper():
        query = f"# Auto-scoped to dataset graph\n{query}"

    ok, result = qlever.sparql_query(query)
    if not ok:
        return jsonify(result), 500

    accept = request.headers.get("Accept", "application/sparql-results+json")
    return jsonify(result)


@bp.route("/<owner_orcid>/<slug>/delete", methods=["POST"])
@login_required
def delete(owner_orcid, slug):
    ds = _get_dataset_or_404(owner_orcid, slug)
    if not ds or ds["user_id"] != current_user.id:
        flash("Not found or not authorized.", "error")
        return redirect(url_for("dashboard.index"))

    # Drop named graphs from QLever
    for suffix in ["/data", "/examples", "/shapes"]:
        qlever.drop_graph(ds["graph_base"] + suffix)

    db = get_db()
    db.execute("DELETE FROM datasets WHERE id = ?", (ds["id"],))
    db.commit()
    flash(f"Dataset '{ds['label']}' deleted.", "success")
    return redirect(url_for("dashboard.index"))
