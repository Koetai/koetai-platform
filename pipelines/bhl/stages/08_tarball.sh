#!/usr/bin/env bash
# Stage 08: tar the actual RDF data for sharing outside Koetai.
#
# Separate from stage 07's Zenodo bundle — that one is the pipeline (mapping,
# shape, query), for people who want to reproduce or adapt the process. This
# one is the data itself, for people like Rod Page who just want the RDF.
set -euo pipefail

WORKDIR="${1:?Usage: 08_tarball.sh <workdir> <index-name>}"
INDEX_NAME="${2:-bhl}"
RDF_DIR="$WORKDIR/rdf"
OUT="$WORKDIR/${INDEX_NAME}-rdf-$(date +%Y%m%d).tar"

echo "Bundling $RDF_DIR/*.nt.gz -> $OUT"
tar -cf "$OUT" -C "$RDF_DIR" $(cd "$RDF_DIR" && ls *.nt.gz)

echo "Stage 08 done."
echo "$OUT"
ls -lh "$OUT"
