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
