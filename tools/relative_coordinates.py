"""Derive explicit group-local coordinates without changing page geometry."""

from __future__ import annotations

import copy
from typing import Any


TRANSFORM_ID = "layout.relative-coordinates.v1"
DEBUG_VISUALIZATION_ID = "debug.visualize-groups.v1"


def _relative(box: dict[str, Any], origin: dict[str, float]) -> dict[str, float]:
    return {"x": float(box["x"]) - origin["x"], "y": float(box["y"]) - origin["y"], "w": float(box["w"]), "h": float(box["h"])}


def _walk_nodes(value: Any, result: dict[str, dict[str, Any]]) -> None:
    if isinstance(value, dict):
        if value.get("id") and value.get("bbox"):
            result[str(value["id"])] = value
        for child in value.values():
            _walk_nodes(child, result)
    elif isinstance(value, list):
        for child in value:
            _walk_nodes(child, result)


def _annotate_group(group: dict[str, Any], parent_origin: dict[str, float], parent_id: str, nodes: dict[str, dict[str, Any]]) -> None:
    box = group.get("bbox") or {}
    if not all(key in box for key in ("x", "y", "w", "h")):
        return
    origin = {"x": float(box["x"]), "y": float(box["y"])}
    group_id = str(group.get("id") or "group")
    local_box = _relative(box, parent_origin)
    group["layout_box"] = {"space": "parent-local", "origin": parent_id, **local_box}
    group["layout_position"] = {"space": "parent-local", "origin": parent_id, "bbox": local_box}
    group["coordinate_space"] = {"name": "group-local", "origin_page": origin, "parent": parent_id}
    node_layout = {}
    for node_id in group.get("node_ids") or []:
        node = nodes.get(str(node_id))
        node_box = (node or {}).get("bbox") or {}
        if all(key in node_box for key in ("x", "y", "w", "h")):
            local_node_box = _relative(node_box, origin)
            node_layout[str(node_id)] = {"page_bbox": copy.deepcopy(node_box), "relative_bbox": local_node_box}
            node["layout_box"] = {"space": "group-local", "origin": group_id, **local_node_box}
    if node_layout:
        group["node_layout"] = node_layout
    for child in group.get("children") or []:
        _annotate_group(child, origin, group_id, nodes)


def apply_relative_coordinates(input_data: dict[str, Any]) -> dict[str, Any]:
    """Add box-owned local coordinate metadata to every existing group tree."""
    out = copy.deepcopy(input_data)
    pages = out.get("pages") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    if not pages:
        pages = [page for document in out.get("documents") or [] for page in document.get("pages") or []]
    annotated = 0
    for page in pages:
        nodes: dict[str, dict[str, Any]] = {}
        _walk_nodes(page, nodes)
        page["coordinate_system"] = {"name": "page", "origin": {"x": 0.0, "y": 0.0}}
        for group in page.get("group_tree") or []:
            _annotate_group(group, {"x": 0.0, "y": 0.0}, str(page.get("id") or "page"), nodes)
            annotated += 1
    out.setdefault("provenance", {})["relative_coordinates"] = {"transform": TRANSFORM_ID, "pages": len(pages), "groups": annotated}
    return out


def apply_debug_visualization(input_data: dict[str, Any], *, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Assign diagnostic paint attributes to renderable operation groups."""
    out = copy.deepcopy(input_data)
    pages = out.get("pages") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    painted = 0
    for page in pages:
        nesting_depth: dict[str, int] = {}

        def visit_tree(group: dict[str, Any], depth: int) -> None:
            if group.get("id"):
                nesting_depth[str(group["id"])] = depth
            if group.get("bbox") and group.get("type") == "group":
                group["paint"] = {
                    "debug": {
                        "fill_gray": max(0.75, 0.96 - depth * 0.06),
                        "stroke_gray": 0.15,
                        "stroke_width": 0.7,
                        "dash": [3, 2],
                    }
                }
            for child in group.get("children") or []:
                if isinstance(child, dict):
                    visit_tree(child, depth + 1)

        for group in page.get("group_tree") or []:
            visit_tree(group, 0)

        semantic_boxes: list[dict[str, float]] = []

        def collect_semantic_boxes(group: dict[str, Any]) -> None:
            box = group.get("bbox") or {}
            if all(key in box for key in ("x", "y", "w", "h")):
                semantic_boxes.append(box)
            for child in group.get("children") or []:
                if isinstance(child, dict):
                    collect_semantic_boxes(child)

        for group in page.get("group_tree") or []:
            collect_semantic_boxes(group)

        def contains(outer: dict[str, float], inner: dict[str, float]) -> bool:
            return (
                float(outer["x"]) <= float(inner["x"])
                and float(outer["y"]) <= float(inner["y"])
                and float(outer["x"]) + float(outer["w"]) >= float(inner["x"]) + float(inner["w"])
                and float(outer["y"]) + float(outer["h"]) >= float(inner["y"]) + float(inner["h"])
                and (float(outer["w"]) > float(inner["w"]) or float(outer["h"]) > float(inner["h"]))
            )

        def operation_depth(group: dict[str, Any]) -> int:
            box = group.get("bbox") or {}
            if not all(key in box for key in ("x", "y", "w", "h")):
                return 0
            return sum(contains(outer, box) for outer in semantic_boxes)

        def paint_operation_group(group: dict[str, Any], depth: int) -> None:
            nonlocal painted
            if group.get("bbox") and group.get("role") != "associated_text" and group.get("type") in {"group", "draw_group"}:
                group["paint"] = {
                    "debug": {
                        "background": True,
                        "fill_gray": max(0.75, 0.96 - depth * 0.06),
                        "stroke_gray": 0.15,
                        "stroke_width": 0.7,
                        "dash": [3, 2],
                    }
                }
                painted += 1
            for child in group.get("children") or []:
                if isinstance(child, dict):
                    paint_operation_group(child, depth + 1)

        for group in page.get("operation_groups") or []:
            paint_operation_group(group, operation_depth(group))
    out.setdefault("provenance", {})["debug_visualization"] = {"transform": DEBUG_VISUALIZATION_ID, "painted_groups": painted}
    return out
