#!/usr/bin/env python3
"""Stage 04: reconcile dwc:scientificName strings to Wikidata taxa.

Batched by *distinct* name, not per-triple — BHL has ~211M page-name triples
(confirmed against the live production graph) but a far smaller distinct-name
set, and only that distinct set needs to hit Wikidata at all. Output:
dwc:scientificNameID triples (per user decision — a page isn't identical to
a taxon concept, so owl:sameAs would overclaim identity).
"""
import argparse
import gzip
import json
import sys
import time
from pathlib import Path

import requests

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"
QUERY_TEMPLATE = (ARTIFACTS_DIR / "reconcile-wikidata.rq").read_text()
WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"
# VALUES-clause size. Tested live against the real endpoint before settling
# here: 200 (the original guess) meant 20,747 batches for BHL's real distinct
# name count — ~29 hours at the observed per-batch pace. 5,000 batches
# confirmed reliably fast (11.8s); 10,000 and 20,000 both failed with gateway
# timeouts (502/504). Staying comfortably under the failure boundary rather
# than chasing the exact ceiling, since a batch that times out just becomes
# retry/backoff overhead instead of the speedup it was meant to be.
BATCH_SIZE = 5000
DWC_SCIENTIFIC_NAME = "http://rs.tdwg.org/dwc/terms/scientificName"
DWC_SCIENTIFIC_NAME_ID = "http://rs.tdwg.org/dwc/terms/scientificNameID"


def extract_distinct_names(rdf_dir: Path) -> set[str]:
    names = set()
    for f in rdf_dir.glob("pagenames_*.nt.gz"):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            for line in fh:
                if DWC_SCIENTIFIC_NAME not in line:
                    continue
                # <subj> <pred> "literal" .
                try:
                    lit = line.split('"', 1)[1].rsplit('"', 1)[0]
                except IndexError:
                    continue
                if lit:
                    names.add(lit)
    return names


def _escape(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', '\\"')


def reconcile_batch(names: list[str], retries: int = 3) -> dict[str, str]:
    """Return {name: wikidata_taxon_uri} for whatever this batch resolved."""
    values = " ".join(f'"{_escape(n)}"' for n in names)
    query = QUERY_TEMPLATE.replace("{{NAMES}}", values)

    for attempt in range(retries):
        try:
            # POST, not GET — confirmed live: a 200-name batch plus the
            # query template's own comment header pushed a GET's URL past
            # Wikidata's length limit (414 URI Too Long), crashing the
            # whole run on the very first batch. POST puts the query in
            # the body, which has no comparable length constraint.
            r = requests.post(
                WIKIDATA_ENDPOINT,
                data={"query": query, "format": "json"},
                headers={"Accept": "application/sparql-results+json",
                         "User-Agent": "bhl-pipeline/1.0 (reconciliation batch job)"},
                timeout=60,
            )
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 5 * (attempt + 1)))
                print(f"  rate-limited, waiting {wait}s...", flush=True)
                time.sleep(wait)
                continue
            r.raise_for_status()
            bindings = r.json()["results"]["bindings"]
            return {b["name"]["value"]: b["taxon"]["value"] for b in bindings}
        except requests.exceptions.RequestException as e:
            # Any other transient failure (timeout, 5xx, connection reset)
            # retries and eventually gives up on just this one batch rather
            # than crashing the whole multi-hour run — the same 414 crash
            # this was already fixed for is exactly the class of bug this
            # guards against happening again for a different underlying cause.
            print(f"  batch request failed ({e}), attempt {attempt + 1}/{retries}", flush=True)
            time.sleep(5 * (attempt + 1))
    print(f"  WARNING: batch failed after {retries} retries, skipping", flush=True)
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    args = ap.parse_args()

    rdf_dir = Path(args.workdir) / "rdf"
    print("Extracting distinct scientificName values...", flush=True)
    names = sorted(extract_distinct_names(rdf_dir))
    print(f"{len(names)} distinct names to reconcile against Wikidata.")

    # Incremental checkpointing: `names` is deterministic (sorted from the
    # same source files), so a restart can skip however many names a prior
    # run already got through, rather than re-querying Wikidata from zero.
    # Added after discovering this stage's real runtime is hours, not
    # minutes — losing that on any interruption would be a real cost, not
    # just an inconvenience.
    checkpoint_path = rdf_dir / "reconcile-checkpoint.json"
    resolved: dict[str, str] = {}
    start_index = 0
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text())
        resolved = checkpoint["resolved"]
        start_index = checkpoint["processed_count"]
        print(f"Resuming from checkpoint: {start_index} names already processed, "
              f"{len(resolved)} resolved so far.", flush=True)

    total_batches = (len(names) - 1) // BATCH_SIZE + 1
    for i in range(start_index, len(names), BATCH_SIZE):
        batch = names[i:i + BATCH_SIZE]
        print(f"  batch {i // BATCH_SIZE + 1}/{total_batches}...", flush=True)
        resolved.update(reconcile_batch(batch))
        time.sleep(1)  # be polite to Wikidata's public endpoint
        checkpoint_path.write_text(json.dumps({
            "resolved": resolved, "processed_count": i + len(batch),
        }))

    print(f"{len(resolved)} names resolved to a Wikidata taxon.")

    # Join back onto pagename triples, writing dwc:scientificNameID triples
    # for any page whose scientificName resolved.
    out_path = rdf_dir / "reconciliation.nt.gz"
    written = 0
    with gzip.open(out_path, "wt", encoding="utf-8") as out:
        for f in rdf_dir.glob("pagenames_*.nt.gz"):
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                for line in fh:
                    if DWC_SCIENTIFIC_NAME not in line:
                        continue
                    subj = line.split(" ", 1)[0]
                    try:
                        lit = line.split('"', 1)[1].rsplit('"', 1)[0]
                    except IndexError:
                        continue
                    taxon = resolved.get(lit)
                    if taxon:
                        out.write(f"{subj} <{DWC_SCIENTIFIC_NAME_ID}> <{taxon}> .\n")
                        written += 1

    checkpoint_path.unlink(missing_ok=True)
    print(f"Stage 04 done. {written} dwc:scientificNameID triples written to {out_path}")


if __name__ == "__main__":
    main()
