# Koetai Platform — Architecture

Koetai is a multi-tenant Flask app: one process serves every user's dataset,
each dataset picks its own triplestore backend, and anything slow (RDF
loading, reasoning, remote fetches) runs as an async background job rather
than on the request thread. This page diagrams the shape of that, grounded
in the actual modules (paths in parentheses throughout).

## System overview

```mermaid
flowchart TB
    Browser["Browser / SPARQL client"]
    ORCID["ORCID OAuth 2.0"]
    GitSrc["GitHub / GitLab\n(example sync)"]
    OtherKoetai["Other Koetai instances\n(Mangal registry)"]

    Caddy["Caddy\nreverse proxy, HTTPS"]
    Flask["Flask app\n(app.py, routes/*)"]
    SQLite[("SQLite\ndb/koetai.db")]
    JobRunner["job_runner\nbackground thread\n(services/job_runner.py)"]

    subgraph Backends ["Pluggable triplestore backends (services/triplestore.py registry)"]
        QLever[("QLever")]
        Fuseki[("Fuseki")]
        Oxigraph[("Oxigraph")]
        OtherStores[("Virtuoso / Blazegraph / RDF4J")]
        Comunica["Comunica\nfederation, no local store"]
    end

    Browser -->|HTTPS| Caddy --> Flask
    Flask -->|OAuth 2.0| ORCID
    Flask <-->|users, datasets,\nshapes, examples, jobs| SQLite
    Flask -->|submit| JobRunner
    JobRunner -->|load / replace / drop graph| Backends
    Flask -->|SPARQL query & update| Backends
    Flask -->|import examples| GitSrc
    Flask -->|list / link to| OtherKoetai
```

Caddy terminates TLS and proxies to the Flask app on :3002
(`koetai-platform.service`). The app itself never talks to a triplestore
directly — every read and write goes through the registry in
`services/triplestore.py`, which resolves a dataset's `platform` DB column
to a backend client. SQLite (no ORM, `services/db.py`) holds everything
*about* datasets — never the RDF itself.

## Dataset anatomy

Each row in `datasets` is the hub for everything a user sees under
`/u/<owner_orcid>/<slug>/...`:

```mermaid
flowchart LR
    Dataset["Dataset\n(owner_orcid / slug)"]
    Graph[("Named graph\non the chosen backend")]
    SPARQL["/sparql endpoint\n(routes/datasets.py)"]
    FDP["FDP + VoID\n(routes/fdp.py)"]
    Shapes["ShEx / SHACL shapes\n(routes/shapes.py)"]
    Examples["SPARQL examples\n(routes/examples.py)"]
    API["SPARQLList REST API\n(routes/sparqlist.py)"]
    Mapping["Column-mapping tool\ntabular -> RDF\n(routes/datasets.py, services/mapping_service.py)"]
    Transfer["Graph transfer\nCONSTRUCT-copy from another\nSPARQL endpoint\n(services/graph_transfer.py)"]

    Dataset --> Graph
    Dataset --> SPARQL
    Dataset --> FDP
    Dataset --> Shapes
    Dataset --> Examples
    Dataset --> API
    Dataset --> Mapping
    Dataset --> Transfer
```

A dataset's `platform` column (`qlever`, `fuseki`, `oxigraph`, `virtuoso`,
`blazegraph`, `rdf4j`, or `comunica`) is fixed per dataset, not per file —
everything loaded into it lands in the same backend and the same named graph
(`graph_base + "/data"`).

## Async job pipeline

RDF never gets parsed, reasoned over, or loaded on the request thread — a
multi-gigabyte file would outlive any HTTP timeout. Three entry points
(direct upload, mapping materialize, graph transfer) all funnel into the same
job queue and the same background thread:

```mermaid
flowchart LR
    Upload["Upload\n(routes/datasets.py: upload)"]
    MapLoad["Mapping materialize\n(routes/datasets.py: mapping_load)"]
    XferSubmit["Graph transfer\n(routes/datasets.py: transfer_submit)"]

    Queue[("upload_jobs table\nstatus=queued")]
    Thread["job_runner background thread\n(single daemon thread,\none job at a time)"]

    Fetch["fetch\n(source_url, if any)"]
    Extract["extract archive\n(.zip / .tgz / .gz)"]
    Normalize["normalize\n(riot, for .owl/.rdf)"]
    Reason["OWL/RDFS reasoning\n(optional: owlrl, or Jena infer)"]
    Load["load / replace graph\nvia triplestore registry"]
    Result[("status=done / error")]

    Upload --> Queue
    MapLoad --> Queue
    XferSubmit --> Queue
    Queue --> Thread
    Thread --> Fetch --> Extract --> Normalize --> Reason --> Load --> Result
```

A page polls `/upload/status/<job_id>` for progress. Steps a given job
doesn't need (no `source_url`, not an archive, not `.owl`/`.rdf`, reasoning
not requested) are skipped, not run as no-ops.

## Triplestore backend abstraction

Every backend implements the same small interface, so `job_runner` and the
SPARQL-proxy route never know which store they're talking to:

```mermaid
classDiagram
    class TriplestoreRegistry {
        +get(dataset_row) Store
        +get_by_name(name) Store
    }
    class QLeverStore {
        +sparql_query()
        +sparql_update()
        +load_rdf_file() batched INSERT DATA
        +replace_graph() drop + load
    }
    class SparqlHttpStore {
        +sparql_query()
        +load_rdf_file() GSP POST, batched for .nt/.nq
        +replace_graph() GSP PUT
    }
    class ComunicaStore {
        +sparql_query() federated over dataset's sources column
    }
    TriplestoreRegistry --> QLeverStore
    TriplestoreRegistry --> SparqlHttpStore : Fuseki, Virtuoso, Oxigraph, Blazegraph, RDF4J
    TriplestoreRegistry --> ComunicaStore
```

QLever has no Graph Store Protocol, so `QLeverStore` speaks SPARQL Update
directly (`services/qlever.py`); every other backend implements GSP and
shares one `SparqlHttpStore` class (`services/sparql_http.py`), configured
per backend with its own base URL, paths, and auth. Both loaders batch
line-based (`.nt`/`.nq`) files so peak memory and request size stay bounded
regardless of file size — the one thing that must never scale with the file
being loaded, on a backend serving every tenant from one shared instance.

## Key files

| Concern | File |
|---|---|
| Route entry points | `routes/*.py` |
| Dataset CRUD, upload, SPARQL proxy | `routes/datasets.py` |
| Triplestore registry | `services/triplestore.py` |
| Backend clients | `services/qlever.py`, `services/sparql_http.py`, `services/comunica.py` |
| Background job queue | `services/job_runner.py` |
| Tabular-to-RDF mapping | `services/mapping_service.py` |
| Cross-instance graph copy | `services/graph_transfer.py` |
| FAIR Data Point / VoID | `routes/fdp.py`, `services/void_service.py` |
| Shape inference/validation | `services/shexer_service.py`, `services/rudof_service.py`, `services/rdfconfig_service.py` |
| Sister-instance registry | `services/mangal.py`, `mangal.yaml` |
| DB schema | `db/schema.sql` |
