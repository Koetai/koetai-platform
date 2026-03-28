#!/usr/bin/env python3
"""
Standalone ShExer runner — called as a subprocess from shexer_service.py
so it runs inside the venv that has shexer installed.
"""
import argparse
import sys
from shexer.shaper import Shaper
from shexer.consts import TURTLE, NT, SHEX_NAMESPACE

FORMAT_MAP = {
    ".ttl": TURTLE,
    ".nt":  NT,
    ".n3":  TURTLE,
}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",  required=True)
    parser.add_argument("--format", default="shex", choices=["shex"])
    parser.add_argument("--graph",  default=None)
    args = parser.parse_args()

    import os
    ext = os.path.splitext(args.input)[1].lower()
    rdf_format = FORMAT_MAP.get(ext, TURTLE)

    shaper = Shaper(
        target_classes=None,
        source_file=args.input,
        input_format=rdf_format,
        all_classes_mode=True,
    )
    result = shaper.shex_graph(string_output=True)
    print(result)

if __name__ == "__main__":
    main()
