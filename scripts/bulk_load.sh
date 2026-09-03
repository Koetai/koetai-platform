#!/usr/bin/env bash
# Load a large RDF file into an Oxigraph-backed dataset with Oxigraph's bulk
# loader instead of over HTTP.
#
# Why this exists: the app loads through the SPARQL Graph Store Protocol, which
# goes through the store's transactional path. Oxigraph's own loader does not,
# and the difference is not marginal — 2.4M triples measured at 67s over HTTP
# against 4s with the loader, about 17x. On a multi-gigabyte dump that is the
# difference between an afternoon and a coffee.
#
# Why it is a script you run, and not a button: the loader needs exclusive
# access to the database directory, so the Oxigraph server has to stop while it
# runs. The app has no way to stop it — they are separate containers, and giving
# the app control of the Docker socket to arrange that would hand it root on the
# host in exchange for a faster import. You are the operator; you can stop it.
#
#   scripts/bulk_load.sh --slug col --file ~/taxa.nt.gz
#   scripts/bulk_load.sh --slug col --file data.nt --graph-suffix /examples
#
# The dataset must already exist in Koetai — create it in the UI first, so its
# graph URI and platform are recorded. Uncompressed .nt/.ttl and gzipped
# versions of both are accepted; the gzip is streamed, not unpacked to disk.
set -euo pipefail

SLUG=""; FILE=""; SUFFIX="/data"; USER_ID="1"; SERVICE="oxigraph"; ASSUME_YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --slug)          SLUG="$2"; shift 2 ;;
    --file)          FILE="$2"; shift 2 ;;
    --graph-suffix)  SUFFIX="$2"; shift 2 ;;
    --user)          USER_ID="$2"; shift 2 ;;
    --service)       SERVICE="$2"; shift 2 ;;
    -y|--yes)        ASSUME_YES=1; shift ;;
    -h|--help)       sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$SLUG" ] && [ -n "$FILE" ] || { echo "usage: $0 --slug SLUG --file FILE" >&2; exit 2; }
[ -f "$FILE" ] || { echo "no such file: $FILE" >&2; exit 1; }

cd "$(dirname "$0")/.."

# The graph URI is the app's, not ours to invent: loading into a graph the app
# does not know about would put the data somewhere nothing ever queries.
read -r PLATFORM GRAPH_BASE <<EOF
$(docker compose exec -T koetai python -c "
import sqlite3, config, sys
c = sqlite3.connect(config.DB_PATH); c.row_factory = sqlite3.Row
r = c.execute('SELECT platform, graph_base FROM datasets WHERE slug=? AND user_id=?',
              ('$SLUG', $USER_ID)).fetchone()
if not r:
    sys.exit('no dataset with that slug')
print(r['platform'], r['graph_base'])" < /dev/null | tr -d '\r')
EOF
[ -n "${GRAPH_BASE:-}" ] || { echo "could not read the dataset from the app" >&2; exit 1; }

if [ "$PLATFORM" != "oxigraph" ]; then
  echo "dataset '$SLUG' is on '$PLATFORM'; this loader is Oxigraph's." >&2
  echo "Fuseki has its own (tdb2.tdbloader) and QLever builds an index offline." >&2
  exit 1
fi

GRAPH="${GRAPH_BASE}${SUFFIX}"
# --profile: the store may be behind one (oxigraph is), and `compose config`
# omits a profiled service entirely unless its profile is named.
COMPOSE_CFG="$(docker compose --profile "$SERVICE" config --format json 2>/dev/null)"
read -r VOLUME IMAGE <<EOF
$(printf '%s' "$COMPOSE_CFG" | python3 -c "
import json, sys
svc = json.load(sys.stdin)['services'].get('$SERVICE')
if not svc:
    sys.exit(\"service '$SERVICE' is not in this compose file\")
vols = [v for v in svc.get('volumes', []) if v.get('target') == '/data'] or svc.get('volumes', [])
if not vols:
    sys.exit(\"service '$SERVICE' has no volume to load into\")
print(vols[0]['source'], svc.get('image', ''))")
EOF
[ -n "${VOLUME:-}" ] || { echo "could not resolve the $SERVICE volume" >&2; exit 1; }

# compose reports the volume by its short name; Docker knows it by the project
# prefix. Ask the container what it actually has mounted — mounting the short
# name would silently create a new, empty volume and "load" into nothing.
REAL_VOLUME="$(docker inspect "$(docker compose ps -aq "$SERVICE" 2>/dev/null | head -1)" \
  --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' 2>/dev/null || true)"
if [ -z "$REAL_VOLUME" ]; then
  PROJECT="$(printf '%s' "$COMPOSE_CFG" | python3 -c "import json,sys; print(json.load(sys.stdin).get('name',''))")"
  REAL_VOLUME="${PROJECT:+${PROJECT}_}${VOLUME}"
fi
docker volume inspect "$REAL_VOLUME" >/dev/null 2>&1 || {
  echo "cannot find the Docker volume '$REAL_VOLUME' — has $SERVICE ever run?" >&2; exit 1; }
VOLUME="$REAL_VOLUME"

case "$FILE" in
  *.gz)  READER="gzip -dc"; FORMAT="${FILE%.gz}"  ;;
  *)     READER="cat";      FORMAT="$FILE"        ;;
esac
case "$FORMAT" in
  *.nt) FORMAT=nt ;; *.ttl) FORMAT=ttl ;; *.n3) FORMAT=ttl ;;
  *) echo "cannot tell the RDF format of $FILE — expected .nt or .ttl (optionally .gz)" >&2; exit 1 ;;
esac

echo "dataset : $SLUG  ($PLATFORM)"
echo "graph   : $GRAPH"
echo "file    : $FILE  ($(du -h "$FILE" | cut -f1), $FORMAT)"
echo "volume  : $VOLUME"
echo
echo "Oxigraph will be stopped while this runs — the endpoint is unavailable until it finishes."
if [ "$ASSUME_YES" -eq 0 ]; then
  printf "Continue? [y/N] "
  # from the terminal, not stdin: stdin may be the file being piped in
  if [ -r /dev/tty ]; then read -r reply < /dev/tty; else read -r reply || reply=""; fi
  case "$reply" in [yY]*) ;; *) echo "aborted"; exit 0 ;; esac
fi

echo "==> stopping $SERVICE"
docker compose stop "$SERVICE"

# The loader reads stdin when given no --file, so a gzip is streamed rather than
# unpacked to disk first — which for a multi-gigabyte dump is a copy avoided.
echo "==> loading (this is the fast part)"
set +e
$READER "$FILE" | docker run --rm -i -v "$VOLUME":/store --entrypoint /usr/local/bin/oxigraph \
  "$IMAGE" load --location /store --format "$FORMAT" --graph "$GRAPH"
STATUS=$?
set -e

echo "==> restarting $SERVICE"
docker compose start "$SERVICE"

if [ "$STATUS" -ne 0 ]; then
  echo "load failed (exit $STATUS); the store is back up but may not have the new data" >&2
  exit "$STATUS"
fi
echo "==> done. The dataset page will show the new count once it recounts."
