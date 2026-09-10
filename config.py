import os
import secrets
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ── Mode ──────────────────────────────────────────────────────────────────────
# All three run the same code and differ only in who may sign in:
#   community — the hosted public site. ORCID sign-in, registration invite-gated.
#   internal  — a group hosts it for its own members. ORCID sign-in, registration
#               gated by an allowlist (ORCID iDs and/or email domains) the host
#               controls, so members join without a per-person invite.
#   local     — a single-user install on your own machine. No accounts, no ORCID.
KOETAI_MODE = os.environ.get("KOETAI_MODE", "community").strip().lower()
if KOETAI_MODE not in ("community", "internal", "local"):
    raise RuntimeError(
        f"KOETAI_MODE must be 'community', 'internal', or 'local', not {KOETAI_MODE!r}"
    )
IS_COMMUNITY = KOETAI_MODE == "community"
IS_INTERNAL  = KOETAI_MODE == "internal"
IS_LOCAL     = KOETAI_MODE == "local"

# community and internal both sign in with ORCID; only local runs without it.
NEEDS_ORCID = IS_COMMUNITY or IS_INTERNAL

# The single user a local install acts as. Keeping an ORCID-shaped slot means the
# /u/<owner>/<slug> URL space, graph URIs and FDP catalogs work unchanged.
LOCAL_ORCID = os.environ.get("LOCAL_ORCID", "local")
LOCAL_USER_NAME = os.environ.get("LOCAL_USER_NAME", "Local User")


def _csv_set(name: str, lower: bool = False) -> frozenset:
    """Parse a comma-separated env var into a set of trimmed, non-empty values."""
    raw = os.environ.get(name, "")
    items = (p.strip() for p in raw.split(","))
    return frozenset(p.lower() if lower else p for p in items if p)


# internal-mode registration allowlist. A first-time ORCID may register only if
# it is on INTERNAL_ALLOWED_ORCIDS, or its (public) ORCID email is under a domain
# in INTERNAL_ALLOWED_DOMAINS. INTERNAL_ADMIN_ORCIDS are always allowed and get
# admin rights — set your own ORCID there to bootstrap a fresh instance.
INTERNAL_ALLOWED_ORCIDS  = _csv_set("INTERNAL_ALLOWED_ORCIDS")
INTERNAL_ALLOWED_DOMAINS = _csv_set("INTERNAL_ALLOWED_DOMAINS", lower=True)

# ORCIDs that are admin on this instance, in any mode, and may register without
# an invitation. Without this a fresh community install cannot be signed into at
# all: registering needs an invite, invites are created by an admin, and nothing
# ever makes the first admin — so there is no way in. INTERNAL_ADMIN_ORCIDS is
# still read, since instances already configured that way should keep working.
ADMIN_ORCIDS = _csv_set("KOETAI_ADMIN_ORCIDS") | _csv_set("INTERNAL_ADMIN_ORCIDS")
INTERNAL_ADMIN_ORCIDS = ADMIN_ORCIDS


def _required(name: str, local_default: str = "") -> str:
    """Config the ORCID modes cannot run without, but local mode never uses.

    Failing loudly here beats an ImportError-shaped KeyError at startup.
    """
    value = os.environ.get(name)
    if value:
        return value
    if not NEEDS_ORCID:
        return local_default
    raise RuntimeError(
        f"{name} is required when KOETAI_MODE={KOETAI_MODE}. "
        f"Set it in .env, or use KOETAI_MODE=local for a single-user install."
    )


# A local install has no shared sessions to protect, so a per-boot key is fine:
# the only cost is that it logs you out on restart, and local mode auto-signs-in.
SECRET_KEY          = _required("SECRET_KEY", local_default=secrets.token_hex(32))
ORCID_CLIENT_ID     = _required("ORCID_CLIENT_ID")
ORCID_CLIENT_SECRET = _required("ORCID_CLIENT_SECRET")
ORCID_REDIRECT_URI  = _required("ORCID_REDIRECT_URI")

ORCID_AUTH_URL    = "https://orcid.org/oauth/authorize"
ORCID_TOKEN_URL   = "https://orcid.org/oauth/token"
ORCID_API_URL     = "https://pub.orcid.org/v3.0"

