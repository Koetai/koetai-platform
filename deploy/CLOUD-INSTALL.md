# Installing Koetai on a cloud server

A start-to-finish install on a fresh Linux host, ending with an HTTPS site you
sign into with your ORCID. Written against Hetzner Cloud because that is what it
was tested on; nothing here is Hetzner-specific beyond the sizing note.

Budget about half an hour, most of it waiting for DNS.

## Before you start

You need three things, and two of them take time to arrange, so do them first:

1. **A domain name** you can add a DNS record to.
2. **An ORCID application** — register at
   <https://orcid.org/developer-tools>. It gives you a client ID and secret, and
   asks for a redirect URI: use `https://YOUR-DOMAIN/auth/callback`. This has to
   match exactly, including the scheme.
3. **Your own ORCID iD.** You will be the first administrator, and on a new
   install nothing else can make one — registration is invitation-only,
   invitations are issued by an administrator, so without this the instance
   cannot be signed into at all.

## 1. Pick a server

The store is what grows. Measured on a real instance: **115 million triples took
21.8 GB**, or roughly 190 bytes per triple. Estimate from the triples you expect,
then leave room — a load expands its source file on disk, and deleted data does
not return space to the filesystem until the store is compacted.

| Triples | Store | Suggested disk |
|---|---|---|
| 100 M | ~19 GB | 40 GB |
| 300 M | ~57 GB | 100 GB |
| 500 M | ~95 GB | 160 GB |

In practice the disk tends to sort itself out: plans scale disk with memory, so
a machine chosen for the RAM below already comes with far more space than the
table asks for. The install tested here had 301 GB — around 1.5 billion triples'
worth — on a 16 GB machine. Check Admin → Storage occasionally rather than
planning for it now.

**Memory: 16 GB, and the reason is not what you would guess.**

Serving is cheap. Oxigraph answers from disk — a full aggregate over 102 million
triples ran in 29 seconds while the process held under 300 MB. Adding datasets
does not raise that; RAM only buys page cache, which makes queries quicker.

Loading is what costs, and how much depends on which path:

| | measured |
|---|---|
| Query over 102M triples | ~300 MB |
| Ordinary upload (batched) | ~1.5 GB above resting, whatever the file size |
| Fast load, 9M triples | **4.1 GB**, and it grows with the file |

The ordinary upload path sends the file in batches, so its cost is bounded by
the batch and not the file. The fast loader sorts in memory instead, which is
where the speed comes from and why it is the hungriest thing here.

So: **8 GB is enough if you only ever use ordinary uploads. 16 GB is the
sensible floor if you use the fast loader**, and 32 GB if you routinely fast-load
files of many gigabytes. `LOADER_MEMORY` caps the loader (6 GB by default) so an
oversized import fails on its own rather than taking the host with it — worth
raising if you have the RAM, since hitting the cap fails the load.

**Processor: ARM is the better buy.** Hetzner's ARM (CAX) instances cost
substantially less per core than the x86 lines, and Oxigraph ships multi-arch
images that run natively. Two things to know before choosing ARM: the Fuseki
image in `docker-compose.yml` is x86-only (use a multi-arch one, or leave Fuseki
alone — Oxigraph is the default), and RUDOF, which shape *validation* uses, has
no ARM build. Shape *inference* is unaffected; it is a Python package.

A CAX31-sized machine — 8 vCPU, 16 GB — is a reasonable starting point for a
few hundred million triples, and its included disk covers that comfortably.

### Making 16 GB go far

The pieces do not all peak together, which is what makes this comfortable rather
than tight:

| | at rest | at peak |
|---|---|---|
| Oxigraph | ~0.3 GB | ~6 GB during a batched load |
| The app | ~60 MB | ~100 MB |
| Fast loader | not running | up to `LOADER_MEMORY` (6 GB) |
| Fuseki, if started | **4.2 GB holding nothing** | more |

The store and the fast loader never overlap: the loader only runs while the
store is stopped, which is the whole reason it needs stopping. So the peak is
one or the other, not both.

Fuseki is the thing to watch. The JVM claims its heap whether or not anything is
in it, so an unused Fuseki costs about 4 GB on a machine where that is a quarter
of the RAM. The production overlay therefore does **not** start it. Add
`--profile fuseki` if you want Fuseki-backed datasets, and drop `FUSEKI_HEAP` to
suit if you do.

On 16 GB with Oxigraph alone, these are reasonable:

```bash
OXIGRAPH_MEM_LIMIT=8g
LOADER_MEMORY=8g
KOETAI_MEM_LIMIT=1g
```

## 2. Point DNS at the server

Add an `A` record for your domain to the server's IPv4 address, and an `AAAA`
for IPv6 if you have one. **Do this before starting the stack.** Caddy asks
Let's Encrypt for a certificate on first run, and that only works once the name
resolves to this host — otherwise it fails, retries with backoff, and you spend a
while wondering why the site will not load.

Check it has propagated:

```bash
dig +short YOUR-DOMAIN
```

### If your DNS is on Cloudflare, turn the proxy off

Set the record to **DNS only** — the grey cloud, not the orange one.

Cloudflare's proxy caps the size of a request body, at 100 MB on the free plan.
This platform exists to move files considerably larger than that, and the proxy
rejects them before they reach Caddy or the app, so the size limits you have
configured here are never consulted and the error does not mention size. Uploads
below the cap work, which makes it a confusing thing to diagnose later.

Proxying also terminates TLS at Cloudflare, and Caddy is already obtaining a
certificate for you, so there is nothing gained here to weigh against it. If you
want Cloudflare's other features, put them in front of something that is not the
upload path.

