"""
OWL / RDFS reasoning pipeline for Koetai platform.

Strategy by regime:
  RDFS          — Jena `infer` CLI (fast, seconds)
  OWL_RL        — Python owlrl via subprocess (slow for large files, run async)
  OWL_RL_Extras — Python owlrl via subprocess (slow, run async)

For async jobs see services/job_runner.py.
"""
import subprocess
import tempfile
from pathlib import Path
import config

OWL_REASON_SCRIPT = Path(__file__).parent / "run_owl_reason.py"
JENA_RIOT   = Path(config.JENA_BIN) / "riot"
JENA_INFER  = Path(config.JENA_BIN) / "infer"

# Files larger than this should warn the user OWL RL will be slow
OWL_RL_WARN_MB = 20


def materialize_rdfs(rdf_file: Path) -> tuple[bool, Path, str]:
    """
    Fast RDFS closure using Jena `infer --rdfs`.
    The input file IS the vocabulary (self-contained ontology).
    Returns (ok, output_path_or_original, message).
    """
    out = tempfile.NamedTemporaryFile(suffix=".nt", delete=False)
    out.close()
    out_path = Path(out.name)

    # Jena infer: infer --rdfs=VOCAB DATA  — when ontology = data, pass same file twice
    cmd = [str(JENA_INFER), f"--rdfs={rdf_file}", str(rdf_file)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            out_path.unlink(missing_ok=True)
            return False, rdf_file, result.stderr.strip() or "Jena RDFS inference failed"
        out_path.write_text(result.stdout, encoding="utf-8")
        # Count triples written
        n = result.stdout.count("\n")
        return True, out_path, f"RDFS closure complete ({n:,} triples)"
    except subprocess.TimeoutExpired:
        out_path.unlink(missing_ok=True)
        return False, rdf_file, "Jena RDFS inference timed out (10 min limit)"
    except Exception as e:
        out_path.unlink(missing_ok=True)
        return False, rdf_file, str(e)


def materialize_owlrl(rdf_file: Path, regime: str = "OWL_RL",
                       timeout: int = 7200) -> tuple[bool, Path, str]:
    """
    OWL RL reasoning via Python owlrl (slow for large files).
    timeout defaults to 2 hours — intended for background jobs.
    Returns (ok, output_path_or_original, message).
    """
    out = tempfile.NamedTemporaryFile(suffix=".ttl", delete=False)
    out.close()
    out_path = Path(out.name)

    try:
        r = subprocess.run(
            [str(config.SHEXER_VENV), str(OWL_REASON_SCRIPT),
             "--input", str(rdf_file), "--regime", regime],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            out_path.unlink(missing_ok=True)
            return False, rdf_file, r.stderr.strip() or "OWL RL reasoning failed"
        out_path.write_text(r.stdout, encoding="utf-8")
        return True, out_path, r.stderr.strip()
    except subprocess.TimeoutExpired:
        out_path.unlink(missing_ok=True)
        return False, rdf_file, f"OWL RL reasoning timed out after {timeout//60} min"
    except Exception as e:
        out_path.unlink(missing_ok=True)
        return False, rdf_file, str(e)


def normalize_to_nt(rdf_file: Path) -> tuple[bool, Path, str]:
    """
    Use Jena riot to parse any RDF format and emit N-Triples.
    Useful for normalizing OWL/XML before loading.
    """
    out = tempfile.NamedTemporaryFile(suffix=".nt", delete=False)
    out.close()
    out_path = Path(out.name)

    try:
        r = subprocess.run(
            [str(JENA_RIOT), "--output=NT", str(rdf_file)],
            capture_output=True, text=True, timeout=300
        )
        if r.returncode != 0:
            out_path.unlink(missing_ok=True)
            return False, rdf_file, r.stderr.strip() or "riot parse failed"
        out_path.write_text(r.stdout, encoding="utf-8")
        return True, out_path, "Parsed OK"
    except subprocess.TimeoutExpired:
        out_path.unlink(missing_ok=True)
        return False, rdf_file, "riot timed out"
    except Exception as e:
        out_path.unlink(missing_ok=True)
        return False, rdf_file, str(e)


def size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)
