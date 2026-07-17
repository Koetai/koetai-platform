"""Dataset view, RDF upload, SPARQL proxy."""
import json
import os
import uuid
from pathlib import Path
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, jsonify, Response)
from flask_login import login_required, current_user
import config
from services.db import get_db
from services import triplestore, owl_service, job_runner


def _schema_from_file(path: Path):
    """Return list of {name, dtype, sample} dicts from a tabular file."""
    import importlib
    suf = path.suffix.lower()
    try:
        if suf == '.parquet':
            pd = importlib.import_module('pandas')
            df = pd.read_parquet(path, engine='pyarrow').head(5)
        elif suf in ('.csv', '.tsv'):
            pd = importlib.import_module('pandas')
            sep = '\t' if suf == '.tsv' else ','
            df = pd.read_csv(path, sep=sep, nrows=5)
        elif suf in ('.xlsx', '.xls'):
            pd = importlib.import_module('pandas')
            df = pd.read_excel(path, nrows=5)
        elif suf == '.json':
            pd = importlib.import_module('pandas')
            df = pd.read_json(path).head(5)
        else:
            return []
        cols = []
        for col in df.columns:
            sample = df[col].dropna().iloc[0] if not df[col].dropna().empty else ''
            cols.append({'name': col, 'dtype': str(df[col].dtype), 'sample': str(sample)})
        return cols
    except Exception:
        return []

bp = Blueprint("datasets", __name__, url_prefix="/u")


def _get_dataset_or_404(owner_orcid, slug):
    db = get_db()
    return db.execute(
        "SELECT d.*, u.orcid_id, u.name as owner_name "
        "FROM datasets d JOIN users u ON u.id = d.user_id "
        "WHERE u.orcid_id = ? AND d.slug = ?",
        (owner_orcid, slug)
    ).fetchone()


@bp.route("/<owner_orcid>/<slug>")
def view(owner_orcid=None, slug=None):
    if owner_orcid is None:
        owner_orcid = current_user.orcid_id
    ds = _get_dataset_or_404(owner_orcid, slug)
    if not ds:
        flash("Dataset not found.", "error")
        return redirect(url_for("dashboard.index"))
    if not ds["is_public"] and (not current_user.is_authenticated or current_user.id != ds["user_id"]):
        flash("This dataset is private.", "error")
        return redirect(url_for("index"))

    db = get_db()
    ts = triplestore.get(ds)
    triple_count = ts.count_triples(ds["graph_base"] + "/data")
    shape_count  = db.execute("SELECT COUNT(*) FROM shapes WHERE dataset_id=?", (ds["id"],)).fetchone()[0]
    git_count    = db.execute("SELECT COUNT(*) FROM github_sources WHERE dataset_id=?", (ds["id"],)).fetchone()[0]
    web_count    = db.execute("SELECT COUNT(*) FROM web_sources WHERE dataset_id=?", (ds["id"],)).fetchone()[0]
    return render_template("dataset.html", ds=ds, triple_count=triple_count,
                           shape_count=shape_count,
                           source_counts={"git": git_count, "web": web_count})


@bp.route("/<owner_orcid>/<slug>/upload", methods=["GET"])
@login_required
def upload_page(owner_orcid, slug):
    ds = _get_dataset_or_404(owner_orcid, slug)
    if not ds or ds["user_id"] != current_user.id:
        flash("Not found or not authorized.", "error")
        return redirect(url_for("dashboard.index"))
    return render_template("upload.html", ds=ds)


@bp.route("/<owner_orcid>/<slug>/mapping", methods=["GET"])
@login_required
def mapping_page(owner_orcid, slug):
    ds = _get_dataset_or_404(owner_orcid, slug)
    if not ds or ds["user_id"] != current_user.id:
        flash("Not found or not authorized.", "error")
        return redirect(url_for("dashboard.index"))
    filename = request.args.get("file", "")
    filepath = config.UPLOAD_DIR / str(ds["id"]) / filename
    columns  = _schema_from_file(filepath) if filepath.exists() else []
    return render_template("mapping.html", ds=ds, filename=filename, columns=columns)


