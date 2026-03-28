import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

SECRET_KEY        = os.environ["SECRET_KEY"]
ORCID_CLIENT_ID   = os.environ["ORCID_CLIENT_ID"]
ORCID_CLIENT_SECRET = os.environ["ORCID_CLIENT_SECRET"]
ORCID_REDIRECT_URI  = os.environ["ORCID_REDIRECT_URI"]

ORCID_AUTH_URL    = "https://orcid.org/oauth/authorize"
ORCID_TOKEN_URL   = "https://orcid.org/oauth/token"
ORCID_API_URL     = "https://pub.orcid.org/v3.0"

QLEVER_PLATFORM_URL = os.environ.get("QLEVER_PLATFORM_URL", "http://localhost:7030")
BASE_URL          = os.environ.get("BASE_URL", "https://koetai.semscape.org")
UPLOAD_DIR        = Path(os.environ.get("UPLOAD_DIR", "/home/debian/koetai-platform/uploads"))
DEPLOY_DIR        = Path(os.environ.get("DEPLOY_DIR", "/home/debian/qlever-sparql-deployment"))
RUDOF_BIN         = os.environ.get("RUDOF_BIN", "/usr/bin/rudof")
SHEXER_VENV       = os.environ.get("SHEXER_VENV", "/home/debian/koetai-admin/venv/bin/python3")
DB_PATH           = Path(__file__).parent / "db" / "koetai.db"
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
GITHUB_ORG        = os.environ.get("GITHUB_ORG", "Koetai")
SPARQL_EXAMPLES_REPO = os.environ.get("SPARQL_EXAMPLES_REPO", "sparql-examples")

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

GRAPH_BASE = BASE_URL + "/u/{user}/{dataset}"

ALLOWED_RDF_EXTENSIONS = {".ttl", ".nt", ".n3", ".rdf", ".owl", ".trig", ".nq", ".jsonld"}
MAX_UPLOAD_MB = 500
