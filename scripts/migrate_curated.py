#!/usr/bin/env python3
"""Migrate a standalone QLever instance into the platform's named-graph model.

Koetai used to serve curated datasets as one QLever instance per dataset, each
on its own port, managed by hand. This loads those source files into the
platform as ordinary datasets — one named graph each — so they are queried,
described and administered like everything else, and the per-instance machinery
can go away.

Idempotent: re-running replaces each graph's contents rather than appending, so
a partial or failed run can simply be repeated.

    python3 scripts/migrate_curated.py --list
    python3 scripts/migrate_curated.py --dry-run
    python3 scripts/migrate_curated.py ordo-orphanet
    python3 scripts/migrate_curated.py            # all of them
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from services import triplestore

# What to migrate. `files` are relative to config.DEPLOY_DIR and load in order.
#
# ordo: the old Qleverfile indexed only Diseases_annotated_with_OFCO.ttl, so the
# 71MB ORDO_en_4.6.nt was never actually served. Both are included here, which
# makes this dataset the full ORDO release rather than a like-for-like copy of
# what endpoint2 returned.
CURATED = {
    "ordo-orphanet": {
        "owner_orcid": "0000-0001-9773-4008",
        "label": "ORDO — Orphanet Rare Disease Ontology",
        "description": (
            "Orphanet Rare Disease Ontology (ORDO) v4.6, with diseases annotated "
            "against OFCO. Previously served as a standalone QLever instance."
        ),
        "files": [
            "ordo/ORDO_en_4.6.nt",
            "ordo/Diseases_annotated_with_OFCO.ttl",
        ],
    },
    "ahri": {
        "owner_orcid": "0000-0003-2328-5208",
        "label": "AHRI — Africa Health Research Institute",
        "description": (
            "AHRI RDF: REDCap forms, OMERO imaging and Sanger microscopy data. "
            "Previously served as a standalone QLever instance."
        ),
        "files": [
            "ahri/all_ome_2025_09_23.nt",
            "ahri/sanger-rdf-20250819_cleaned.ttl",
            "ahri/AHRIrc_2025_09_17.ttl",
        ],
    },
}

BACKEND = "fuseki"


def _db():
    conn = sqlite3.connect(config.DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def _owner(conn, orcid):
    row = conn.execute("SELECT * FROM users WHERE orcid_id = ?", (orcid,)).fetchone()
    if row is None:
        raise SystemExit(
            f"No user with ORCID {orcid}. Curated datasets need an existing owner; "
            f"the ORCID is baked into the graph URI permanently."
        )
    return row


def ensure_dataset(conn, slug, spec):
    """Create the dataset row if absent. Returns the row."""
    owner = _owner(conn, spec["owner_orcid"])
    graph_base = f"{config.BASE_URL}/u/{owner['orcid_id']}/{slug}"
    with conn:
        conn.execute(
            """INSERT OR IGNORE INTO datasets
                   (user_id, slug, label, description, graph_base, platform, is_public)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (owner["id"], slug, spec["label"], spec["description"], graph_base, BACKEND),
        )
    return conn.execute("SELECT * FROM datasets WHERE slug = ?", (slug,)).fetchone()


def migrate(slug, spec, dry_run=False):
    conn = _db()
    owner = _owner(conn, spec["owner_orcid"])
    graph_base = f"{config.BASE_URL}/u/{owner['orcid_id']}/{slug}"
    graph = graph_base + "/data"

    print(f"\n{slug}")
    print(f"  owner   {spec['owner_orcid']}")
    print(f"  backend {BACKEND}")
    print(f"  graph   {graph}")

    paths = []
    for rel in spec["files"]:
        p = config.DEPLOY_DIR / rel
        if not p.exists():
            raise SystemExit(f"  missing source file: {p}")
        paths.append(p)
        print(f"  file    {rel}  ({p.stat().st_size / 1e6:.0f} MB)")

    # Check everything above before touching the database, so --dry-run really
    # does nothing: it is meant for confirming sources and owners.
    if dry_run:
        exists = conn.execute(
            "SELECT 1 FROM datasets WHERE slug = ?", (slug,)
        ).fetchone()
        print(f"  dataset row {'exists' if exists else 'would be created'}")
        print("  (dry run — nothing written)")
        return

    ds = ensure_dataset(conn, slug, spec)
    ts = triplestore.get(ds)

    # First file replaces the graph so re-runs don't double-load; the rest append.
    for i, p in enumerate(paths):
        verb = "replace" if i == 0 else "append "
        print(f"  {verb} {p.name} ...", end="", flush=True)
        if i == 0:
            ok, msg = ts.replace_graph(graph, p)
        else:
            ok, msg = ts.load_rdf_file(graph, p)
        if not ok:
            raise SystemExit(f" FAILED\n    {msg[:400]}")
        print(" ok")

    print(f"  triples {ts.count_triples(graph):,}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("datasets", nargs="*", help="slugs to migrate (default: all)")
    ap.add_argument("--list", action="store_true", help="show what would be migrated")
    ap.add_argument("--dry-run", action="store_true", help="check sources, load nothing")
    args = ap.parse_args()

    if args.list:
        for slug, spec in CURATED.items():
            print(f"{slug:16} owner={spec['owner_orcid']}  files={len(spec['files'])}")
        return

    slugs = args.datasets or list(CURATED)
    for slug in slugs:
        if slug not in CURATED:
            raise SystemExit(f"Unknown dataset {slug!r}. Known: {', '.join(CURATED)}")
        migrate(slug, CURATED[slug], dry_run=args.dry_run)


if __name__ == "__main__":
    main()