@bp.route("/<owner_orcid>/<slug>/mapping/preview", methods=["POST"])
@login_required
def mapping_preview(owner_orcid, slug):
    ds = _get_dataset_or_404(owner_orcid, slug)
    if not ds or ds["user_id"] != current_user.id:
        flash("Not authorized.", "error")
        return redirect(url_for("dashboard.index"))
    raw = request.form.get("mapping_json", "{}")
    try:
        mapping = json.loads(raw)
    except Exception:
        flash("Invalid mapping data.", "error")
        return redirect(url_for("datasets.mapping_page", owner_orcid=owner_orcid, slug=slug))

    # Run Morph-KGC on first 20 rows for preview
    try:
        import morph_kgc, tempfile, yaml as _yaml
        filepath = config.UPLOAD_DIR / str(ds["id"]) / mapping.get("filename", "")
        yarrrml  = _build_yarrrml(mapping)
        with tempfile.TemporaryDirectory() as tmp:
            yfile = Path(tmp) / "mapping.yaml"
            yfile.write_text(yarrrml)
            cfg_ini = (
                f"[CONFIGURATION]\noutput_format = N-TRIPLES\nchunk_size = 20\n\n"
                f"[DataSource]\nmappings = {yfile}\n"
            )
            ifile = Path(tmp) / "config.ini"
            ifile.write_text(cfg_ini)
            g = morph_kgc.materialize(str(ifile))
            preview_ttl = g.serialize(format="turtle")
    except Exception as e:
        preview_ttl = f"# Preview failed: {e}"

    return render_template("mapping_preview.html", ds=ds, mapping=mapping,
                           preview_ttl=preview_ttl)


def _build_yarrrml(m):
    """Generate YARRRML string from mapping config dict."""
    import yaml as _yaml
    doc = {
        "prefixes": {
            "ex"     : m.get("baseUri", "http://example.org/"),
            "xsd"    : "http://www.w3.org/2001/XMLSchema#",
            "rdf"    : "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
            "rdfs"   : "http://www.w3.org/2000/01/rdf-schema#",
            "schema" : "http://schema.org/",
            "dcterms": "http://purl.org/dc/terms/",
        },
        "mappings": {
            m.get("datasetSlug", "dataset"): {
                "sources": [[f"{m.get('filename', 'data.parquet')}~parquet"]],
                "s": f"ex:$({m.get('subjectCol', 'id')})",
                "po": (
                    [["a", f"<{m['typeUri']}>"]] if m.get("typeUri") else []
                ) + [
                    [f"<{col['predUri']}>",
                     f"$({col['name']})@en" if col['dtype'] == '@en'
                     else f"$({col['name']})~iri" if col['dtype'] == 'IRI'
                     else [f"$({col['name']})", col['dtype']]]
                    for col in m.get("columns", []) if col.get("predUri")
                ],
            }
        }
    }
    return _yaml.dump(doc, allow_unicode=True, sort_keys=False)


@bp.route("/<owner_orcid>/<slug>/fdp-meta", methods=["GET"])
@login_required
def fdp_meta_page(owner_orcid, slug):
    ds = _get_dataset_or_404(owner_orcid, slug)
    if not ds or ds["user_id"] != current_user.id:
        flash("Not found or not authorized.", "error")
        return redirect(url_for("dashboard.index"))
    return render_template("fdp_meta.html", ds=ds)


@bp.route("/<owner_orcid>/<slug>/fdp-meta", methods=["POST"])
@login_required
def update_fdp_meta(owner_orcid, slug):
    ds = _get_dataset_or_404(owner_orcid, slug)
    if not ds or ds["user_id"] != current_user.id:
        flash("Not authorized.", "error")
        return redirect(url_for("dashboard.index"))
    db = get_db()
    db.execute(
        "UPDATE datasets SET fdp_license=?, fdp_version=?, fdp_keywords=?, fdp_theme=? "
        "WHERE id=?",
        (
            request.form.get("fdp_license", "").strip() or "https://creativecommons.org/licenses/by/4.0/",
            request.form.get("fdp_version", "").strip() or "1.0",
            request.form.get("fdp_keywords", "").strip(),
            request.form.get("fdp_theme", "").strip(),
            ds["id"],
        )
    )
    db.commit()
    flash("FDP metadata saved.", "success")
    return redirect(url_for("datasets.view", owner_orcid=owner_orcid, slug=slug))


