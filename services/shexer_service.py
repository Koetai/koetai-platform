"""ShExer-based shape inference for Koetai platform."""
import subprocess
import tempfile
import json
from pathlib import Path
import config
from services import shex_parse


SHEXER_SCRIPT = Path(__file__).parent / "run_shexer.py"

# ShExer's lightrdf parser only handles these formats natively
_SHEXER_NATIVE = {".ttl", ".nt", ".n3"}


def _ensure_shexer_compatible(rdf_file: Path) -> tuple[Path, bool]:
    """
    If rdf_file is OWL/XML or RDF/XML, convert to N-Triples via Jena riot.
    Returns (path_to_use, is_temp) — caller must delete if is_temp=True.
    """
    if rdf_file.suffix.lower() in _SHEXER_NATIVE:
        return rdf_file, False

    from services.owl_service import normalize_to_nt
    ok, nt_path, msg = normalize_to_nt(rdf_file)
    if ok:
        return nt_path, True
    # Fall back to original and let ShExer report the error
    return rdf_file, False


def infer_shex(rdf_file: Path, graph_uri: str = None,
               rdfconfig_dir: Path = None) -> tuple[bool, str]:
    """
    Infer a ShEx schema from an RDF file using ShExer.
    Automatically converts OWL/XML → N-Triples before passing to ShExer.
    If rdfconfig_dir is given, also write model.yaml + prefix.yaml there.
    Returns (success, shex_string_or_error).
    """
    input_path, is_temp = _ensure_shexer_compatible(rdf_file)

    cmd = [
        str(config.SHEXER_VENV),
        str(SHEXER_SCRIPT),
        "--input", str(input_path),
        "--format", "shex",
    ]
    if graph_uri:
        cmd += ["--graph", graph_uri]
    if rdfconfig_dir:
        rdfconfig_dir.mkdir(parents=True, exist_ok=True)
        cmd += ["--rdfconfig-dir", str(rdfconfig_dir)]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode == 0:
            return True, r.stdout.strip()
        return False, r.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "ShExer timed out"
    except Exception as e:
        return False, str(e)
    finally:
        if is_temp:
            input_path.unlink(missing_ok=True)


def shex_to_mermaid(shex_str: str) -> str:
    """
    Convert a ShEx compact schema string to a Mermaid classDiagram.
    Handles both prefixed names (:Thing) and full URIs (<http://...>).
    """
    import re

    def local(token: str) -> str:
        """Extract a safe local name from a prefixed name or URI."""
        token = token.strip()
        # Full URI: <http://example.org/Foo> or <http://...#Bar>
        m = re.match(r'<[^>]*[/#]([^/>#+]+)>', token)
        if m:
            return re.sub(r'\W', '_', m.group(1))
        # Prefixed: ex:Foo or :Foo
        if ":" in token:
            local_part = token.split(":")[-1]
            return re.sub(r'\W', '_', local_part) or "Unknown"
        return re.sub(r'\W', '_', token) or "Unknown"

    # Comments go first: they carry braces, and a body read as "up to the next
    # closing brace" would end inside one.
    cleaned = shex_parse.strip_comments(shex_str)

    classes = {}
    for name_token, body in shex_parse.shape_blocks(cleaned):
        class_name = local(name_token)
        if not class_name or class_name in ("_", "", "Unknown"):
            continue
        props = []
        for line in body.splitlines():
            line = re.sub(r'(?<![<\S])#.*$', '', line).strip().rstrip(';').strip()
            if not line:
                continue
            # Skip rdf:type self-references
            if 'rdf:type' in line or 'rdf_type' in line.lower() or 'rdf-syntax-ns#type' in line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            prop = local(parts[0])
            # Type: value set [ex:Foo] or IRI or datatype
            raw_type = parts[1]
            if raw_type.startswith('['):
                # Value set — join until ]
                joined = ' '.join(parts[1:])
                inner = re.search(r'\[([^\]]+)\]', joined)
                typ = local(inner.group(1).split()[0]) if inner else "IRI"
            else:
                typ = local(raw_type).rstrip('_')
            # Cardinality from line
            card = ''
            card_m = re.search(r'(\+|\?|\*|\{[^}]+\})\s*(?:;|$)', line)
            if card_m:
                c = card_m.group(1)
                card = '0..*' if c == '*' else ('0..1' if c == '?' else ('1..*' if c == '+' else c.strip('{}')))

            if prop:
                props.append((prop, typ, card))

        # ShExer emits one constraint per observed datatype, so a predicate with
        # mixed values arrives several times over — datePublished as gYear, date
        # and gYearMonth. Three identical rows differing only in type read as a
        # mistake; one row naming the types it takes is the same information.
        merged, order = {}, []
        for prop, typ, card in props:
            if prop not in merged:
                merged[prop] = ([], card)
                order.append(prop)
            types, first_card = merged[prop]
            if typ and typ not in types:
                types.append(typ)
        props = [(prop, "|".join(merged[prop][0]) or "IRI", merged[prop][1])
                 for prop in order]

        classes[class_name] = props

    if not classes:
        return None  # No diagram to show

    lines = ["classDiagram"]
    for cls, props in classes.items():
        lines.append(f"  class {cls} {{")
        for prop, typ, card in props:
            label = f"{typ} {prop}" + (f" [{card}]" if card else "")
            lines.append(f"    +{label}")
        lines.append("  }")

    return "\n".join(lines)
