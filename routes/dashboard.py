"""User dashboard — list datasets, create new ones, manage invites."""
import json
import os
import secrets
import shutil
from pathlib import Path
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
import config
from services.db import get_db
from services import triplestore

_COSTS_FILE = Path(__file__).parent.parent / "costs_config.json"
_CLAUDE_EUR  = {'Pro': 18, 'Max 5×': 91, 'Max 20×': 183, 'API': 0}

def _load_costs():
    if _COSTS_FILE.exists():
        return json.loads(_COSTS_FILE.read_text())
    return {"claude_plan": "Pro", "extra_storage_gb": 0, "history": []}

def _save_costs(data):
    _COSTS_FILE.write_text(json.dumps(data, indent=2))

_DEFAULT_VM_OPTIONS = [
    {"name": "c5.small",  "vcpu": 2,  "ram_gb": 4,  "disk_gb": 25,  "eur_mo": 21.88},
    {"name": "c5.medium", "vcpu": 4,  "ram_gb": 8,  "disk_gb": 50,  "eur_mo": 43.75},
    {"name": "s5.medium", "vcpu": 4,  "ram_gb": 16, "disk_gb": 100, "eur_mo": 37.50},
    {"name": "c5.large",  "vcpu": 8,  "ram_gb": 16, "disk_gb": 100, "eur_mo": 87.50},
    {"name": "c5.xlarge", "vcpu": 16, "ram_gb": 32, "disk_gb": 200, "eur_mo": 175.00},
]

def _compute_total(cfg):
    vm_cost      = cfg.get("vm_cost", 43.75)
    storage_cost = round(int(cfg.get("extra_storage_gb", 0)) * 0.095, 2)
    claude_eur   = _CLAUDE_EUR.get(cfg.get("claude_plan", "Pro"), 0)
    return round(vm_cost + storage_cost + claude_eur, 2)

def _archive_current_month(cfg):
    """Add current month to history if not already present."""
    import datetime
    now = datetime.date.today()
    key = f"{now.year}-{now.month:02d}"
    history = cfg.setdefault("history", [])
    if not any(e["key"] == key for e in history):
        history.append({
            "key":     key,
            "year":    now.year,
            "month":   now.month,
            "vm":      43.75,
            "storage": round(int(cfg.get("extra_storage_gb", 0)) * 0.095, 2),
            "claude":  _CLAUDE_EUR.get(cfg.get("claude_plan", "Pro"), 0),
            "total":   _compute_total(cfg),
        })
        _save_costs(cfg)
    return history

bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@bp.route("/")
@login_required
def index():
    db = get_db()
    datasets = db.execute(
        """SELECT d.*,
             (SELECT COUNT(*) FROM github_sources WHERE dataset_id=d.id) AS git_count,
             (SELECT COUNT(*) FROM web_sources    WHERE dataset_id=d.id) AS web_count,
             (SELECT COUNT(*) FROM shapes         WHERE dataset_id=d.id) AS shape_count
           FROM datasets d
           WHERE user_id = ? ORDER BY created_at DESC""",
        (current_user.id,)
    ).fetchall()
    return render_template("dashboard.html", datasets=datasets)


