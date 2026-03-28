"""SQLite connection helper."""
import sqlite3
from pathlib import Path
import config

def get_db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    schema = (Path(__file__).parent.parent / "db" / "schema.sql").read_text()
    with get_db() as conn:
        conn.executescript(schema)
