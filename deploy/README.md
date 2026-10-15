# Deployment — semscape.org

The live configuration for the semscape.org deployment, captured from the
running host so it stops living only in `/etc/caddy/` and `/var/www/`.

Three vhosts, one Caddy:

| Host | What it is | Backend |
|---|---|---|
| **koetai.semscape.org** | the platform (this repo) | Flask on :3002 |
| **sparql.semscape.org** | **legacy — redirect-only since 2026-09-10**, see below | Caddy `redir`, no backend |
| **mangal.semscape.org** | vanity host for the Mangal network directory | rewrites `/` to `/mangal`, same Flask app on :3002 |

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
3. **Legacy static site — retired.** `sparql.semscape.org` used to serve
   `frontend/` via Caddy's `file_server` from `/var/www/koetai/`. As of the
   2026-09-10 consolidation it no longer does: the live Caddy config for that
   host is pure `redir`, nothing is read from `/var/www/koetai/` any more. See
   [frontend/](#frontend) below — the files and the `cp` habit outlived the
   routing that made copying them matter.
4. **Caddy** — `/etc/caddy/Caddyfile` is the live config. **The copy committed
   here (`deploy/Caddyfile`) is currently stale** — see the warning below
   before trusting or deploying from it.

Updating the live host from this repo:

```bash
git pull
sudo systemctl restart koetai-platform   # only if app code (not just templates) needs a fresh import;
                                          # Jinja templates are cached per-process either way — a
                                          # template-only change still needs this restart to show up
caddy validate --config /etc/caddy/Caddyfile   # if you ARE pushing a Caddyfile change — always, before reloading
sudo systemctl reload caddy                    # only if /etc/caddy/Caddyfile actually changed
```

`deploy/Caddyfile` is not part of this flow right now (see below) — edit
`/etc/caddy/Caddyfile` directly, then paste the result back into this repo as
its own change, rather than deploying the committed file outward.

Copying `deploy/frontend/*` to `/var/www/koetai/` is optional housekeeping, not
a deploy step: nothing serves that directory any more (see
[frontend/](#frontend)). Do it only to keep the on-disk archive matching the
repo when `deploy/frontend/*` changes, e.g.:

```bash
cp deploy/frontend/sparql.html deploy/frontend/index_ahri.html /var/www/koetai/
```

## Caddyfile

**Warning: `deploy/Caddyfile` in this repo does not match the live
`/etc/caddy/Caddyfile`.** It still has `sparql.semscape.org` as `root * /var/www/koetai`
+ `file_server`, a `/api/*` proxy to a FastAPI process on :3000, and an
`/admin-api/*` + `/admin.html` block behind `{env.ADMIN_PASSWORD_HASH}` basic
auth. All of that was retired in the 2026-09-10 consolidation. The live file
has none of it: `sparql.semscape.org` is `redir`-only (see below), there is no
admin basic-auth block at all any more, and `mangal.semscape.org` (added since)
isn't in the committed copy either. Treat `deploy/Caddyfile` as **historical**
until someone copies the real `/etc/caddy/Caddyfile` back over it — don't
`cp` it onto a live host as this doc used to suggest.

What the **live** Caddyfile encodes, post-consolidation:

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
  koetai-admin was shut down and its routes (and the admin.html page, and the
  basic-auth block guarding it) removed entirely — there is nothing on this
  host left for `ADMIN_PASSWORD_HASH` to protect.
- Everything else on `sparql.semscape.org` → **301** to `koetai.semscape.org/`.
- `mangal.semscape.org` → rewrites `/` to `/mangal`, then proxies the same
  Flask app on :3002. Not present in `deploy/Caddyfile`.

## frontend/

**No longer served by Caddy.** Until 2026-09-10, `sparql.semscape.org` was a
static site — `sparql.html` (multi-endpoint SPARQL editor), `fdp.html` (FDP
browser), `index_ahri.html` (AHRI landing page), `examples.html`, `index.html`
— plus a FastAPI proxy on :3000, served via `root * /var/www/koetai` +
`file_server`. That host is now `redir`-only (see the Caddyfile section
above), so none of this is reachable from the live site any more.

The files still exist both here and on disk at `/var/www/koetai/`, and are
still occasionally edited and kept in sync by hand (e.g. the clickable-URL fix
in issue #19/PR #22 touched `sparql.html` and `index_ahri.html` alongside the
platform's own templates) — but that sync is pure archival housekeeping now,
not a deployment step. If this functionality is wanted again, it needs
`/etc/caddy/Caddyfile` to actually route to `/var/www/koetai/` once more, which
it currently does not.

This is distinct from the platform's own UI, which is server-rendered from
`../templates/` and served on koetai.semscape.org.

## Relationship to the old koetai-deploy repo

`codeberg.org/andrawaag/koetai` (a.k.a. koetai-deploy) packaged an earlier
architecture: one QLever container per dataset, a routing proxy, and a fork of
the admin API. That model has been superseded — datasets are named graphs in a
shared store now, and a local install is `docker compose up` from the repo root
(`../docker-compose.yml`). The parts of koetai-deploy still worth keeping — the
legacy frontend archive and a historical Caddy config — are here. Its history
remains on Codeberg for reference; the repo can be archived.
