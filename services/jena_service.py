"""Apache Jena ShEx validation via the bundled `shex` CLI tool."""
import subprocess
import tempfile
from pathlib import Path
import config


def validate_shex(rdf_file: Path, shex_content: str) -> tuple[bool, str]:
    """
    Validate an RDF file against a ShEx schema using Jena's native shex CLI.
    Returns (conforms: bool, report: str).
    """
    shex_bin = Path(config.JENA_BIN) / "shex"

    with tempfile.NamedTemporaryFile(suffix=".shex", mode="w",
                                     delete=False, encoding="utf-8") as f:
        f.write(shex_content)
        shex_path = f.name

    try:
        result = subprocess.run(
            [str(shex_bin), "validate",
             "--data",   str(rdf_file),
             "--schema", shex_path],
            capture_output=True, text=True, timeout=120
        )
        output = result.stdout + result.stderr
        conforms = result.returncode == 0 and "nonconformant" not in output.lower()
        return conforms, output.strip() or "Validation complete (no output)."
    except subprocess.TimeoutExpired:
        return False, "Jena ShEx validation timed out."
    except Exception as e:
        return False, str(e)
    finally:
        Path(shex_path).unlink(missing_ok=True)
