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
    """Resolve active layout items relative to their preceding sibling."""
    out = copy.deepcopy(input_data)
    pages = out.get("pages") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    if not pages:
        pages = [page for document in out.get("documents") or [] for page in document.get("pages") or []]
    annotated = 0
    flowed = 0

    def children(value: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            child
            for key in ("children", "operation_groups")
            for child in value.get(key) or []
            if isinstance(child, dict)
        ]

    def owned_ordinals(value: dict[str, Any]) -> set[int]:
        if value.get("role") == "associated_text":
            return set()
        result = {
            int(item)
            for item in (value.get("operation_ordinals") or []) + (value.get("source_operation_ordinals") or [])
        }
        for child in children(value):
            result.update(owned_ordinals(child))
        return result

    def local_text_span(operations: list[dict[str, Any]], candidate: dict[str, Any]) -> list[int]:
        anchors = [int(value) for value in candidate.get("text_span") or []]
        if not anchors:
            return []
        end_ordinal = max(anchors)
        end_index = next((index for index, operation in enumerate(operations)
                          if int(operation.get("ordinal", 0)) == end_ordinal), None)
        if end_index is None:
            return sorted(set(anchors))
        text_start = next((index for index in range(end_index, -1, -1)
                           if operations[index].get("operator") == "BT"), None)
        if text_start is None:
            return sorted(set(anchors))
        start = text_start
        for index in range(text_start - 1, -1, -1):
            operator = operations[index].get("operator")
            if operator == "q":
                start = index
                break
            if operator in {"Q", "BT", "ET", "BDC", "EMC"}:
                break
        end = next((index for index in range(end_index, len(operations))
                    if operations[index].get("operator") == "ET"), end_index)
        if end + 1 < len(operations) and operations[end + 1].get("operator") == "Q":
            end += 1
        return [int(operation.get("ordinal", 0)) for operation in operations[start:end + 1]]

    def annotate_siblings(items: list[dict[str, Any]], parent_id: str) -> None:
        nonlocal annotated
        previous_id = parent_id
        previous_box = {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0}
        for item in items:
            coordinate_space = item.get("coordinate_space") or {}
            if isinstance(coordinate_space, dict) and coordinate_space.get("name") == "parent-relative-pdf":
                continue
            box = item.get("bbox") or {}
            if all(key in box for key in ("x", "y", "w", "h")):
                item["layout_position"] = {
                    "space": "previous-sibling-local",
                    "relative_to": previous_id,
                    "offset": {
                        "x": float(box["x"]) - previous_box["x"],
                        "y": float(box["y"]) - previous_box["y"],
                    },
                    "bbox": {
                        "x": float(box["x"]) - previous_box["x"],
                        "y": float(box["y"]) - previous_box["y"],
                        "w": float(box["w"]),
                        "h": float(box["h"]),
                    },
                }
                previous_id = str(item.get("id") or parent_id)
                previous_box = {key: float(box[key]) for key in ("x", "y", "w", "h")}
                annotated += 1
            annotate_siblings(children(item), str(item.get("id") or parent_id))

    def growth_containers(items: list[dict[str, Any]], ancestor: bool = False) -> list[dict[str, Any]]:
        result = []
        for item in items:
            growth = (item.get("layout") or {}).get("growth")
            current = bool(growth and growth.get("before") and growth.get("after"))
            if current and not ancestor:
                result.append(item)
            result.extend(growth_containers(children(item), ancestor or current))
        return result

    for page in pages:
        nodes: dict[str, dict[str, Any]] = {}
        _walk_nodes(page, nodes)
        page["coordinate_system"] = {"name": "page", "origin": {"x": 0.0, "y": 0.0}}
        for group in page.get("group_tree") or []:
            _annotate_group(group, {"x": 0.0, "y": 0.0}, str(page.get("id") or "page"), nodes)
            annotated += 1

        active_groups = page.get("operation_groups") or []
        from tools.operation_grouping import _text_operation_candidates
        for container in growth_containers(active_groups):
            growth = (container.get("layout") or {}).get("growth") or {}
            before = growth.get("before") or {}
            after = growth.get("after") or {}
            delta = float(after.get("y", 0)) + float(after.get("h", 0)) - float(before.get("y", 0)) - float(before.get("h", 0))
            if delta <= 0:
                continue
            threshold = float(before.get("y", 0)) + float(before.get("h", 0))
            claimed = set().union(*(owned_ordinals(group) for group in active_groups))
            operations = page.get("operations") or (page.get("realization") or {}).get("operations") or []
            candidates = sorted(
                _text_operation_candidates(page),
                key=lambda candidate: (
                    float(candidate["bbox"].get("y", 0)),
                    float(candidate["bbox"].get("x", 0)),
                    int(candidate.get("operation_ordinal", 0)),
                ),
            )
            previous_id = str(container.get("id") or "page")
            previous_box = before
            new_groups = []

            def collect_geometry_containers(value: Any) -> list[dict[str, Any]]:
                if not isinstance(value, dict):
                    return []
                result = [value] if value.get("role") == "geometry_container" else []
                for key in ("children", "operation_groups"):
                    for child in value.get(key) or []:
                        result.extend(collect_geometry_containers(child))
                return result

            page_height = float(page.get("height") or 841.92)
            for geometry_container in [
                geometry
                for root in page.get("group_tree") or []
                for geometry in collect_geometry_containers(root)
            ]:
                box = geometry_container.get("bbox") or {}
                x0 = float(box.get("x", 0))
                y0 = page_height - float(box.get("y", 0)) - float(box.get("h", 0))
                x1 = x0 + float(box.get("w", 0))
                y1 = y0 + float(box.get("h", 0))
                frame_ordinals = set()
                for index, operation in enumerate(operations):
                    if operation.get("operator") != "re":
                        continue
                    values = operation.get("operands") or []
                    if len(values) < 4:
                        continue
                    rx, ry, rw, rh = (float(value) for value in values[:4])
                    inside = rx >= x0 - 2 and ry >= y0 - 2 and rx + rw <= x1 + 2 and ry + rh <= y1 + 2
                    horizontal = rw >= float(box.get("w", 0)) * 0.8 and rh <= 2
                    vertical = rh >= 10 and rw <= 2 and (abs(rx - x0) <= 2 or abs(rx + rw - x1) <= 2)
                    if not inside or not (horizontal or vertical):
                        continue
                    frame_ordinals.add(int(operation.get("ordinal", 0)))
                    if index + 1 < len(operations) and operations[index + 1].get("operator") == "f*":
                        frame_ordinals.add(int(operations[index + 1].get("ordinal", 0)))
                frame_ordinals -= claimed
                if float(box.get("y", 0)) < threshold or not frame_ordinals:
                    continue
                shifted_box = copy.deepcopy(box)
                shifted_box["y"] = float(shifted_box.get("y", 0)) + delta
                new_groups.append({
                    "id": f"{container.get('id', 'container')}-flow-container-{geometry_container.get('id', 'geometry')}",
                    "type": "group",
                    "role": "flow_container",
                    "bbox": shifted_box,
                    "operation_ordinals": sorted(frame_ordinals),
                    "coordinate_space": {"name": "page", "origin": "top-left", "mapping": "absolute"},
                    "render_transform": [1, 0, 0, 1, 0, -delta],
                })
                claimed.update(frame_ordinals)

            loose_rule_ordinals = set()
            for index, operation in enumerate(operations):
                if operation.get("operator") != "re":
                    continue
                values = operation.get("operands") or []
                if len(values) < 4:
                    continue
                rx, ry, rw, rh = (float(value) for value in values[:4])
                top = page_height - ry - rh
                if rw < 300 or rh > 2 or top < threshold:
                    continue
                ordinal = int(operation.get("ordinal", 0))
                if ordinal in claimed:
                    continue
                loose_rule_ordinals.add(ordinal)
                if index + 1 < len(operations) and operations[index + 1].get("operator") == "f*":
                    loose_rule_ordinals.add(int(operations[index + 1].get("ordinal", 0)))
            if loose_rule_ordinals:
                new_groups.append({
                    "id": f"{container.get('id', 'container')}-flow-rules",
                    "type": "group",
                    "role": "flow_paint",
                    "bbox": {
                        "x": 0.0,
                        "y": threshold + delta,
                        "w": float(page.get("width") or 595.32),
                        "h": 0.0,
                    },
                    "operation_ordinals": sorted(loose_rule_ordinals),
                    "coordinate_space": {"name": "page", "origin": "top-left", "mapping": "absolute"},
                    "render_transform": [1, 0, 0, 1, 0, -delta],
                })
                claimed.update(loose_rule_ordinals)

            root_replacement = []
            for sibling in active_groups:
                if sibling is container:
                    root_replacement.append(sibling)
                    continue
                sibling_box = sibling.get("bbox") or {}
                if float(sibling_box.get("y", 0)) < threshold:
                    root_replacement.append(sibling)
                    continue
                shifted_box = copy.deepcopy(sibling_box)
                shifted_box["y"] = float(shifted_box.get("y", 0)) + delta
                root_replacement.append({
                    "id": f"{container.get('id', 'container')}-flow-group-{sibling.get('id', 'group')}",
                    "type": "group",
                    "role": "flow_container",
                    "bbox": shifted_box,
                    "children": [sibling],
                    "coordinate_space": {"name": "page", "origin": "top-left", "mapping": "absolute"},
                    "render_transform": [1, 0, 0, 1, 0, -delta],
                })
            active_groups[:] = root_replacement

            for candidate in candidates:
                box = candidate.get("bbox") or {}
                if float(box.get("y", 0)) < threshold:
                    continue
                source_span = local_text_span(operations, candidate)
                source_ordinals = set(source_span)
                if not source_ordinals or source_ordinals & claimed:
                    continue
                shifted_box = copy.deepcopy(box)
                shifted_box["y"] = float(shifted_box.get("y", 0)) + delta
                group_id = f"{container.get('id', 'container')}-flow-{candidate['operation_ordinal']}"
                new_groups.append({
                    "id": group_id,
                    "type": "group",
                    "role": "flow_item",
                    "bbox": shifted_box,
                    "operation_ordinals": source_span,
                    "coordinate_space": {"name": "page", "origin": "top-left", "mapping": "absolute"},
                    "render_transform": [1, 0, 0, 1, 0, -delta],
                    "layout_position": {
                        "space": "previous-sibling-local",
                        "relative_to": previous_id,
                        "offset": {
                            "x": float(box.get("x", 0)) - float(previous_box.get("x", 0)),
                            "y": float(box.get("y", 0)) - float(previous_box.get("y", 0)),
                        },
                    },
                })
                claimed.update(source_ordinals)
                previous_id = group_id
                previous_box = box
                flowed += 1
            active_groups.extend(new_groups)

        annotate_siblings(active_groups, str(page.get("id") or "page"))
    out.setdefault("provenance", {})["relative_coordinates"] = {
        "transform": TRANSFORM_ID,
        "pages": len(pages),
        "groups": annotated,
        "flow_items": flowed,
        "flow_model": "previous_sibling",
    }
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
