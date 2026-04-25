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

## Setup

### Prerequisites

- Python 3.11+
- [QLever](https://github.com/ad-freiburg/qlever) running on a local port
- [Apache Jena](https://jena.apache.org/) binaries (`riot`, `infer`)
- [RUDOF](https://github.com/rudof-project/rudof) installed as `/usr/bin/rudof`
- Docker (for rdf-config)
- Caddy (for HTTPS / reverse proxy)

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
# edit .env — set SECRET_KEY, ORCID credentials, BASE_URL, paths
```

Key `.env` variables:

| Variable | Description |
|---|---|
| `SECRET_KEY` | Flask session secret |
| `ORCID_CLIENT_ID` | ORCID developer app client ID |
| `ORCID_CLIENT_SECRET` | ORCID developer app client secret |
| `ORCID_REDIRECT_URI` | e.g. `https://yourdomain.org/auth/callback` |
| `BASE_URL` | Public base URL |
| `QLEVER_PLATFORM_URL` | QLever instance URL (default `http://localhost:7030`) |
| `DEPLOY_DIR` | Path to qlever-sparql-deployment directory |
| `JENA_BIN` | Path to Jena `bin/` directory |
| `RUDOF_BIN` | Path to rudof binary |
| `SHEXER_VENV` | Path to Python interpreter with shexer installed |

### Run

```bash
flask --app app run
# or with gunicorn:
gunicorn -w 2 -b 127.0.0.1:5000 app:app
```

The included `koetai-platform.service` systemd unit can be used for production deployment.

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
