#!/usr/bin/env python3
"""Bring an existing koetai.db up to the multi-backend schema.

Two changes to the `datasets` table:
  1. widen the `platform` CHECK from ('qlever','fuseki') to every supported
     backend — without this, the multi-backend registry can resolve a store the
     database will then refuse to store a row for;
  2. add a nullable `sources` column for comunica federation datasets.

SQLite cannot ALTER a CHECK constraint, so the table is rebuilt. Idempotent: it
detects an already-migrated database and does nothing. Run with the app's config
so it targets the same DB:

    python3 scripts/migrate_backends_schema.py            # migrate config.DB_PATH
    python3 scripts/migrate_backends_schema.py --db PATH  # or an explicit file
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config

NEW_DATASETS = """
CREATE TABLE datasets_new (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    slug        TEXT    NOT NULL,
    label       TEXT    NOT NULL,
    description TEXT,
    graph_base  TEXT    NOT NULL UNIQUE,
    port        INTEGER,
    platform    TEXT    NOT NULL DEFAULT 'qlever'
                CHECK(platform IN ('qlever','fuseki','virtuoso','oxigraph',
                                   'blazegraph','rdf4j','comunica')),
    sources     TEXT,
    is_public    INTEGER NOT NULL DEFAULT 1,
    fdp_license  TEXT    NOT NULL DEFAULT 'https://creativecommons.org/licenses/by/4.0/',
    fdp_version  TEXT    NOT NULL DEFAULT '1.0',
    fdp_keywords TEXT    NOT NULL DEFAULT '',
    fdp_theme    TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, slug)
);
"""

# Columns carried over from the old table (everything except the new `sources`).
CARRY = ("id, user_id, slug, label, description, graph_base, port, platform, "
         "is_public, fdp_license, fdp_version, fdp_keywords, fdp_theme, created_at")


def already_migrated(conn) -> bool:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(datasets)")]
    return "sources" in cols


def migrate(db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if already_migrated(conn):
        print(f"  {db_path}: already migrated (has `sources` column) — nothing to do")
        return

    before = conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]

    # SQLite table-rebuild dance (per the SQLite docs' recommended order):
    # build the replacement under a TEMP name, copy, drop the original, then
    # rename the replacement into place. Doing it this way — rather than renaming
    # the original out of the way first — is essential: a modern SQLite
    # `ALTER TABLE datasets RENAME ...` silently rewrites the FK references in
    # every child table to follow the rename, which would leave them pointing at
    # a table we then drop. Renaming datasets_new (which nothing references) is
    # safe. FKs off so the DROP of the referenced table is allowed.
    conn.executescript("PRAGMA foreign_keys=OFF;")
    with conn:
        conn.executescript(NEW_DATASETS)
        conn.execute(f"INSERT INTO datasets_new ({CARRY}) SELECT {CARRY} FROM datasets;")
        conn.execute("DROP TABLE datasets;")
        conn.execute("ALTER TABLE datasets_new RENAME TO datasets;")
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.executescript("PRAGMA foreign_keys=ON;")

    after = conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
    if before != after:
        raise SystemExit(f"  ROW COUNT CHANGED {before} -> {after}; aborting (DB left as-is)")
    if fk:
        raise SystemExit(f"  FOREIGN KEY violations after rebuild: {fk}")
    print(f"  {db_path}: migrated OK — {after} datasets preserved, `sources` added, "
          f"platform CHECK widened")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB_PATH), help="path to koetai.db")
    args = ap.parse_args()
    migrate(Path(args.db))


if __name__ == "__main__":
    main()
