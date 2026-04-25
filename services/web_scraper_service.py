"""Web page scraper — finds RDF/OWL download links on a page and tracks updates."""
import requests
from pathlib import Path
from urllib.parse import urljoin, urlparse
import uuid
import config

try:
    from bs4 import BeautifulSoup
    _BS4 = True
except ImportError:
    _BS4 = False

RDF_EXTENSIONS = {".ttl", ".nt", ".n3", ".rdf", ".owl", ".trig", ".nq", ".jsonld"}
# Archives: .zip/.tgz always included; .gz/.bz2 only when inner extension is RDF
ARCHIVE_EXTENSIONS = {".zip", ".tgz", ".gz", ".bz2"}

_HEADERS = {
    "User-Agent": "Koetai-Platform/1.0 (RDF metadata harvester; https://koetai.semscape.org)"
}


def _is_rdf_link(href: str) -> bool:
    path = urlparse(href).path
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in RDF_EXTENSIONS:
        return True
    if suffix in {".zip", ".tgz"}:
        return True
    if suffix in {".gz", ".bz2"}:
        # Accept .ttl.gz, .owl.gz, .tar.gz, etc.
        inner = Path(p.stem).suffix.lower()
        return inner in RDF_EXTENSIONS or inner == ".tar"
    return False


def scrape_page(page_url: str) -> tuple[bool, list[dict] | str]:
    """Fetch page_url and return list of {filename, url, etag, last_modified, content_length}."""
    if not _BS4:
        return False, "BeautifulSoup4 not installed (pip install beautifulsoup4)"
    try:
        r = requests.get(page_url, headers=_HEADERS, timeout=30)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        soup = BeautifulSoup(r.text, "html.parser")
        found = {}
        for tag in soup.find_all("a", href=True):
            href = tag["href"].strip()
            abs_url = urljoin(page_url, href)
            if _is_rdf_link(abs_url):
                if abs_url not in found:
                    found[abs_url] = Path(urlparse(abs_url).path).name
        files = []
        for url, fname in found.items():
            meta = _head_file(url)
            files.append({
                "filename": fname,
                "url": url,
                "etag": meta.get("etag"),
                "last_modified": meta.get("last_modified"),
                "content_length": meta.get("content_length"),
            })
        return True, files
    except Exception as e:
        return False, str(e)


def _head_file(url: str) -> dict:
    try:
        r = requests.head(url, headers=_HEADERS, timeout=15, allow_redirects=True)
        return {
            "etag": r.headers.get("ETag", "").strip('"'),
            "last_modified": r.headers.get("Last-Modified", ""),
            "content_length": int(r.headers["Content-Length"])
                              if r.headers.get("Content-Length") else None,
        }
    except Exception:
        return {}


def check_file_update(url: str, stored_etag: str | None, stored_lm: str | None) -> dict:
    """HEAD request to check if a file has changed."""
    meta = _head_file(url)
    if not meta:
        return {"has_update": False, "error": "Could not reach file"}
    etag = meta.get("etag") or ""
    lm   = meta.get("last_modified") or ""
    has_update = False
    if etag and stored_etag:
        has_update = etag != stored_etag
    elif lm and stored_lm:
        has_update = lm != stored_lm
    return {
        "has_update": has_update,
        "etag": etag or None,
        "last_modified": lm or None,
        "content_length": meta.get("content_length"),
        "error": None,
    }


def download_file(url: str, dest: Path) -> tuple[bool, str]:
    """Download a file to dest."""
    try:
        r = requests.get(url, headers=_HEADERS, timeout=120, stream=True)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        return True, str(dest)
    except Exception as e:
        return False, str(e)
