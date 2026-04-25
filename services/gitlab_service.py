"""GitLab repository integration — mirrors github_service.py for GitLab API."""
import requests
from pathlib import Path
import urllib.parse
import config

RDF_EXTENSIONS = {".ttl", ".nt", ".n3", ".rdf", ".owl", ".trig", ".nq", ".jsonld"}

_BASE = "https://gitlab.com/api/v4"


def _headers():
    h = {"Accept": "application/json"}
    token = getattr(config, "GITLAB_TOKEN", "")
    if token:
        h["PRIVATE-TOKEN"] = token
    return h


def _encode(repo: str) -> str:
    return urllib.parse.quote(repo, safe="")


def get_default_branch(repo: str) -> tuple[bool, str]:
    url = f"{_BASE}/projects/{_encode(repo)}"
    try:
        r = requests.get(url, headers=_headers(), timeout=15)
        if r.status_code == 200:
            return True, r.json().get("default_branch", "main")
        return False, "main"
    except Exception:
        return False, "main"


def get_latest_sha(repo: str, branch: str) -> tuple[bool, str]:
    url = f"{_BASE}/projects/{_encode(repo)}/repository/branches/{urllib.parse.quote(branch, safe='')}"
    try:
        r = requests.get(url, headers=_headers(), timeout=15)
        if r.status_code == 200:
            return True, r.json()["commit"]["id"]
        return False, r.json().get("message", r.text)
    except Exception as e:
        return False, str(e)


def list_rdf_files(repo: str, branch: str, path: str = "") -> tuple[bool, list[dict]]:
    """Return list of RDF/OWL files using GitLab recursive tree API."""
    url = f"{_BASE}/projects/{_encode(repo)}/repository/tree"
    params = {"recursive": "true", "per_page": 100, "ref": branch}
    if path:
        params["path"] = path.rstrip("/")

    files = []
    page = 1
    try:
        while True:
            params["page"] = page
            r = requests.get(url, headers=_headers(), params=params, timeout=15)
            if r.status_code != 200:
                return False, r.json().get("message", r.text)
            items = r.json()
            if not items:
                break
            for item in items:
                if item["type"] != "blob":
                    continue
                item_path = item["path"]
                if Path(item_path).suffix.lower() in RDF_EXTENSIONS:
                    encoded_file = urllib.parse.quote(item_path, safe="")
                    raw_url = f"https://gitlab.com/{repo}/-/raw/{branch}/{item_path}"
                    files.append({
                        "path":         item_path,
                        "sha":          item.get("id", ""),
                        "size":         item.get("size", 0),
                        "download_url": raw_url,
                    })
            if len(items) < 100:
                break
            page += 1
        return True, files
    except Exception as e:
        return False, str(e)


def download_file(download_url: str, dest: Path) -> tuple[bool, str]:
    """Download a raw file from GitLab."""
    headers = _headers()
    headers.pop("Accept", None)  # don't force JSON for raw download
    try:
        r = requests.get(download_url, headers=headers, timeout=120, stream=True)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        return True, str(dest)
    except Exception as e:
        return False, str(e)


def check_for_update(repo: str, branch: str, stored_sha: str | None) -> dict:
    ok, current_sha = get_latest_sha(repo, branch)
    if not ok:
        return {"has_update": False, "current_sha": None, "error": current_sha}
    return {
        "has_update": stored_sha != current_sha,
        "current_sha": current_sha,
        "error": None,
    }
