#!/usr/bin/env python3
"""Stage 07: bundle the semantic artifacts and create a Zenodo DRAFT deposition.

Per user decision: draft only, via Zenodo's API, never auto-published — a
published deposition gets a permanent, citable DOI that isn't easily undone.
This stage stops after creating the draft; publishing is manual, in Zenodo's
own web UI. Requires ZENODO_TOKEN in the environment — deliberately not
defaulted or invented here; if it's missing this stage explains what to set
and exits, rather than silently skipping Zenodo.
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import requests

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"
ZENODO_API = "https://zenodo.org/api/deposit/depositions"


def build_provenance(workdir: Path, git_commit: str, dump_checksum: str) -> str:
    template = (ARTIFACTS_DIR / "provenance.ttl.j2").read_text()
    return (
        template.replace("{{RUN_DATE}}", date.today().isoformat())
        .replace("{{GIT_COMMIT}}", git_commit)
        .replace("{{DUMP_CHECKSUM}}", dump_checksum)
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--git-commit", default="unknown")
    ap.add_argument("--dump-checksum", default="unknown", help="sha256 of data.zip, for provenance")
    args = ap.parse_args()

    token = os.environ.get("ZENODO_TOKEN")
    if not token:
        print("ZENODO_TOKEN not set. Set it to a Zenodo personal access token "
              "(zenodo.org -> Applications -> Personal access tokens) and re-run this stage.\n"
              "Nothing was sent to Zenodo.")
        sys.exit(1)

    workdir = Path(args.workdir)
    provenance = build_provenance(workdir, args.git_commit, args.dump_checksum)
    (workdir / "provenance.ttl").write_text(provenance)

    auth = {"Authorization": f"Bearer {token}"}

    print("Creating Zenodo draft deposition...")
    r = requests.post(ZENODO_API, headers=auth, json={}, timeout=30)
    r.raise_for_status()
    deposition = r.json()
    dep_id = deposition["id"]
    bucket_url = deposition["links"]["bucket"]

    metadata = {
        "metadata": {
            "title": "BHL-to-RDF pipeline artifacts (Koetai)",
            "upload_type": "software",
            "description": (
                "Reproducible RML/YARRRML mapping, ShEx validation shape, and "
                "Wikidata reconciliation query used to materialize the "
                "Biodiversity Heritage Library TSV dump into RDF for Koetai. "
                "Draft deposition — review before publishing."
            ),
            "creators": [{"name": "Koetai"}],
        }
    }
    requests.put(f"{ZENODO_API}/{dep_id}", headers=auth,
                 json=metadata, timeout=30).raise_for_status()

    for f in [ARTIFACTS_DIR / "bhl-mapping.yarrrml.yml", ARTIFACTS_DIR / "bhl-shape.shex",
              ARTIFACTS_DIR / "reconcile-wikidata.rq", ARTIFACTS_DIR / "lookups.py",
              workdir / "provenance.ttl"]:
        print(f"  uploading {f.name}...")
        with open(f, "rb") as fh:
            requests.put(f"{bucket_url}/{f.name}", headers=auth,
                         data=fh, timeout=120).raise_for_status()

    print(f"\nStage 07 done. Draft deposition created: {deposition['links']['html']}")
    print("NOT published — review and click Publish yourself in Zenodo's UI when ready.")


if __name__ == "__main__":
    main()
