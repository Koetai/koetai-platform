"""RUDOF-based shape inference and validation for Koetai platform."""
import subprocess
import tempfile
from pathlib import Path
import config


def infer_shacl(rdf_file: Path) -> tuple[bool, str]:
    """Infer SHACL shapes from an RDF file using RUDOF."""
    cmd = [config.RUDOF_BIN, "shacl-infer", "--input", str(rdf_file)]
    return _run_rudof(cmd)


def infer_shex_rudof(rdf_file: Path) -> tuple[bool, str]:
    """Infer ShEx shapes from an RDF file using RUDOF."""
    cmd = [config.RUDOF_BIN, "shex-infer", "--input", str(rdf_file)]
    return _run_rudof(cmd)


def validate_shex(rdf_file: Path, shex_content: str, node_selector: str = None) -> tuple[bool, str]:
    """Validate RDF data against a ShEx schema using RUDOF."""
    with tempfile.NamedTemporaryFile(suffix=".shex", mode="w", delete=False) as sf:
        sf.write(shex_content)
        shex_path = sf.name

    cmd = [
        config.RUDOF_BIN, "validate",
        "--schema", shex_path,
        "--data",   str(rdf_file),
        "--schema-format", "ShExC",
    ]
    if node_selector:
        cmd += ["--node", node_selector]

    ok, out = _run_rudof(cmd)
    Path(shex_path).unlink(missing_ok=True)
    return ok, out


def validate_shacl(rdf_file: Path, shacl_content: str) -> tuple[bool, str]:
    """Validate RDF data against a SHACL schema using RUDOF."""
    with tempfile.NamedTemporaryFile(suffix=".ttl", mode="w", delete=False) as sf:
        sf.write(shacl_content)
        shacl_path = sf.name

    cmd = [
        config.RUDOF_BIN, "shacl-validate",
        "--shapes", shacl_path,
        "--data",   str(rdf_file),
    ]
    ok, out = _run_rudof(cmd)
    Path(shacl_path).unlink(missing_ok=True)
    return ok, out


def shacl_to_mermaid(shacl_str: str) -> str:
    """Convert SHACL shapes to a Mermaid classDiagram."""
    import re
    lines = ["classDiagram"]

    # Extract NodeShapes and their properties
    shape_re  = re.compile(r'(\w+:\w+|\S+)\s+a\s+sh:NodeShape', re.MULTILINE)
    target_re = re.compile(r'sh:targetClass\s+(\S+)', re.MULTILINE)
    prop_re   = re.compile(
        r'sh:property\s*\[([^\]]+)\]',
        re.DOTALL | re.MULTILINE
    )
    path_re   = re.compile(r'sh:path\s+(\S+)')
    dtype_re  = re.compile(r'sh:(?:datatype|class)\s+(\S+)')

    for sm in shape_re.finditer(shacl_str):
        shape_name = sm.group(1).split(":")[-1]
        block_start = sm.start()
        block_text  = shacl_str[block_start:block_start + 2000]

        lines.append(f"  class {shape_name} {{")
        for pm in prop_re.finditer(block_text):
            prop_block = pm.group(1)
            path_m  = path_re.search(prop_block)
            dtype_m = dtype_re.search(prop_block)
            if path_m:
                pname = path_m.group(1).split(":")[-1].split("/")[-1]
                ptype = dtype_m.group(1).split(":")[-1] if dtype_m else "Any"
                lines.append(f"    +{ptype} {pname}")
        lines.append("  }")

    return "\n".join(lines)


def _run_rudof(cmd: list, timeout: int = 120) -> tuple[bool, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0:
            return True, r.stdout.strip()
        return False, r.stderr.strip() or r.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "rudof timed out"
    except Exception as e:
        return False, str(e)
