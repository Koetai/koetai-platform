#!/usr/bin/env bash
# One command: rebuild the BHL RDF from the latest BHL dump on a throwaway VM.
#
#   ./bhl-refresh.sh --host <VM-IP>            # build, verify, pull RDF back
#   ./bhl-refresh.sh --host <VM-IP> --swap     # ...and swap it into production
#
# Everything else is derived. The only unavoidable input is the VM address
# (this script has no cloud credentials, so it cannot create/delete a VM).
# VM needs: Debian 13, your SSH key, sudo, ~8 GB RAM, a >=100 GB empty extra
# volume (formatted automatically). Optional: ZENODO_TOKEN=... in the
# environment also creates an UNPUBLISHED Zenodo draft (publishing stays manual).
#
# Safe to interrupt and re-run: the VM-side run is detached and resumes from
# the last finished stage; re-running attaches to a run already in progress.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
HOST=""; SWAP=0; DRY=0
GRAPH="${BHL_GRAPH:-}"; INDEX_NAME="platform"
EXPORTS="${BHL_EXPORTS:-/mnt/qlever-data/exports}"

while [ $# -gt 0 ]; do case "$1" in
  --host) HOST="$2"; shift 2 ;;
  --swap) SWAP=1; shift ;;
  --dry-run) DRY=1; shift ;;
  --graph) GRAPH="$2"; shift 2 ;;
  -h|--help) sed -n 2,14p "$0"; exit 0 ;;
  *) echo "unknown argument $1"; exit 1 ;;
esac; done
[ -n "$HOST" ] || { echo "usage: $0 --host <VM-IP> [--swap] [--dry-run]"; exit 1; }

ts() { date -u +"[%Y-%m-%d %H:%M:%S UTC]"; }
SSH_OPTS=(-o ServerAliveInterval=30 -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new)
rssh() { ssh "${SSH_OPTS[@]}" "$HOST" "$@"; }

# Graph URI defaults to the registered bhl dataset's graph (no need to type it).
if [ -z "$GRAPH" ]; then
  base=$(sqlite3 "$REPO/db/koetai.db" "select graph_base from datasets where slug='bhl' limit 1")
  [ -n "$base" ] || { echo "no 'bhl' dataset found; pass --graph"; exit 1; }
  GRAPH="$base/data"
fi

R='~/bhl-pipeline-run'
echo "$(ts) host=$HOST graph=$GRAPH index=$INDEX_NAME swap=$SWAP"
rssh "echo ssh-ok" >/dev/null || { echo "cannot ssh to $HOST (key authorized? VM up?)"; exit 1; }
for f in /usr/bin/rudof /usr/bin/qlever-index /usr/bin/qlever-server; do [ -x "$f" ] || { echo "missing $f locally"; exit 1; }; done
mkdir -p "$EXPORTS"
if [ "$DRY" = 1 ]; then echo "dry run: preflight ok, nothing started"; exit 0; fi

echo "$(ts) uploading pipeline + production binaries..."
rssh "mkdir -p $R/artifacts $R/stages ~/bhl-bin"
scp -q "${SSH_OPTS[@]}" "$HERE"/artifacts/* "$HOST:$R/artifacts/"
scp -q "${SSH_OPTS[@]}" "$HERE"/stages/* "$HOST:$R/stages/"
# skip re-uploading ~135 MB of binaries when already present
rssh "test -x ~/bhl-bin/qlever-server" || scp -q "${SSH_OPTS[@]}" /usr/bin/rudof /usr/bin/qlever-index /usr/bin/qlever-server "$HOST:~/bhl-bin/"
if [ -n "${ZENODO_TOKEN:-}" ]; then
  printf '%s' "$ZENODO_TOKEN" | rssh "umask 077; cat > ~/.zenodo_token"
fi

echo "$(ts) bootstrapping VM..."
VOL=$(rssh "bash $R/stages/00_bootstrap.sh" | tail -1)
[ -n "$VOL" ] || { echo "bootstrap failed"; exit 1; }
WORKDIR="$VOL/workdir"; BUILD_DIR="$VOL/build"
echo "$(ts) working volume: $VOL"

# Start detached, unless a run is already alive (then just attach to it).
if rssh "test -f $R/run.pid && kill -0 \$(cat $R/run.pid) 2>/dev/null"; then
  echo "$(ts) run already in progress on the VM, attaching"
else
  rssh -n "cd $R && rm -f status && WORKDIR=$WORKDIR BUILD_DIR=$BUILD_DIR GRAPH='$GRAPH' INDEX_NAME=$INDEX_NAME \
    nohup setsid bash stages/remote_run.sh >> run.log 2>&1 < /dev/null & echo \$! > $R/run.pid; sleep 1; echo started" >/dev/null
  echo "$(ts) pipeline started on the VM (detached; safe to Ctrl-C and re-run to re-attach)"
fi

# Poll. Tolerates ssh hiccups; stops on done/failed.
fails=0; last=""
while :; do
  if out=$(rssh "cat $R/status 2>/dev/null; echo; tail -n 1 $R/run.log | cut -c1-160" 2>/dev/null); then
    fails=0
    st=$(echo "$out" | head -1); line=$(echo "$out" | tail -1)
    [ "$st $line" != "$last" ] && echo "$(ts) $st | $line"; last="$st $line"
    case "$st" in
      done) break ;;
      failed*) echo "$(ts) FAILED ($st). Log tail:"; rssh "tail -n 40 $R/run.log"
               echo "Fix the cause, then re-run the same command: finished stages are skipped."; exit 1 ;;
    esac
  else
    fails=$((fails+1)); [ "$fails" -ge 30 ] && { echo "VM unreachable for 1h, giving up (run continues on VM; re-run to re-attach)"; exit 1; }
  fi
  sleep 120
done

echo "$(ts) pulling RDF tarball back..."
DEST="$EXPORTS/$(date -u +%Y%m%d)"; mkdir -p "$DEST/run-logs"
TAR=$(rssh "ls $WORKDIR/bhl-rdf-*.tar | tail -1")
scp -q "${SSH_OPTS[@]}" "$HOST:$TAR" "$HOST:$WORKDIR/bhl-rdf.tar.sha256" "$DEST/"
scp -q "${SSH_OPTS[@]}" "$HOST:$R/run.log" "$DEST/run-logs/" || true
scp -q "${SSH_OPTS[@]}" "$HOST:$BUILD_DIR/*index-log.txt" "$DEST/run-logs/" || true
( cd "$DEST" && sha256sum -c bhl-rdf.tar.sha256 ) || { echo "CHECKSUM MISMATCH — do not delete the VM"; exit 1; }
EXPECTED=$(rssh "cat $WORKDIR/expected-count")
echo "$(ts) RDF verified and stored in $DEST ($EXPECTED triples)"

if [ "$SWAP" = 1 ]; then
  echo "$(ts) --swap given: swapping into production"
  bash "$HERE/swap_production.sh" "$HOST" "$BUILD_DIR" "$GRAPH" "$EXPECTED"
fi

echo
echo "$(ts) ALL DONE."
echo "RDF secured at: $DEST"
[ "$SWAP" = 1 ] || echo "Production NOT changed (re-run with --swap to apply; it re-uses the finished build)."
echo "The VM $HOST is now safe to delete in your cloud console."
