# Koetai — FAIR SPARQL Endpoint Platform

**Koetai** is a multi-tenant SaaS platform for hosting FAIR SPARQL endpoints, built on [QLever](https://github.com/ad-freiburg/qlever). It lets researchers publish RDF datasets as queryable SPARQL endpoints with shapes, examples, REST APIs, and schema visualisations — all under a single hosted service.

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
| **Multi-tenancy** | QLever named graphs per user/dataset |
| **RDF upload** | Turtle, N-Triples, RDF/XML, OWL/XML; async background indexing |
| **OWL reasoning** | RDFS (Jena, fast) or OWL-RL (owlrl, async) |
| **Shape inference** | [ShExer](https://github.com/DaniFdezAlvarez/shexer) for ShEx; [RUDOF](https://github.com/rudof-project/rudof) for ShEx/SHACL validation |
| **Schema visualisation** | [rdf-config](https://github.com/dbcls/rdf-config) SVG diagrams + SPARQL skeletons from inferred shapes |
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
├── QLever SPARQL          — named graph per dataset, async background indexer
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

A dataset's backend is chosen per dataset (`platform` column). Any of these
open-source stores work:

**qlever** · **fuseki** · **virtuoso** · **oxigraph** · **blazegraph** · **rdf4j**

All but QLever are reached over SPARQL 1.1 Query/Update plus the Graph Store
Protocol, so supporting another compliant store is a few lines in
`services/triplestore.py`. Configure only the ones you run — see `.env.example`.

> **QLever and durability**: QLever holds SPARQL UPDATEs in memory unless the
> server is started with `--persist-updates` (Qleverfile: `PERSIST_UPDATES = true`).
> Without it, uploaded data is silently lost when the engine stops.

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
- A triplestore — [QLever](https://github.com/ad-freiburg/qlever),
  [Fuseki](https://jena.apache.org/documentation/fuseki2/),
  [Virtuoso](https://github.com/openlink/virtuoso-opensource),
  [Oxigraph](https://github.com/oxigraph/oxigraph), Blazegraph or RDF4J
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
│   ├── triplestore.py       # QLever / Fuseki abstraction
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
