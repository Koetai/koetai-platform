-- Koetai Platform — SQLite schema

CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    orcid_id    TEXT    NOT NULL UNIQUE,   -- e.g. 0000-0002-1825-0097
    name        TEXT,
    email       TEXT,
    is_admin    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS invitations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT    NOT NULL UNIQUE,
    created_by  INTEGER NOT NULL REFERENCES users(id),
    used_by     INTEGER REFERENCES users(id),
    used_at     TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS datasets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    slug        TEXT    NOT NULL,          -- URL-safe name
    label       TEXT    NOT NULL,
    description TEXT,
    graph_base  TEXT    NOT NULL UNIQUE,   -- base URI for named graphs
    port        INTEGER,                   -- QLever instance port (NULL = shared)
    platform    TEXT    NOT NULL DEFAULT 'qlever' CHECK(platform IN ('qlever','fuseki')),
    is_public    INTEGER NOT NULL DEFAULT 1,
    fdp_license  TEXT    NOT NULL DEFAULT 'https://creativecommons.org/licenses/by/4.0/',
    fdp_version  TEXT    NOT NULL DEFAULT '1.0',
    fdp_keywords TEXT    NOT NULL DEFAULT '',   -- comma-separated
    fdp_theme    TEXT    NOT NULL DEFAULT '',   -- vocabulary/theme URI
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, slug)
);

CREATE TABLE IF NOT EXISTS shapes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id      INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    format          TEXT    NOT NULL CHECK(format IN ('shex','shacl')),
    source          TEXT    NOT NULL CHECK(source IN ('inferred','uploaded')),
    content         TEXT    NOT NULL,
    mermaid         TEXT,                  -- Mermaid diagram source
    rdfconfig_model  TEXT,                 -- model.yaml content
    rdfconfig_prefix TEXT,                 -- prefix.yaml content
    rdfconfig_svg    TEXT,                 -- SVG from rdf-config --schema
    rdfconfig_sparql TEXT,                 -- SPARQL from rdf-config --sparql
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS examples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id  INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    slug        TEXT    NOT NULL,
    label       TEXT    NOT NULL,
    description TEXT,
    query       TEXT    NOT NULL,
    keywords    TEXT,                      -- JSON array
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(dataset_id, slug)
);

CREATE TABLE IF NOT EXISTS github_sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id      INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    repo            TEXT    NOT NULL,   -- "owner/repo"
    branch          TEXT    NOT NULL DEFAULT 'main',
    path            TEXT    NOT NULL DEFAULT '',
    provider        TEXT    NOT NULL DEFAULT 'github',  -- github | gitlab
    last_commit_sha TEXT,
    last_imported_at TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(dataset_id, provider, repo, branch, path)
);

CREATE TABLE IF NOT EXISTS web_sources (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id       INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    page_url         TEXT    NOT NULL,
    label            TEXT    NOT NULL DEFAULT '',
    last_checked_at  TEXT,
    last_imported_at TEXT,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(dataset_id, page_url)
);

CREATE TABLE IF NOT EXISTS web_source_files (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id      INTEGER NOT NULL REFERENCES web_sources(id) ON DELETE CASCADE,
    filename       TEXT    NOT NULL,
    url            TEXT    NOT NULL,
    etag           TEXT,
    last_modified  TEXT,
    content_length INTEGER,
    imported_at    TEXT,
    UNIQUE(source_id, url)
);

CREATE TABLE IF NOT EXISTS upload_jobs (
    id          TEXT    PRIMARY KEY,           -- UUID
    dataset_id  INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'queued'
                        CHECK(status IN ('queued','running','done','error')),
    phase       TEXT    NOT NULL DEFAULT '',   -- 'parsing', 'reasoning', 'loading'
    message     TEXT    NOT NULL DEFAULT '',
    file_path   TEXT    NOT NULL,
    graph_uri   TEXT    NOT NULL,
    apply_owl   INTEGER NOT NULL DEFAULT 0,
    owl_regime  TEXT    NOT NULL DEFAULT 'OWL_RL',
    replace_data INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS sparqlist_queries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id  INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    slug        TEXT    NOT NULL,
    label       TEXT    NOT NULL,
    description TEXT,
    template    TEXT    NOT NULL,          -- SPARQL template with {{param}} placeholders
    params      TEXT    NOT NULL DEFAULT '[]',  -- JSON array of {name, label, default}
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(dataset_id, slug)
);
