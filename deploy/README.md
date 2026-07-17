# Deployment — semscape.org

The live configuration for the two-site deployment, captured from the running
host so it stops living only in `/etc/caddy/` and `/var/www/`.

Two vhosts, one Caddy:

| Host | What it is | Backend |
|---|---|---|
| **koetai.semscape.org** | the platform (this repo) | Flask on :3002 |
| **sparql.semscape.org** | the static SPARQL explorer + FDP/admin pages | files in `frontend/`, proxied services |

## How this host is deployed

The platform itself runs as a systemd service, not from `docker compose` (that
compose file is for *local* single-user installs — see the repo root README).
The production layout:

1. **App** — `koetai-platform.service` runs `venv/bin/python3 app.py` on :3002
   from a checkout of this repo, with real settings in `.env` (ORCID,
   `KOETAI_MODE=community`, backend URLs). `.env.example` lists every variable.
2. **Triplestore** — `qlever-platform.service` runs `qlever-server` on :7030
   (Qleverfile in `qlever-sparql-deployment/platform/`, `PERSIST_UPDATES = true`
   so uploads survive restarts). Fuseki on :3030 holds the larger datasets.
3. **Static site** — the `frontend/` files are served by Caddy from
   `/var/www/koetai/`.
4. **Caddy** — `/etc/caddy/Caddyfile` is this directory's `Caddyfile` with a real
   `ADMIN_PASSWORD_HASH` in the environment.

Updating the live host from this repo, until a deploy script exists:

```bash
git pull
sudo cp deploy/Caddyfile   /etc/caddy/Caddyfile     # then set ADMIN_PASSWORD_HASH
sudo cp deploy/frontend/*  /var/www/koetai/
caddy validate --config /etc/caddy/Caddyfile        # always, before reloading
sudo systemctl reload caddy
sudo systemctl restart koetai-platform              # only if app code changed
```

## Caddyfile

`Caddyfile` is the live config with one edit: the admin basic-auth hash is
replaced by `{env.ADMIN_PASSWORD_HASH}` so no credential is committed. Generate a
hash with `caddy hash-password` and provide it via the environment (systemd
`EnvironmentFile`, or the shell that launches Caddy).

What it encodes, post-consolidation:

- `sparql.semscape.org/endpoint2`, `/endpoint3` → **308** to the platform. The
  ordo and ahri datasets were migrated into the platform's named-graph model, so
  the old standalone-QLever URLs redirect to their real home.
- `sparql.semscape.org/fdp/*` published identifiers → **301** to the platform's
  FAIR Data Point. They never resolved before (there was no `/fdp/*` route); the
  platform now generates the FDP from its datasets.
- `endpoint1` (olympics), `endpoint-fdp`, `endpoint-wikipathways` — **retired**.
  Olympics was QLever's demo dataset; the FDP moved into the platform; the
  WikiPathways download had silently failed and only ever held VoID metadata.
- `/admin-api/*` + `/admin.html` — **retired**. These proxied koetai-admin on
  :3001, which managed the standalone QLever instances. With those gone,
  koetai-admin was shut down and its routes (and the admin.html page) removed.

## frontend/

The static `sparql.semscape.org` site, served by Caddy's `file_server` from
`/var/www/koetai/`. `sparql.html` is the multi-endpoint SPARQL editor; its
endpoint list points at the migrated platform datasets. `fdp.html` and
`index_ahri.html` are the FDP browser and the AHRI landing page.

This is distinct from the platform's own UI, which is server-rendered from
`../templates/` and served on koetai.semscape.org.

## Relationship to the old koetai-deploy repo

`codeberg.org/andrawaag/koetai` (a.k.a. koetai-deploy) packaged an earlier
architecture: one QLever container per dataset, a routing proxy, and a fork of
the admin API. That model has been superseded — datasets are named graphs in a
shared store now, and a local install is `docker compose up` from the repo root
(`../docker-compose.yml`). The parts of koetai-deploy still worth keeping — the
live frontend and the real Caddy config — are here. Its history remains on
Codeberg for reference; the repo can be archived.
