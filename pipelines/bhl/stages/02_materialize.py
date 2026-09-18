#!/usr/bin/env python3
"""Stage 02: run the YARRRML mapping over BHL's TSVs, table by table.

Confirmed empirically against this pipeline's own mapping and this exact
Morph-KGC version: materialize() holds every triple in memory (a Python set,
then a full rdflib Graph) with no streaming-to-disk mode. At BHL's real scale
(500M+ triples) that doesn't fit. So this script bounds memory itself, the
same way today's production BHL load already proved out:
  - one Morph-KGC call per table (per mapping group), not all 15 at once.
  - the two genuinely huge tables (page.tsv, pagename.tsv), plus partpage.tsv,
    are additionally pre-split into row-chunks before Morph-KGC ever sees
    them, so even one table's single call stays memory-bounded.
  - each individual materialize call runs in its OWN fresh subprocess, not
    in-process here. Diagnosed live: repeated morph_kgc.materialize_set()
    calls within one long-lived Python process accumulate memory across
    calls (confirmed — 3 chunks completed healthily in ~2min each, then the
    4th stalled with the process at 11.2GB RSS on a 15GB box, 0 swap, and
    zero output progress for 30 minutes). A subprocess per call guarantees
    the OS fully reclaims that memory on exit, regardless of what Morph-KGC
    or its multiprocessing pool leaves behind internally.
  - every output path is checked before doing work, so a killed/resumed run
    never repeats a chunk that already completed.

Also runs the two post-processing fixes from artifacts/lookups.py — DOI
'%2F' unescaping and identifier-to-owl:sameAs resolution — as a streaming
line-by-line pass over each output file, not a whole-file load.
"""
import argparse
import gzip
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "artifacts"))
import lookups  # noqa: E402
import yaml  # noqa: E402

# Tables materialized whole, one Morph-KGC call each.
SIMPLE_MAPPINGS = [
    "items", "titles", "title_subjects", "creators", "title_creators",
    "creator_identifiers", "title_identifiers", "title_dois", "part_dois",
    "parts", "part_creator_entities", "part_creators", "part_identifiers",
]
# (mapping names sharing one source, source tsv basename, row-chunk size).
# Chunk sizes target ~20M triples/chunk (a wide margin under the 8M-triple
# case confirmed fast — 24.3s — once _run_one moved to a subprocess-per-call
# design), by dividing by each mapping's real triples-per-row rather than
# reusing one rows-per-chunk number across mappings with different fan-out.
CHUNKED_MAPPINGS = [
    (["pages"], "page", 3_300_000),           # 6 triples/row -> ~20M/chunk
    (["pagenames"], "pagename", 10_000_000),  # <=2 triples/row -> ~20M/chunk
    (["part_has_page", "part_page_position"], "partpage", 5_000_000),  # up to 4 triples/row -> ~20M/chunk
]

ARTIFACTS_DIR = Path(__file__).resolve().parent.parent / "artifacts"
MAPPING_FILE = ARTIFACTS_DIR / "bhl-mapping.yarrrml.yml"


def _worker(mapping_yaml: str, out_path: str):
    """Run in a fresh subprocess: one Morph-KGC call, write N-Triples, exit.

    Exiting is the memory-reclaim mechanism — nothing here needs to clean up
    after itself beyond the files it writes, because the whole process goes
    away and the OS reclaims everything, which is the point.
    """
    import morph_kgc

    out_path = Path(out_path)
    ini = out_path.parent / f".{out_path.stem}.config.ini"
    ini.write_text(f"[CONFIGURATION]\noutput_format = N-TRIPLES\n\n[DataSource]\nmappings = {mapping_yaml}\n")

    # materialize_set() (raw triple strings) instead of materialize() (which
    # wraps the same strings in a full rdflib Graph round-trip: join into one
    # big N-Quads blob, then graph.parse() it back). Diagnosed live: the
    # round-trip, not generation, was the original bottleneck — materialize_set()
    # alone stayed fast well past where materialize() hung (confirmed: 8M
    # triples, 180s+ hang with materialize() vs 24.3s with materialize_set()
    # + a direct write). Each returned string is "subj pred obj" with no
    # trailing terminator, added here on write.
    triples = morph_kgc.materialize_set(str(ini))
    with open(out_path, "w", encoding="utf-8") as f:
        for t in triples:
            f.write(t + " .\n")
    ini.unlink()
    print(f"__TRIPLE_COUNT__{len(triples)}")


def _run_one(mapping_name: str, doc: dict, out_path: Path) -> int:
    """Materialize a single mapping group via a scratch config, in a fresh
    subprocess. Returns the triple count parsed from the worker's stdout."""
    scratch = out_path.parent / f".{out_path.stem}.mapping.yaml"
    single = {"prefixes": doc["prefixes"], "mappings": {mapping_name: doc["mappings"][mapping_name]}}
    scratch.write_text(yaml.dump(single, allow_unicode=True, sort_keys=False))

    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--_worker", str(scratch), str(out_path)],
        capture_output=True, text=True,
    )
    scratch.unlink()
    if result.returncode != 0:
        raise RuntimeError(f"worker failed for {out_path.name}:\n{result.stderr[-4000:]}")

    for line in result.stdout.splitlines():
        if line.startswith("__TRIPLE_COUNT__"):
            return int(line[len("__TRIPLE_COUNT__"):])
    raise RuntimeError(f"worker for {out_path.name} produced no triple count:\n{result.stdout}\n{result.stderr}")


