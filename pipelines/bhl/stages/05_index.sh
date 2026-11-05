#!/usr/bin/env bash
# Stage 05: QLever bulk index build.
#
# Encodes the exact recipe validated against production earlier this session
# — nothing in koetai-platform documented this before, so this stage *is*
# the durable record of it now:
#   - `qlever index` (offline bulk build), not the live SPARQL Update path —
#     architecturally bounded memory (STXXL_MEMORY) regardless of corpus
#     size, unlike incremental updates.
#   - `--multi-input-json` with a per-file `graph` tag is mandatory, not
#     optional — the first attempt at today's real BHL build omitted this
#     and all 546.8M triples silently landed in QLever's unnamed default
#     graph, invisible to Koetai's per-dataset graph scoping. Caught only by
#     the mandatory pre-swap verification on a temp port (stage 06) — never
#     skip that step because this one looks like it succeeded.
#   - `--input-files` is required by the CLI's argparser even though
#     `--multi-input-json` supplies the real file list — a dummy glob
#     satisfies the pre-check without being otherwise used.
#   - Qleverfile's own INPUT_FILES/CAT_INPUT_FILES/FORMAT keys conflict with
#     these CLI flags and must not be present.
set -euo pipefail

# Raise the open-file soft limit to the hard limit. The vocabulary merge opens
# every partial vocabulary at once (one per 1M-triple batch: 793 for this
# corpus, ~2 file handles each) and the default soft limit is 1024, so it died
# at ~file 479 with "Too many open files" after a 30-minute parse that could
# not be resumed. qlever's own CLI only raises this when it judges the input
# to exceed 10GB, and gzip'd input of 8GB does not trigger that. Reproduced on
# a small build (806 partial vocabularies, fails at the default limit, passes
# with this line) before relying on it.
ulimit -Sn "$(ulimit -Hn)"

WORKDIR="${1:?Usage: 05_index.sh <workdir> <graph-uri> <index-name>}"
GRAPH_URI="${2:?graph URI, e.g. https://koetai.semscape.org/u/<orcid>/bhl/data}"
INDEX_NAME="${3:-bhl}"

# Absolute, resolved before the cd below — the symlinks made further down
# point at $RDF_DIR, and a relative path there resolves against the *build*
# directory instead, giving 59 dangling links.
WORKDIR="$(cd "$WORKDIR" && pwd)"
RDF_DIR="$WORKDIR/rdf"
# BUILD_DIR may point at a different, larger volume — the parse phase alone
# wrote ~57MB of temp files per million triples (28GB by 540M triples in the
# first attempt, which then ran the 59GB root disk to 0 and hung the build).
BUILD_DIR="${BUILD_DIR:-$WORKDIR/build}"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

# Optional: build with a specific QLever binary pair instead of whatever is
# on PATH. Set QLEVER_BIN_DIR to a directory holding qlever-index and
# qlever-server. This matters because a QLever index is tied to the binary
# version that wrote it — the target server must be able to load it, so the
# safest build uses the *same version as the server that will serve it*.
INDEX_BINARY_LINE=""
SERVER_BINARY_LINE=""
if [ -n "${QLEVER_BIN_DIR:-}" ]; then
  INDEX_BINARY_LINE="INDEX_BINARY = $QLEVER_BIN_DIR/qlever-index"
  SERVER_BINARY_LINE="SERVER_BINARY = $QLEVER_BIN_DIR/qlever-server"
fi

# qlever index resolves --input-files as a glob relative to cwd; symlink
# every source in rather than fight absolute-path glob support.
ln -sf "$RDF_DIR"/*.nt.gz .

FILES_JSON=$(python3 -c "
import glob, json
files = sorted(glob.glob('*.nt.gz'))
print(json.dumps([{'cmd': f'zcat {f}', 'format': 'nt', 'graph': '$GRAPH_URI'} for f in files]))
")

cat > Qleverfile <<EOF
[data]
NAME = $INDEX_NAME

[index]
$INDEX_BINARY_LINE
STXXL_MEMORY = 4G
SETTINGS_JSON = { "ascii-prefixes-only": false, "num-triples-per-batch": 1000000, "prefixes-external": [""] }

[server]
$SERVER_BINARY_LINE
PORT = 7031
ACCESS_TOKEN = $INDEX_NAME
MEMORY_FOR_QUERIES = 5G
PERSIST_UPDATES = true

[runtime]
SYSTEM = native
EOF

echo "Building index with graph tagging for: $GRAPH_URI"
# DRY_RUN=1 prints the exact command qlever would run and stops — for
# checking flag compatibility with a specific binary before a multi-hour build.
if [ -n "${DRY_RUN:-}" ]; then
  qlever index --input-files "*.nt.gz" --multi-input-json "$FILES_JSON" --overwrite-existing --show
  exit 0
fi
qlever index --input-files "*.nt.gz" --multi-input-json "$FILES_JSON" --overwrite-existing

echo
echo "Stage 05 done. Index built in $BUILD_DIR — do NOT swap into production yet."
echo "Run stage 06 (verify on a temp port) before touching the live qlever-platform.service."
