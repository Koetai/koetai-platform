"""SPARQLList-style REST API from parameterized SPARQL templates."""
import json
import re
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from flask_login import login_required, current_user
from services.db import get_db
from services import qlever

bp = Blueprint("sparqlist", __name__, url_prefix="/u")


def _get_dataset(owner_orcid, slug):
    db = get_db()
    return db.execute(
        "SELECT d.*, u.orcid_id FROM datasets d JOIN users u ON u.id = d.user_id "
        "WHERE u.orcid_id = ? AND d.slug = ?",
        (owner_orcid, slug)
    ).fetchone()


def _render_template_query(template: str, params: dict) -> str:
    """Replace {{param_name}} with values from params dict."""
    def replacer(m):
        key = m.group(1).strip()
        val = params.get(key, "")
        # Basic SPARQL injection guard: only allow safe characters
        val = re.sub(r'[^a-zA-Z0-9_\-\.:/# ]', '', str(val))
        return val
    return re.sub(r'\{\{(\w+)\}\}', replacer, template)


# ── Management UI ────────────────────────────────────────────────────────────

@bp.route("/<owner_orcid>/<slug>/sparqlist")
def list_queries(owner_orcid, slug):
    ds = _get_dataset(owner_orcid, slug)
    if not ds:
        flash("Dataset not found.", "error")
        return redirect(url_for("main.index"))
    db = get_db()
    queries = db.execute(
        "SELECT * FROM sparqlist_queries WHERE dataset_id = ? ORDER BY label",
        (ds["id"],)
    ).fetchall()
    return render_template("sparqlist.html", ds=ds, queries=queries)


@bp.route("/<owner_orcid>/<slug>/sparqlist/new", methods=["GET", "POST"])
@login_required
def new_query(owner_orcid, slug):
    ds = _get_dataset(owner_orcid, slug)
    if not ds or ds["user_id"] != current_user.id:
        flash("Not authorized.", "error")
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        q_slug   = request.form["slug"].strip().lower().replace(" ", "-")
        label    = request.form["label"].strip()
        desc     = request.form.get("description", "").strip()
        template = request.form["template"].strip()
        # params: JSON textarea e.g. [{"name":"limit","label":"Limit","default":"10"}]
        params   = request.form.get("params", "[]").strip()
        try:
            json.loads(params)  # validate JSON
        except json.JSONDecodeError:
            flash("Params must be valid JSON.", "error")
            return render_template("sparqlist_edit.html", ds=ds, query=None)

        db = get_db()
        try:
            db.execute(
                "INSERT INTO sparqlist_queries (dataset_id, slug, label, description, template, params) "
                "VALUES (?,?,?,?,?,?)",
                (ds["id"], q_slug, label, desc, template, params)
            )
            db.commit()
            flash(f"API query '{label}' saved.", "success")
        except Exception as e:
            flash(f"Error: {e}", "error")
        return redirect(url_for("sparqlist.list_queries", owner_orcid=owner_orcid, slug=slug))

    return render_template("sparqlist_edit.html", ds=ds, query=None)


# ── REST API endpoint ─────────────────────────────────────────────────────────

@bp.route("/<owner_orcid>/<slug>/api/<q_slug>", methods=["GET"])
def api_call(owner_orcid, slug, q_slug):
    """
    Public REST API endpoint.
    GET /u/{orcid}/{dataset}/api/{query}?param=value
    Returns SPARQL results as JSON.
    """
    ds = _get_dataset(owner_orcid, slug)
    if not ds:
        return jsonify({"error": "Dataset not found"}), 404

    db = get_db()
    q = db.execute(
        "SELECT * FROM sparqlist_queries WHERE dataset_id = ? AND slug = ?",
        (ds["id"], q_slug)
    ).fetchone()
    if not q:
        return jsonify({"error": "Query not found"}), 404

    # Build param dict from request args + defaults
    params_def = json.loads(q["params"])
    params = {p["name"]: request.args.get(p["name"], p.get("default", ""))
              for p in params_def}

    query = _render_template_query(q["template"], params)

    ok, result = qlever.sparql_query(query)
    if not ok:
        return jsonify(result), 500

    # Optionally simplify output for REST consumers
    fmt = request.args.get("_format", "sparql")  # sparql | simple
    if fmt == "simple":
        try:
            bindings = result["results"]["bindings"]
            simplified = [
                {k: v["value"] for k, v in row.items()}
                for row in bindings
            ]
            return jsonify(simplified)
        except (KeyError, TypeError):
            pass

    return jsonify(result)


@bp.route("/<owner_orcid>/<slug>/api/<q_slug>/describe")
def api_describe(owner_orcid, slug, q_slug):
    """Return API description (OpenAPI-lite) for a SPARQLList query."""
    ds = _get_dataset(owner_orcid, slug)
    if not ds:
        return jsonify({"error": "Dataset not found"}), 404
    db = get_db()
    q = db.execute(
        "SELECT * FROM sparqlist_queries WHERE dataset_id = ? AND slug = ?",
        (ds["id"], q_slug)
    ).fetchone()
    if not q:
        return jsonify({"error": "Query not found"}), 404

    params_def = json.loads(q["params"])
    return jsonify({
        "slug":        q["slug"],
        "label":       q["label"],
        "description": q["description"],
        "endpoint":    f"/u/{owner_orcid}/{slug}/api/{q_slug}",
        "parameters":  params_def,
        "formats":     ["sparql", "simple"],
    })
