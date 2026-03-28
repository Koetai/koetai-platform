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
    is_public   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, slug)
);

CREATE TABLE IF NOT EXISTS shapes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id  INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    format      TEXT    NOT NULL CHECK(format IN ('shex','shacl')),
    source      TEXT    NOT NULL CHECK(source IN ('inferred','uploaded')),
    content     TEXT    NOT NULL,
    mermaid     TEXT,                      -- Mermaid diagram source
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
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
