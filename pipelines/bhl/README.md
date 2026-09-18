# BHL -> RDF pipeline

Reproducible conversion of the [Biodiversity Heritage Library data export](https://www.biodiversitylibrary.org/data)
into RDF, loaded into QLever. Transformation **logic** lives in `artifacts/`;
`stages/` is generic orchestration code.

## Artifacts (the logic)
| File | Purpose |
|---|---|
| `artifacts/bhl-mapping.yarrrml.yml` | YARRRML/RML mapping, BHL TSVs -> RDF (run by Morph-KGC, no database) |
| `artifacts/bhl-shape.shex` | ShEx shapes the output must satisfy (checked with RUDOF) |
| `artifacts/reconcile-wikidata.rq` | Wikidata query: `dwc:scientificName` -> taxon (`wdt:P225`) |
| `artifacts/lookups.py` | Identifier -> URI resolution, DOI `%2F` repair |
| `artifacts/provenance.ttl.j2` | Provenance template for the Zenodo bundle |

## Stages
1. `01_fetch.sh` download the dump  2. `02_materialize.py` Morph-KGC -> chunked `.nt.gz`
3. `03_validate.sh` grep + sampled ShEx gate  4. `04_reconcile.py` Wikidata `dwc:scientificNameID`
5. `05_index.sh` QLever bulk build  6. `06_verify.sh` temp-port check (exact triple count)
7. `07_package.py` Zenodo **draft** (needs `ZENODO_TOKEN`; never publishes)  8. `08_tarball.sh` RDF tarball

`run_pipeline.sh --host <IP> [--from NN]` copies everything to a throwaway VM
you already have SSH access to and runs the stages there. It does not provision
or delete VMs, and never swaps a production index.

## Lessons baked in (each cost real time)
- Bulk build needs ~75 MB temp per million triples (~60 GB for 790M) plus the
  final index: use a separate large volume (`BUILD_DIR`).
- `ulimit -n` must be raised before the vocabulary merge (stage 05 does it).
- A QLever index is tied to the binary version; build with the version the
  target server runs (`QLEVER_BIN_DIR`).
- Morph-KGC: use `materialize_set()` in a fresh subprocess per chunk; add
  `~literal` to multi-reference templates; set datatypes explicitly.
- Always verify on a temp port (stage 06) before swapping into production.

## Known limitations
- Some `dcterms:date` values are year ranges typed `xsd:gYear` (e.g. `"1798-1803"`).
- A few creator `owl:sameAs` links derive from malformed source identifiers.
- `dwc:scientificNameID` is page-level: on multi-name pages the name<->id
  pairing is lost; where several Wikidata items share a name, one is kept.
