#!/usr/bin/env python3
"""Reject common private or source-derived artifacts before publication."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


BLOCKED_SUFFIXES = {".pdf", ".tif", ".tiff", ".jpg", ".jpeg", ".png", ".webp"}
BLOCKED_PARTS = {"private", "source-data"}
ABSOLUTE_WORKSPACE = re.compile(r"/(?:home|Users)/[^/\s]+/")


def repository_files(root: Path, tracked_only: bool) -> list[Path]:
    if tracked_only:
        output = subprocess.check_output(["git", "ls-files"], cwd=root, text=True)
        return [root / line for line in output.splitlines() if line]
    return [path for path in root.rglob("*") if path.is_file() and ".git" not in path.parts]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--tracked-only", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    errors: list[str] = []

    for path in repository_files(root, args.tracked_only):
        relative = path.relative_to(root)
        if path.name.endswith(".__tmp__"):
            errors.append(f"temporary repack artifact: {relative}")
        if any(part in BLOCKED_PARTS for part in relative.parts):
            errors.append(f"private path: {relative}")
        if path.suffix.lower() in BLOCKED_SUFFIXES:
            errors.append(f"source-media extension: {relative}")
        if path.suffix.lower() in {".md", ".json", ".toml", ".txt", ".py", ".sh"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            if ABSOLUTE_WORKSPACE.search(text):
                errors.append(f"absolute user path: {relative}")
        if relative.parts[:1] == ("datasets",) and path.name == "manifest.json":
            errors.append(f"dataset manifest requires rights review: {relative}")

    if errors:
        print("\n".join(sorted(set(errors))))
        return 1
    print("Publishability checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
