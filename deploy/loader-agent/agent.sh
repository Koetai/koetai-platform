#!/bin/sh
# Loader agent — the one privileged thing in this deployment.
#
# It exists so the web app can trigger a bulk load without itself holding the
# Docker socket. Loading with the store's own loader is roughly 17x faster than
# going through the SPARQL Graph Store Protocol, but the loader needs exclusive
# access to the database directory, so something has to stop the store. If the
# app did that, the app would need the socket — and an app with the socket is an
# app with root on the host.
#
# So the capability lives here instead, and is deliberately narrow:
#
#   * the container to stop, the volume to load into and the loader image are
#     configuration, not part of a request. A request cannot name a different
#     container, mount a different volume, or run a different image;
#   * a request's file must resolve inside ALLOWED_ROOT, so it can only load
#     what the app has already written to its own uploads directory;
#   * the only verbs are stop, run the loader, start. There is no path here that
#     runs an arbitrary command.
#
# The worst a compromised app can do through this agent is load a file it has
# already written into a graph, and cause the store to restart. That is a much
# smaller thing than the socket itself.
set -eu

REQ_DIR="${REQ_DIR:-/work/requests}"
RES_DIR="${RES_DIR:-/work/results}"
ALLOWED_ROOT="${ALLOWED_ROOT:-/data/uploads}"
STORE_CONTAINER="${STORE_CONTAINER:?STORE_CONTAINER is required}"
STORE_VOLUME="${STORE_VOLUME:?STORE_VOLUME is required}"
STORE_IMAGE="${STORE_IMAGE:-ghcr.io/oxigraph/oxigraph:latest}"
POLL_SECONDS="${POLL_SECONDS:-3}"

mkdir -p "$REQ_DIR" "$RES_DIR"
echo "loader-agent: watching $REQ_DIR for $STORE_CONTAINER (volume $STORE_VOLUME)"

# A heartbeat the app can see, so the UI offers a fast load only when something
# is actually here to do it — the same way it gates on rudof or Node.
heartbeat() { date -u +%s > "$RES_DIR/.agent-alive"; }
heartbeat

fail() {   # id, message
  printf '{"id":"%s","status":"error","message":%s,"finished":"%s"}\n' \
    "$1" "$(printf '%s' "$2" | jq -Rs .)" "$(date -u +%FT%TZ)" > "$RES_DIR/$1.json"
  echo "loader-agent: $1 failed: $2"
}

process() {
  req="$1"
  id="$(jq -r '.id // empty' "$req")"
  [ -n "$id" ] || { echo "loader-agent: request without an id, ignoring"; rm -f "$req"; return; }

  file="$(jq -r '.file // empty' "$req")"
  graph="$(jq -r '.graph // empty' "$req")"
  format="$(jq -r '.format // "nt"' "$req")"
  rm -f "$req"

  printf '{"id":"%s","status":"running","message":"starting","started":"%s"}\n' \
    "$id" "$(date -u +%FT%TZ)" > "$RES_DIR/$id.json"

  # A request may only name a file the app has already written, and only by a
  # path that stays inside it once resolved — no traversal out via "..".
  case "$format" in nt|ttl|nq) ;; *) fail "$id" "unsupported format: $format"; return ;; esac
  [ -n "$graph" ] || { fail "$id" "no graph given"; return; }
  real="$(realpath "$file" 2>/dev/null || true)"
  case "$real" in
    "$ALLOWED_ROOT"/*) ;;
    *) fail "$id" "file is outside $ALLOWED_ROOT"; return ;;
  esac
  [ -f "$real" ] || { fail "$id" "no such file: $file"; return; }

  echo "loader-agent: $id loading $real into $graph"
  started="$(date -u +%FT%TZ)"

  if ! docker stop "$STORE_CONTAINER" >/dev/null 2>&1; then
    fail "$id" "could not stop $STORE_CONTAINER"; return
  fi

  # A gzip is streamed in rather than expanded to disk first; at this size that
  # is a copy worth not making.
  set +e
  case "$real" in
    *.gz)  gzip -dc "$real" | docker run --rm -i -v "$STORE_VOLUME":/store \
             --entrypoint /usr/local/bin/oxigraph "$STORE_IMAGE" \
             load --location /store --format "$format" --graph "$graph" 2>"$RES_DIR/$id.log" ;;
    *.bz2) bzip2 -dc "$real" | docker run --rm -i -v "$STORE_VOLUME":/store \
             --entrypoint /usr/local/bin/oxigraph "$STORE_IMAGE" \
             load --location /store --format "$format" --graph "$graph" 2>"$RES_DIR/$id.log" ;;
    *)     docker run --rm -i -v "$STORE_VOLUME":/store \
             --entrypoint /usr/local/bin/oxigraph "$STORE_IMAGE" \
             load --location /store --format "$format" --graph "$graph" \
             < "$real" 2>"$RES_DIR/$id.log" ;;
  esac
  status=$?
  set -e

  # Always bring the store back, whether or not the load worked: leaving every
  # dataset offline because one import failed would be the worse outcome.
  docker start "$STORE_CONTAINER" >/dev/null 2>&1 || true

  summary="$(tail -c 400 "$RES_DIR/$id.log" 2>/dev/null || true)"
  if [ "$status" -eq 0 ]; then
    printf '{"id":"%s","status":"done","message":%s,"started":"%s","finished":"%s"}\n' \
      "$id" "$(printf '%s' "$summary" | jq -Rs .)" "$started" "$(date -u +%FT%TZ)" > "$RES_DIR/$id.json"
    echo "loader-agent: $id done"
  else
    fail "$id" "loader exited $status: $summary"
  fi
  rm -f "$RES_DIR/$id.log"
}

while true; do
  heartbeat
  for req in "$REQ_DIR"/*.json; do
    [ -e "$req" ] || continue
    process "$req" || echo "loader-agent: unexpected failure handling $req"
  done
  sleep "$POLL_SECONDS"
done
