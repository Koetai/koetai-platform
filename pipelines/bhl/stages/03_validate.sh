#!/usr/bin/env bash
# Stage 03: quality gate before anything gets indexed.
#
# Two checks, in order — cheap/whole-corpus first, expensive/sampled second:
#   1. grep for the string-content bugs (#1 DOI '%2F', #6 urn:bhl:creator:)
#      across every output file. A plain grep, not a ShEx facet — tested
#      directly against this pipeline's own shape: AND-combining a regex
#      pattern facet with a shape body doesn't validate reliably in this
#      rudof build, and string-content checks on an IRI are a grep's job
#      before they're a graph-shape validator's job anyway.
#   2. rudof, sampled: a handful of real node IRIs per type, each checked
#      with `rudof validate -n <node> -l <shape>` (the single-node form —
#      confirmed working end-to-end against this shape; the bulk ShapeMap
#      form hit unrelated parser issues in this rudof build and sampling is
#      both simpler and cheaper across hundreds of millions of triples
#      anyway).
set -euo pipefail

WORKDIR="${1:?Usage: 03_validate.sh <workdir>}"
SHAPE="$(dirname "$0")/../artifacts/bhl-shape.shex"
RDF_DIR="$WORKDIR/rdf"
SAMPLE_N="${SAMPLE_N:-50}"

echo "=== Check 1: grep for #1 (DOI %2F) and #6 (urn:bhl:creator:) ==="
# Scoped to the files that could actually contain these strings, not all 58
# outputs — DOI/creator content never appears in the page/pagename chunks,
# which are the overwhelming majority of this corpus's bytes. The first
# version of this check scanned everything indiscriminately and took over
# 30 minutes decompressing hundreds of megabytes of page/pagename data that
# structurally cannot match either pattern; this is the fix.
fail=0
doi_files=("$RDF_DIR"/title_dois*.nt.gz "$RDF_DIR"/part_dois*.nt.gz)
creator_files=("$RDF_DIR"/creators*.nt.gz "$RDF_DIR"/title_creators*.nt.gz \
               "$RDF_DIR"/part_creator_entities*.nt.gz "$RDF_DIR"/part_creators*.nt.gz \
               "$RDF_DIR"/creator_identifiers*.nt.gz)
if zgrep -l 'doi\.org/[^ ]*%2F' "${doi_files[@]}" > /dev/null 2>&1; then
  echo "FAIL: found '%2F' inside a doi.org IRI — issue #1 regressed"
  fail=1
fi
if zgrep -l 'urn:bhl:creator:' "${creator_files[@]}" > /dev/null 2>&1; then
  echo "FAIL: found urn:bhl:creator: — issue #6 regressed"
  fail=1
fi
[ "$fail" -eq 0 ] && echo "OK: no %2F in DOIs, no urn:bhl:creator: anywhere"

echo
echo "=== Check 2: sampled rudof validation ($SAMPLE_N nodes per type) ==="
declare -A TYPE_SHAPE=(
  ["https://www.biodiversitylibrary.org/vocab/Title"]="Title"
  ["http://purl.org/ontology/bibo/Article"]="Part"
  ["http://xmlns.com/foaf/0.1/Agent"]="Creator"
  ["http://purl.org/ontology/bibo/Book"]="Item"
  ["http://purl.org/ontology/bibo/Page"]="Page"
  ["https://www.biodiversitylibrary.org/vocab/PagePosition"]="PagePosition"
)
# Files where each type's rdf:type triple actually appears — narrow, for
# finding sample node IRIs cheaply. The same over-broad-scan mistake as
# check 1 (unscoped `*.nt.gz`, 6 full passes over all 58 files instead of
# one each over the handful that matter) applied here too.
declare -A TYPE_FILES=(
  ["Title"]="titles*.nt.gz"
  ["Part"]="parts*.nt.gz"
  ["Creator"]="creators*.nt.gz part_creator_entities*.nt.gz"
  ["Item"]="items*.nt.gz"
  ["Page"]="pages_*.nt.gz"
  ["PagePosition"]="part_page_position_*.nt.gz"
)
# Files where a sampled node's OWN triples can appear — broader than
# TYPE_FILES, since e.g. a Title's dcterms:creator lives in
# title_creators*.nt.gz, a different file from where its rdf:type is
# asserted. Using TYPE_FILES here would silently starve the shape check of
# properties that are real but just filed elsewhere, failing every sample
# for a property that does exist, just not in the file this looked in.
declare -A ENTITY_FILES=(
  ["Title"]="titles*.nt.gz title_creators*.nt.gz title_identifiers*.nt.gz title_dois*.nt.gz"
  ["Part"]="parts*.nt.gz part_creators*.nt.gz part_identifiers*.nt.gz part_dois*.nt.gz part_has_page*.nt.gz"
  ["Creator"]="creators*.nt.gz part_creator_entities*.nt.gz creator_identifiers*.nt.gz"
  ["Item"]="items*.nt.gz"
  ["Page"]="pages_*.nt.gz"
  ["PagePosition"]="part_page_position_*.nt.gz"
)

