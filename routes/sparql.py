"""Instance-wide SPARQL: one endpoint per store, across all its datasets.

Each dataset's own endpoint (routes/datasets.py) is confined to that dataset's
graphs. This one is confined to the data graphs of every dataset in a store that
the caller may see: public ones, plus their own private ones when signed in. A
bare pattern reads all of them as one default graph, and GRAPH ?g names the
dataset each triple came from, so querying across datasets keeps provenance.

It is one endpoint per store rather than one for the instance because a query
runs inside a single store; datasets in different stores cannot be joined by it.
"""
from flask import Blueprint, request, redirect, url_for, jsonify, Response
from flask_login import current_user

from services.db import get_db
from services import triplestore, sparql_http

bp = Blueprint("sparql", __name__, url_prefix="/sparql")


def visible_datasets(platform=None):
    """Datasets in the cross-dataset stores that the current caller may query.

    The same rule as a dataset's own endpoint: public to everyone, private to
    its owner only. Restricted to CROSS_DATASET_BACKENDS, so a store that has not
    been checked to honour a multi-graph scope is never offered one.
    """
    platforms = ((platform,) if platform else triplestore.CROSS_DATASET_BACKENDS)
    platforms = [p for p in platforms if p in triplestore.CROSS_DATASET_BACKENDS]
    if not platforms:
        return []
    user_id = current_user.id if current_user.is_authenticated else None
    marks = ",".join("?" * len(platforms))
    return get_db().execute(
        f"""SELECT d.slug, d.label, d.graph_base, d.platform, d.is_public,
                   u.orcid_id, u.name AS owner_name
            FROM datasets d JOIN users u ON u.id = d.user_id
            WHERE d.platform IN ({marks})
              AND (d.is_public = 1 OR d.user_id = ?)
            ORDER BY d.graph_base""",
        (*platforms, user_id),
    ).fetchall()


def store_summaries():
    """One entry per store that holds at least one dataset the caller can see."""
    counts = {}
    for ds in visible_datasets():
        counts[ds["platform"]] = counts.get(ds["platform"], 0) + 1
    return [
        {"platform": p,
         "label": triplestore.BACKEND_INFO.get(p, (p,))[0],
         "datasets": counts[p],
         "endpoint": url_for("sparql.store_endpoint", platform=p, _external=True)}
        for p in triplestore.CROSS_DATASET_BACKENDS if p in counts
    ]


def read_query():
    """The query from a SPARQL 1.1 Protocol request, or "".

    Accepts all three forms: GET with ?query=, POST form-encoded, and POST with
    the query as the body under application/sparql-query.
    """
    if request.method == "GET":
        return request.args.get("query", "")
    if request.mimetype == "application/sparql-query":
        return request.get_data(as_text=True)
    return request.form.get("query", "")


def proxy_query(ts, query, scope):
    """Run `query` on store `ts` within `scope` and relay the store's answer."""
    # Federation and QLever stores only offer the parsed-JSON interface.
    raw = getattr(ts, "sparql_query_raw", None)
    if raw is None:
        ok, result = ts.sparql_query(query, graphs=scope)
        return jsonify(result), (200 if ok else 500)

    # Return what the client asked for, in the form the store produced it.
    # Forcing results JSON on everything meant DESCRIBE and CONSTRUCT could not
    # work at all: they return a graph, the store answered 406, and the client
    # was told its Accept header was wrong when it had never set one.
    accept = sparql_http.accept_for(query, request.headers.get("Accept", ""))
    ok, body, content_type, status = raw(query, graphs=scope, accept=accept)
    if not ok:
        # Keep the JSON error shape the editor expects, but carry the store's
        # own message and status rather than flattening everything to 500.
        return jsonify({"error": body.decode("utf-8", "replace").strip()}), status
    return Response(body, status=200, content_type=content_type)


@bp.route("/<platform>", methods=["GET", "POST"])
def store_endpoint(platform):
    """SPARQL across every dataset in one store that the caller may see."""
    if platform not in triplestore.CROSS_DATASET_BACKENDS:
        return jsonify({"error": f"No cross-dataset endpoint for {platform!r}. "
                                 f"Available: {', '.join(triplestore.CROSS_DATASET_BACKENDS)}"}), 404

    query = read_query()
    # A browser opening the URL gets the editor, as on a dataset's own endpoint.
    if request.method == "GET" and (
            not query or "text/html" in request.headers.get("Accept", "")):
        return redirect(url_for("sparql_editor", store=platform))
    if not query:
        return jsonify({"error": "No query provided"}), 400

    scope = triplestore.cross_dataset_scope(visible_datasets(platform))
    if scope is None:
        return jsonify({"error": f"No datasets you can see are stored in {platform}."}), 404
    return proxy_query(triplestore.get_by_name(platform), query, scope)
