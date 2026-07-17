import os
import secrets
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ── Mode ──────────────────────────────────────────────────────────────────────
# community: the hosted, multi-tenant site — ORCID sign-in, invitation-gated.
# local:     a single-user install on your own machine — no accounts, no ORCID.
# The two run the same code; local simply skips the parts that only make sense
# when strangers share an instance.
KOETAI_MODE = os.environ.get("KOETAI_MODE", "community").strip().lower()
if KOETAI_MODE not in ("community", "local"):
    raise RuntimeError(
        f"KOETAI_MODE must be 'community' or 'local', not {KOETAI_MODE!r}"
    )
IS_LOCAL = KOETAI_MODE == "local"

# The single user a local install acts as. Keeping an ORCID-shaped slot means the
# /u/<owner>/<slug> URL space, graph URIs and FDP catalogs work unchanged.
LOCAL_ORCID = os.environ.get("LOCAL_ORCID", "local")
LOCAL_USER_NAME = os.environ.get("LOCAL_USER_NAME", "Local User")


def _required(name: str, local_default: str = "") -> str:
    """Config that community mode cannot run without, but local mode never uses.

    Failing loudly here beats an ImportError-shaped KeyError at startup.
    """
    value = os.environ.get(name)
    if value:
        return value
    if IS_LOCAL:
        return local_default
    raise RuntimeError(
        f"{name} is required when KOETAI_MODE=community. "
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
MAX_UPLOAD_MB = 500
