#!/usr/bin/env bash
# Replace a dataset's data from the host, when Koetai runs under Docker.
#
# A thin wrapper: copies the file into the koetai container's data volume and
# runs scripts/replace_dataset.py there, which does the actual load through the
# app's own upload job. Every other option is passed through unchanged.
#
#   scripts/replace-dataset.sh --slug col --file ~/taxa.nt.gz
#   scripts/replace-dataset.sh --slug col --file more.ttl --append
#   scripts/replace-dataset.sh --help
#
# Unlike scripts/bulk_load.sh this works on every backend and keeps the store
# up throughout; for a multi-gigabyte dump on Oxigraph the bulk loader is still
# the faster route.
set -euo pipefail
cd "$(dirname "$0")/.."

ARGS=(); FILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --file) FILE="$2"; shift 2 ;;
    *)      ARGS+=("$1"); shift ;;
  esac
done

if [ -z "$FILE" ]; then
  exec docker compose exec -T koetai python scripts/replace_dataset.py "${ARGS[@]}"
fi
[ -f "$FILE" ] || { echo "no such file: $FILE" >&2; exit 1; }

# Into the data volume, not the container's /tmp: the script then hard-links it
# into the uploads directory instead of copying a large file a second time.
INCOMING="/data/incoming/$(date +%s)-$$-$(basename "$FILE")"
docker compose exec -T koetai mkdir -p /data/incoming
docker compose cp "$FILE" "koetai:$INCOMING"
trap 'docker compose exec -T koetai rm -f "$INCOMING"' EXIT

docker compose exec -T koetai python scripts/replace_dataset.py --file "$INCOMING" "${ARGS[@]}"
