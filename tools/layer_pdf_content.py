#!/usr/bin/env python3
"""Merge a native PDF text layer over a native PDF artwork layer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pikepdf


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artwork", required=True, type=Path)
    parser.add_argument("--text", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--pages", default="all", help="1-based page list to layer, e.g. 4 or 2,4")
    args = parser.parse_args()
    with pikepdf.Pdf.open(args.artwork) as artwork, pikepdf.Pdf.open(args.text) as text_pdf:
        selected = set(range(len(artwork.pages))) if args.pages == "all" else {int(value) - 1 for value in args.pages.split(",")}
        for index, (target, layer) in enumerate(zip(artwork.pages, text_pdf.pages)):
            if index in selected:
                target.add_overlay(layer, push_stack=True, shrink=False, expand=False)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        page_count = len(artwork.pages)
        artwork.save(args.out)
    print(json.dumps({"output": str(args.out.resolve()), "pages": page_count}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
