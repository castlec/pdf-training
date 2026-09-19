#!/usr/bin/env python3
"""Export the validated text-only projection for a future translation pass."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from tools.translation_projection import PROJECTION_SCHEMA

REQUEST_SCHEMA = "pdf-training-translation-request-v1"


def build_request(document: dict[str, Any]) -> dict[str, Any]:
    projection = ((document.get("metadata") or {}).get("translation_projection"))
    if not isinstance(projection, dict) or projection.get("schema") != PROJECTION_SCHEMA:
        raise ValueError("input does not contain a translation projection")
    validation = projection.get("validation") or {}
    if validation.get("status") != "passed":
        raise ValueError("translation projection has not passed validation")
    units = []
    for unit in projection.get("units") or []:
        context = unit.get("context") or {}
        units.append({
            "id": unit["id"],
            "text": unit["source_text"],
            "page_id": context.get("page_id"),
            "group_path": [
                {key: item[key] for key in ("id", "role", "layout_kind") if key in item}
                for item in context.get("group_path") or []
            ],
            "geometry_group_ids": list(context.get("geometry_group_ids") or []),
            "table_path": copy.deepcopy(context.get("table_path") or []),
        })
    return {
        "schema": REQUEST_SCHEMA,
        "status": "ready_for_model",
        "model_invoked": False,
        "source_sha256": projection.get("source_sha256"),
        "target_language": projection.get("target_language", "English"),
        "model_input_policy": copy.deepcopy(projection.get("model_input_policy") or {}),
        "translation_prompt": projection.get("translation_prompt"),
        "terminology_prompt": projection.get("terminology_prompt"),
        "units": units,
        "summary": {"units": len(units), "pages": projection.get("summary", {}).get("pages", 0)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transformed", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    document = json.loads(args.transformed.read_text(encoding="utf-8"))
    request = build_request(document)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"schema": REQUEST_SCHEMA, "output": str(args.output), "units": len(request["units"]), "model_invoked": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
