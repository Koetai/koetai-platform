"""Web page scraper — finds RDF/OWL download links on a page and tracks updates."""
import zipfile
import tarfile
import shutil
import gzip
import bz2
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


ARCHIVE_EXTENSIONS = {".zip", ".gz", ".bz2", ".tgz"}

# 1 MiB: big enough that copying a multi-gigabyte member is not syscall-bound,
# small enough to stay negligible against the process footprint.
_COPY_CHUNK = 1024 * 1024


def extract_rdf_files(archive_path: Path, dest_dir: Path) -> list[Path]:
    """Extract the RDF files from an archive, returning the paths written.

    Every member is streamed with copyfileobj rather than read into a bytes
    object. A gzipped dump is the normal way a multi-gigabyte graph is
    published, and decompressing one in memory needs the whole expansion at
    once — which is precisely the size this path exists to handle.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix   = archive_path.suffix.lower()
    suffixes = [s.lower() for s in archive_path.suffixes]
    extracted = []

    def _spill(fsrc, out: Path):
        with open(out, "wb") as f_out:
            shutil.copyfileobj(fsrc, f_out, _COPY_CHUNK)
        extracted.append(out)

    if suffix == ".zip":
        with zipfile.ZipFile(archive_path) as zf:
            for name in zf.namelist():
                if Path(name).suffix.lower() in RDF_EXTENSIONS:
                    with zf.open(name) as f_in:
                        _spill(f_in, dest_dir / Path(name).name)

    elif suffix == ".tgz" or (suffix == ".gz" and ".tar" in suffixes):
        with tarfile.open(archive_path, "r|*") as tf:      # streaming mode
            for member in tf:
                if member.isfile() and Path(member.name).suffix.lower() in RDF_EXTENSIONS:
                    f_in = tf.extractfile(member)
                    if f_in:
                        _spill(f_in, dest_dir / Path(member.name).name)

    elif suffix in (".gz", ".bz2"):
        opener = gzip.open if suffix == ".gz" else bz2.open
        inner_name = archive_path.stem       # "data.ttl" from "data.ttl.gz"
        inner_ext  = Path(inner_name).suffix.lower()
        out_name   = inner_name if inner_ext in RDF_EXTENSIONS else inner_name + ".ttl"
        with opener(archive_path, "rb") as f_in:
            _spill(f_in, dest_dir / out_name)

    return extracted
