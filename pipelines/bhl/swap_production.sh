#!/usr/bin/env bash
# Swap a verified, bulk-built QLever index into production, with rollback.
# Usage: swap_production.sh <vm-host> <build-dir-on-vm> <graph-uri> <expected-count>
# Only ever run from bhl-refresh.sh --swap (passing that flag is the consent).
set -euo pipefail
HOST="$1"; BUILD_DIR="$2"; GRAPH="$3"; EXPECTED="$4"
DATA=/mnt/qlever-data; PORT=7030; SVC=qlever-platform.service
TS=$(date -u +%Y%m%d-%H%M%S)
NEW="$DATA/newindex-$TS"; OLD="$DATA/old-index-$TS"
SSH="ssh -o ServerAliveInterval=30 -o StrictHostKeyChecking=accept-new $HOST"
q() { curl -s -m "${2:-120}" -H "Accept: application/sparql-results+json" \
      --data-urlencode "query=$1" "http://127.0.0.1:$PORT/" \
      | python3 -c "import json,sys; print(json.load(sys.stdin)['results']['bindings'][0]['c']['value'])"; }

echo "--- guard: the live index must hold only the target graph ---"
# Total from the server's own stats (instant); a whole-store COUNT(*) exceeds
# the 30 s query timeout. The graph-scoped count can take ~26 s cold, so retry
# (the retry hits a warm cache).
live_total=$(curl -s -m 20 "http://127.0.0.1:$PORT/?cmd=stats" | python3 -c "import json,sys; print(json.load(sys.stdin)['num-triples-normal'])")
live_graph=""
for try in 1 2 3; do
  live_graph=$(q "SELECT (COUNT(*) AS ?c) WHERE { GRAPH <$GRAPH> { ?s ?p ?o } }" 2>/dev/null) && break || live_graph=""
done
[ -n "$live_graph" ] || { echo "REFUSING: could not count the live target graph"; exit 3; }
echo "live total=$live_total  in target graph=$live_graph"
if [ $((live_total - live_graph)) -gt 100000 ]; then
  echo "REFUSING: $((live_total - live_graph)) triples in other graphs would be lost by a whole-index swap."
  exit 3
fi

echo "--- space check ---"
need=$($SSH "du -sb $BUILD_DIR --exclude='*.nt.gz' --exclude=Qleverfile" | cut -f1)
free=$(df -B1 --output=avail "$DATA" | tail -1)
if [ "$free" -lt $((need + 2000000000)) ]; then
  echo "REFUSING: need $((need/1000000000)) GB free in $DATA, have $((free/1000000000)) GB."
  echo "Delete an old-index-* rollback directory you no longer need, then re-run --swap."
  exit 4
fi

echo "--- copy new index to $NEW (production untouched) ---"
mkdir -p "$NEW"
$SSH "cd $BUILD_DIR && tar -cf - platform.*" | tar -xf - -C "$NEW"
ls "$NEW"/platform.index.pso >/dev/null   # sanity: core file exists
[ "$(ls "$NEW" | wc -l)" -eq "$($SSH "ls $BUILD_DIR/platform.* | wc -l")" ] || { echo "file count mismatch"; exit 5; }

echo "--- swap (downtime starts) ---"
mkdir -p "$OLD"
sudo systemctl stop $SVC
( cd "$DATA" && mv platform.* "$OLD"/ && mv "$NEW"/platform.* . )
sudo systemctl start $SVC
rollback() {
  echo "!!! verification failed, rolling back"
  sudo systemctl stop $SVC
  ( cd "$DATA" && mkdir -p "failed-$TS" && mv platform.* "failed-$TS"/ && mv "$OLD"/platform.* . )
  sudo systemctl start $SVC
  exit 6
}
for i in $(seq 1 60); do curl -s -o /dev/null -m 3 "http://127.0.0.1:$PORT/" && break; sleep 2; done
echo "--- verify live (first query on a cold cache can be slow) ---"
got=$(q "SELECT (COUNT(*) AS ?c) WHERE { GRAPH <$GRAPH> { ?s ?p ?o } }" 300) || rollback
echo "expected=$EXPECTED  live=$got"
[ "$got" = "$EXPECTED" ] || rollback
rmdir "$NEW" 2>/dev/null || true

echo "--- restore examples RDF ---"
"$(dirname "$0")/../../venv/bin/python" "$(dirname "$0")/backfill_examples.py" "${GRAPH%/data}" || echo "WARNING: example backfill had failures"
systemctl is-active $SVC koetai-platform.service
echo "Swap complete. Rollback copy kept at $OLD (delete when satisfied)."
