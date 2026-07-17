#!/usr/bin/env python3
"""Koetai Platform — main Flask application."""

import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "0")  # enforce HTTPS in prod

from flask import Flask, render_template, redirect, url_for
from flask_login import LoginManager
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

import json as _json
import config
from services.db import init_db, get_db, close_db
from models import User

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_MB * 1024 * 1024

CORS(app)

# ── Jinja filters ─────────────────────────────────────────────────────────────
@app.template_filter("from_json")
def from_json_filter(value):
    try:
        return _json.loads(value) if value else []
    except Exception:
        return []

# ── Flask-Login ───────────────────────────────────────────────────────────────
login_manager = LoginManager(app)
login_manager.login_view  = "auth.login"
login_manager.login_message = "Please sign in with your ORCID iD."

@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
    return User.from_row(row) if row else None

if config.IS_LOCAL:
    from flask_login import login_user, current_user
    from services.db import get_local_user_row

    @app.before_request
    def _sign_in_local_user():
        """A local install has exactly one user and no sign-in step.

        Doing this here rather than stubbing login_required means every
        @login_required route and every current_user reference keeps working
        untouched, and community mode is completely unaffected.
        """
        if not current_user.is_authenticated:
            row = get_local_user_row()
            if row:
                login_user(User.from_row(row))

# ── Blueprints ────────────────────────────────────────────────────────────────
from routes.auth      import bp as auth_bp
from routes.dashboard import bp as dashboard_bp
from routes.datasets  import bp as datasets_bp
from routes.shapes    import bp as shapes_bp
from routes.examples  import bp as examples_bp
from routes.sparqlist import bp as sparqlist_bp
from routes.github      import bp as github_bp
from routes.web_sources import bp as web_sources_bp
from routes.fdp        import bp as fdp_bp

app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(datasets_bp)
app.register_blueprint(shapes_bp)
app.register_blueprint(examples_bp)
app.register_blueprint(sparqlist_bp)
app.register_blueprint(github_bp)
app.register_blueprint(web_sources_bp)
app.register_blueprint(fdp_bp)

# ── Template context: monthly cost indicator ─────────────────────────────────
@app.context_processor
def inject_monthly_cost():
    from routes.dashboard import _load_costs, _compute_total
    try:
        cfg = _load_costs()
        return {"monthly_cost_eur": _compute_total(cfg)}
    except Exception:
        return {"monthly_cost_eur": None}

# ── Main routes ───────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/help")
def helppage():
    return render_template("help.html")

@app.route("/health")
def health():
    return {"status": "ok"}

@app.route("/endpoints")
def endpoints():
    """Public listing of all public SPARQL endpoints, grouped by owner."""
    db = get_db()
    rows = db.execute(
        """SELECT d.id, d.slug, d.label, d.description, d.graph_base,
                  d.platform, d.created_at,
                  u.orcid_id, u.name as owner_name
           FROM datasets d
           JOIN users u ON u.id = d.user_id
           WHERE d.is_public = 1
           ORDER BY u.name, u.orcid_id, d.label""",
    ).fetchall()

    # Group by owner
    owners = {}
    for r in rows:
        key = r["orcid_id"]
        if key not in owners:
            owners[key] = {"orcid_id": r["orcid_id"],
                           "name": r["owner_name"] or r["orcid_id"],
                           "datasets": []}
        owners[key]["datasets"].append(dict(r))

    return render_template("endpoints.html", owners=list(owners.values()))


@app.route("/sparql-editor")
def sparql_editor():
    """Multi-endpoint SPARQL editor — select any public dataset from a dropdown."""
    db = get_db()
    datasets = db.execute(
        """SELECT d.id, d.slug, d.label, d.graph_base, d.platform,
                  u.orcid_id, u.name as owner_name
           FROM datasets d
           JOIN users u ON u.id = d.user_id
           WHERE d.is_public = 1
           ORDER BY u.name, d.label""",
    ).fetchall()
    return render_template("sparql_editor.html", datasets=[dict(d) for d in datasets])

# ── Init ──────────────────────────────────────────────────────────────────────
app.teardown_appcontext(close_db)

with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=3002, debug=False)
