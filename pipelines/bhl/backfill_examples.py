#!/usr/bin/env python3
"""Re-write a dataset's saved examples into its triplestore as RDF.

Needed after a QLever index swap: examples are stored both in SQLite and as RDF
(SPARQL Update, kept in the old index's update log), so a fresh bulk-built
index has none of the RDF copies. Idempotent (INSERT DATA of the same triples).
Run with the platform's venv from the repo root.
"""
import argparse, os, sqlite3, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

ap = argparse.ArgumentParser()
ap.add_argument("graph_base", help="dataset graph_base, e.g. https://koetai.semscape.org/u/<orcid>/bhl")
ap.add_argument("--dry-run", action="store_true")
args = ap.parse_args()

db = sqlite3.connect(os.path.join(ROOT, "db", "koetai.db"))
db.row_factory = sqlite3.Row
ds = db.execute("SELECT d.*, u.orcid_id FROM datasets d JOIN users u ON u.id = d.user_id "
                "WHERE d.graph_base = ?", (args.graph_base,)).fetchone()
if not ds:
    sys.exit(f"no dataset with graph_base {args.graph_base}")
rows = db.execute("SELECT * FROM examples WHERE dataset_id = ?", (ds["id"],)).fetchall()
print(f"{len(rows)} examples for {ds['slug']}")
if args.dry_run:
    sys.exit(0)

from routes.examples import _store_example_rdf
bad = 0
for ex in rows:
    ok = _store_example_rdf(dict(ds), ex["slug"], ex["label"], ex["description"] or "",
                            ex["query"], ex["keywords"] or "[]")
    if not (ok[0] if isinstance(ok, tuple) else ok):
        bad += 1
        print(f"  FAILED: {ex['slug']}: {ok}")
print(f"backfilled {len(rows) - bad}/{len(rows)}")
sys.exit(1 if bad else 0)
