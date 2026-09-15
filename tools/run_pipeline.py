#!/usr/bin/env python3
"""Execute a persisted pdf-training pipeline manifest.

The manifest is the source of truth for stage order. This runner does not
select, infer, skip, or insert transforms.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA = "pdf-training-pipeline-v1"


class PipelineError(ValueError):
    pass


def load_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != SCHEMA:
        raise PipelineError(f"expected manifest schema {SCHEMA}")
    stages = value.get("stages")
    if not isinstance(stages, list) or not stages:
        raise PipelineError("manifest must contain a non-empty stages list")
    ids = [stage.get("id") for stage in stages]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise PipelineError("stages require unique ids")
    return value


def expand(value: str, variables: dict[str, str]) -> str:
    result = value
    for key, replacement in variables.items():
        result = result.replace("${" + key + "}", replacement)
    if "${" in result:
        raise PipelineError(f"undeclared pipeline variable in {value!r}")
    return result


def stage_command(stage: dict[str, Any], variables: dict[str, str]) -> list[str]:
    command = stage.get("command")
    if not isinstance(command, list) or not command or any(not isinstance(item, str) for item in command):
        raise PipelineError(f"stage {stage.get('id')} requires a non-empty command array")
    return [expand(item, variables) for item in command]


def run_manifest(manifest_path: Path, *, initial: dict[str, str], dry_run: bool = False) -> list[dict[str, Any]]:
    manifest = load_manifest(manifest_path)
    variables = {key: str(value) for key, value in (manifest.get("variables") or {}).items()}
    variables.update(initial)
    report = []
    for index, stage in enumerate(manifest["stages"], start=1):
        stage_id = str(stage["id"])
        command = stage_command(stage, variables)
        record = {
            "index": index,
            "id": stage_id,
            "input_schema": stage.get("input_schema"),
            "output_schema": stage.get("output_schema"),
            "command": command,
        }
        if dry_run:
            report.append({**record, "status": "planned"})
            continue
        completed = subprocess.run(command, cwd=manifest_path.parent.parent, check=False)
        if completed.returncode:
            raise PipelineError(f"stage {stage_id} failed with exit code {completed.returncode}")
        for output_key in stage.get("required_outputs") or []:
            output = variables.get(str(output_key))
            if not output or not Path(output).exists():
                raise PipelineError(f"stage {stage_id} did not produce declared output {output_key}")
        report.append({**record, "status": "completed"})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--var", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    initial = {}
    for item in args.var:
        if "=" not in item:
            parser.error(f"--var requires NAME=VALUE: {item}")
        key, value = item.split("=", 1)
        initial[key] = value
    try:
        report = run_manifest(args.manifest, initial=initial, dry_run=args.dry_run)
    except (OSError, PipelineError, json.JSONDecodeError) as exc:
        print(f"pipeline error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"schema": "pdf-training-pipeline-report-v1", "stages": report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
