"""
rdf-config integration for Koetai platform.

Pipeline:
  1. ShExer generates model.yaml + prefix.yaml into a temp directory
  2. We run rdf-config via Docker to produce:
       --schema  → SVG diagram
       --sparql  → SPARQL query stubs
       --senbero → ASCII schema (for debugging)
"""
import subprocess
import tempfile
import shutil
from pathlib import Path
import config

DOCKER_IMAGE = "dbcls/rdf-config:latest"


def generate_from_shex_output(rdfconfig_dir: Path,
                               endpoint_url: str = None
                               ) -> dict[str, str | None]:
    """
    Given a directory containing model.yaml and prefix.yaml (produced by ShExer),
    optionally write an endpoint.yaml, then run rdf-config to produce:
      - svg:     SVG schema diagram
      - sparql:  SPARQL query stubs
      - senbero: ASCII schema

    Returns dict with keys: model_yaml, prefix_yaml, endpoint_yaml, svg, sparql, senbero
    Each value is a string or None on failure.
    """
    result = {
        "model_yaml":    _read(rdfconfig_dir / "model.yaml"),
        "prefix_yaml":   _read(rdfconfig_dir / "prefix.yaml"),
        "endpoint_yaml": None,
        "svg":           None,
        "sparql":        None,
        "senbero":       None,
    }

    if not result["model_yaml"]:
        return result  # ShExer didn't produce model.yaml — nothing to do

    # Write endpoint.yaml if we have an endpoint URL
    if endpoint_url:
        ep_yaml = f"- endpoint: {endpoint_url}\n  graphs:\n    - graph: default\n"
        (rdfconfig_dir / "endpoint.yaml").write_text(ep_yaml)
        result["endpoint_yaml"] = ep_yaml

    # Run each rdf-config subcommand
    result["svg"]     = _run_rdfconfig(rdfconfig_dir, "--schema")
    result["sparql"]  = _run_rdfconfig(rdfconfig_dir, "--sparql", "sparql")
    result["senbero"] = _run_rdfconfig(rdfconfig_dir, "--senbero")

    return result


def generate_for_dataset(rdf_file: Path,
                          shexer_venv: str,
                          run_shexer_script: Path,
                          endpoint_url: str = None
                          ) -> dict[str, str | None]:
    """
    Full pipeline: run ShExer on rdf_file with rdfconfig_directory,
    then generate rdf-config outputs.
    Returns same dict as generate_from_shex_output() plus 'shex' key.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="koetai_rdfcfg_"))
    try:
        r = subprocess.run(
            [shexer_venv, str(run_shexer_script),
             "--input", str(rdf_file),
             "--rdfconfig-dir", str(tmpdir)],
            capture_output=True, text=True, timeout=300
        )
        shex = r.stdout.strip() if r.returncode == 0 else None

        outputs = generate_from_shex_output(tmpdir, endpoint_url=endpoint_url)
        outputs["shex"] = shex
        return outputs
    except subprocess.TimeoutExpired:
        return {"error": "ShExer timed out", "shex": None,
                "model_yaml": None, "prefix_yaml": None,
                "endpoint_yaml": None, "svg": None, "sparql": None, "senbero": None}
    except Exception as e:
        return {"error": str(e), "shex": None,
                "model_yaml": None, "prefix_yaml": None,
                "endpoint_yaml": None, "svg": None, "sparql": None, "senbero": None}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _run_rdfconfig(config_dir: Path, *args: str, timeout: int = 120) -> str | None:
    """
    Run rdf-config via Docker against config_dir.
    Extra args are passed after --config /config.
    Returns stdout string or None on failure.
    """
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{config_dir}:/config",
        DOCKER_IMAGE,
        "rdf-config", "--config", "/config",
        *args,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8") if path.exists() else None
    except Exception:
        return None
