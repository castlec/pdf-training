#!/usr/bin/env python3
"""Transform PDF content streams while preserving native non-text artwork."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pikepdf


# Text state/showing operators. Graphic operators such as re, m, l, c, S, f,
# Do, cm, and clipping operators are deliberately retained.
TEXT_OPERATORS = {
    "BT", "ET", "Tf", "Tj", "TJ", "'", '"', "Td", "TD", "Tm", "T*",
    "Tc", "Tw", "Tz", "TL", "Tr", "Ts",
}
TEXT_LAYER_OPERATORS = TEXT_OPERATORS | {
    "q", "Q", "cm", "gs", "g", "G", "rg", "RG", "k", "K", "sc", "SC", "scn", "SCN", "BDC", "EMC",
}


def transform_page(pdf: pikepdf.Pdf, page: pikepdf.Page, mode: str) -> dict[str, int]:
    page.contents_coalesce()
    instructions = pikepdf.parse_content_stream(page)
    kept = []
    removed = Counter()
    for instruction in instructions:
        operator = str(instruction.operator)
        remove = operator in TEXT_OPERATORS if mode == "artwork" else operator not in TEXT_LAYER_OPERATORS
        if remove:
            removed[operator] += 1
        else:
            kept.append(instruction)
    page.Contents = pdf.make_stream(pikepdf.unparse_content_stream(kept))
    return {key: removed[key] for key in sorted(removed)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pages", default="all", help="1-based page list, e.g. 4 or 2,4")
    parser.add_argument("--mode", choices=("artwork", "text"), default="artwork")
    args = parser.parse_args()
    with pikepdf.Pdf.open(args.input) as pdf:
        selected = set(range(len(pdf.pages))) if args.pages == "all" else {int(value) - 1 for value in args.pages.split(",")}
        reports = []
        for index, page in enumerate(pdf.pages):
            if index in selected:
                reports.append({"page": index + 1, "mode": args.mode, "removed": transform_page(pdf, page, args.mode)})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        pdf.save(args.output)
    print(json.dumps({"output": str(args.output.resolve()), "pages": reports}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