def _postprocess(nt_path: Path):
    """Stream-fix DOI encoding and add resolved-identifier owl:sameAs triples,
    writing a .gz alongside the original rather than rewriting it in place —
    a failure partway leaves the original untouched."""
    gz_path = nt_path.with_suffix(nt_path.suffix + ".gz")
    added = 0
    with open(nt_path, "r", encoding="utf-8") as src, gzip.open(gz_path, "wt", encoding="utf-8") as dst:
        for line in src:
            fixed = lookups.fix_doi_encoding(line)
            dst.write(fixed)
            # dcterms:identifier "Namespace:Value" -> add owl:sameAs if resolvable.
            if "http://purl.org/dc/terms/identifier" in fixed and '"' in fixed:
                subj = fixed.split(" ", 1)[0]
                lit = fixed.split('"', 1)[1].rsplit('"', 1)[0]
                if ":" in lit:
                    ns, _, val = lit.partition(":")
                    uri = lookups.identifier_to_uri(ns, val)
                    if uri:
                        dst.write(f"{subj} <http://www.w3.org/2002/07/owl#sameAs> <{uri}> .\n")
                        added += 1
    nt_path.unlink()
    return added


def _materialize_and_postprocess(mapping_name: str, doc: dict, out_dir: Path, tag: str) -> tuple[int, int]:
    """Skips work entirely if the final .nt.gz already exists — the resume
    safety net. A killed run's already-completed chunks are never repeated."""
    gz_path = out_dir / f"{tag}.nt.gz"
    if gz_path.exists():
        print(f"[{tag}] already done, skipping", flush=True)
        return 0, 0
    nt_path = out_dir / f"{tag}.nt"
    n = _run_one(mapping_name, doc, nt_path)
    resolved = _postprocess(nt_path)
    print(f"[{tag}] {n} triples, {resolved} identifiers resolved to owl:sameAs")
    return n, resolved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir", nargs="?", help="directory from stage 01 (contains tsv/)")
    ap.add_argument("--only", help="comma-separated mapping names, for re-running one table after an artifact fix")
    ap.add_argument("--_worker", nargs=2, metavar=("MAPPING_YAML", "OUT_PATH"), help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args._worker:
        _worker(*args._worker)
        return

    workdir = Path(args.workdir)
    tsv_dir = workdir / "tsv"
    out_dir = workdir / "rdf"
    out_dir.mkdir(exist_ok=True)

    doc = yaml.safe_load(MAPPING_FILE.read_text())
    # Absolute paths — Morph-KGC resolves `sources` against the process cwd,
    # not the mapping file's directory (same fix services/mapping_service.py
    # already applies for the same reason).
    for name, m in doc["mappings"].items():
        fname, kind = m["sources"][0][0].split("~")
        m["sources"] = [[str((tsv_dir / fname).resolve()) + "~" + kind]]

    only = set(args.only.split(",")) if args.only else None
    total_triples = total_resolved = 0

    for name in SIMPLE_MAPPINGS:
        if only and name not in only:
            continue
        n, resolved = _materialize_and_postprocess(name, doc, out_dir, name)
        total_triples += n
        total_resolved += resolved

    for names, tsv_base, chunk_rows in CHUNKED_MAPPINGS:
        names = [n for n in names if not only or n in only]
        if not names:
            continue
        src = tsv_dir / f"{tsv_base}.tsv"
        header = src.open(encoding="utf-8-sig").readline()
        chunk_dir = workdir / f"{tsv_base}_chunks"
        chunk_dir.mkdir(exist_ok=True)

        # Split into row-chunks, header repeated on each (mirrors today's
        # production BHL load, which used the same page/pagename chunking).
        # Always re-split (cheap relative to materializing) rather than trust
        # stale chunk files from a differently-sized previous attempt.
        chunk_paths = []
        with open(src, encoding="utf-8-sig") as f:
            f.readline()  # skip header, already captured
            idx, out_f, count = 0, None, 0
            for line in f:
                if out_f is None or count >= chunk_rows:
                    if out_f:
                        out_f.close()
                    idx += 1
                    count = 0
                    chunk_path = chunk_dir / f"{tsv_base}_{idx:03d}.tsv"
                    out_f = open(chunk_path, "w", encoding="utf-8")
                    out_f.write(header)
                    chunk_paths.append(chunk_path)
                out_f.write(line)
                count += 1
            if out_f:
                out_f.close()

        for i, chunk_path in enumerate(chunk_paths, 1):
            for name in names:
                tag = f"{name}_{i:03d}"
                chunk_doc = {
                    "prefixes": doc["prefixes"],
                    "mappings": {name: {**doc["mappings"][name], "sources": [[f"{chunk_path.resolve()}~csv"]]}},
                }
                print(f"[{tag}] chunk {i}/{len(chunk_paths)} ({chunk_path.name})...", flush=True)
                n, resolved = _materialize_and_postprocess(name, chunk_doc, out_dir, tag)
                total_triples += n
                total_resolved += resolved

    print(f"\nStage 02 done. {total_triples} triples materialized this run, {total_resolved} identifiers resolved.")
    print(f"Output: {out_dir}/*.nt.gz")


if __name__ == "__main__":
    main()
