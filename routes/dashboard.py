"""User dashboard — list datasets, create new ones, manage invites."""
import os
import secrets
import shutil
from pathlib import Path
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
import config
from services.db import get_db
from services import triplestore

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@bp.route("/")
@login_required
def index():
    db = get_db()
    datasets = db.execute(
        """SELECT d.*,
             (SELECT COUNT(*) FROM github_sources WHERE dataset_id=d.id) AS git_count,
             (SELECT COUNT(*) FROM web_sources    WHERE dataset_id=d.id) AS web_count,
             (SELECT COUNT(*) FROM shapes         WHERE dataset_id=d.id) AS shape_count
           FROM datasets d
           WHERE user_id = ? ORDER BY created_at DESC""",
        (current_user.id,)
    ).fetchall()
    return render_template("dashboard.html", datasets=datasets)


@bp.route("/dataset/new", methods=["GET", "POST"])
@login_required
def new_dataset():
    if request.method == "POST":
        label       = request.form["label"].strip()
        slug        = request.form["slug"].strip().lower().replace(" ", "-")
        description = request.form.get("description", "").strip()
        is_public   = 1 if request.form.get("is_public") else 0
        platform    = request.form.get("platform", "qlever")
        if platform not in triplestore.SUPPORTED:
            platform = "qlever"

        if not label or not slug:
            flash("Label and slug are required.", "error")
            return render_template("dataset_new.html")

        # A federation dataset is defined by its sources, not an upload.
        sources = None
        if platform == "comunica":
            sources = "\n".join(
                line.strip() for line in request.form.get("sources", "").splitlines()
                if line.strip()
            )
            if not sources:
                flash("A federation dataset needs at least one source.", "error")
                return render_template("dataset_new.html")

        fdp_keywords = request.form.get("fdp_keywords", "").strip()
        fdp_theme    = request.form.get("fdp_theme", "").strip()
        fdp_license  = request.form.get("fdp_license", "https://creativecommons.org/licenses/by/4.0/").strip()
        fdp_version  = request.form.get("fdp_version", "1.0").strip()

        graph_base = f"{config.BASE_URL}/u/{current_user.orcid_id}/{slug}"
        db = get_db()
        try:
            db.execute(
                "INSERT INTO datasets (user_id, slug, label, description, graph_base, platform, sources, "
                "is_public, fdp_keywords, fdp_theme, fdp_license, fdp_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (current_user.id, slug, label, description, graph_base, platform, sources, is_public,
                 fdp_keywords, fdp_theme, fdp_license, fdp_version)
            )
            db.commit()
        except Exception as e:
            flash(f"Could not create dataset: {e}", "error")
            return render_template("dataset_new.html")

        flash(f"Dataset '{label}' created.", "success")
        return redirect(url_for("datasets.view", owner_orcid=current_user.orcid_id, slug=slug))

    return render_template("dataset_new.html")


@bp.route("/admin/storage")
@login_required
def admin_storage():
    if not current_user.is_admin:
        flash("Admin only.", "error")
        return redirect(url_for("dashboard.index"))

    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY created_at").fetchall()

    rows = []
    total_bytes = 0
    for u in users:
        user_dir = config.UPLOAD_DIR / str(u["id"])
        if user_dir.exists():
            user_bytes = sum(f.stat().st_size for f in user_dir.rglob("*") if f.is_file())
        else:
            user_bytes = 0

        datasets = db.execute(
            "SELECT id, slug, label FROM datasets WHERE user_id=? ORDER BY slug",
            (u["id"],)
        ).fetchall()

        ds_rows = []
        for ds in datasets:
            ds_dir = config.UPLOAD_DIR / str(u["id"]) / ds["slug"]
            if ds_dir.exists():
                ds_bytes = sum(f.stat().st_size for f in ds_dir.rglob("*") if f.is_file())
            else:
                ds_bytes = 0
            ds_rows.append({"slug": ds["slug"], "label": ds["label"],
                             "bytes": ds_bytes, "mb": round(ds_bytes / 1e6, 1)})

        total_bytes += user_bytes
        rows.append({
            "id":       u["id"],
            "orcid_id": u["orcid_id"],
            "name":     u["name"] or u["orcid_id"],
            "is_admin": u["is_admin"],
            "bytes":    user_bytes,
            "mb":       round(user_bytes / 1e6, 1),
            "gb":       round(user_bytes / 1e9, 3),
            "datasets": ds_rows,
        })

    rows.sort(key=lambda r: r["bytes"], reverse=True)
    total_gb = round(total_bytes / 1e9, 3)

    disk = shutil.disk_usage("/")
    disk_total_gb = round(disk.total / 1e9, 1)
    disk_used_gb  = round(disk.used  / 1e9, 1)
    disk_pct      = int(disk.used / disk.total * 100)

    return render_template("admin_storage.html",
        rows=rows, total_gb=total_gb,
        disk_total_gb=disk_total_gb, disk_used_gb=disk_used_gb, disk_pct=disk_pct)


@bp.route("/invites", methods=["GET", "POST"])
@login_required
def invites():
    if not current_user.is_admin:
        flash("Admin only.", "error")
        return redirect(url_for("dashboard.index"))

    db = get_db()
    if request.method == "POST":
        code = secrets.token_urlsafe(16)
        db.execute("INSERT INTO invitations (code, created_by) VALUES (?,?)",
                   (code, current_user.id))
        db.commit()
        flash(f"Invite created: {config.BASE_URL}/auth/invite/{code}", "success")

    invites = db.execute(
        "SELECT i.*, u.name as used_by_name FROM invitations i "
        "LEFT JOIN users u ON u.id = i.used_by "
        "WHERE i.created_by = ? ORDER BY i.created_at DESC",
        (current_user.id,)
    ).fetchall()
    return render_template("invites.html", invites=invites, base_url=config.BASE_URL)
