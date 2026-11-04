#!/usr/bin/env python3
"""Replace a dataset's data with a new file, from the command line.

The same load as the upload page with "Replace existing data" ticked: it goes
through services/job_runner, so it works on every backend, applies OWL
reasoning if asked, unpacks archives, and is recorded as an upload job like any
other. Meant for datasets rebuilt by something already running on the server,
where opening a browser to re-upload is the only manual step left.

Run it where the app runs. Under Docker, scripts/replace-dataset.sh copies the
file into the container and calls this for you.

    python3 scripts/replace_dataset.py --slug col --file /data/incoming/taxa.nt.gz
    python3 scripts/replace_dataset.py --slug col --file more.ttl --append
    python3 scripts/replace_dataset.py --owner 0000-0001-2345-6789 --slug col --file taxa.nt

It deliberately does not import app.py: that marks every 'running' upload job
as interrupted on import, which is right for a starting server and wrong for a
command run beside one that may be mid-load.

The dataset must already exist — create it in the UI first. The file is left
where it is: the job loads a link or copy of it from the uploads directory.
"""
import argparse
import os
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from services import job_runner
from werkzeug.utils import secure_filename


def find_dataset(slug, owner):
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    sql = ("SELECT d.*, u.orcid_id FROM datasets d JOIN users u ON u.id = d.user_id "
           "WHERE d.slug = ?")
    args = [slug]
    if owner:
        sql += " AND u.orcid_id = ?"
        args.append(owner)
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    if not rows:
        sys.exit(f"no dataset '{slug}'" + (f" owned by {owner}" if owner else ""))
    if len(rows) > 1:
        owners = ", ".join(r["orcid_id"] for r in rows)
        sys.exit(f"'{slug}' is a slug of more than one owner ({owners}); pass --owner")
    return rows[0]


def stage(src: Path, user_id: int, slug: str) -> Path:
    """Put the file where uploads go, under the name the upload route would use.

    The job deletes its source once loaded (unless KEEP_UPLOADED_SOURCES), so it
    must not be handed the caller's own file. A hard link costs nothing on the
    same filesystem; a copy is the fallback across filesystems.
    """
    upload_dir = config.UPLOAD_DIR / str(user_id) / slug
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"{uuid.uuid4().hex}_{secure_filename(src.name)}"
    try:
        os.link(src, dest)
    except OSError:
        shutil.copyfile(src, dest)
    return dest


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--slug", required=True, help="the dataset's slug, as in its URL")
    p.add_argument("--file", required=True, type=Path, help="RDF file or archive to load")
    p.add_argument("--owner", help="owner's ORCID iD; needed only if the slug is ambiguous "
                                   "(defaults to the local user in local mode)")
    p.add_argument("--append", action="store_true",
                   help="add to the existing data instead of replacing it")
    p.add_argument("--owl", action="store_true", help="apply OWL/RDFS reasoning before loading")
    p.add_argument("--owl-regime", default="OWL_RL", help="reasoning regime (default OWL_RL)")
    p.add_argument("-q", "--quiet", action="store_true", help="don't print progress messages")
    a = p.parse_args()

    src = a.file.expanduser()
    if not src.is_file():
        sys.exit(f"no such file: {src}")
    ext = src.suffix.lower()
    if ext not in config.ALLOWED_UPLOAD_EXTENSIONS:
        sys.exit(f"unsupported format: {ext}")

    owner = a.owner or (config.LOCAL_ORCID if config.IS_LOCAL else None)
    ds = find_dataset(a.slug, owner)
    if ds["platform"] == "comunica":
        sys.exit(f"'{a.slug}' is a federation dataset — it has no store to load into")

    graph_uri = ds["graph_base"] + "/data"
    replace = not a.append
    print(f"dataset : {a.slug} ({ds['platform']}, owner {ds['orcid_id']})")
    print(f"graph   : {graph_uri}")
    print(f"file    : {src}")
    print(f"mode    : {'replace' if replace else 'append'}"
          + (f", {a.owl_regime} reasoning" if a.owl else ""))

    status = job_runner.run_now(
        dataset_id=ds["id"],
        user_id=ds["user_id"],
        file_path=stage(src, ds["user_id"], a.slug),
        graph_uri=graph_uri,
        apply_owl=a.owl,
        owl_regime=a.owl_regime,
        replace_data=replace,
        progress=None if a.quiet else (lambda msg: print(f"  {msg}", flush=True)),
    )
    print(f"{status['status']}: {status['message']}")
    sys.exit(0 if status["status"] == "done" else 1)


if __name__ == "__main__":
    main()
