#!/usr/bin/env bash
# Stage 01: download BHL's dump and prepare it for Morph-KGC.
#
# Two things this pipeline needs that the raw dump doesn't give directly:
#   - Morph-KGC's TSV auto-detection keys off extension (confirmed by testing
#     this pipeline's own mapping: reads header/first rows, infers the tab
#     delimiter itself, but only when the source is named *.tsv) — BHL ships
#     *.txt, so every table gets copied to a .tsv name, not moved (original
#     kept as the untouched source of truth).
#   - doi.txt covers both titles and parts via an EntityType column;
#     bhl-mapping.yarrrml.yml relies on that already being split into two
#     files (doi_title.tsv / doi_part.tsv) rather than filtering rows itself.
set -euo pipefail

WORKDIR="${1:?Usage: 01_fetch.sh <workdir>}"
mkdir -p "$WORKDIR"
cd "$WORKDIR"

DUMP_URL="https://www.biodiversitylibrary.org/data/data.zip"
echo "Downloading $DUMP_URL ..."
curl -sL -o data.zip "$DUMP_URL"

echo "Extracting ..."
unzip -o -q data.zip -d raw

echo "Renaming *.txt -> *.tsv (originals kept in raw/) ..."
mkdir -p tsv
for f in raw/Data/*.txt; do
  base="$(basename "$f" .txt)"
  cp "$f" "tsv/${base}.tsv"
done

echo "Splitting doi.tsv by EntityType ..."
python3 - "tsv/doi.tsv" "tsv" <<'PY'
import sys, csv
src, outdir = sys.argv[1], sys.argv[2]
with open(src, encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f, delimiter="\t")
    fieldnames = reader.fieldnames
    title_rows, part_rows = [], []
    for row in reader:
        (title_rows if row["EntityType"] == "Title" else part_rows).append(row)

for name, rows in [("doi_title.tsv", title_rows), ("doi_part.tsv", part_rows)]:
    with open(f"{outdir}/{name}", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        w.writeheader()
        w.writerows(rows)
print(f"doi_title.tsv: {len(title_rows)} rows, doi_part.tsv: {len(part_rows)} rows")
PY

echo "Stage 01 done. Sources in $(pwd)/tsv/"
