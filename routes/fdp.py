"""FAIR Data Point — dynamic DCAT/FDP metadata for all public Koetai datasets.

Hierarchy:
  Repository   https://koetai.semscape.org/fdp
  Catalog      https://koetai.semscape.org/fdp/catalog/{orcid}
  Dataset      https://koetai.semscape.org/fdp/dataset/{orcid}/{slug}
  Distribution https://koetai.semscape.org/fdp/distribution/{orcid}/{slug}/sparql
               https://koetai.semscape.org/fdp/distribution/{orcid}/{slug}/git/{n}
               https://koetai.semscape.org/fdp/distribution/{orcid}/{slug}/web/{n}

All resources support content negotiation:
  Accept: text/turtle  → Turtle
  else                 → HTML
"""
from datetime import datetime, timezone
from flask import Blueprint, render_template, request, Response, abort
from services.db import get_db
import config

bp = Blueprint("fdp", __name__, url_prefix="/fdp")

# ── Shared RDF prefixes ──────────────────────────────────────────────────────
_PREFIXES = """\
@prefix dcat:  <http://www.w3.org/ns/dcat#> .
@prefix dct:   <http://purl.org/dc/terms/> .
@prefix foaf:  <http://xmlns.com/foaf/0.1/> .
@prefix r3d:   <http://www.re3data.org/schema/3-0#> .
@prefix fdp:   <http://rdf.biosemantics.org/ontologies/fdp-o#> .
@prefix xsd:   <http://www.w3.org/2001/XMLSchema#> .
@prefix ldp:   <http://www.w3.org/ns/ldp#> .
@prefix lang:  <http://id.loc.gov/vocabulary/iso639-1/> .
@prefix prov:  <http://www.w3.org/ns/prov#> .
"""

_ORG_URI  = f"{config.BASE_URL}/fdp/org"
_REPO_URI = f"{config.BASE_URL}/fdp"

_GH_BASE  = {"github": "https://github.com", "gitlab": "https://gitlab.com"}


# ── URI helpers ──────────────────────────────────────────────────────────────

def _catalog_uri(orcid):
    return f"{config.BASE_URL}/fdp/catalog/{orcid}"

def _dataset_uri(orcid, slug):
    return f"{config.BASE_URL}/fdp/dataset/{orcid}/{slug}"

def _sparql_dist_uri(orcid, slug):
    return f"{config.BASE_URL}/fdp/distribution/{orcid}/{slug}/sparql"

def _git_dist_uri(orcid, slug, n):
    return f"{config.BASE_URL}/fdp/distribution/{orcid}/{slug}/git/{n}"

def _web_dist_uri(orcid, slug, n):
    return f"{config.BASE_URL}/fdp/distribution/{orcid}/{slug}/web/{n}"

def _sparql_endpoint(orcid, slug):
    return f"{config.BASE_URL}/u/{orcid}/{slug}/sparql"


