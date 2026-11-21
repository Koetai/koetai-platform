# Backups — the case for them, and how to add them

**Nothing in this repository backs anything up.** That is a deliberate choice
for a small install, not an oversight, and this note exists so the choice can be
revisited on purpose rather than after a loss.

Read this when the answer to "who else would be upset if this instance vanished
tomorrow?" stops being "nobody".

## Why there is nothing here yet

A backup regime is cheap to write and expensive to keep. The script is twenty
lines; what costs is the attention — checking it still runs, noticing when it
silently stops, keeping somewhere to put the files, and periodically proving a
restore actually works. An unverified backup is worse than none, because it
buys confidence it has not earned.

For a single-author instance holding data that also exists on the author's own
machine, that ongoing cost buys very little. The calculus changes sharply the
moment other people's work is in there.

## What is actually at risk

The valuable thing and the big thing are not the same thing, and that is the
whole shape of the problem. Measured on a fresh cloud install:

| | size | replaceable? |
|---|---|---|
| **`koetai.db`** | **~100 KB** | **No** |
| **`.env`** | ~1 KB | Only by re-registering with ORCID |
| Uploaded sources | 0 → GBs | Yes, if the originals exist elsewhere |
| Triplestore volume | 0 → 100s of GB | Yes, by reloading |
| Caddy certificates | ~170 KB | Yes, re-issued automatically |

`koetai.db` holds users, datasets, graph URIs, shapes, SPARQL examples, saved
queries, web sources and FDP metadata. It stores *metadata about* triples and
never the triples themselves, which is why it stays in the low megabytes however
large the store grows.

Lose it and the triples survive in the store, but nothing knows they are there:
no dataset points at them, no endpoint serves them, and the graph URIs that
would let you find them again were only ever recorded in that file. The store
becomes an anonymous heap.

So the backup that matters is **the database and `.env`** — kilobytes, taken in
under a second. Copying hundreds of gigabytes nightly to protect data you can
regenerate is the wrong trade, and the thing most likely to make the regime get
switched off.

### The caveat on "replaceable"

Reloading the store assumes the sources still exist. In production
`KEEP_UPLOADED_SOURCES` defaults to true, so they sit in the `koetai-data`
volume — on the same disk as everything else, which is no help if the disk is
what failed. Recovery therefore assumes one of: the originals are still on your
own machine, they are fetchable again from their URLs, or you chose to back up
the sources too.

Worth deciding explicitly rather than discovering.

## The one technical catch

**You cannot simply copy the database file.** Koetai runs SQLite in WAL mode, so
at any moment the durable state is spread across `koetai.db`, `koetai.db-wal`
and `koetai.db-shm`. A `cp` taken mid-write captures an inconsistent set, and the
result may be subtly wrong or refuse to open at all — and you will not find out
until you need it.

Use SQLite's online backup API, which takes a consistent snapshot of a database
that is being written to. It is available as `sqlite3`'s `.backup` command, and
from Python as `Connection.backup()`, which is convenient here because the app
container already has Python. `VACUUM INTO` is an equivalent alternative that
also compacts the result.

## A shape for the implementation

Untested — written from the design, not run. Treat it as a starting point, and
prove the restore before trusting it.

```sh
#!/bin/sh
# Consistent snapshot of the Koetai database plus .env. Small and quick;
# safe to run against a live instance.
set -eu
cd "$(dirname "$0")/.."

DEST="${KOETAI_BACKUP_DIR:-./backups}"
KEEP="${KOETAI_BACKUP_KEEP:-14}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$DEST"

# The backup API, not cp — see "The one technical catch" above.
$COMPOSE exec -T koetai python -c "
import sqlite3
src = sqlite3.connect('/data/koetai.db')
dst = sqlite3.connect('/data/.snapshot.db')
src.backup(dst)
dst.close(); src.close()"

$COMPOSE cp koetai:/data/.snapshot.db "$STAGE/koetai.db"
$COMPOSE exec -T koetai rm -f /data/.snapshot.db
cp .env "$STAGE/.env"

tar czf "$DEST/koetai-$STAMP.tar.gz" -C "$STAGE" koetai.db .env
chmod 600 "$DEST/koetai-$STAMP.tar.gz"

ls -1t "$DEST"/koetai-*.tar.gz | tail -n +$((KEEP + 1)) | xargs -r rm -f
echo "backup: $DEST/koetai-$STAMP.tar.gz"
```

The archive contains credentials, hence `chmod 600`; whatever it is copied to
deserves the same care.

Nightly, via cron:

```
15 3 * * * cd /root/koetai-platform && ./scripts/backup.sh >> /var/log/koetai-backup.log 2>&1
```

### Getting it off the machine

**This is the part that matters.** A backup on the same disk protects against
the application, not against the disk, the server, or the account. Everything
above is the easy half.

Roughly by effort:

- **Pull to your own machine.** A cron on *your* side running `rsync` or `scp`
  from the server. Free, and the copy is somewhere genuinely independent — but
  it only runs when your machine is awake.
- **Hetzner Storage Box.** A few euros a month, speaks `rsync` and `sftp`, and
  sits in the same datacentre so transfers are quick. Purpose-built for exactly
  this, and the least fiddly paid option.
- **Any S3-compatible bucket.** More setup and a credential to manage, but the
  most portable and the easiest to make append-only, which is what protects a
  backup from a mistake on the server propagating into it.

Given the sizes involved, almost anything works. The failure mode to design
against is not capacity — it is nobody noticing the copies stopped six weeks
ago.

## Restoring

A backup nobody has restored is a hypothesis. Test it once, on a scratch host,
before relying on it.

```sh
# Stop the app first: restoring under a running instance means writing the file
# out from under open connections.
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop koetai

tar xzf koetai-TIMESTAMP.tar.gz -C /tmp/restore
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  cp /tmp/restore/koetai.db koetai:/data/koetai.db

docker compose -f docker-compose.yml -f docker-compose.prod.yml start koetai
```

If the store was lost too, the database now describes datasets whose graphs are
empty. Each needs its source reloaded into the graph URI the database already
records — the URIs are what make this recoverable at all, and why the database
is the piece worth protecting.

Restoring `.env` matters for a different reason: `SECRET_KEY` signs sessions, so
a different one signs everybody out, and `BASE_URL` is baked into existing graph
URIs and must come back identical.

## When to stop deferring this

Any one of these is enough:

- **Someone other than you has data in it.** Their work is not yours to lose,
  and they have no way to know the risk they are running.
- **Something in it does not exist anywhere else** — shapes written in the UI,
  curated SPARQL examples, dataset descriptions. These are authored *here* and
  have no upstream copy, unlike the triples.
- **Anything cites it.** A URI in a paper is a promise about the future.
- **It stops being quick to rebuild.** The honest test: could you reconstruct
  this instance in an afternoon from things you still have? While the answer is
  yes, this note can keep waiting.
