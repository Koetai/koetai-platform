"""What the app can find out about the machine it is running on.

Written after three OOM kills that the app had no way to see coming: a load
would fail with "Remote end closed connection without response" while the real
event — the kernel taking the triplestore at ~10 GB — was invisible from inside
Koetai and even from `docker inspect`, because a global OOM sets no cgroup flag.

Everything here is read without privileges. That bounds it: /proc/meminfo in a
container reports the whole Docker VM rather than this container, which is the
number that matters for headroom but cannot be attributed to any one process.
Per-container memory, per-volume disk and container logs all need the Docker
API, and mounting its socket to draw a nicer graph would give the web app root
on the host. So this reports what is free and says plainly what it cannot see.
"""
from pathlib import Path
import shutil

import requests

import config


def _meminfo() -> dict:
    """Memory from /proc, in bytes. Empty if unreadable (not Linux, say)."""
    out = {}
    try:
        for line in open("/proc/meminfo"):
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts:
                out[key] = int(parts[0]) * 1024
    except (OSError, ValueError):
        return {}
    return out


def memory() -> dict | None:
    info = _meminfo()
    total = info.get("MemTotal")
    avail = info.get("MemAvailable", info.get("MemFree"))
    if not total or avail is None:
        return None
    return {
        "total": total,
        "available": avail,
        "used": total - avail,
        "percent_used": round((total - avail) / total * 100),
        "swap_total": info.get("SwapTotal", 0),
    }


def disk(path: Path = None) -> dict | None:
    """Free space where the app writes.

    The stores' volumes are not mounted here, but on a single-host install they
    sit on the same filesystem, so this is the shared pool rather than only the
    app's own usage.
    """
    try:
        total, used, free = shutil.disk_usage(path or config.UPLOAD_DIR)
    except OSError:
        return None
    return {"total": total, "used": used, "free": free,
            "percent_used": round(used / total * 100) if total else 0}


def _fuseki_metrics() -> dict:
    """Fuseki reports on itself over /$/metrics and /$/server; use it.

    Nothing else here does — Oxigraph has no metrics endpoint, and QLever's is
    not reachable the same way — so this is deliberately Fuseki-shaped rather
    than a general abstraction over one implementation.
    """
    auth = (config.FUSEKI_USER, config.FUSEKI_PASSWORD) if config.FUSEKI_USER else None
    out = {}
    try:
        r = requests.get(f"{config.FUSEKI_BASE_URL}/$/server", auth=auth, timeout=5)
        if r.status_code < 400:
            d = r.json()
            out["version"] = d.get("version")
            out["uptime_s"] = d.get("uptime")
    except Exception:
        pass
    try:
        r = requests.get(f"{config.FUSEKI_BASE_URL}/$/metrics", auth=auth, timeout=5)
        if r.status_code < 400:
            used = maximum = 0
            for line in r.text.splitlines():
                if line.startswith("#") or 'area="heap"' not in line:
                    continue
                name, _, value = line.rpartition(" ")
                try:
                    v = float(value)
                except ValueError:
                    continue
                if v < 0:                      # -1 means "no limit for this pool"
                    continue
                if name.startswith("jvm_memory_used_bytes"):
                    used += v
                elif name.startswith("jvm_memory_max_bytes"):
                    maximum += v
            if maximum:
                out["heap_used"] = int(used)
                out["heap_max"] = int(maximum)
                out["heap_percent"] = round(used / maximum * 100)
    except Exception:
        pass
    return out


def backends() -> list[dict]:
    """Each configured backend: whether it answers, and whatever it reports.

    Only Fuseki reports anything beyond being alive. Saying so is the point:
    the store that ran out of memory here was Oxigraph, and its footprint is
    exactly what none of this can show.
    """
    from services import triplestore

    status = triplestore.available()
    rows = []
    for name, (label, _desc, _tested, _kind) in triplestore.BACKEND_INFO.items():
        row = {"name": name, "label": label, "available": status.get(name, False),
               "detail": {}}
        if name == "fuseki" and row["available"]:
            row["detail"] = _fuseki_metrics()
        rows.append(row)
    rows.sort(key=lambda r: (not r["available"], r["name"]))
    return rows


def snapshot() -> dict:
    """Everything at once, for the admin page."""
    mem = memory()
    return {
        "memory": mem,
        "disk": disk(),
        "backends": backends(),
        # A load holds its transaction in memory until it commits, so headroom
        # is the number that decides whether a big import survives. Batching
        # bounds it, but the store's own working set still grows.
        "low_memory": bool(mem and mem["available"] < 2 * 1024**3),
    }