# Triplestore backends. A dataset's `platform` column selects one of these;
# services/triplestore.py resolves the name to a client. Only the stores you
# actually run need to be configured — the rest simply report as unavailable.
QLEVER_PLATFORM_URL  = os.environ.get("QLEVER_PLATFORM_URL", "http://localhost:7030")
FUSEKI_BASE_URL      = os.environ.get("FUSEKI_BASE_URL", "http://localhost:3030")
FUSEKI_DATASET       = os.environ.get("FUSEKI_DATASET", "koetai")
# Leave blank for an unsecured Fuseki. A Fuseki started with ADMIN_PASSWORD set
# (as the docker image does) rejects writes with 401 without these.
FUSEKI_USER          = os.environ.get("FUSEKI_USER", "")
FUSEKI_PASSWORD      = os.environ.get("FUSEKI_PASSWORD", "")
VIRTUOSO_URL         = os.environ.get("VIRTUOSO_URL", "http://localhost:8890")
VIRTUOSO_USER        = os.environ.get("VIRTUOSO_USER", "")
VIRTUOSO_PASSWORD    = os.environ.get("VIRTUOSO_PASSWORD", "")
OXIGRAPH_URL         = os.environ.get("OXIGRAPH_URL", "http://localhost:7878")
BLAZEGRAPH_URL       = os.environ.get("BLAZEGRAPH_URL", "http://localhost:9999/bigdata")
RDF4J_URL            = os.environ.get("RDF4J_URL", "http://localhost:8080/rdf4j-server")
RDF4J_REPO           = os.environ.get("RDF4J_REPO", "koetai")
# Comunica: a federation *engine*, not a store. Datasets with platform='comunica'
# federate over a source list rather than holding uploaded data.
COMUNICA_BIN         = os.environ.get("COMUNICA_BIN", "comunica-sparql")
COMUNICA_TIMEOUT     = int(os.environ.get("COMUNICA_TIMEOUT", "120"))
BASE_URL          = os.environ.get("BASE_URL", "https://koetai.semscape.org")
UPLOAD_DIR        = Path(os.environ.get("UPLOAD_DIR", "/home/debian/koetai-platform/uploads"))
DEPLOY_DIR        = Path(os.environ.get("DEPLOY_DIR", "/home/debian/qlever-sparql-deployment"))
RUDOF_BIN         = os.environ.get("RUDOF_BIN", "/usr/bin/rudof")
JENA_BIN          = os.environ.get("JENA_BIN", "/home/debian/apache-jena-6.0.0/bin")
# Interpreter used for the shape-inference and OWL-reasoning subprocesses. These
# run out-of-process because they are slow and memory-hungry, not because they
# need a different environment: shexer/owlrl/lightrdf are in requirements.txt, so
# the running interpreter can serve. It used to default into koetai-admin's venv,
# which made this project silently depend on a sibling checkout.
SHEXER_VENV       = os.environ.get("SHEXER_VENV", sys.executable)
DB_PATH           = Path(os.environ.get("KOETAI_DB_PATH",
                                        Path(__file__).parent / "db" / "koetai.db"))
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
GITLAB_TOKEN      = os.environ.get("GITLAB_TOKEN", "")
GITHUB_ORG        = os.environ.get("GITHUB_ORG", "Koetai")
SPARQL_EXAMPLES_REPO = os.environ.get("SPARQL_EXAMPLES_REPO", "sparql-examples")

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

GRAPH_BASE = BASE_URL + "/u/{user}/{dataset}"

ALLOWED_RDF_EXTENSIONS = {".ttl", ".nt", ".n3", ".rdf", ".owl", ".trig", ".nq", ".jsonld"}
# Compressed RDF is how a large graph is normally published and normally kept:
# the upload cap applies to the bytes on the wire, so a gzip both fits under it
# and transfers in a fraction of the time. Accepted anywhere raw RDF is.
ALLOWED_ARCHIVE_EXTENSIONS = {".zip", ".gz", ".bz2", ".tgz"}
ALLOWED_UPLOAD_EXTENSIONS  = ALLOWED_RDF_EXTENSIONS | ALLOWED_ARCHIVE_EXTENSIONS
# Browser upload cap, enforced by Flask against Content-Length. Large files are
# better fetched server-side (Web sources), which this does not limit.
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "500"))
# Reachability probes for the "which stores are running" check. Deliberately
# short: this runs while a page is being rendered, and an unreachable host that
# refuses the connection answers instantly while one that simply never replies
# would otherwise hold the request for the full query timeout.
BACKEND_PROBE_TIMEOUT = int(os.environ.get("BACKEND_PROBE_TIMEOUT", "3"))
# How long a single load into a triplestore may take. Loading is bounded by the
# store, not the network: roughly 200 MB of N-Triples per 75 s into Fuseki on a
# laptop, so the old 300 s ceiling failed part-way through anything much over
# half a gigabyte. An hour is generous rather than tight; a job that hits it is
# stuck, not slow.
RDF_LOAD_TIMEOUT = int(os.environ.get("RDF_LOAD_TIMEOUT", "3600"))
# Line-based RDF (N-Triples, N-Quads) is sent to the store in batches of this
# many lines rather than as one request. A Graph Store Protocol write is one
# transaction, and the store holds it in memory until it commits: an unchunked
# 9M-triple load was measured at 12.7 GB resident, and two real imports were
# killed by the kernel OOM killer at ~10 GB. Batching bounds that to roughly the
# size of one batch, at the cost of the load no longer being atomic.
#
# 200,000 is measured, not guessed. Loading 2.4M triples into an empty Oxigraph,
# varying only this:
#
#     50,000    56.5s   42k/s   1652 MiB peak
#    200,000    38.6s   62k/s   1657 MiB
#    500,000    36.7s   65k/s   1907 MiB
#  1,000,000    45.0s   53k/s   2084 MiB
#  2,000,000    44.0s   55k/s   2665 MiB
#
# Bigger is not better past ~500k: throughput falls again while memory keeps
# climbing, so the largest batches are worse on both counts. 500k buys about 5%
# for 15% more memory, which is the wrong trade when memory is what kills a
# large load. Throughput against a store that already holds a hundred million
# triples is roughly half these figures — the shape holds, the numbers do not.
RDF_LOAD_BATCH_LINES = int(os.environ.get("RDF_LOAD_BATCH_LINES", "200000"))
# Whether the original file survives a successful load. Derived files — an
# OWL/XML conversion, a reasoned graph, the members of an archive — are always
# removed; this governs only the thing that was uploaded or fetched.
#
# Keeping it means a graph can be reloaded without fetching again, and that what
# was actually ingested is still on disk. It also means storing every upload for
# ever, which at multi-gigabyte scale is most of the disk. On by default because
# that is what the platform has always done; a large single-user install
# probably wants it off.
KEEP_UPLOADED_SOURCES = os.environ.get(
    "KEEP_UPLOADED_SOURCES", "true").strip().lower() not in ("0", "false", "no", "off")
