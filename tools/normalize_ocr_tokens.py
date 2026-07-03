#!/usr/bin/env python3
"""Replace internal OCR tokens using explicit TOKEN=VALUE mappings."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_mapping(value: str) -> tuple[str, str]:
    source, separator, target = value.partition("=")
    if not separator or not source:
        raise argparse.ArgumentTypeError("mapping must be TOKEN=VALUE")
    return source, target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, help="Input text file; stdin when omitted.")
    parser.add_argument(
        "--replace",
        action="append",
        default=[],
        type=parse_mapping,
        metavar="TOKEN=VALUE",
        help="Repeatable replacement applied in command-line order.",
    )
    args = parser.parse_args()
    text = args.path.read_text(encoding="utf-8") if args.path else sys.stdin.read()
    for source, target in args.replace:
        text = text.replace(source, target)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
