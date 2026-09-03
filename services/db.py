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


# Columns added to existing tables after the first release. CREATE TABLE IF NOT
# EXISTS leaves an existing table alone, so a new column in schema.sql would
# never reach a database that already has one. Adding a nullable column is safe
# and reversible, so it runs at startup rather than as a migration script.
_ADDED_COLUMNS = {
    "upload_jobs": [
        ("source_url", "TEXT"),
        ("source_label", "TEXT"),
        ("web_source_file_id", "INTEGER"),
    ],
}


def _add_missing_columns(conn):
    for table, columns in _ADDED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue                      # table absent; schema.sql will create it
        for name, decl in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_db():
    schema = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode = WAL")
    with conn:
        conn.executescript(schema)
        _add_missing_columns(conn)
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
