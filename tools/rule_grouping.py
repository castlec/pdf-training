"""Deterministic grouping rules for render-flow diagnostics."""

from __future__ import annotations

import copy
from typing import Any


def _box(node: dict[str, Any]) -> dict[str, float] | None:
    value = node.get("bbox") or {}
    try:
        box = {key: float(value[key]) for key in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError):
        return None
    if box["w"] <= 0 or box["h"] <= 0:
        return None
    return box


def _is_horizontal_rule(node: dict[str, Any], page_width: float) -> bool:
    box = _box(node)
    if not box:
        return False
    kind = str(node.get("type") or node.get("role") or "").lower()
    return kind in {"rule", "horizontal_rule", "line"} and box["w"] >= page_width * 0.5 and box["w"] >= box["h"] * 20


def _contains(outer: dict[str, float], inner: dict[str, float]) -> bool:
    return inner["x"] >= outer["x"] and inner["y"] >= outer["y"] and inner["x"] + inner["w"] <= outer["x"] + outer["w"] and inner["y"] + inner["h"] <= outer["y"] + outer["h"]


def _raw_horizontal_rule_nodes(page: dict[str, Any]) -> list[dict[str, Any]]:
    media_box = page.get("media_box") or []
    width = float(page.get("width") or (media_box[2] - media_box[0] if len(media_box) >= 4 else 0))
    height = float(page.get("height") or (media_box[3] - media_box[1] if len(media_box) >= 4 else 0))
    operations = page.get("operations") or (page.get("realization") or {}).get("operations") or []
    rules = []
    for operation in operations:
        if operation.get("operator") != "re":
            continue
        values = operation.get("operands") or []
        if len(values) < 4 or float(values[2]) < width * 0.5 or float(values[3]) > 2:
            continue
        rules.append({
            "id": f"{page.get('id') or 'page'}-raw-rule-{operation.get('ordinal')}",
            "type": "rule",
            "bbox": {"x": float(values[0]), "y": height - float(values[1]) - float(values[3]), "w": float(values[2]), "h": float(values[3])},
        })
    return rules


def group_page_by_horizontal_rules(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Emit groups whose scope closes when a horizontal rule is encountered."""
    media_box = page.get("media_box") or []
    page_width = float(page.get("width") or (media_box[2] - media_box[0] if len(media_box) >= 4 else 0))
    page_height = float(page.get("height") or (media_box[3] - media_box[1] if len(media_box) >= 4 else 0))
    nodes = [node for node in page.get("nodes") or [] if _box(node)]
    if not nodes:
        nodes = _raw_horizontal_rule_nodes(page)
    rules = sorted((node for node in nodes if _is_horizontal_rule(node, page_width)), key=lambda node: (_box(node)["y"], str(node.get("id") or "")))
    groups: list[dict[str, Any]] = []
    scope_start = 0.0
    scope_nodes: list[dict[str, Any]] = []

    def emit(index: int, scope_end: float, closing_rule: dict[str, Any] | None) -> None:
        ordered = sorted(scope_nodes, key=lambda item: (_box(item)["y"], _box(item)["x"], str(item.get("id") or "")))
        if ordered:
            boxes = [_box(node) for node in ordered]
            content_box = {"x": min(box["x"] for box in boxes), "y": min(box["y"] for box in boxes), "w": max(box["x"] + box["w"] for box in boxes) - min(box["x"] for box in boxes), "h": max(box["y"] + box["h"] for box in boxes) - min(box["y"] for box in boxes)}
        else:
            content_box = {"x": 0.0, "y": scope_start, "w": page_width, "h": max(0.0, scope_end - scope_start)}
        section_box = {"x": 0.0, "y": scope_start, "w": page_width, "h": max(0.0, scope_end - scope_start)}
        content = {"id": f"{page.get('id') or 'page'}-rule-section-{index:02d}-content", "type": "content", "rule": "between_horizontal_rules", "depth": 1, "bbox": content_box, "node_ids": [str(node.get("id")) for node in ordered], "children": []}
        groups.append({"id": f"{page.get('id') or 'page'}-rule-section-{index:02d}", "type": "group", "rule": "horizontal_rule_scope", "depth": 0, "bbox": section_box, "closing_rule_id": str(closing_rule.get("id")) if closing_rule else None, "node_ids": content["node_ids"], "children": [content]})

    for index, rule in enumerate(rules, start=1):
        rule_box = _box(rule)
        rule_y = rule_box["y"] + rule_box["h"] / 2
        scope_nodes = [node for node in nodes if node not in rules and scope_start <= _box(node)["y"] + _box(node)["h"] / 2 < rule_y]
        emit(index, rule_y, rule)
        scope_start = rule_y
    scope_nodes = [node for node in nodes if node not in rules and _box(node)["y"] + _box(node)["h"] / 2 >= scope_start]
    emit(len(rules) + 1, page_height, None)

    # Existing structural groups remain available for later nesting transforms.
    for child in page.get("structure_groups") or []:
        child_box = child.get("bbox")
        if not child_box:
            continue
        parents = [group for group in groups if _contains(group["bbox"], child_box)]
        if parents:
            parent = min(parents, key=lambda item: item["bbox"]["h"])
            nested = copy.deepcopy(child)
            nested["type"] = "group"
            nested["depth"] = 2
            nested["rule"] = nested.get("rule") or "existing_structure_group"
            parent["children"].append(nested)
    return groups


def apply_horizontal_rule_grouping(input_data: dict[str, Any]) -> dict[str, Any]:
    """Add rule-delimited group trees without changing source nodes."""
    out = copy.deepcopy(input_data)
    pages = out.get("pages") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    if not pages:
        pages = [page for document in out.get("documents") or [] for page in document.get("pages") or []]
    for page in pages:
        page["group_tree"] = group_page_by_horizontal_rules(page)
    out.setdefault("provenance", {})["horizontal_rule_grouping"] = {"rule": "horizontal_rule_section", "pages": len(pages), "groups": sum(len(page.get("group_tree") or []) for page in pages)}
    return out