@bp.route("/dataset/new", methods=["GET", "POST"])
@login_required
def new_dataset():
    if request.method == "POST":
        label       = request.form["label"].strip()
        slug        = request.form["slug"].strip().lower().replace(" ", "-")
        description = request.form.get("description", "").strip()
        is_public   = 1 if request.form.get("is_public") else 0
        platform    = request.form.get("platform", "qlever")
        if platform not in triplestore.SUPPORTED:
            platform = "qlever"

        if not label or not slug:
            flash("Label and slug are required.", "error")
            return render_template("dataset_new.html")

        # A federation dataset is defined by its sources, not an upload.
        sources = None
        if platform == "comunica":
            sources = "\n".join(
                line.strip() for line in request.form.get("sources", "").splitlines()
                if line.strip()
            )
            if not sources:
                flash("A federation dataset needs at least one source.", "error")
                return render_template("dataset_new.html")

        fdp_keywords = request.form.get("fdp_keywords", "").strip()
        fdp_theme    = request.form.get("fdp_theme", "").strip()
        fdp_license  = request.form.get("fdp_license", "https://creativecommons.org/licenses/by/4.0/").strip()
        fdp_version  = request.form.get("fdp_version", "1.0").strip()

        graph_base = f"{config.BASE_URL}/u/{current_user.orcid_id}/{slug}"
        db = get_db()
        try:
            db.execute(
                "INSERT INTO datasets (user_id, slug, label, description, graph_base, platform, sources, "
                "is_public, fdp_keywords, fdp_theme, fdp_license, fdp_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (current_user.id, slug, label, description, graph_base, platform, sources, is_public,
                 fdp_keywords, fdp_theme, fdp_license, fdp_version)
            )
            db.commit()
        except Exception as e:
            flash(f"Could not create dataset: {e}", "error")
            return render_template("dataset_new.html")

        flash(f"Dataset '{label}' created.", "success")
        return redirect(url_for("datasets.view", owner_orcid=current_user.orcid_id, slug=slug))

    return render_template("dataset_new.html")


@bp.route("/costs")
@login_required
def costs():
    if not current_user.is_admin:
        flash("Admin only.", "error")
        return redirect(url_for("dashboard.index"))

    cfg              = _load_costs()
    claude_plan      = cfg.get("claude_plan", "Pro")
    extra_storage_gb = int(cfg.get("extra_storage_gb", 0))
    claude_eur       = _CLAUDE_EUR.get(claude_plan, 0)

    disk = shutil.disk_usage("/")
    disk_total_gb = round(disk.total / 1e9, 1)
    disk_used_gb  = round(disk.used  / 1e9, 1)
    disk_pct      = int(disk.used / disk.total * 100)

    cpu_count = os.cpu_count() or 0
    ram_gb    = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9)
    vm_flavor = f"{cpu_count} vCPU / {ram_gb} GB RAM / {disk_total_gb} GB disk"

    # Match live specs against the options table to find current VM cost
    vm_options = cfg.get("vm_options", _DEFAULT_VM_OPTIONS)
    current_opt = next(
        (o for o in vm_options if o["vcpu"] == cpu_count and o["ram_gb"] == ram_gb),
        None
    )
    vm_cost = current_opt["eur_mo"] if current_opt else cfg.get("vm_cost", 43.75)
    cfg["vm_cost"] = vm_cost  # keep _compute_total in sync

    storage_cost = round(extra_storage_gb * 0.095, 2)
    total_cost   = round(vm_cost + storage_cost + claude_eur, 2)

    # Annotate options with delta vs current and whether they are below current
    for opt in vm_options:
        opt["delta"]   = round(opt["eur_mo"] - vm_cost, 2)
        opt["current"] = (opt is current_opt)
        opt["below"]   = (opt["vcpu"] <= cpu_count and opt["ram_gb"] <= ram_gb
                          and opt["disk_gb"] <= disk_total_gb and not opt["current"])

    history = _archive_current_month(cfg)

    years = {}
    for e in sorted(history, key=lambda x: x["key"]):
        years.setdefault(e["year"], []).append(e)

    upload_dir = config.UPLOAD_DIR
    uploads_gb = 0
    if upload_dir.exists():
        uploads_gb = round(sum(f.stat().st_size for f in upload_dir.rglob("*") if f.is_file()) / 1e9, 2)

    disk_items = [
        {"path": "fuseki-data/databases/koetai/", "size": "~15 GB",
         "note": "Fuseki triple store — compact via Fuseki admin UI if needed"},
        {"path": str(upload_dir.relative_to(Path.home())), "size": f"{uploads_gb} GB",
         "note": "Source files kept after import — safe to delete once loaded into the triplestore"},
        {"path": "~/all_ome_2025_09_23.nt", "size": "138 MB",
         "note": "Loose raw data file — delete if already loaded"},
        {"path": "~/sanger-rdf-20250819_cleaned.ttl", "size": "64 MB",
         "note": "Loose raw data file — delete if already loaded"},
        {"path": "qlever-sparql-deployment/hoom/", "size": "~400 MB",
         "note": "QLever index for hoom_orphanet (not running)"},
    ]

    return render_template("costs.html",
        vm_cost=vm_cost, vm_flavor=vm_flavor, vm_options=vm_options,
        storage_cost=storage_cost, extra_storage_gb=extra_storage_gb,
        claude_plan=claude_plan, claude_cost_eur=claude_eur,
        total_cost=total_cost,
        disk_total_gb=disk_total_gb, disk_used_gb=disk_used_gb, disk_pct=disk_pct,
        uploads_gb=uploads_gb, disk_items=disk_items,
        years=years,
    )