# ── Misc helpers ─────────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _esc(s):
    return (s or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

def _wants_rdf():
    a = request.headers.get("Accept", "")
    return "text/turtle" in a or "application/ld+json" in a or "application/rdf+xml" in a

def _ttl_response(ttl, uri):
    r = Response(ttl, mimetype="text/turtle")
    r.headers["Link"] = f'<{uri}>; rel="self"'
    r.headers["Content-Disposition"] = "inline"
    return r

def _git_repo_url(provider, repo, branch, path):
    base = _GH_BASE.get(provider, "https://github.com")
    url  = f"{base}/{repo}/tree/{branch}"
    return f"{url}/{path}" if path else url

def _git_raw_url(provider, repo, branch, path):
    """Canonical download URL for a whole git tree (points to repo root or subpath)."""
    base = _GH_BASE.get(provider, "https://github.com")
    if provider == "gitlab":
        return f"{base}/{repo}/-/archive/{branch}/{repo.split('/')[-1]}-{branch}.tar.gz"
    return f"{base}/{repo}/archive/refs/heads/{branch}.zip"


# ── DB queries ───────────────────────────────────────────────────────────────

def _public_catalogs():
    return get_db().execute(
        "SELECT DISTINCT u.orcid_id, u.name FROM users u "
        "JOIN datasets d ON d.user_id=u.id WHERE d.is_public=1"
    ).fetchall()

def _user_public_datasets(orcid):
    return get_db().execute(
        "SELECT d.* FROM datasets d JOIN users u ON u.id=d.user_id "
        "WHERE u.orcid_id=? AND d.is_public=1 ORDER BY d.label", (orcid,)
    ).fetchall()

def _get_user(orcid):
    return get_db().execute(
        "SELECT * FROM users WHERE orcid_id=?", (orcid,)
    ).fetchone()

def _get_dataset(orcid, slug):
    return get_db().execute(
        "SELECT d.* FROM datasets d JOIN users u ON u.id=d.user_id "
        "WHERE u.orcid_id=? AND d.slug=? AND d.is_public=1", (orcid, slug)
    ).fetchone()

def _git_sources(ds_id):
    return get_db().execute(
        "SELECT * FROM github_sources WHERE dataset_id=? ORDER BY id", (ds_id,)
    ).fetchall()

def _web_sources(ds_id):
    return get_db().execute(
        "SELECT ws.*, "
        "  (SELECT COUNT(*) FROM web_source_files wf "
        "   WHERE wf.source_id=ws.id AND wf.imported_at IS NOT NULL) AS imported_count "
        "FROM web_sources ws WHERE ws.dataset_id=? ORDER BY ws.id", (ds_id,)
    ).fetchall()

def _web_files(source_id):
    return get_db().execute(
        "SELECT * FROM web_source_files WHERE source_id=? AND imported_at IS NOT NULL ORDER BY filename",
        (source_id,)
    ).fetchall()


# ── Turtle builders ──────────────────────────────────────────────────────────

def _org_ttl():
    return f"""<{_ORG_URI}> a foaf:Organization ;
    foaf:name "Koetai Platform" ;
    foaf:homepage <{config.BASE_URL}> .\n"""


def _repository_ttl(catalogs):
    cat_uris = [_catalog_uri(c["orcid_id"]) for c in catalogs]
    catalog_triples = "\n".join(f"    r3d:dataCatalog <{u}> ;" for u in cat_uris)
    contains = ", ".join(f"<{u}>" for u in cat_uris) or "<>"
    now = _now()
    return f"""{_PREFIXES}
{_org_ttl()}
<{_REPO_URI}> a r3d:Repository ;
    dct:title "Koetai FAIR SPARQL Platform"@en ;
    dct:description "A multi-tenant FAIR SPARQL endpoint platform hosting public RDF knowledge graphs."@en ;
    dct:hasVersion "1.0" ;
    dct:publisher <{_ORG_URI}> ;
    dct:language lang:en ;
    dct:license <https://creativecommons.org/licenses/by/4.0/> ;
    fdp:metadataIdentifier <{_REPO_URI}> ;
    fdp:metadataIssued "{now}"^^xsd:dateTime ;
    fdp:metadataModified "{now}"^^xsd:dateTime ;
    r3d:repositoryIdentifier <{_REPO_URI}> ;
    r3d:institution <{_ORG_URI}> ;
{catalog_triples}
    ldp:contains {contains} .
"""


def _catalog_ttl(user, datasets):
    orcid = user["orcid_id"]
    uri   = _catalog_uri(orcid)
    now   = _now()
    ds_uris  = [_dataset_uri(orcid, d["slug"]) for d in datasets]
    ds_lines = "\n".join(f"    dcat:dataset <{u}> ;" for u in ds_uris)
    contains = ", ".join(f"<{u}>" for u in ds_uris) or "<>"
    return f"""{_PREFIXES}
{_org_ttl()}
<{uri}> a dcat:Catalog ;
    dct:title "{_esc(user['name'] or orcid)} — Koetai Datasets"@en ;
    dct:description "Public RDF datasets published by ORCID {orcid} on the Koetai platform."@en ;
    dct:hasVersion "1.0" ;
    dct:publisher <{_ORG_URI}> ;
    dct:language lang:en ;
    dct:license <https://creativecommons.org/licenses/by/4.0/> ;
    dct:isPartOf <{_REPO_URI}> ;
    fdp:metadataIdentifier <{uri}> ;
    fdp:metadataIssued "{now}"^^xsd:dateTime ;
    fdp:metadataModified "{now}"^^xsd:dateTime ;
{ds_lines}
    ldp:contains {contains} .
"""


def _all_dist_uris(orcid, slug, git_sources, web_sources):
    """Collect all distribution URIs for a dataset."""
    uris = [_sparql_dist_uri(orcid, slug)]
    for i, _ in enumerate(git_sources):
        uris.append(_git_dist_uri(orcid, slug, i))
    for i, _ in enumerate(web_sources):
        uris.append(_web_dist_uri(orcid, slug, i))
    return uris


def _dataset_ttl(ds, user, git_srcs, web_srcs):
    orcid = user["orcid_id"]
    uri   = _dataset_uri(orcid, ds["slug"])
    now   = _now()

    kw_ttl = ""
    if ds["fdp_keywords"]:
        parts = [f'"{_esc(k.strip())}"@en' for k in ds["fdp_keywords"].split(",") if k.strip()]
        if parts:
            kw_ttl = "    dcat:keyword " + ", ".join(parts) + " ;\n"
    theme_ttl = f"    dcat:theme <{ds['fdp_theme']}> ;\n" if ds["fdp_theme"] else ""

    dist_uris = _all_dist_uris(orcid, ds["slug"], git_srcs, web_srcs)
    dist_lines = "\n".join(f"    dcat:distribution <{u}> ;" for u in dist_uris)
    contains   = ", ".join(f"<{u}>" for u in dist_uris)

    # prov:wasDerivedFrom for git repos
    prov_lines = ""
    for gs in git_srcs:
        repo_url = _git_repo_url(gs["provider"], gs["repo"], gs["branch"], gs["path"])
        prov_lines += f"    prov:wasDerivedFrom <{repo_url}> ;\n"
    for ws in web_srcs:
        prov_lines += f"    prov:wasDerivedFrom <{ws['page_url']}> ;\n"

    return f"""{_PREFIXES}
{_org_ttl()}
<{uri}> a dcat:Dataset ;
    dct:title "{_esc(ds['label'])}"@en ;
    dct:description "{_esc(ds['description'] or ds['label'])}"@en ;
    dct:hasVersion "{_esc(ds['fdp_version'])}" ;
    dct:publisher <{_ORG_URI}> ;
    dct:language lang:en ;
    dct:license <{ds['fdp_license']}> ;
    dct:identifier "{uri}" ;
    dct:isPartOf <{_catalog_uri(orcid)}> ;
    dcat:accessURL <{_sparql_endpoint(orcid, ds['slug'])}> ;
{dist_lines}
{kw_ttl}{theme_ttl}{prov_lines}    fdp:metadataIdentifier <{uri}> ;
    fdp:metadataIssued "{ds['created_at']}Z"^^xsd:dateTime ;
    fdp:metadataModified "{now}"^^xsd:dateTime ;
    ldp:contains {contains} .
"""


def _sparql_dist_ttl(ds, user):
    orcid  = user["orcid_id"]
    uri    = _sparql_dist_uri(orcid, ds["slug"])
    sparql = _sparql_endpoint(orcid, ds["slug"])
    now    = _now()
    return f"""{_PREFIXES}
{_org_ttl()}
<{uri}> a dcat:Distribution ;
    dct:title "{_esc(ds['label'])} — SPARQL endpoint"@en ;
    dct:description "SPARQL 1.1 query endpoint for the {_esc(ds['label'])} dataset."@en ;
    dct:license <{ds['fdp_license']}> ;
    dcat:accessURL <{sparql}> ;
    dcat:mediaType "application/sparql-results+json" ;
    dct:format <https://www.iana.org/assignments/media-types/application/sparql-results+json> ;
    dct:isPartOf <{_dataset_uri(orcid, ds['slug'])}> ;
    fdp:metadataIdentifier <{uri}> ;
    fdp:metadataIssued "{now}"^^xsd:dateTime ;
    fdp:metadataModified "{now}"^^xsd:dateTime .
"""


def _git_dist_ttl(ds, user, gs, n):
    orcid    = user["orcid_id"]
    uri      = _git_dist_uri(orcid, ds["slug"], n)
    repo_url = _git_repo_url(gs["provider"], gs["repo"], gs["branch"], gs["path"])
    dl_url   = _git_raw_url(gs["provider"], gs["repo"], gs["branch"], gs["path"])
    provider = gs["provider"].capitalize()
    path_note = f" ({gs['path']})" if gs["path"] else ""
    now = _now()
    imported = gs["last_imported_at"] or now
    return f"""{_PREFIXES}
{_org_ttl()}
<{uri}> a dcat:Distribution ;
    dct:title "{_esc(ds['label'])} — {provider} source: {_esc(gs['repo'])}{_esc(path_note)}"@en ;
    dct:description "RDF files imported from {provider} repository {_esc(gs['repo'])} (branch: {_esc(gs['branch'])})."@en ;
    dct:license <{ds['fdp_license']}> ;
    dcat:accessURL <{repo_url}> ;
    dcat:downloadURL <{dl_url}> ;
    dcat:mediaType "text/turtle" ;
    dct:isPartOf <{_dataset_uri(orcid, ds['slug'])}> ;
    prov:wasDerivedFrom <{repo_url}> ;
    fdp:metadataIdentifier <{uri}> ;
    fdp:metadataIssued "{imported}Z"^^xsd:dateTime ;
    fdp:metadataModified "{now}"^^xsd:dateTime .
"""


def _web_dist_ttl(ds, user, ws, n):
    orcid   = user["orcid_id"]
    uri     = _web_dist_uri(orcid, ds["slug"], n)
    now     = _now()
    imported = ws["last_imported_at"] or now
    files   = _web_files(ws["id"])
    file_lines = ""
    for f in files:
        file_lines += f"    dcat:downloadURL <{f['url']}> ;\n"
    return f"""{_PREFIXES}
{_org_ttl()}
<{uri}> a dcat:Distribution ;
    dct:title "{_esc(ds['label'])} — Web source: {_esc(ws['label'] or ws['page_url'])}"@en ;
    dct:description "RDF files harvested from {_esc(ws['page_url'])}. {ws['imported_count']} file(s) imported."@en ;
    dct:license <{ds['fdp_license']}> ;
    dcat:accessURL <{ws['page_url']}> ;
{file_lines}    dct:isPartOf <{_dataset_uri(orcid, ds['slug'])}> ;
    prov:wasDerivedFrom <{ws['page_url']}> ;
    fdp:metadataIdentifier <{uri}> ;
    fdp:metadataIssued "{imported}Z"^^xsd:dateTime ;
    fdp:metadataModified "{now}"^^xsd:dateTime .
"""


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("", strict_slashes=False)
def repository():
    catalogs = _public_catalogs()
    if _wants_rdf():
        return _ttl_response(_repository_ttl(catalogs), _REPO_URI)
    return render_template("fdp_index.html",
                           repo_uri=_REPO_URI, catalogs=catalogs,
                           catalog_uri=_catalog_uri)


@bp.route("/catalog/<orcid>")
def catalog(orcid):
    user = _get_user(orcid)
    if not user:
        abort(404)
    datasets = _user_public_datasets(orcid)
    if _wants_rdf():
        return _ttl_response(_catalog_ttl(user, datasets), _catalog_uri(orcid))
    return render_template("fdp_catalog.html",
                           user=user, datasets=datasets,
                           catalog_uri=_catalog_uri(orcid),
                           dataset_uri=_dataset_uri)


@bp.route("/dataset/<orcid>/<slug>")
def dataset(orcid, slug):
    user = _get_user(orcid)
    ds   = _get_dataset(orcid, slug)
    if not user or not ds:
        abort(404)
    git_srcs = _git_sources(ds["id"])
    web_srcs = _web_sources(ds["id"])
    if _wants_rdf():
        return _ttl_response(_dataset_ttl(ds, user, git_srcs, web_srcs),
                             _dataset_uri(orcid, slug))
    # Build distribution list for template
    dists = _build_dist_list(ds, user, git_srcs, web_srcs)
    return render_template("fdp_dataset.html",
                           user=user, ds=ds,
                           dataset_uri=_dataset_uri(orcid, slug),
                           catalog_uri=_catalog_uri(orcid),
                           sparql_uri=_sparql_endpoint(orcid, slug),
                           distributions=dists)


@bp.route("/distribution/<orcid>/<slug>/sparql")
def dist_sparql(orcid, slug):
    user = _get_user(orcid)
    ds   = _get_dataset(orcid, slug)
    if not user or not ds:
        abort(404)
    if _wants_rdf():
        return _ttl_response(_sparql_dist_ttl(ds, user), _sparql_dist_uri(orcid, slug))
    return redirect_to_dataset(orcid, slug)


@bp.route("/distribution/<orcid>/<slug>/git/<int:n>")
def dist_git(orcid, slug, n):
    user = _get_user(orcid)
    ds   = _get_dataset(orcid, slug)
    if not user or not ds:
        abort(404)
    srcs = _git_sources(ds["id"])
    if n >= len(srcs):
        abort(404)
    if _wants_rdf():
        return _ttl_response(_git_dist_ttl(ds, user, srcs[n], n),
                             _git_dist_uri(orcid, slug, n))
    return redirect_to_dataset(orcid, slug)


@bp.route("/distribution/<orcid>/<slug>/web/<int:n>")
def dist_web(orcid, slug, n):
    user = _get_user(orcid)
    ds   = _get_dataset(orcid, slug)
    if not user or not ds:
        abort(404)
    srcs = _web_sources(ds["id"])
    if n >= len(srcs):
        abort(404)
    if _wants_rdf():
        return _ttl_response(_web_dist_ttl(ds, user, srcs[n], n),
                             _web_dist_uri(orcid, slug, n))
    return redirect_to_dataset(orcid, slug)


def redirect_to_dataset(orcid, slug):
    from flask import redirect, url_for
    return redirect(url_for("fdp.dataset", orcid=orcid, slug=slug))


# ── Template helper ───────────────────────────────────────────────────────────

def _build_dist_list(ds, user, git_srcs, web_srcs):
    """Build list of distribution dicts for the HTML template."""
    orcid = user["orcid_id"]
    dists = [{
        "uri":      _sparql_dist_uri(orcid, ds["slug"]),
        "title":    "SPARQL endpoint",
        "type":     "sparql",
        "access":   _sparql_endpoint(orcid, ds["slug"]),
        "icon":     "bi-braces",
        "media":    "application/sparql-results+json",
        "imported": None,
        "files":    [],
    }]
    for i, gs in enumerate(git_srcs):
        repo_url = _git_repo_url(gs["provider"], gs["repo"], gs["branch"], gs["path"])
        dists.append({
            "uri":      _git_dist_uri(orcid, ds["slug"], i),
            "title":    f"{gs['provider'].capitalize()}: {gs['repo']}" + (f" / {gs['path']}" if gs["path"] else ""),
            "type":     gs["provider"],
            "access":   repo_url,
            "icon":     "bi-github" if gs["provider"] == "github" else "bi-git",
            "media":    "text/turtle",
            "imported": gs["last_imported_at"],
            "branch":   gs["branch"],
            "files":    [],
        })
    for i, ws in enumerate(web_srcs):
        files = _web_files(ws["id"])
        dists.append({
            "uri":      _web_dist_uri(orcid, ds["slug"], i),
            "title":    ws["label"] or ws["page_url"],
            "type":     "web",
            "access":   ws["page_url"],
            "icon":     "bi-globe2",
            "media":    "text/turtle",
            "imported": ws["last_imported_at"],
            "files":    [dict(f) for f in files],
        })
    return dists
