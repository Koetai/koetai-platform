#!/usr/bin/env bash
# Stage 06: verify on a temp port before any production swap.
#
# Non-negotiable per this session's own established discipline: the first
# real BHL bulk build technically succeeded but was unusable (missing graph
# context, caught only here). Never swap an unverified build into
# production — this stage is what makes that catchable every time.
set -euo pipefail

WORKDIR="${1:?Usage: 06_verify.sh <workdir> <graph-uri> <index-name> <expected-triple-count>}"
GRAPH_URI="${2:?}"
INDEX_NAME="${3:-bhl}"
EXPECTED_COUNT="${4:?expected total triple count, from stage 05s reported total}"

BUILD_DIR="${BUILD_DIR:-$WORKDIR/build}"
cd "$BUILD_DIR"

# QLever's tooling moved from separate qlever-server/qlever-index binaries to
# subcommands of one `qlever` CLI (confirmed on the actual VM this runs on —
# 0.6.0 here vs 0.5.44 on production). `qlever start` reads PORT/ACCESS_TOKEN/
# MEMORY_FOR_QUERIES from the Qleverfile stage 05 already wrote, backgrounds
# itself via nohup, so no manual `&`/PID/trap bookkeeping is needed — `qlever
# stop` is the correct shutdown instead of killing a PID directly.
echo "Starting temp qlever server on :7031 ..."
qlever start --description "bhl-pipeline verification (temp port)"
trap "qlever stop 2>/dev/null || true" EXIT

for i in $(seq 1 30); do
  curl -s -o /dev/null "http://localhost:7031/" && break
  sleep 1
done

echo
echo "=== graph-scoped count (must exactly match stage 05's reported total) ==="
actual=$(curl -s -H "Accept: application/sparql-results+json" \
  --data-urlencode "query=SELECT (COUNT(*) AS ?c) WHERE { GRAPH <$GRAPH_URI> { ?s ?p ?o } }" \
  "http://localhost:7031/" | python3 -c "import json,sys; print(json.load(sys.stdin)['results']['bindings'][0]['c']['value'])")

echo "expected: $EXPECTED_COUNT"
echo "actual:   $actual"

if [ "$actual" != "$EXPECTED_COUNT" ]; then
  echo
  echo "FAIL: triple count mismatch — this is exactly the missing-graph-context"
  echo "bug from earlier this session. Do NOT swap into production."
  exit 1
fi

echo
echo "=== spot-check: a real species query (Gadus morhua, per this session's earlier findings) ==="
SPOT_QUERY="PREFIX dwc: <http://rs.tdwg.org/dwc/terms/>
SELECT (COUNT(*) AS ?c) WHERE { GRAPH <${GRAPH_URI}> { ?page dwc:scientificName 'Gadus morhua' } }"
curl -s -H "Accept: application/sparql-results+json" \
  --data-urlencode "query=${SPOT_QUERY}" \
  "http://localhost:7031/"
echo
echo "(expect a real, nonzero count)"

echo
echo "Stage 06 passed count verification. Review the spot-check above, then confirm the swap:"
echo "  Old (stale) files to remove from the live data dir, new files to move in from $BUILD_DIR"
echo "  This script does NOT touch production itself — swap is a separate, explicit, confirmed step."
