#!/usr/bin/env python3
"""Project the faithful PDF IR into the renderable document envelope."""

from __future__ import annotations

import copy
from typing import Any

from tools.transform_library import TransformError

RAW_SCHEMA = "pdf-training-pdf-ir-v1"
OUTPUT_SCHEMA = "pdf-training-renderable-dataset-v1"


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict) and value.get("type") == "number":
        return float(value["value"])
    raise TransformError(f"expected numeric PDF value, got {value!r}")


def _box(value: Any) -> dict[str, float]:
    if not isinstance(value, list) or len(value) != 4:
        raise TransformError("PDF page box must contain four numbers")
    x0, y0, x1, y1 = (_number(item) for item in value)
    if x1 <= x0 or y1 <= y0:
        raise TransformError("PDF page box must have positive dimensions")
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def project(document: dict[str, Any]) -> dict[str, Any]:
    """Create a renderable envelope without interpreting raw PDF content."""
    if document.get("schema") != RAW_SCHEMA:
        raise TransformError(f"expected input schema {RAW_SCHEMA}")
    pages = []
    for raw_page in document.get("pages") or []:
        media_box = _box(raw_page.get("media_box"))
        crop_box = _box(raw_page.get("crop_box") or raw_page.get("media_box"))
        page_id = str(raw_page.get("id") or f"page-{len(pages) + 1:03d}")
        pages.append({
            "schema": "pdf-training-renderable-page-v1",
            "id": page_id,
            "source_page_index": raw_page.get("index"),
            "output_page_index": len(pages),
            "width": media_box["w"],
            "height": media_box["h"],
            "nodes": [],
            "realization": {
                "schema": "pdf-training-raw-realization-v1",
                "media_box": media_box,
                "crop_box": crop_box,
                "rotate": copy.deepcopy(raw_page.get("rotate", 0)),
                "user_unit": copy.deepcopy(raw_page.get("user_unit", 1)),
                "page_ref": raw_page.get("page_ref"),
                "resources_ref": raw_page.get("resources_ref"),
                "contents_ref": raw_page.get("contents_ref"),
                "operations": copy.deepcopy(raw_page.get("operations") or []),
                "structured_operations": copy.deepcopy(raw_page.get("structured_operations") or {}),
            },
        })
    return {
        "schema": OUTPUT_SCHEMA,
        "name": str((document.get("source") or {}).get("path") or "pdf-document"),
        "source": copy.deepcopy(document.get("source") or {}),
        "realization_objects": copy.deepcopy(document.get("objects") or {}),
        "documents": [{"id": str((document.get("source") or {}).get("path") or "pdf-document"), "pages": pages}],
        "provenance": {"projection": {"transform": "project.pdf-ir.v1", "semantic_interpretation": False}},
    }