@bp.route("/<owner_orcid>/<slug>/upload", methods=["POST"])
@login_required
def upload(owner_orcid, slug):
    ds = _get_dataset_or_404(owner_orcid, slug)
    if not ds or ds["user_id"] != current_user.id:
        return jsonify({"error": "Not found or not authorized"}), 403

    f = request.files.get("rdf_file")
    if not f:
        return jsonify({"error": "No file provided"}), 400

    ext = Path(f.filename).suffix.lower()
    if ext not in config.ALLOWED_RDF_EXTENSIONS:
        return jsonify({"error": f"Unsupported format: {ext}"}), 400

    upload_dir = config.UPLOAD_DIR / str(current_user.id) / slug
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / f"{uuid.uuid4().hex}{ext}"
    f.save(str(file_path))

    apply_owl    = request.form.get("apply_owl") == "true"
    owl_regime   = request.form.get("owl_regime", "OWL_RL")
    replace_data = request.form.get("replace_data") == "true"
    graph_uri    = ds["graph_base"] + "/data"

    # Always use async job pipeline — avoids HTTP timeouts for large/slow files
    job_id = job_runner.submit(
        dataset_id=ds["id"],
        user_id=current_user.id,
        file_path=file_path,
        graph_uri=graph_uri,
        apply_owl=apply_owl,
        owl_regime=owl_regime,
        replace_data=replace_data,
    )
    return jsonify({"job_id": job_id, "graph": graph_uri})


@bp.route("/<owner_orcid>/<slug>/upload/status/<job_id>")
@login_required
def upload_status(owner_orcid, slug, job_id):
    """Poll endpoint for async upload job status."""
    status = job_runner.get_status(job_id)
    if status is None:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(status)


@bp.route("/<owner_orcid>/<slug>/sparql", methods=["GET", "POST"])
def sparql_endpoint(owner_orcid, slug):
    """Proxy SPARQL requests, confined to this dataset's own named graphs."""
    ds = _get_dataset_or_404(owner_orcid, slug)
    if not ds:
        return jsonify({"error": "Dataset not found"}), 404

    # Private datasets are owner-only — for the editor as well as the query,
    # since the editor page itself discloses the dataset's metadata.
    if not ds["is_public"] and (not current_user.is_authenticated
                                or current_user.id != ds["user_id"]):
        return jsonify({"error": "This dataset is private"}), 403

    # GET → render the editor (query param pre-fills it)
    if request.method == "GET":
        preload_query = request.args.get("query", "")
        return render_template("yasgui.html", ds=ds, preload_query=preload_query)

    query = request.form.get("query", "")
    if not query:
        return jsonify({"error": "No query provided"}), 400

    ts = triplestore.get(ds)
    ok, result = ts.sparql_query(query, graphs=triplestore.dataset_scope(ds))
    if not ok:
        return jsonify(result), 500

    return jsonify(result)


@bp.route("/<owner_orcid>/<slug>/delete", methods=["POST"])
@login_required
def delete(owner_orcid, slug):
    ds = _get_dataset_or_404(owner_orcid, slug)
    if not ds or ds["user_id"] != current_user.id:
        flash("Not found or not authorized.", "error")
        return redirect(url_for("dashboard.index"))

    ts = triplestore.get(ds)
    for suffix in triplestore.GRAPH_SUFFIXES:
        ts.drop_graph(ds["graph_base"] + suffix)

    db = get_db()
    db.execute("DELETE FROM datasets WHERE id = ?", (ds["id"],))
    db.commit()
    flash(f"Dataset '{ds['label']}' deleted.", "success")
    return redirect(url_for("dashboard.index"))
