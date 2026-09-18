#!/usr/bin/env bash
# Runs ON the VM, detached (started by bhl-refresh.sh). Runs stages 01-08 in
# order; each finished stage leaves a marker in $WORKDIR/.done, so starting it
# again after any interruption resumes instead of repeating hours of work.
# Env: WORKDIR BUILD_DIR GRAPH INDEX_NAME  (+ optional ~/.zenodo_token)
set -uo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
cd "$R"
export PATH="$HOME/bhl-venv/bin:$HOME/bhl-bin:$PATH"
export QLEVER_BIN_DIR="$HOME/bhl-bin" BUILD_DIR
PY="$HOME/bhl-venv/bin/python3"
mkdir -p "$WORKDIR/.done"

trap 'echo "failed $CUR" > "$R/status"' ERR
set -e
stage() {  # stage <NN> <command...>
  CUR="$1"; shift
  if [ -e "$WORKDIR/.done/$CUR" ]; then echo "[stage $CUR] already done, skipping"; return; fi
  echo "running $CUR" > "$R/status"
  echo "=== [$(date -u +%FT%TZ)] stage $CUR ==="
  "$@"
  touch "$WORKDIR/.done/$CUR"
}

stage 01 bash stages/01_fetch.sh "$WORKDIR"
stage 02 "$PY" stages/02_materialize.py "$WORKDIR"
stage 03 bash stages/03_validate.sh "$WORKDIR"
stage 04 "$PY" stages/04_reconcile.py "$WORKDIR"
stage 05 bash stages/05_index.sh "$WORKDIR" "$GRAPH" "$INDEX_NAME"

# Expected triple count comes from the index build's own log — no manual input.
EXPECTED=$(grep -h "Statistics for PSO" "$BUILD_DIR"/*index-log.txt | tail -1 | sed 's/.*#triples = //; s/,//g')
[ -n "$EXPECTED" ] || { echo "cannot read triple count from index log"; false; }
echo "$EXPECTED" > "$WORKDIR/expected-count"
stage 06 bash stages/06_verify.sh "$WORKDIR" "$GRAPH" "$INDEX_NAME" "$EXPECTED"

if [ -s "$HOME/.zenodo_token" ]; then
  stage 07 env ZENODO_TOKEN="$(cat "$HOME/.zenodo_token")" "$PY" stages/07_package.py "$WORKDIR"
fi
stage 08 bash stages/08_tarball.sh "$WORKDIR" bhl
( cd "$WORKDIR" && sha256sum bhl-rdf-*.tar | tee bhl-rdf.tar.sha256 )
echo "done" > "$R/status"
