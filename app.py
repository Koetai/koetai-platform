#!/usr/bin/env python3
"""Koetai Platform — main Flask application."""

import os
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "0")  # enforce HTTPS in prod

from flask import Flask, render_template, redirect, url_for
from flask_login import LoginManager
from flask_cors import CORS

import config
from services.db import init_db, get_db
from models import User

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_MB * 1024 * 1024

CORS(app)

# ── Flask-Login ───────────────────────────────────────────────────────────────
login_manager = LoginManager(app)
login_manager.login_view  = "auth.login"
login_manager.login_message = "Please sign in with your ORCID iD."

@login_manager.user_loader
def load_user(user_id):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
    return User.from_row(row) if row else None

# ── Blueprints ────────────────────────────────────────────────────────────────
from routes.auth      import bp as auth_bp
from routes.dashboard import bp as dashboard_bp
from routes.datasets  import bp as datasets_bp
from routes.shapes    import bp as shapes_bp
from routes.examples  import bp as examples_bp
from routes.sparqlist import bp as sparqlist_bp

app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(datasets_bp)
app.register_blueprint(shapes_bp)
app.register_blueprint(examples_bp)
app.register_blueprint(sparqlist_bp)

# ── Main routes ───────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health():
    return {"status": "ok"}

# ── Init ──────────────────────────────────────────────────────────────────────
with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=3002, debug=False)
