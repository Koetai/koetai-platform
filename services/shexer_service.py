"""ShExer-based shape inference for Koetai platform."""
import subprocess
import tempfile
import json
from pathlib import Path
import config


SHEXER_SCRIPT = Path(__file__).parent / "run_shexer.py"


def infer_shex(rdf_file: Path, graph_uri: str = None) -> tuple[bool, str]:
    """
    Infer a ShEx schema from an RDF file using ShExer.
    Returns (success, shex_string_or_error).
    """
    cmd = [
        str(config.SHEXER_VENV),
        str(SHEXER_SCRIPT),
        "--input", str(rdf_file),
        "--format", "shex",
    ]
    if graph_uri:
        cmd += ["--graph", graph_uri]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode == 0:
            return True, r.stdout.strip()
        return False, r.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "ShExer timed out"
    except Exception as e:
        return False, str(e)


def shex_to_mermaid(shex_str: str) -> str:
    """
    Convert a ShEx schema string to a Mermaid class diagram.
    Parses ShEx shapes and emits erDiagram / classDiagram notation.
    """
    import re
    lines = []
    lines.append("classDiagram")

    # Simple regex-based parser for ShEx compact syntax
    # Shape: <URI> { prop IRI ; ... }
    shape_pattern = re.compile(r'<([^>]+)>\s*\{([^}]*)\}', re.DOTALL)
    prop_pattern  = re.compile(r'(\S+)\s+(\S+)', re.MULTILINE)

    classes = {}
    for m in shape_pattern.finditer(shex_str):
        shape_uri = m.group(1).split("/")[-1].split("#")[-1]
        body = m.group(2)
        props = []
        for pm in prop_pattern.finditer(body):
            prop_name = pm.group(1).split(":")[-1].split("/")[-1]
            prop_type = pm.group(2).split(":")[-1].split("/")[-1].rstrip("+?*")
            props.append((prop_name, prop_type))
        classes[shape_uri] = props

    for cls, props in classes.items():
        lines.append(f"  class {cls} {{")
        for name, typ in props:
            lines.append(f"    +{typ} {name}")
        lines.append("  }")

    return "\n".join(lines)