## 3. Install Docker and get the code

```bash
curl -fsSL https://get.docker.com | sh
git clone https://github.com/rdmpage/koetai-platform.git
cd koetai-platform
```

That is the fork, deliberately. The production overlay and the Caddy
configuration this guide depends on are not in the upstream repository, so a
clone of `Koetai/koetai-platform` reaches step 5 and stops with a missing
`docker-compose.prod.yml`.

## 4. Write the configuration

Everything the production overlay needs comes from a `.env` file beside the
compose files. It refuses to start rather than guess, so a missing value is an
error at the point of typing rather than a puzzle later.

```bash
cat > .env <<'EOF'
KOETAI_DOMAIN=koetai.example.org
BASE_URL=https://koetai.example.org

# Anything long and random. Sessions are signed with it; changing it later
# signs everyone out. Generate it on the server —
#   openssl rand -hex 32
# — rather than anywhere it would pass through a third party on the way.
SECRET_KEY=CHANGE-ME

# From https://orcid.org/developer-tools. Copy the client ID exactly as that
# page gives it, whatever shape it is — on a personal account it is commonly
# your own ORCID iD rather than an APP-prefixed code, and that is correct.
# The redirect URI must match what you registered, character for character.
ORCID_CLIENT_ID=0000-0000-0000-0000
ORCID_CLIENT_SECRET=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
ORCID_REDIRECT_URI=https://koetai.example.org/auth/callback

# You. Without this nobody can sign in — see "Before you start".
KOETAI_ADMIN_ORCIDS=0000-0000-0000-0000

# Only read in internal mode; harmless otherwise.
# KOETAI_MODE=internal
# INTERNAL_ALLOWED_ORCIDS=0000-0001-1111-1111
# INTERNAL_ALLOWED_DOMAINS=your-institute.org

FUSEKI_ADMIN_PASSWORD=CHANGE-ME-TOO
EOF
chmod 600 .env
```

`BASE_URL` deserves a moment. It becomes part of every dataset's graph URIs, and
those are written into the store when a dataset is created. Changing it later
does not rewrite them, so decide the final hostname **before** loading anything.

### Which mode?

- **`community`** (the default) — anyone with an ORCID may join, but only with an
  invitation. Invitations are issued from the Admin menu. This is the public
  model.
- **`internal`** — no invitations; instead an allowlist of ORCIDs, or of email
  domains for people who have made an email public on ORCID. Better for a group
  hosting for its own members.

Both sign in with ORCID. The third mode, `local`, signs *every visitor* in as the
same user and must not be used on a public host.

## 5. Start it

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
               --profile oxigraph up -d
```

The first start takes a few minutes: it builds the app image and Caddy fetches a
certificate. Watch it happen:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f caddy
```

Then open `https://YOUR-DOMAIN` and sign in with the ORCID you listed as an
administrator. You should arrive at an empty dashboard with an Admin menu.

## 6. Check it over

- **Admin → Storage** reports host memory and disk. Worth a look after a large
  load: memory is what decides whether one survives.
- **My Datasets → New Dataset** lists the triplestores this install actually has
  running; Oxigraph should be selectable and Fuseki not, unless you started it.
- Uploads up to `MAX_UPLOAD_MB` (4 GB by default) go through the browser.
  Anything larger is better fetched by the dataset's **Web** tab, which pulls
  from a URL server-side and has no such limit.

## What this does not set up

- **Backups.** Nothing here backs anything up. What matters is the `koetai-data`
  volume — it holds the SQLite database, which is the only copy of your users,
  datasets and shapes. The triplestore volumes hold data that can be reloaded
  from sources; the database cannot be reconstructed. `deploy/BACKUPS.md` covers
  what is worth copying, why a plain `cp` of a live SQLite file is unsafe, and
  when deferring this stops being reasonable.
- **The fast loader.** `docker-compose.yml` has an optional `loader` service,
  behind the `fastload` profile, that makes large imports several times quicker.
  It mounts the Docker socket, which is root on the host. On a machine only you
  use that may be a fair trade; on a shared instance think harder, and read
  `deploy/loader-agent/agent.sh` first — it is deliberately short. Over SSH,
  `scripts/bulk_load.sh` gives the same speed with no daemon holding the socket.
- **A firewall.** Only 80 and 443 need to be open. Nothing else in the stack
  publishes a port — the app, the stores and the agent all talk over the compose
  network.
- **Fuseki.** The production overlay puts it behind a profile, so it does *not*
  start with the command in step 5 — an idle JVM costs about 4.2 GB holding
  nothing. Add `--profile fuseki` alongside `--profile oxigraph` if you want
  Fuseki-backed datasets.

## If something goes wrong

**The certificate never arrives.** Almost always DNS: check `dig +short
YOUR-DOMAIN` matches the server, and that 80 and 443 are reachable from outside.
Let's Encrypt validates over port 80.

**"Your ORCID is not permitted on this instance."** In `community` mode you
needed an invitation, or to be listed in `KOETAI_ADMIN_ORCIDS`. In `internal`
mode you are not on the allowlist. Both are in `.env`; restart after editing.

**Sign-in loops back to the front page.** Usually `ORCID_REDIRECT_URI` not
matching what is registered with ORCID — including `https` versus `http`.

**A large upload fails with a dropped connection.** Historically this was the
store running out of memory; loads are now sent in batches, which bounds it.
Check Admin → Storage for headroom, and `dmesg | grep -i "killed process"` on
the host for a definitive answer.

**Uploads above about 100 MB fail, smaller ones work.** A proxy in front of the
server is refusing the body — Cloudflare's does this by default. See step 2.
