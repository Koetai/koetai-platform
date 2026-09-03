#!/usr/bin/env python3
"""Remove upload directories that no dataset owns any more.

Deleting a dataset now removes its files (routes/datasets.py), but anything
deleted before that did not, and a failed upload used to leave its file behind
with no job left to clean it. Those directories are unreferenced: nothing in the
database points at them and no page lists them, so they simply accumulate.

A directory is orphaned when <UPLOAD_DIR>/<user_id>/<slug> has no matching
datasets row for that user and slug. Everything else is left alone — including
the source files of live datasets, which are kept or not according to
KEEP_UPLOADED_SOURCES.

    python3 scripts/prune_uploads.py            # report only
    python3 scripts/prune_uploads.py --delete   # actually remove them
    python3 scripts/prune_uploads.py --db PATH  # against another database
"""
import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def find_orphans(db_path: Path, upload_dir: Path) -> list[Path]:
    conn = sqlite3.connect(db_path, timeout=10)
    owned = {(str(uid), slug) for uid, slug in
             conn.execute("SELECT user_id, slug FROM datasets")}
    conn.close()

    orphans = []
    if not upload_dir.is_dir():
        return orphans
    for user_dir in sorted(upload_dir.iterdir()):
        if not user_dir.is_dir():
            continue
        for slug_dir in sorted(user_dir.iterdir()):
            if slug_dir.is_dir() and (user_dir.name, slug_dir.name) not in owned:
                orphans.append(slug_dir)
    return orphans


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--delete", action="store_true",
                    help="remove the directories (otherwise just report)")
    ap.add_argument("--db", type=Path, default=config.DB_PATH)
    ap.add_argument("--uploads", type=Path, default=config.UPLOAD_DIR)
    args = ap.parse_args()

    orphans = find_orphans(args.db, args.uploads)
    if not orphans:
        print(f"No orphaned upload directories under {args.uploads}")
        return

    total = 0
    for d in orphans:
        size = _dir_size(d)
        total += size
        print(f"  {'removing' if args.delete else 'orphaned'}  {d}  ({_human(size)})")
        if args.delete:
            shutil.rmtree(d, ignore_errors=True)

    verb = "Removed" if args.delete else "Would remove"
    print(f"{verb} {len(orphans)} directory(ies), {_human(total)}")
    if not args.delete:
        print("Re-run with --delete to remove them.")


if __name__ == "__main__":
    main()
