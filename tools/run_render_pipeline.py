#!/usr/bin/env python3
"""Run the fixed extract -> transforms -> render lifecycle."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from tools.transform_library import TransformError, apply_transform

ROOT = Path(__file__).resolve().parents[1]
EXTRACTOR = ROOT / "tools" / "extract_pdf_ir.py"
RENDERER = ROOT / "tools" / "render_pdf_ir.py"
MANIFEST_SCHEMA = "pdf-training-transform-pipeline-v1"


class PipelineError(ValueError):
    pass


def load_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != MANIFEST_SCHEMA:
        raise PipelineError(f"expected manifest schema {MANIFEST_SCHEMA}")
    if "input" in value or "render" in value:
        raise PipelineError("manifest may declare transforms only; input and render are fixed")
    if not isinstance(value.get("transforms"), list):
        raise PipelineError("manifest transforms must be a list")
    return value


def run(manifest_path: Path, source_pdf: Path, output_pdf: Path, *, work_dir: Path | None = None, document_id: str | None = None) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    temporary = tempfile.TemporaryDirectory(prefix="pdf-training-pipeline-") if work_dir is None else None
    root = Path(temporary.name) if temporary else work_dir
    assert root is not None
    root.mkdir(parents=True, exist_ok=True)
    extracted = root / "extracted.json"
    extract_command = [sys.executable, str(EXTRACTOR), "--pdf", str(source_pdf), "--out", str(extracted)]
    subprocess.run(extract_command, cwd=ROOT, check=True)

    document_path = extracted
    document = json.loads(document_path.read_text(encoding="utf-8"))
    applied = []
    for stage in manifest["transforms"]:
        transform_id = stage.get("id")
        if not transform_id:
            raise PipelineError("every manifest transform requires id")
        options = stage.get("options") or {}
        if not isinstance(options, dict):
            raise PipelineError(f"transform {transform_id} options must be an object")
        document = apply_transform(document, str(transform_id), options)
        applied.append(str(transform_id))
    transformed_path = root / "transformed.json"
    transformed_path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    render_command = [sys.executable, str(RENDERER), "--input", str(transformed_path), "--out", str(output_pdf)]
    subprocess.run(render_command, cwd=ROOT, check=True)
    report = {"schema": "pdf-training-render-pipeline-report-v1", "pipeline": manifest["id"], "source_pdf": str(source_pdf), "input_schema": document.get("schema"), "transforms": applied, "output_pdf": str(output_pdf)}
    if temporary:
        temporary.cleanup()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("source_pdf", type=Path)
    parser.add_argument("output_pdf", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--document-id")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.manifest, args.source_pdf, args.output_pdf, work_dir=args.work_dir, document_id=args.document_id), indent=2))
    except (OSError, subprocess.CalledProcessError, ValueError, TransformError, json.JSONDecodeError) as exc:
        print(f"render pipeline error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
