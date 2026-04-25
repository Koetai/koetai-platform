"""ORCID OAuth 2.0 authentication routes."""
import secrets
from flask import Blueprint, redirect, request, session, url_for, flash
from flask_login import login_user, logout_user, login_required
from requests_oauthlib import OAuth2Session
import config
from services.db import get_db
from models import User

bp = Blueprint("auth", __name__, url_prefix="/auth")


def _orcid_session(state=None):
    return OAuth2Session(
        client_id=config.ORCID_CLIENT_ID,
        redirect_uri=config.ORCID_REDIRECT_URI,
        scope=["openid"],
        state=state,
    )


@bp.route("/login")
def login():
    oauth = _orcid_session()
    auth_url, state = oauth.authorization_url(config.ORCID_AUTH_URL)
    session["oauth_state"] = state
    return redirect(auth_url)


@bp.route("/callback")
def callback():
    state = session.get("oauth_state")
    oauth = _orcid_session(state=state)

    try:
        token = oauth.fetch_token(
            config.ORCID_TOKEN_URL,
            authorization_response=request.url,
            client_secret=config.ORCID_CLIENT_SECRET,
            include_client_id=True,
        )
    except Exception as e:
        flash(f"ORCID authentication failed: {e}", "error")
        return redirect(url_for("index"))

    orcid_id = token.get("orcid") or token.get("sub", "")
    name     = token.get("name", "")

    if not orcid_id:
        flash("Could not retrieve ORCID iD.", "error")
        return redirect(url_for("index"))

    db   = get_db()
    user = db.execute("SELECT * FROM users WHERE orcid_id = ?", (orcid_id,)).fetchone()

    if user is None:
        # Check for valid unused invite in session
        invite_code = session.pop("invite_code", None)
        if not invite_code:
            flash("An invitation code is required to register. Please use an invite link.", "warning")
            return redirect(url_for("index"))

        invite = db.execute(
            "SELECT * FROM invitations WHERE code = ? AND used_by IS NULL", (invite_code,)
        ).fetchone()
        if not invite:
            flash("Invalid or already-used invitation code.", "error")
            return redirect(url_for("index"))

        db.execute(
            "INSERT INTO users (orcid_id, name) VALUES (?, ?)", (orcid_id, name)
        )
        new_user = db.execute("SELECT * FROM users WHERE orcid_id = ?", (orcid_id,)).fetchone()
        db.execute(
            "UPDATE invitations SET used_by = ?, used_at = datetime('now') WHERE id = ?",
            (new_user["id"], invite["id"])
        )
        db.commit()
        user = db.execute("SELECT * FROM users WHERE orcid_id = ?", (orcid_id,)).fetchone()

    login_user(User.from_row(user))
    return redirect(url_for("dashboard.index"))


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))


@bp.route("/invite/<code>")
def accept_invite(code):
    """Store invite code in session then redirect to ORCID login."""
    db = get_db()
    invite = db.execute(
        "SELECT * FROM invitations WHERE code = ? AND used_by IS NULL", (code,)
    ).fetchone()
    if not invite:
        flash("This invitation link is invalid or has already been used.", "error")
        return redirect(url_for("index"))
    session["invite_code"] = code
    return redirect(url_for("auth.login"))
