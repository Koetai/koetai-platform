"""SQLite connection helper."""
import sqlite3
from pathlib import Path
from flask import g
import config


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(config.DB_PATH, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        g.db = conn
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    schema = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode = WAL")
    with conn:
        conn.executescript(schema)
    conn.close()
    if config.IS_LOCAL:
        ensure_local_user()


def ensure_local_user():
    """Create the single user a local install acts as, if it isn't there yet.

    Idempotent: local installs have no sign-up flow, so this is the only way the
    row ever appears. It is an admin because on your own machine there is nobody
    else to administer the instance.
    """
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (orcid_id, name, is_admin) VALUES (?, ?, 1)",
            (config.LOCAL_ORCID, config.LOCAL_USER_NAME),
        )
    row = conn.execute(
        "SELECT * FROM users WHERE orcid_id = ?", (config.LOCAL_ORCID,)
    ).fetchone()
    conn.close()
    return row


def get_local_user_row():
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM users WHERE orcid_id = ?", (config.LOCAL_ORCID,)
    ).fetchone()
    conn.close()
    return row
