"""The Mangal — registry of Koetai instances.

A *mangal* is the whole mangrove ecosystem; this module reads the human-edited
``mangal.yaml`` registry (one entry per Koetai node) and renders it either as a
list of dicts for the HTML page or as an RDF/Turtle catalogue for machines.

The registry is deliberately a flat file that people update by pull request, so
every node that ships this repo renders the same, self-consistent list.
"""

from pathlib import Path
import yaml

import config

_REGISTRY = Path(__file__).resolve().parent.parent / "mangal.yaml"

# Canonical URL of the Mangal itself (the network hub).
MANGAL_URL = "https://mangal.semscape.org"


def load_instances():
    """Return the registry instances as a list of dicts, canonical first.

    Never raises: a missing or malformed registry yields an empty list so the
    page still renders.
    """
    try:
        data = yaml.safe_load(_REGISTRY.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    items = data.get("instances") or []

    def s(v):
        # YAML may hand back dates/ints; coerce everything to a clean string.
        return "" if v is None else str(v).strip()

    # Normalise + drop entries without the required fields.
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        url = s(it.get("url")).rstrip("/")
        name = s(it.get("name"))
        if not url or not name:
            continue
        out.append({
            "name":        name,
            "url":         url,
            "operator":    s(it.get("operator")),
            "orcid":       s(it.get("orcid")),
            "region":      s(it.get("region")),
            "since":       s(it.get("since")),
            "description": " ".join(s(it.get("description")).split()),
            "canonical":   bool(it.get("canonical")),
        })
    # Canonical node(s) first, then alphabetical by name.
    out.sort(key=lambda i: (not i["canonical"], i["name"].lower()))
    return out


# ── Turtle serialisation ─────────────────────────────────────────────────────

_PREFIXES = """\
@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix dct:  <http://purl.org/dc/terms/> .
@prefix foaf: <http://xmlns.com/foaf/0.1/> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
"""


def _esc(s):
    return (s or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def to_turtle(instances):
    """Serialise the registry as a dcat:Catalog of dcat:DataService nodes."""
    services = " ,\n              ".join(f"<{i['url']}>" for i in instances) or ""
    blocks = [
        _PREFIXES,
        f"<{MANGAL_URL}> a dcat:Catalog ;",
        '    dct:title "The Mangal — the Koetai network"@en ;',
        '    dct:description "A registry of Koetai FAIR SPARQL instances. '
        'Each node watches its own RDF graphs, as the anableps watches the '
        'mangroves from the waterline."@en ;',
        (f"    dcat:service {services} ." if services else "    ."),
        "",
    ]
    for i in instances:
        lines = [
            f"<{i['url']}> a dcat:DataService ;",
            f'    dct:title "{_esc(i["name"])}"@en ;',
            f"    dcat:endpointURL <{i['url']}> ;",
        ]
        if i["description"]:
            lines.append(f'    dct:description "{_esc(i["description"])}"@en ;')
        if i["region"]:
            lines.append(f'    dct:spatial "{_esc(i["region"])}" ;')
        if i["since"]:
            lines.append(f'    dct:issued "{i["since"]}"^^xsd:date ;')
        # Publisher (operator), with ORCID as identifier when present.
        if i["operator"] or i["orcid"]:
            pub = [f'a foaf:Person ; foaf:name "{_esc(i["operator"])}"']
            if i["orcid"]:
                pub.append(f'foaf:homepage <https://orcid.org/{i["orcid"]}>')
            lines.append("    dct:publisher [ " + " ; ".join(pub) + " ] ;")
        # Turn the trailing " ;" of the last line into " ."
        lines[-1] = lines[-1].rstrip(" ;") + " ."
        blocks.append("\n".join(lines))
        blocks.append("")
    return "\n".join(blocks) + "\n"
