#!/usr/bin/env python3
"""
Standalone OWL RL reasoner — called as a subprocess from owl_service.py
so it runs inside the venv that has owlrl + rdflib installed.

Reads an RDF/OWL file, applies OWL RL + RDFS closure, writes materialized
triples to stdout in Turtle format.
"""
import argparse
import sys

import rdflib
import owlrl


FORMAT_MAP = {
    ".ttl":    "turtle",
    ".n3":     "n3",
    ".nt":     "nt",
    ".rdf":    "xml",
    ".owl":    "xml",
    ".jsonld": "json-ld",
    ".trig":   "trig",
    ".nq":     "nquads",
}


def main():
    parser = argparse.ArgumentParser(description="Apply OWL RL reasoning to an RDF file.")
    parser.add_argument("--input",  required=True, help="Input RDF/OWL file path")
    parser.add_argument("--regime", default="OWL_RL",
                        choices=["OWL_RL", "RDFS", "OWL_RL_Extras"],
                        help="Reasoning regime")
    args = parser.parse_args()

    import os
    ext = os.path.splitext(args.input)[1].lower()
    fmt = FORMAT_MAP.get(ext, "xml")

    g = rdflib.Dataset()
    try:
        g.parse(args.input, format=fmt)
    except Exception as e:
        print(f"ERROR: Could not parse {args.input}: {e}", file=sys.stderr)
        sys.exit(1)

    before = len(g)

    regime_map = {
        "OWL_RL":       owlrl.OWLRL_Semantics,
        "RDFS":         owlrl.RDFS_Semantics,
        "OWL_RL_Extras": owlrl.OWLRL_Extension,
    }
    regime = regime_map[args.regime]

    try:
        owlrl.DeductiveClosure(regime).expand(g)
    except Exception as e:
        print(f"ERROR: Reasoning failed: {e}", file=sys.stderr)
        sys.exit(1)

    after = len(g)
    print(f"# Inferred {after - before} new triples ({before} original, {after} total)",
          file=sys.stderr)

    sys.stdout.write(g.serialize(format="turtle"))


if __name__ == "__main__":
    main()
