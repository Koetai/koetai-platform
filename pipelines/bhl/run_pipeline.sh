#!/usr/bin/env bash
# BHL -> RDF pipeline orchestrator.
#
# Generic by design: this script knows how to run numbered stages in order
# and stream their output back — it has no BHL-specific knowledge itself,
# that all lives in artifacts/ and stages/*. Adapting the pipeline means
# editing those, not this file.
#
# Does NOT provision or manage cloud infrastructure — assumes --host already
# has this machine's SSH key in its authorized_keys (the same manual setup
# used for every remote VM this session). Does NOT swap a verified build
# into production or delete the VM afterward — both are deliberate, separate,
# manually-confirmed actions; see the summary this script prints at the end.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: run_pipeline.sh --host <IP> [--from <stage>] [--graph <uri>] [--index-name <name>]

  --host <IP>        Target VM. Required. Must already have this machine's
                      SSH key in its authorized_keys (this script does not
                      provision infrastructure).
  --from <stage>      Resume from a specific stage (01-08). Default: 01.
                      Useful after fixing one artifact without repeating the
                      whole multi-hour run.
  --graph <uri>       Target named graph URI. Default: prompts.
  --index-name <name> QLever index name. Default: bhl.
USAGE
  exit 1
}

HOST=""
FROM="01"
GRAPH=""
INDEX_NAME="bhl"

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --from) FROM="$2"; shift 2 ;;
    --graph) GRAPH="$2"; shift 2 ;;
    --index-name) INDEX_NAME="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown argument: $1"; usage ;;
  esac
done

[ -z "$HOST" ] && { read -rp "Target VM IP (must already trust this machine's SSH key): " HOST; }
[ -z "$GRAPH" ] && { read -rp "Target named graph URI: " GRAPH; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE_DIR="~/bhl-pipeline-run"

echo "Copying artifacts and stages to $HOST..."
ssh "$HOST" "mkdir -p $REMOTE_DIR/artifacts $REMOTE_DIR/stages"
scp -q "$SCRIPT_DIR"/artifacts/* "$HOST:$REMOTE_DIR/artifacts/"
scp -q "$SCRIPT_DIR"/stages/* "$HOST:$REMOTE_DIR/stages/"
ssh "$HOST" "chmod +x $REMOTE_DIR/stages/*.sh $REMOTE_DIR/stages/*.py"

run_stage() {
  local num="$1"; shift
  echo
  echo "=== Stage $num ==="
  ssh "$HOST" "cd $REMOTE_DIR && $*"
}

# Zero-padded two-digit stage numbers, so lexicographic string comparison
# equals numeric comparison — "$FROM" <= "$1" decides whether to run.
should_run() { [ "$FROM" \< "$1" ] || [ "$FROM" = "$1" ]; }

# Python stages run under the venv provisioned on the target host (needs
# morph_kgc/requests/pyyaml; Debian trixie's PEP 668 enforcement means the
# system python3 can't have these installed directly).
PYTHON="~/bhl-venv/bin/python3"

if should_run 01; then run_stage 01 "bash stages/01_fetch.sh workdir"; fi
if should_run 02; then run_stage 02 "$PYTHON stages/02_materialize.py workdir"; fi
if should_run 03; then
  if ! run_stage 03 "bash stages/03_validate.sh workdir"; then
    echo "Stage 03 (validation gate) failed on the remote host — stopping. Fix artifacts/bhl-mapping.yarrrml.yml, then re-run with --from 02."
    exit 1
  fi
fi
if should_run 04; then run_stage 04 "$PYTHON stages/04_reconcile.py workdir"; fi
if should_run 05; then run_stage 05 "bash stages/05_index.sh workdir '$GRAPH' '$INDEX_NAME'"; fi

echo
echo "=== Stage 06: verify (needs stage 05's reported triple total) ==="
read -rp "Triple count reported by stage 05: " EXPECTED_COUNT
run_stage 06 "bash stages/06_verify.sh workdir '$GRAPH' '$INDEX_NAME' '$EXPECTED_COUNT'"

echo
read -rp "Stage 06 passed — run stage 07 (Zenodo draft)? [y/N] " do_zenodo
if [ "$do_zenodo" = "y" ]; then
  read -rsp "ZENODO_TOKEN: " ztoken; echo
  run_stage 07 "ZENODO_TOKEN='$ztoken' $PYTHON stages/07_package.py workdir"
fi

run_stage 08 "bash stages/08_tarball.sh workdir '$INDEX_NAME'"

cat <<EOF

=====================================================================
Pipeline complete. Nothing has touched production yet — the verified
build sits in $REMOTE_DIR/workdir/build/ on $HOST.

Remaining steps, all manual and explicit (same discipline as every
production swap this session):
  1. Review stage 06's spot-check output above.
  2. On the production host: stop qlever-platform.service, remove the
     stale platform.* files, move in the new ones from
     $HOST:$REMOTE_DIR/workdir/build/, restart, re-verify on the real
     port before calling it done.
  3. Once that's confirmed stable, $HOST is safe to delete — via Cyso
     Cloud's console, manually (this script has no cloud credentials
     and will not do this for you).
=====================================================================
EOF
