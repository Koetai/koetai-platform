#!/usr/bin/env bash
# Interactive installer for Koetai (local, single-user) on macOS.
# Automates the docker-compose path from the README's Setup section — nothing
# here is Koetai-internal magic. Safe to re-run: it pulls instead of
# re-cloning, and reuses an existing .env.
set -euo pipefail

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ask()  { local prompt="$1" default="$2" ans; read -r -p "$prompt [$default]: " ans; echo "${ans:-$default}"; }
yesno(){ local prompt="$1" default="${2:-y}" ans; read -r -p "$prompt [y/n] (default $default): " ans; ans="${ans:-$default}"; [[ "$ans" =~ ^[Yy] ]]; }

say "Koetai local install (macOS)"
echo "Single-user instance in Docker — no ORCID account, no cloud sign-in,"
echo "your data stays on this Mac."

# ── Prerequisites ────────────────────────────────────────────────────────────
say "1/6 Checking prerequisites"
command -v git >/dev/null || { echo "git not found. Install Xcode Command Line Tools: xcode-select --install"; exit 1; }
if ! command -v docker >/dev/null; then
  echo "Docker not found. Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop isn't running. Starting it..."
  open -a Docker
  echo -n "Waiting for Docker to come up"
  for _ in $(seq 1 60); do
    docker info >/dev/null 2>&1 && { echo " ready."; break; }
    echo -n "."; sleep 2
  done
  docker info >/dev/null 2>&1 || { echo; echo "Docker Desktop didn't start in time — start it manually and re-run this script."; exit 1; }
fi
echo "git and Docker OK."

# ── Choices ──────────────────────────────────────────────────────────────────
say "2/6 A few questions"

DIR=$(ask "Install into which directory?" "$HOME/koetai-platform")

REPO_CHOICE=$(ask "Clone from (github/codeberg)?" "github")
case "$REPO_CHOICE" in
  codeberg) REPO_URL="https://codeberg.org/andrawaag/koetai-platform.git" ;;
  *)        REPO_URL="https://github.com/Koetai/koetai-platform.git" ;;
esac

echo
echo "Which triplestore should back your datasets?"
echo "  1) Oxigraph  — recommended: small, fast, no extra config"
echo "  2) Fuseki    — heavier, the other tested option"
echo "  3) Both"
STORE_CHOICE=$(ask "Choice" "1")
case "$STORE_CHOICE" in
  2) STORES="fuseki"; COMPOSE_PROFILE="" ;;
  3) STORES="fuseki oxigraph"; COMPOSE_PROFILE="--profile oxigraph" ;;
  *) STORES="oxigraph"; COMPOSE_PROFILE="--profile oxigraph" ;;
esac

PORT=$(ask "Port for Koetai" "3002")
while lsof -i ":$PORT" >/dev/null 2>&1; do
  echo "Port $PORT is already in use."
  PORT=$(ask "Try a different port" "3003")
done

# ── Clone ────────────────────────────────────────────────────────────────────
say "3/6 Getting the code"
if [ -d "$DIR/.git" ]; then
  echo "$DIR already exists — pulling latest instead of cloning."
  git -C "$DIR" pull --ff-only
else
  git clone "$REPO_URL" "$DIR"
fi
cd "$DIR"

# ── Configure ────────────────────────────────────────────────────────────────
say "4/6 Configuring"
[ -f .env ] || cp .env.example .env
# KOETAI_MODE is hardcoded to `local` in docker-compose.yml for this path, so
# nothing to set there. Only the port needs to go in .env — compose reads
# ${KOETAI_PORT:-3002} from it automatically.
if grep -q '^KOETAI_PORT=' .env 2>/dev/null; then
  sed -i '' "s/^KOETAI_PORT=.*/KOETAI_PORT=$PORT/" .env
else
  echo "KOETAI_PORT=$PORT" >> .env
fi
echo "Wrote $DIR/.env (port=$PORT)."

# ── Start ────────────────────────────────────────────────────────────────────
say "5/6 Starting containers ($STORES) — first run also builds the image, this can take a few minutes"
# shellcheck disable=SC2086
docker compose $COMPOSE_PROFILE up -d --build koetai $STORES

echo -n "Waiting for Koetai to answer on port $PORT"
for _ in $(seq 1 60); do
  curl -sf "http://localhost:$PORT/" >/dev/null 2>&1 && { echo " up."; break; }
  echo -n "."; sleep 2
done
if ! curl -sf "http://localhost:$PORT/" >/dev/null 2>&1; then
  echo
  echo "Didn't come up in time. Recent logs:"
  docker compose logs koetai --tail 50
  exit 1
fi

# ── Done ─────────────────────────────────────────────────────────────────────
say "6/6 Done"
echo "Koetai is running at: http://localhost:$PORT"
if [[ "$STORES" == *oxigraph* ]]; then
  echo "When you create your first dataset, set its backend to 'oxigraph' in the New Dataset form."
fi
echo
echo "Useful commands (run from $DIR):"
echo "  docker compose logs -f koetai        # follow the app log"
echo "  docker compose down                  # stop (keeps your data)"
echo "  docker compose $COMPOSE_PROFILE up -d koetai $STORES   # start again"
echo "  docker compose down -v               # stop AND delete all data"
echo

if yesno "Open it in your browser now?" "y"; then
  open "http://localhost:$PORT"
fi