@bp.route("/costs/set", methods=["POST"])
@login_required
def set_costs():
    if not current_user.is_admin:
        flash("Admin only.", "error")
        return redirect(url_for("dashboard.index"))
    cfg = _load_costs()
    cfg["claude_plan"]      = request.form.get("claude_plan", "Pro")
    cfg["extra_storage_gb"] = int(request.form.get("extra_storage_gb", 0) or 0)
    _save_costs(cfg)
    flash("Cost settings saved.", "success")
    return redirect(url_for("dashboard.costs"))


@bp.route("/admin/storage")
@login_required
def admin_storage():
    if not current_user.is_admin:
        flash("Admin only.", "error")
        return redirect(url_for("dashboard.index"))

    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY created_at").fetchall()

    rows = []
    total_bytes = 0
    for u in users:
        user_dir = config.UPLOAD_DIR / str(u["id"])
        if user_dir.exists():
            user_bytes = sum(f.stat().st_size for f in user_dir.rglob("*") if f.is_file())
        else:
            user_bytes = 0

        datasets = db.execute(
            "SELECT id, slug, label FROM datasets WHERE user_id=? ORDER BY slug",
            (u["id"],)
        ).fetchall()

        ds_rows = []
        for ds in datasets:
            ds_dir = config.UPLOAD_DIR / str(u["id"]) / ds["slug"]
            if ds_dir.exists():
                ds_bytes = sum(f.stat().st_size for f in ds_dir.rglob("*") if f.is_file())
            else:
                ds_bytes = 0
            ds_rows.append({"slug": ds["slug"], "label": ds["label"],
                             "bytes": ds_bytes, "mb": round(ds_bytes / 1e6, 1)})

        total_bytes += user_bytes
        rows.append({
            "id":       u["id"],
            "orcid_id": u["orcid_id"],
            "name":     u["name"] or u["orcid_id"],
            "is_admin": u["is_admin"],
            "bytes":    user_bytes,
            "mb":       round(user_bytes / 1e6, 1),
            "gb":       round(user_bytes / 1e9, 3),
            "datasets": ds_rows,
        })

    rows.sort(key=lambda r: r["bytes"], reverse=True)
    total_gb = round(total_bytes / 1e9, 3)

    disk = shutil.disk_usage("/")
    disk_total_gb = round(disk.total / 1e9, 1)
    disk_used_gb  = round(disk.used  / 1e9, 1)
    disk_pct      = int(disk.used / disk.total * 100)

    return render_template("admin_storage.html",
        rows=rows, total_gb=total_gb,
        disk_total_gb=disk_total_gb, disk_used_gb=disk_used_gb, disk_pct=disk_pct)


@bp.route("/invites", methods=["GET", "POST"])
@login_required
def invites():
    if not current_user.is_admin:
        flash("Admin only.", "error")
        return redirect(url_for("dashboard.index"))

    db = get_db()
    if request.method == "POST":
        code = secrets.token_urlsafe(16)
        db.execute("INSERT INTO invitations (code, created_by) VALUES (?,?)",
                   (code, current_user.id))
        db.commit()
        flash(f"Invite created: {config.BASE_URL}/auth/invite/{code}", "success")

    invites = db.execute(
        "SELECT i.*, u.name as used_by_name FROM invitations i "
        "LEFT JOIN users u ON u.id = i.used_by "
        "WHERE i.created_by = ? ORDER BY i.created_at DESC",
        (current_user.id,)
    ).fetchall()
    return render_template("invites.html", invites=invites, base_url=config.BASE_URL)
