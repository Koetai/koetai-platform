"""GitHub repository integration — list, download, and track RDF/OWL files."""
import requests
from pathlib import Path
import config

RDF_EXTENSIONS = {".ttl", ".nt", ".n3", ".rdf", ".owl", ".trig", ".nq", ".jsonld"}

_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _auth():
    if config.GITHUB_TOKEN:
        return {"Authorization": f"Bearer {config.GITHUB_TOKEN}"}
    return {}


def get_default_branch(repo: str) -> tuple[bool, str]:
    """Return the default branch name for a repository."""
    url = f"https://api.github.com/repos/{repo}"
    try:
        r = requests.get(url, headers={**_HEADERS, **_auth()}, timeout=15)
        if r.status_code == 200:
            return True, r.json().get("default_branch", "main")
        return False, "main"
    except Exception:
        return False, "main"


def get_latest_sha(repo: str, branch: str) -> tuple[bool, str]:
    """Return the latest commit SHA on a branch."""
    url = f"https://api.github.com/repos/{repo}/commits/{branch}"
    try:
        r = requests.get(url, headers={**_HEADERS, **_auth()}, timeout=15)
        if r.status_code == 200:
            return True, r.json()["sha"]
        return False, r.json().get("message", r.text)
    except Exception as e:
        return False, str(e)


def list_rdf_files(repo: str, branch: str, path: str = "") -> tuple[bool, list[dict]]:
    """
    Return list of RDF/OWL files in repo at path using the Git Trees API.
    Each item: {path, sha, size, download_url}
    """
    url = f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"
    try:
        r = requests.get(url, headers={**_HEADERS, **_auth()}, timeout=15)
        if r.status_code != 200:
            return False, r.json().get("message", r.text)
        tree = r.json().get("tree", [])
        files = []
        for item in tree:
            if item["type"] != "blob":
                continue
            item_path = item["path"]
            if path and not item_path.startswith(path.rstrip("/") + "/"):
                continue
            if Path(item_path).suffix.lower() in RDF_EXTENSIONS:
                files.append({
                    "path": item_path,
                    "sha":  item["sha"],
                    "size": item.get("size", 0),
                    "download_url": f"https://raw.githubusercontent.com/{repo}/{branch}/{item_path}",
                })
        return True, files
    except Exception as e:
        return False, str(e)


def download_file(download_url: str, dest: Path) -> tuple[bool, str]:
    """Download a raw file from GitHub to dest path."""
    try:
        r = requests.get(download_url, headers=_auth(), timeout=120, stream=True)
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
    """
    Compare stored commit SHA against current HEAD.
    Returns {has_update: bool, current_sha: str, error: str|None}
    """
    ok, current_sha = get_latest_sha(repo, branch)
    if not ok:
        return {"has_update": False, "current_sha": None, "error": current_sha}
    return {
        "has_update": stored_sha != current_sha,
        "current_sha": current_sha,
        "error": None,
    }
