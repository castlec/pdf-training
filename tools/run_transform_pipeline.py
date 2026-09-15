#!/usr/bin/env python3
"""Execute a persisted transform-ID manifest; input and rendering stay outside it."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from tools.transform_library import TransformError, apply_transform

SCHEMA = "pdf-training-transform-pipeline-v1"


def load_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != SCHEMA:
        raise TransformError(f"expected manifest schema {SCHEMA}")
    transforms = value.get("transforms")
    if not isinstance(transforms, list):
        raise TransformError("manifest transforms must be a list")
    return value


def run(manifest_path: Path, input_path: Path, output_path: Path) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    document = json.loads(input_path.read_text(encoding="utf-8"))
    applied = []
    for stage in manifest["transforms"]:
        transform_id = stage.get("id")
        if not transform_id:
            raise TransformError("every manifest transform requires id")
        options = stage.get("options") or {}
        if not isinstance(options, dict):
            raise TransformError(f"transform {transform_id} options must be an object")
        document = apply_transform(document, str(transform_id), options)
        applied.append(str(transform_id))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"schema": "pdf-training-transform-report-v1", "pipeline": manifest["id"], "transforms": applied, "output": str(output_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.manifest, args.input, args.output), indent=2))
    except (OSError, ValueError, TransformError, json.JSONDecodeError) as exc:
        print(f"transform pipeline error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