rudof_fail=0
for type_iri in "${!TYPE_SHAPE[@]}"; do
  shape="${TYPE_SHAPE[$type_iri]}"
  # Sample node IRIs of this type directly from the N-Triples output.
  # Capped at 2 files, not the full set (up to 21 for pages_*) — diagnosed
  # live: for Page/PagePosition virtually every row matches this type
  # pattern (one per page), so even one file has vastly more matches than
  # SAMPLE_N needs. `head -n 500000` downstream was meant to short-circuit
  # this, but zgrep's own multi-file loop doesn't reliably stop on a
  # downstream SIGPIPE between files — confirmed live: still decompressing
  # file 18/21 after 25 minutes despite head only wanting 500K of the ~16M
  # matches already available in file 1 alone. Not passing the other 19
  # files to zgrep in the first place sidesteps that entirely.
  files=()
  file_count=0
  for pattern in ${TYPE_FILES[$shape]}; do
    for f in "$RDF_DIR"/$pattern; do
      [ -e "$f" ] || continue
      files+=("$f")
      file_count=$((file_count + 1))
      [ "$file_count" -ge 2 ] && break 2
    done
  done
  # `head` before `shuf` stays as a second safety net (cheap, still correct
  # for types where a whole file's matches could still be large).
  mapfile -t nodes < <(zgrep -h "> <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <$type_iri>" \
    "${files[@]}" 2>/dev/null | awk '{print $1}' | head -n 500000 | shuf -n "$SAMPLE_N" 2>/dev/null || true)
  [ "${#nodes[@]}" -eq 0 ] && { echo "  $shape: no sample nodes found, skipping"; continue; }

  # Same file-count cap per pattern, and for the same reason: a sampled
  # node's own triples are confirmed present in the 2-file subset `files[]`
  # already scanned above (chunked mappings like pages_*.nt.gz don't split
  # one entity's triples across chunks), so extraction never needs more of
  # that pattern than were already searched for sampling. Scanning the full
  # 21 files per node x 50 nodes here would have been the same stall moved
  # one step later instead of actually fixed.
  entity_files=()
  for pattern in ${ENTITY_FILES[$shape]}; do
    pattern_count=0
    for f in "$RDF_DIR"/$pattern; do
      [ -e "$f" ] || continue
      entity_files+=("$f")
      pattern_count=$((pattern_count + 1))
      [ "$pattern_count" -ge 2 ] && break
    done
  done

  # Decompress entity_files ONCE per type, not once per node — this is what
  # actually stalled PagePosition even after capping the file *count* to 1:
  # the loop below still re-decompressed that same 247MB file separately for
  # each of the 50 sampled nodes (50x the same gzip work). grep against an
  # already-plain file is cheap; repeated gzip decompression is what's
  # expensive, so do it exactly once per type instead of once per node.
  entity_plain="$(mktemp --suffix=.nt)"
  zcat "${entity_files[@]}" > "$entity_plain" 2>/dev/null || true
  creator_plain="$(mktemp --suffix=.nt)"
  zcat "$RDF_DIR"/creators*.nt.gz "$RDF_DIR"/part_creator_entities*.nt.gz > "$creator_plain" 2>/dev/null || true

  ok=0
  sample_nt="$(mktemp --suffix=.nt)"
  for node in "${nodes[@]}"; do
    # rudof can't read .gz directly (confirmed — it tries to parse the raw
    # compressed bytes as text). <Title>/<Part> also reference @<Creator>,
    # so any bhl:creator/N objects mentioned need their own triples pulled
    # in too, or that referenced-shape check would fail for having nothing
    # to check against.
    grep -h "^$node " "$entity_plain" > "$sample_nt" 2>/dev/null || true
    mapfile -t referenced < <(grep -o 'https://www\.biodiversitylibrary\.org/creator/[0-9]*' "$sample_nt" | sort -u)
    for ref in "${referenced[@]}"; do
      grep -h "^<$ref> " "$creator_plain" >> "$sample_nt" 2>/dev/null || true
    done
    result=$(rudof validate --mode shex "$sample_nt" -t ntriples -s "$SHAPE" -f shexc \
      -n "$node" -l "<http://base/$shape>" -r details 2>&1) || true
    if echo "$result" | grep -q "OK"; then
      ok=$((ok + 1))
    else
      echo "  $shape FAIL on $node:"
      echo "$result" | sed 's/^/    /'
      rudof_fail=1
    fi
  done
  rm -f "$entity_plain" "$creator_plain" "$sample_nt"
  echo "  $shape: $ok/${#nodes[@]} passed"
done

if [ "$fail" -ne 0 ] || [ "$rudof_fail" -ne 0 ]; then
  echo
  echo "Stage 03 FAILED — fix artifacts/bhl-mapping.yarrrml.yml and re-run stage 02 before continuing."
  exit 1
fi
echo
echo "Stage 03 passed."

