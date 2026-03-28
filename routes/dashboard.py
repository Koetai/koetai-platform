"""User dashboard — list datasets, create new ones, manage invites."""
import secrets
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
import config
from services.db import get_db

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@bp.route("/")
@login_required
def index():
    db = get_db()
    datasets = db.execute(
        "SELECT * FROM datasets WHERE user_id = ? ORDER BY created_at DESC",
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

        if not label or not slug:
            flash("Label and slug are required.", "error")
            return render_template("dataset_new.html")

        graph_base = f"{config.BASE_URL}/u/{current_user.orcid_id}/{slug}"
        db = get_db()
        try:
            db.execute(
                "INSERT INTO datasets (user_id, slug, label, description, graph_base, is_public) VALUES (?,?,?,?,?,?)",
                (current_user.id, slug, label, description, graph_base, is_public)
            )
            db.commit()
        except Exception as e:
            flash(f"Could not create dataset: {e}", "error")
            return render_template("dataset_new.html")

        flash(f"Dataset '{label}' created.", "success")
        return redirect(url_for("datasets.view", slug=slug))

    return render_template("dataset_new.html")


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
