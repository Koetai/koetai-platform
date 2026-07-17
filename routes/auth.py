"""ORCID OAuth 2.0 authentication routes."""
import secrets
import requests
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
    # A local install is already signed in as its single user (see app.py), and
    # has no ORCID credentials to redirect with.
    if config.IS_LOCAL:
        return redirect(url_for("dashboard.index"))
    oauth = _orcid_session()
    auth_url, state = oauth.authorization_url(config.ORCID_AUTH_URL)
    session["oauth_state"] = state
    return redirect(auth_url)


@bp.route("/callback")
def callback():
    if config.IS_LOCAL:
        return redirect(url_for("dashboard.index"))
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
        # First sign-in: whether this ORCID may register depends on the mode.
        if config.IS_INTERNAL:
            registered = _register_internal(db, orcid_id, name, token.get("access_token"))
        else:
            registered = _register_with_invite(db, orcid_id, name)
        if not registered:
            return redirect(url_for("index"))
        user = db.execute("SELECT * FROM users WHERE orcid_id = ?", (orcid_id,)).fetchone()

    login_user(User.from_row(user))
    return redirect(url_for("dashboard.index"))


def _register_with_invite(db, orcid_id, name):
    """community mode: a new user needs an unused invitation code in session."""
    invite_code = session.pop("invite_code", None)
    if not invite_code:
        flash("An invitation code is required to register. Please use an invite link.", "warning")
        return False

    invite = db.execute(
        "SELECT * FROM invitations WHERE code = ? AND used_by IS NULL", (invite_code,)
    ).fetchone()
    if not invite:
        flash("Invalid or already-used invitation code.", "error")
        return False

    db.execute("INSERT INTO users (orcid_id, name) VALUES (?, ?)", (orcid_id, name))
    new_user = db.execute("SELECT * FROM users WHERE orcid_id = ?", (orcid_id,)).fetchone()
    db.execute(
        "UPDATE invitations SET used_by = ?, used_at = datetime('now') WHERE id = ?",
        (new_user["id"], invite["id"])
    )
    db.commit()
    return True


def _register_internal(db, orcid_id, name, access_token):
    """internal mode: a new user must match the host's allowlist. No invite."""
    is_admin = orcid_id in config.INTERNAL_ADMIN_ORCIDS
    allowed = is_admin or orcid_id in config.INTERNAL_ALLOWED_ORCIDS

    # Email-domain matching is best-effort: it only works when the user has made
    # an email public on ORCID. The ORCID allowlist is the reliable path.
    if not allowed and config.INTERNAL_ALLOWED_DOMAINS:
        email = _orcid_public_email(orcid_id, access_token)
        if email and email.rsplit("@", 1)[-1].lower() in config.INTERNAL_ALLOWED_DOMAINS:
            allowed = True

    if not allowed:
        flash("Your ORCID is not permitted on this instance. "
              "Ask the administrator to add you to the allowlist.", "error")
        return False

    db.execute(
        "INSERT INTO users (orcid_id, name, is_admin) VALUES (?, ?, ?)",
        (orcid_id, name, 1 if is_admin else 0),
    )
    db.commit()
    return True


def _orcid_public_email(orcid_id, access_token):
    """Return a public email from the ORCID record, or None. Best-effort."""
    if not access_token:
        return None
    try:
        r = requests.get(
            f"{config.ORCID_API_URL}/{orcid_id}/email",
            headers={"Authorization": f"Bearer {access_token}",
                     "Accept": "application/json"},
            timeout=10,
        )
        if r.ok:
            for e in r.json().get("email", []):
                if e.get("email"):
                    return e["email"]
    except Exception:
        pass
    return None


@bp.route("/logout")
@login_required
def logout():
    # Signing out of a local install is meaningless — the next request would sign
    # the same user straight back in — so say so rather than appear to no-op.
    if config.IS_LOCAL:
        flash("This is a local install; there is no account to sign out of.", "info")
        return redirect(url_for("index"))
    logout_user()
    return redirect(url_for("index"))


@bp.route("/invite/<code>")
def accept_invite(code):
    """Store invite code in session then redirect to ORCID login."""
    if config.IS_LOCAL:
        flash("Invitations only apply to the hosted community platform.", "info")
        return redirect(url_for("index"))
    db = get_db()
    invite = db.execute(
        "SELECT * FROM invitations WHERE code = ? AND used_by IS NULL", (code,)
    ).fetchone()
    if not invite:
        flash("This invitation link is invalid or has already been used.", "error")
        return redirect(url_for("index"))
    session["invite_code"] = code
    return redirect(url_for("auth.login"))
