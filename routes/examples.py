"""SPARQL examples — store, browse, run (sparql-examples RDF format)."""
import json
from flask import (Blueprint, render_template, request, jsonify,
                   redirect, url_for, flash)
from flask_login import login_required, current_user
import config
from services.db import get_db
from services import qlever

bp = Blueprint("examples", __name__, url_prefix="/u")


def _get_dataset(owner_orcid, slug):
    db = get_db()
    return db.execute(
        "SELECT d.*, u.orcid_id FROM datasets d JOIN users u ON u.id = d.user_id "
        "WHERE u.orcid_id = ? AND d.slug = ?",
        (owner_orcid, slug)
    ).fetchone()


@bp.route("/<owner_orcid>/<slug>/examples")
def list_examples(owner_orcid, slug):
    ds = _get_dataset(owner_orcid, slug)
    if not ds:
        flash("Dataset not found.", "error")
        return redirect(url_for("main.index"))

    db = get_db()
    examples = db.execute(
        "SELECT * FROM examples WHERE dataset_id = ? ORDER BY label",
        (ds["id"],)
    ).fetchall()
    return render_template("examples.html", ds=ds, examples=examples)


@bp.route("/<owner_orcid>/<slug>/examples/new", methods=["GET", "POST"])
@login_required
def new_example(owner_orcid, slug):
    ds = _get_dataset(owner_orcid, slug)
    if not ds or ds["user_id"] != current_user.id:
        flash("Not authorized.", "error")
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        label       = request.form["label"].strip()
        ex_slug     = request.form["slug"].strip().lower().replace(" ", "-")
        description = request.form.get("description", "").strip()
        query       = request.form["query"].strip()
        keywords    = json.dumps([k.strip() for k in request.form.get("keywords", "").split(",") if k.strip()])

        db = get_db()
        try:
            db.execute(
                "INSERT INTO examples (dataset_id, slug, label, description, query, keywords) VALUES (?,?,?,?,?,?)",
                (ds["id"], ex_slug, label, description, query, keywords)
            )
            # Also store as RDF in the :examples named graph
            _store_example_rdf(ds, ex_slug, label, description, query, keywords)
            db.commit()
            flash(f"Example '{label}' saved.", "success")
        except Exception as e:
            flash(f"Error: {e}", "error")
        return redirect(url_for("examples.list_examples", owner_orcid=owner_orcid, slug=slug))

    return render_template("example_edit.html", ds=ds, example=None)


@bp.route("/<owner_orcid>/<slug>/examples/<ex_slug>/run", methods=["POST"])
def run_example(owner_orcid, slug, ex_slug):
    ds = _get_dataset(owner_orcid, slug)
    if not ds:
        return jsonify({"error": "Dataset not found"}), 404

    db = get_db()
    ex = db.execute(
        "SELECT * FROM examples WHERE dataset_id = ? AND slug = ?",
        (ds["id"], ex_slug)
    ).fetchone()
    if not ex:
        return jsonify({"error": "Example not found"}), 404

    ok, result = qlever.sparql_query(ex["query"])
    if not ok:
        return jsonify(result), 500
    return jsonify(result)


@bp.route("/<owner_orcid>/<slug>/examples/<ex_slug>/delete", methods=["POST"])
@login_required
def delete_example(owner_orcid, slug, ex_slug):
    ds = _get_dataset(owner_orcid, slug)
    if not ds or ds["user_id"] != current_user.id:
        return jsonify({"error": "Not authorized"}), 403

    db = get_db()
    db.execute(
        "DELETE FROM examples WHERE dataset_id = ? AND slug = ?",
        (ds["id"], ex_slug)
    )
    db.commit()
    return jsonify({"success": True})


def _store_example_rdf(ds, ex_slug, label, description, query, keywords_json):
    """Store example as RDF (schema:target / sib sparql-examples format) in QLever."""
    ex_uri      = f"{ds['graph_base']}/examples/{ex_slug}"
    endpoint_uri = f"{config.BASE_URL}/u/{ds['orcid_id']}/{ds['slug']}/sparql"
    keywords_list = json.loads(keywords_json)
    kw_triples  = "\n".join(f'  schema:keywords "{kw}" ;' for kw in keywords_list)
    escaped_query = query.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    escaped_desc  = description.replace('"', '\\"')

    ttl = f"""
PREFIX schema: <https://schema.org/>
PREFIX sh:     <http://www.w3.org/ns/shacl#>
PREFIX rdfs:   <http://www.w3.org/2000/01/rdf-schema#>

<{ex_uri}> a schema:SoftwareSourceCode ;
  schema:name "{label}" ;
  rdfs:comment "{escaped_desc}" ;
  schema:target <{endpoint_uri}> ;
  sh:select "{escaped_query}" ;
{kw_triples}
  .
"""
    graph_uri = ds["graph_base"] + "/examples"
    update = f"""
INSERT DATA {{
  GRAPH <{graph_uri}> {{
    {ttl}
  }}
}}"""
    qlever.sparql_update(update)
