#!/usr/bin/env python3
"""
Standalone ShExer runner — called as a subprocess from shexer_service.py
so it runs inside the venv that has shexer installed.

rdf-config YAML files (model.yaml, prefix.yaml) are generated from the
ShEx output by our own parser — we do NOT use ShExer's built-in
rdfconfig_directory feature because it requires a SPARQL endpoint to
populate shape examples and crashes without one.
"""
import argparse
import os
import re
import sys
from pathlib import Path
from shexer.shaper import Shaper
from shexer.consts import TURTLE, NT

FORMAT_MAP = {
    ".ttl": TURTLE,
    ".nt":  NT,
    ".n3":  TURTLE,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",         required=True)
    parser.add_argument("--format",        default="shex", choices=["shex"])
    parser.add_argument("--graph",         default=None)
    parser.add_argument("--rdfconfig-dir", default=None, dest="rdfconfig_dir",
                        help="Write model.yaml + prefix.yaml here")
    args = parser.parse_args()

    ext = os.path.splitext(args.input)[1].lower()
    rdf_format = FORMAT_MAP.get(ext, TURTLE)

    shaper = Shaper(
        graph_file_input=args.input,
        input_format=rdf_format,
        all_classes_mode=True,
    )

    # Always get plain ShEx — never pass rdfconfig_directory to avoid the
    # NoneType crash in rdfconfig_serializer when no endpoint is available
    shex = shaper.shex_graph(string_output=True)
    print(shex)

    if args.rdfconfig_dir:
        os.makedirs(args.rdfconfig_dir, exist_ok=True)
        _write_rdfconfig_yaml(shex, Path(args.rdfconfig_dir))


# ── rdf-config YAML generator ──────────────────────────────────────────────

def _local(token: str) -> str:
    """Return a safe local name from a URI or prefixed name."""
    token = token.strip().strip("<>")
    for sep in ("#", "/"):
        if sep in token:
            token = token.rsplit(sep, 1)[-1]
    return re.sub(r"\W+", "_", token).strip("_") or "Unknown"


def _write_rdfconfig_yaml(shex: str, out_dir: Path):
    """
    Parse ShEx compact syntax and write:
      out_dir/model.yaml   — rdf-config data model
      out_dir/prefix.yaml  — prefix declarations
    """
    lines = shex.splitlines()

    # ── 1. Extract PREFIX declarations ──────────────────────────────────────
    prefix_map: dict[str, str] = {}
    prefix_re = re.compile(r'^PREFIX\s+(\w*):\s*<([^>]+)>', re.IGNORECASE)
    for line in lines:
        m = prefix_re.match(line.strip())
        if m:
            prefix_map[m.group(1)] = m.group(2)

    # ── 2. Extract shapes ───────────────────────────────────────────────────
    # Join back so we can do multi-line shape matching
    text = "\n".join(lines)

    # Match: <URI> { ... } or prefix:local { ... }
    shape_re = re.compile(
        r'((?:<[^>]+>)|(?:[\w]+:[\w\-_]+)|(?::[\w\-_]+))\s*\{([^}]*)\}',
        re.DOTALL
    )

    shapes: dict[str, list[dict]] = {}

    for m in shape_re.finditer(text):
        raw_name = m.group(1).strip()
        body     = m.group(2)

        # Skip PREFIX blocks accidentally matched
        if raw_name.upper().startswith("PREFIX") or raw_name.upper().startswith("BASE"):
            continue

        shape_name = _local(raw_name)
        if not shape_name:
            continue

        props = []
        for prop_line in body.splitlines():
            prop_line = re.sub(r'#.*$', '', prop_line).strip().rstrip(';').strip()
            if not prop_line:
                continue
            parts = prop_line.split()
            if len(parts) < 2:
                continue

            prop_token = parts[0]
            type_token = parts[1]

            # Skip rdf:type self-references
            if 'type' in prop_token.lower() and 'rdf' in prop_token.lower():
                continue
            if type_token.startswith('['):
                joined = ' '.join(parts[1:])
                inner  = re.search(r'\[([^\]]+)\]', joined)
                type_name = _local(inner.group(1).split()[0]) if inner else "IRI"
            else:
                type_name = _local(type_token).rstrip('_')

            prop_name = _local(prop_token)
            if not prop_name:
                continue

            # Cardinality
            card_m = re.search(r'(\+|\*|\?|\{[\d,]+\})\s*(?:;|$)', prop_line)
            cardinality = "?"
            if card_m:
                c = card_m.group(1)
                if c == '+':
                    cardinality = "+"
                elif c == '*':
                    cardinality = "*"
                elif c == '?':
                    cardinality = "?"
                else:
                    cardinality = c.strip('{}')

            # Resolve full URI for property from prefix_map
            full_uri = None
            if ':' in prop_token:
                pfx, local_part = prop_token.split(':', 1)
                if pfx in prefix_map:
                    full_uri = prefix_map[pfx] + local_part
            elif prop_token.startswith('<'):
                full_uri = prop_token.strip('<>')

            props.append({
                "prop_name":   prop_name,
                "type_name":   type_name,
                "cardinality": cardinality,
                "full_uri":    full_uri,
            })

        shapes[shape_name] = props

    # ── 3. Write prefix.yaml ─────────────────────────────────────────────────
    prefix_yaml_lines = []
    for pfx, uri in prefix_map.items():
        safe_pfx = pfx if pfx else "_"
        prefix_yaml_lines.append(f'{safe_pfx}: "{uri}"')

    (out_dir / "prefix.yaml").write_text(
        "\n".join(prefix_yaml_lines) + "\n", encoding="utf-8"
    )

    # ── 4. Write model.yaml ───────────────────────────────────────────────────
    model_lines = []
    for shape_name, props in shapes.items():
        model_lines.append(f"{shape_name}:")
        if not props:
            model_lines.append("  []")
            continue
        for p in props:
            type_str = p["type_name"]
            card     = p["cardinality"]
            # rdf-config format: "- prop_name: TypeName"  with optional cardinality comment
            model_lines.append(f"  - {p['prop_name']}: {type_str}  # {card}")
        model_lines.append("")

    (out_dir / "model.yaml").write_text(
        "\n".join(model_lines) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
