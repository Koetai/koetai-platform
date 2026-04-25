"""GitHub / GitLab repository sources — add, browse, import, update-check."""
import uuid
from pathlib import Path
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, jsonify)
from flask_login import login_required, current_user
import config
from services.db import get_db
from services import triplestore, owl_service
from services import github_service
from services import gitlab_service


def _svc(provider: str):
    """Return the appropriate service module for the given provider."""
    return gitlab_service if provider == "gitlab" else github_service

bp = Blueprint("github", __name__, url_prefix="/u")


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


@bp.route("/<owner_orcid>/<slug>/github")
@login_required
def view(owner_orcid, slug):
    ds = _get_dataset_or_403(owner_orcid, slug)
    if not ds:
        flash("Not found or not authorized.", "error")
        return redirect(url_for("dashboard.index"))

    db = get_db()
    sources = db.execute(
        "SELECT * FROM github_sources WHERE dataset_id = ? ORDER BY created_at DESC",
        (ds["id"],)
    ).fetchall()
    return render_template("github.html", ds=ds, sources=sources)


@bp.route("/<owner_orcid>/<slug>/github/add", methods=["POST"])
@login_required
def add_source(owner_orcid, slug):
    ds = _get_dataset_or_403(owner_orcid, slug)
    if not ds:
        return jsonify({"error": "Not found or not authorized"}), 403

    repo     = request.form.get("repo", "").strip().strip("/")
    branch   = request.form.get("branch", "").strip()
    path     = request.form.get("path", "").strip().strip("/")
    provider = request.form.get("provider", "github")
    if provider not in ("github", "gitlab"):
        provider = "github"

    # Auto-detect default branch if not specified
    if not branch:
        ok, detected = _svc(provider).get_default_branch(repo)
        branch = detected if ok else "main"

    if not repo or "/" not in repo:
        flash("Enter a valid repository (owner/repo).", "error")
        return redirect(url_for("github.view", owner_orcid=owner_orcid, slug=slug))

    db = get_db()
    try:
        db.execute(
            "INSERT INTO github_sources (dataset_id, repo, branch, path, provider) VALUES (?,?,?,?,?)",
            (ds["id"], repo, branch, path, provider)
        )
        db.commit()
    except Exception as e:
        flash(f"Could not add source: {e}", "error")
    return redirect(url_for("github.view", owner_orcid=owner_orcid, slug=slug))


@bp.route("/<owner_orcid>/<slug>/github/<int:source_id>/files")
@login_required
def list_files(owner_orcid, slug, source_id):
    ds = _get_dataset_or_403(owner_orcid, slug)
    if not ds:
        return jsonify({"error": "Not authorized"}), 403

    db = get_db()
    src = db.execute("SELECT * FROM github_sources WHERE id = ? AND dataset_id = ?",
                     (source_id, ds["id"])).fetchone()
    if not src:
        return jsonify({"error": "Source not found"}), 404

    ok, files = _svc(src["provider"]).list_rdf_files(src["repo"], src["branch"], src["path"])
    if not ok:
        return jsonify({"error": files}), 500
    return jsonify({"files": files})


@bp.route("/<owner_orcid>/<slug>/github/<int:source_id>/import", methods=["POST"])
@login_required
def import_files(owner_orcid, slug, source_id):
    ds = _get_dataset_or_403(owner_orcid, slug)
    if not ds:
        return jsonify({"error": "Not authorized"}), 403

    db = get_db()
    src = db.execute("SELECT * FROM github_sources WHERE id = ? AND dataset_id = ?",
                     (source_id, ds["id"])).fetchone()
    if not src:
        return jsonify({"error": "Source not found"}), 404

    file_paths  = request.json.get("files", [])  # list of {path, download_url}
    apply_owl   = request.json.get("apply_owl", False)
    owl_regime  = request.json.get("owl_regime", "OWL_RL")
    if not file_paths:
        return jsonify({"error": "No files selected"}), 400

    graph_uri   = ds["graph_base"] + "/data"
    ts          = triplestore.get(ds)
    upload_dir  = config.UPLOAD_DIR / str(current_user.id) / slug / "github"
    imported    = []
    errors      = []
    OWL_EXTS    = {".owl", ".rdf"}

    # Release read transaction before long network/triplestore operations
    db.commit()

    try:
        for item in file_paths:
            file_path_str = item.get("path", "")
            download_url  = item.get("download_url", "")
            ext = Path(file_path_str).suffix.lower()
            dest = upload_dir / f"{uuid.uuid4().hex}{ext}"

            ok, msg = _svc(src["provider"]).download_file(download_url, dest)
            if not ok:
                errors.append(f"{file_path_str}: {msg}")
                continue

            load_path = dest
            if apply_owl and ext in OWL_EXTS:
                ok_r, reasoned, owl_msg = owl_service.materialize(dest, regime=owl_regime)
                if ok_r:
                    load_path = reasoned
                else:
                    errors.append(f"{file_path_str} (OWL reasoning): {owl_msg}")
                    continue

            ok, msg = ts.load_rdf_file(graph_uri, load_path)
            if load_path != dest:
                load_path.unlink(missing_ok=True)
            if not ok:
                errors.append(f"{file_path_str}: {msg}")
            else:
                imported.append(file_path_str)

        # Record latest commit SHA
        ok, sha = _svc(src["provider"]).get_latest_sha(src["repo"], src["branch"])
        if ok:
            db.execute(
                "UPDATE github_sources SET last_commit_sha = ?, last_imported_at = datetime('now') WHERE id = ?",
                (sha, source_id)
            )
            db.commit()

    except Exception as e:
        return jsonify({"imported": imported, "errors": errors + [f"Unexpected error: {e}"]}), 500

    return jsonify({"imported": imported, "errors": errors})


@bp.route("/<owner_orcid>/<slug>/github/<int:source_id>/check")
@login_required
def check_update(owner_orcid, slug, source_id):
    ds = _get_dataset_or_403(owner_orcid, slug)
    if not ds:
        return jsonify({"error": "Not authorized"}), 403

    db = get_db()
    src = db.execute("SELECT * FROM github_sources WHERE id = ? AND dataset_id = ?",
                     (source_id, ds["id"])).fetchone()
    if not src:
        return jsonify({"error": "Source not found"}), 404

    result = _svc(src["provider"]).check_for_update(src["repo"], src["branch"], src["last_commit_sha"])
    return jsonify(result)


@bp.route("/<owner_orcid>/<slug>/github/<int:source_id>/delete", methods=["POST"])
@login_required
def delete_source(owner_orcid, slug, source_id):
    ds = _get_dataset_or_403(owner_orcid, slug)
    if not ds:
        flash("Not authorized.", "error")
        return redirect(url_for("dashboard.index"))

    db = get_db()
    db.execute("DELETE FROM github_sources WHERE id = ? AND dataset_id = ?",
               (source_id, ds["id"]))
    db.commit()
    flash("Repository source removed.", "success")
    return redirect(url_for("github.view", owner_orcid=owner_orcid, slug=slug))
