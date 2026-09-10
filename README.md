# Koetai — FAIR SPARQL Endpoint Platform

**Koetai** is a multi-tenant SaaS platform for hosting FAIR SPARQL endpoints. Each dataset picks its own triplestore — Fuseki and Oxigraph are the two that ship ready to run (see [Triplestores](#triplestores)). It lets researchers publish RDF datasets as queryable SPARQL endpoints with shapes, examples, REST APIs, and schema visualisations — all under a single hosted service.

Live instance: **https://koetai.semscape.org**

### Repositories

This project is hosted on two forges. **Codeberg is the primary repository** — all issues, feature requests, and pull requests should be filed there. GitHub is a secondary mirror.

| Forge | URL | Role |
|---|---|---|
| **Codeberg** (primary) | https://codeberg.org/andrawaag/koetai-platform | Issues, PRs, development |
| GitHub (mirror) | https://github.com/Koetai/koetai-platform | Read-only mirror |

---

## Features

| Area | Details |
|---|---|
| **Authentication** | ORCID OAuth 2.0, invitation-only registration |
| **Multi-tenancy** | A named graph per user/dataset, in whichever store backs it |
| **RDF upload** | Turtle, N-Triples, RDF/XML, OWL/XML; async background indexing |
| **OWL reasoning** | RDFS (Jena, fast) or OWL-RL (owlrl, async) |
| **Shape inference** | [ShExer](https://github.com/DaniFdezAlvarez/shexer) for ShEx; [RUDOF](https://github.com/rudof-project/rudof) for ShEx/SHACL validation |
| **Schema visualisation** | Mermaid class diagrams from inferred shapes, always; [rdf-config](https://github.com/dbcls/rdf-config) SVG diagrams and SPARQL skeletons additionally, if rdf-config is available (it runs via Docker and is not in the image) |
| **SPARQL examples** | [sparql-examples](https://github.com/sib-swiss/sparql-examples) RDF format; stored per dataset |
| **SPARQL editor** | CodeMirror 5 with SPARQL syntax highlighting, auto-complete, and per-dataset or cross-dataset querying |
| **REST API** | [SPARQLList](https://github.com/dbcls/sparqlist)-style parameterised SPARQL queries exposed as HTTP endpoints |
| **FAIR Data Point** | DCAT metadata hierarchy (Repository → Catalog → Dataset → Distribution) |
| **GitHub/GitLab sync** | Import SPARQL examples from repositories |
| **Web source harvesting** | Scrape and ingest RDF from URLs |
| **Admin panel** | Disk usage per user, cost overview, invitation management |
| **Public endpoint listing** | Browse all public datasets grouped by owner |

---

## Architecture

```
Flask (Python)
├── ORCID OAuth 2.0        — flask-login + requests-oauthlib
├── SQLite                 — users, datasets, shapes, examples, jobs
├── Triplestore            — named graph per dataset, chosen per dataset (see below)
├── Apache Jena 6          — riot (RDF parsing/conversion), infer (RDFS reasoning)
├── owlrl (Python)         — OWL-RL materialisation (background job)
├── ShExer (Python)        — ShEx shape inference from uploaded data
├── RUDOF (/usr/bin/rudof) — ShEx / SHACL validation
├── rdf-config (Docker)    — SVG schema + SPARQL skeleton generation
└── Caddy                  — reverse proxy, HTTPS, basic auth on admin routes
```

---

## Three modes

The same codebase runs as the public hosted platform, as a self-hosted instance
for a group, or as a single-user install. `KOETAI_MODE` picks which.

| | `community` | `internal` | `local` |
|---|---|---|---|
| Who it's for | the public site at koetai.semscape.org | a group hosting for its members | one person, one machine |
| Sign-in | ORCID OAuth | ORCID OAuth | none — auto-signed-in |
| Who may register | invitation-only | allowlist (ORCID / email domain) | n/a — single user |
| Needs ORCID credentials | yes | yes | no |
| Owner in URLs | the user's ORCID iD | the user's ORCID iD | `local` (override with `LOCAL_ORCID`) |

`community` is the default.

**internal** lets an organisation run its own instance. Members sign in with
ORCID and are admitted automatically if they match the allowlist — no
per-person invites. The host controls membership with three settings:

```bash
KOETAI_MODE=internal
INTERNAL_ADMIN_ORCIDS=0000-0000-0000-0000        # always allowed, and made admin
INTERNAL_ALLOWED_ORCIDS=0000-0001-1111-1111,...  # specific people
INTERNAL_ALLOWED_DOMAINS=your-institute.org      # anyone with a public ORCID email here
```

Put your own ORCID in `INTERNAL_ADMIN_ORCIDS` to bootstrap a fresh instance.
Domain matching is best-effort — it only works when a user has made an email
public on ORCID — so the ORCID allowlist is the reliable path.

**local** needs no ORCID app and no invitation; the ORCID block in `.env` can
stay blank.

```bash
KOETAI_MODE=local BASE_URL=http://localhost:3002 python3 app.py
```

## Triplestores

A dataset's backend is chosen per dataset, in its `platform` column. Every
backend below is implemented in `services/triplestore.py`, but they are not all
equally proven, so this table says plainly where each one stands:

| `platform` | State | Notes |
|---|---|---|
| `fuseki` | **Tested, ships by default** | Apache Jena TDB2. Started by `docker compose up`. The safest choice. |
| `oxigraph` | **Tested** | `docker compose --profile oxigraph up -d`. Smaller and lighter than Fuseki; needs 0.5.11 or later (see below). |
| `qlever` | **Queries only** | Not started by compose — it serves an index built offline, so it needs its own setup. Uploading from Koetai does not work: the app sends no access token, which QLever requires for updates, and files over 50 MB take a rebuild path that assumes the app and QLever share a filesystem. |
| `virtuoso` | *Implemented, untested* | Reached over the same SPARQL 1.1 + Graph Store Protocol client as Fuseki and Oxigraph, and configurable in `.env`, but not exercised. |
| `blazegraph` | *Implemented, untested* | As above. Uses `context-uri` rather than `graph` for the Graph Store parameter. |
| `rdf4j` | *Implemented, untested* | As above. |
| `comunica` | *Implemented, needs Node* | Not a store at all — see [Federation datasets](#federation-datasets-comunica). Needs `@comunica/query-sparql` on `PATH`, which the Docker image does not include. |

"Implemented, untested" means the code path exists and is configured the same way
as the tested ones — they are all instances of one SPARQL 1.1 + Graph Store
Protocol client differing only in URL layout and auth — but nobody has run
Koetai against them. Treat them as a starting point, not a promise. Adding
another compliant store is a few lines in the same registry.

The **New Dataset** form lists every backend above, ordered with the reachable
ones first and one of those preselected. A store that is not running is shown
but cannot be chosen, and the untested ones are labelled as such, so the form
reflects what the install actually has rather than a fixed list.

Configure only the stores you actually run; the rest report as unavailable. See
`.env.example`.

> **QLever and durability**: QLever holds SPARQL UPDATEs in memory unless the
> server is started with `--persist-updates` (Qleverfile: `PERSIST_UPDATES = true`).
> Without it, uploaded data is silently lost when the engine stops.

> **Oxigraph needs 0.5.11 or later**: earlier 0.5.x releases honour only the
> *last* `named-graph-uri` of a SPARQL request and drop the rest, which left a
> dataset's `/examples` and `/shapes` graphs unreachable through `GRAPH` and made
> the query editor's prefilled query return nothing. Fixed in
> [0.5.11](https://github.com/oxigraph/oxigraph/releases/tag/v0.5.11)
> ([#1862](https://github.com/oxigraph/oxigraph/issues/1862),
> [#1835](https://github.com/oxigraph/oxigraph/issues/1835)), which
> `ghcr.io/oxigraph/oxigraph:latest` now resolves to.

### Disk usage

Two things surprise people here, and both bite hardest on the large datasets
these stores exist for.

**An index is several times its source.** A 203 MB N-Triples file (2.4M triples)
became about 1.1 GB of TDB2. That was synthetic data with every subject and
literal distinct, so it is close to a worst case for dictionary compression —
real vocabulary repeats and does better — but budget a multiple of the raw size,
not a margin on it. Uploaded sources are stored too unless
`KEEP_UPLOADED_SOURCES=false` (see `.env.example`).

**Deleting data does not give the space back.** Neither store returns freed
pages to the filesystem; they keep them for reuse. After deleting every dataset,
an instance reporting *zero graphs* still held 6.8 GB of Fuseki and 7.3 GB of
Oxigraph. Nothing is wrong — but "no triples" and "no disk used" are unrelated,
and a workflow that loads and drops big graphs grows the high-water mark, not
the current size. Size a host for the former.

To actually reclaim it:

```bash
# Fuseki — compact, then delete the generation it superseded. Compaction writes
# a new Data-NNNN beside the old one; without the second step the directory
# briefly gets *bigger*.
curl -u admin:PASSWORD -X POST http://localhost:3030/\$/compact/koetai
#   ... wait for the task to finish (GET /$/tasks), then:
rm -rf /fuseki/databases/koetai/Data-0001     # the lower-numbered one

# Oxigraph — needs exclusive access, so stop the server first
oxigraph optimize --location /data
```

Those took a Fuseki volume from 6.8 GB to 201 MB and an Oxigraph one from 7.3 GB
to 83 MB. On a local install `docker compose down -v` is the blunter equivalent,
and destroys the data with it.

### Federation datasets (Comunica)

A dataset with `platform='comunica'` is **virtual**: it stores no data of its own
and instead federates every query, at request time, across a list of external
sources (SPARQL endpoints, RDF files, TPF) held in its `sources` column. Uploads
don't apply — the dataset *is* its source list. Powered by
[Comunica](https://comunica.dev) (`npm install -g @comunica/query-sparql@^3`).

Reliability follows the sources: if a remote endpoint is down or rate-limits, the
query surfaces that error rather than partial data. Use it to join your own data
against public knowledge graphs without copying them in.

## Setup

### Prerequisites

- Python 3.11+
- A triplestore — [Fuseki](https://jena.apache.org/documentation/fuseki2/) or
  [Oxigraph](https://github.com/oxigraph/oxigraph) are the tested pair and both
  ship in `docker-compose.yml`; see [Triplestores](#triplestores) for the rest
- Caddy (for HTTPS / reverse proxy) — not needed for a local install

Optional — only for the shapes, reasoning and diagram features:

- [Apache Jena](https://jena.apache.org/) binaries (`riot`, `infer`)
- [RUDOF](https://github.com/rudof-project/rudof) installed as `/usr/bin/rudof`
- Docker (for rdf-config)

### Install

```bash
git clone https://github.com/Koetai/koetai-platform.git
cd koetai-platform
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
# community: set SECRET_KEY, ORCID credentials, BASE_URL, paths
# local:     set KOETAI_MODE=local — the ORCID settings can stay blank
```

Key `.env` variables:

| Variable | Description |
|---|---|
| `KOETAI_MODE` | `community` (default), `internal`, or `local` — see [Three modes](#three-modes) |
| `SECRET_KEY` | Flask session secret. Auto-generated per boot in `local` |
| `ORCID_CLIENT_ID` | ORCID developer app client ID — *community + internal* |
| `ORCID_CLIENT_SECRET` | ORCID developer app client secret — *community + internal* |
| `ORCID_REDIRECT_URI` | e.g. `https://yourdomain.org/auth/callback` — *community + internal* |
| `BASE_URL` | Public base URL |
| `QLEVER_PLATFORM_URL` | QLever instance URL (default `http://localhost:7030`) |
| `DEPLOY_DIR` | Path to qlever-sparql-deployment directory |
| `JENA_BIN` | Path to Jena `bin/` directory |
| `RUDOF_BIN` | Path to rudof binary |
| `SHEXER_VENV` | Path to Python interpreter with shexer installed |

### Run

```bash
flask --app app run
# or with gunicorn — one worker, many threads (see below):
gunicorn --workers 1 --threads 8 -b 127.0.0.1:3002 app:app
```

Use a **single** gunicorn worker. `services/job_runner.py` runs a per-process
background thread that polls the upload-jobs table; a second worker would start a
second runner and the two would race to claim the same job. Scale with `--threads`,
not `--workers`. The included `koetai-platform.service` systemd unit and the
`Dockerfile` both run one worker.

### Database

```bash
flask --app app shell
>>> from services.db import init_db; init_db()
```

---

## Project structure

```
koetai-platform/
├── app.py                   # Flask app factory, blueprint registration
├── config.py                # Config from .env
├── db/
│   ├── schema.sql           # SQLite schema
│   └── koetai.db            # runtime DB (not in repo)
├── routes/
│   ├── auth.py              # ORCID OAuth, login/logout
│   ├── dashboard.py         # User dashboard, admin storage/cost views
│   ├── datasets.py          # Dataset CRUD, upload, SPARQL endpoint proxy
│   ├── examples.py          # SPARQL examples (sparql-examples format)
│   ├── shapes.py            # ShEx/SHACL inference and validation
│   ├── sparqlist.py         # SPARQLList-style parameterised REST API
│   ├── fdp.py               # FAIR Data Point DCAT metadata
│   ├── github.py            # GitHub/GitLab SPARQL example sync
│   └── web_sources.py       # Web source harvesting
├── services/
│   ├── triplestore.py       # backend registry — resolves a dataset's platform
│   ├── job_runner.py        # Async background upload job queue
│   ├── owl_service.py       # RDFS/OWL reasoning via Jena + owlrl
│   ├── shexer_service.py    # ShEx inference via ShExer
│   ├── rudof_service.py     # ShEx/SHACL validation via RUDOF
│   ├── rdfconfig_service.py # rdf-config SVG/SPARQL generation
│   └── ...
├── templates/               # Jinja2 HTML templates
├── static/
│   └── sparql-hint.js       # CodeMirror SPARQL auto-complete
└── uploads/                 # Uploaded RDF files (not in repo)
```

---

## SPARQL Auto-complete

The built-in SPARQL editor provides auto-complete for:

- **SPARQL keywords** — `SELECT`, `WHERE`, `FILTER`, `OPTIONAL`, `BIND`, etc.
- **Prefix declarations** — typing `PREFIX ` suggests `prefix: <URI>` for 25+ well-known namespaces
- **Local names** — typing `rdfs:` suggests `label`, `subClassOf`, `Class`, etc. for all common vocabularies
- **Declared prefixes** — any `PREFIX` declared in the current query is immediately available

Trigger with **Ctrl+Space** or by typing.

---

## Related repositories

- [Koetai/sparql-examples](https://github.com/Koetai/sparql-examples) — fork of sib-swiss/sparql-examples

---

## License

MIT
