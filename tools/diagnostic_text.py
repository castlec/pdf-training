#!/usr/bin/env python3
"""Evaluation-only transforms for exercising document layout behavior."""

from __future__ import annotations

import copy
from typing import Any

INJECT_TEXT_LINES_TRANSFORM_ID = "evaluation.inject-six-lines-into-first-cell.v1"


def inject_text_lines_into_first_cell(input_data: dict[str, Any], *, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add declared test text as child-owned content to the first table cell."""
    out = copy.deepcopy(input_data)
    options = options or {}
    target_page = str(options.get("page_id") or "page-004")
    lines = [str(line) for line in options.get("lines") or [
        "Synthetic line 1",
        "Synthetic line 2",
        "Synthetic line 3",
        "Synthetic line 4",
        "Synthetic line 5",
        "Synthetic line 6",
    ]]
    if len(lines) != 6:
        raise ValueError("evaluation text injection requires exactly six lines")
    injected = 0
    for page in out.get("pages") or []:
        if str(page.get("id")) != target_page:
            continue
        groups = page.get("operation_groups") or []
        table = next((group for group in groups if group.get("layout_kind") == "table"), None)
        if table is None or not table.get("children"):
            raise ValueError(f"page {target_page} has no one-cell table group")
        cell = table["children"][0]
        bbox = cell.get("bbox") or {}
        page_height = float(page.get("height") or 841.92)
        start_top = float(bbox.get("y", 0)) + float(bbox.get("h", 0)) + 8.0
        line_height = 14.0
        x = float(bbox.get("x", 0)) + 4.0
        next_ordinal = max((int(operation["ordinal"]) for operation in page.get("operations") or []), default=-1) + 1
        line_groups = []
        for index, line in enumerate(lines):
            top = start_top + index * line_height
            pdf_y = page_height - top - 10.0
            line_ordinals = []
            for operator, operands in (
                ("BT", []),
                ("Tf", [{"type": "name", "value": "/F4"}, 12]),
                ("Tm", [1, 0, 0, 1, x, pdf_y]),
                ("Tj", [{"type": "string", "value": line}]),
                ("ET", []),
            ):
                ordinal = next_ordinal
                next_ordinal += 1
                page.setdefault("operations", []).append({"ordinal": ordinal, "operator": operator, "operands": operands})
                line_ordinals.append(ordinal)
            line_groups.append({
                "id": f"{cell.get('id', 'cell')}-synthetic-line-{index + 1}",
                "type": "group",
                "role": "synthetic_text_line",
                "bbox": {
                    "x": x,
                    "y": top,
                    "w": max(12.0, len(line) * 6.0),
                    "h": 12.0,
                },
                "operation_ordinals": line_ordinals,
                "coordinate_space": "page",
                "render_mode": "source_operations",
            })
        structured = page.get("structured_operations") or {}
        structured_children = structured.get("children") if isinstance(structured, dict) else None
        if not isinstance(structured_children, list):
            raise ValueError(f"page {target_page} has no structured operation children")
        effective_state = copy.deepcopy(structured_children[-1].get("effective_state", {})) if structured_children else {}
        synthetic_ordinals = [
            ordinal
            for line_group in line_groups
            for ordinal in line_group["operation_ordinals"]
        ]
        for operation in page["operations"][-len(synthetic_ordinals):]:
            structured_children.append({
                "type": "operation",
                "source_ordinal": int(operation["ordinal"]),
                "operator": operation["operator"],
                "operands": copy.deepcopy(operation.get("operands") or []),
                "effective_state": copy.deepcopy(effective_state),
            })
        cell.setdefault("children", []).append({
            "id": f"{cell.get('id', 'cell')}-synthetic-text",
            "type": "group",
            "role": "synthetic_text",
            "bbox": {
                "x": min(line["bbox"]["x"] for line in line_groups),
                "y": min(line["bbox"]["y"] for line in line_groups),
                "w": max(line["bbox"]["x"] + line["bbox"]["w"] for line in line_groups) - min(line["bbox"]["x"] for line in line_groups),
                "h": max(line["bbox"]["y"] + line["bbox"]["h"] for line in line_groups) - min(line["bbox"]["y"] for line in line_groups),
            },
            "children": line_groups,
            "coordinate_space": "page",
        })
        injected = len(lines)
        break
    if injected != 6:
        raise ValueError(f"target page {target_page} was not found")
    out.setdefault("provenance", {})["injected_text_lines"] = {
        "transform": INJECT_TEXT_LINES_TRANSFORM_ID,
        "page_id": target_page,
        "lines": lines,
        "count": injected,
        "ownership_transfer": "child_groups",
    }
    return out
