"""Diagnostic transform for render items that have not gained structural ownership."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from typing import Any

TRANSFORM_ID = "diagnostics.unprocessed-items.v1"

_PATH_CONSTRUCTORS = {"m", "l", "c", "v", "y", "h", "re"}
_PATH_PAINT = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*"}
_TEXT_OPERATORS = {"BT", "ET"}


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _source_ordinals(value: Any) -> list[int]:
    if isinstance(value, list):
        return [int(item) for item in value if isinstance(item, (int, float))]
    if isinstance(value, dict):
        start = value.get("start")
        end = value.get("end")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            return list(range(int(start), int(end) + 1))
    return []


def _owner_ids(page: dict[str, Any]) -> dict[int, set[str]]:
    owners: dict[int, set[str]] = defaultdict(set)

    def visit(value: Any, ancestors: tuple[str, ...] = ()) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item, ancestors)
            return
        if not isinstance(value, dict):
            return

        node_id = value.get("id")
        path = ancestors + ((str(node_id),) if node_id is not None else ())
        for key in ("source_operation_ordinals", "operation_ordinals"):
            for ordinal in _source_ordinals(value.get(key)):
                owners[ordinal].update(path)
        for key in ("children", "nodes"):
            visit(value.get(key), path)

    visit(page.get("operation_groups"))
    visit(page.get("group_tree"))
    return owners


def _state_by_ordinal(page: dict[str, Any]) -> dict[int, dict[str, Any]]:
    states: dict[int, dict[str, Any]] = {}

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        if value.get("type") == "operation" and isinstance(value.get("source_ordinal"), int):
            state = value.get("effective_state")
            if isinstance(state, dict):
                states[value["source_ordinal"]] = state
        visit(value.get("children"))

    visit(page.get("structured_operations"))
    return states


def _graphics_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(state, dict):
        return None
    graphics = state.get("graphics")
    if not isinstance(graphics, dict):
        return None
    keys = ("ctm", "line_width", "stroke_color", "fill_color", "stroke_gray", "fill_gray")
    return {key: deepcopy(graphics[key]) for key in keys if key in graphics}


def _bbox(operations: list[dict[str, Any]]) -> dict[str, float] | None:
    points: list[tuple[float, float]] = []
    for operation in operations:
        operator = operation.get("operator")
        operands = operation.get("operands", [])
        numbers = [_number(value) for value in operands]
        if operator == "re" and len(numbers) >= 4 and all(value is not None for value in numbers[:4]):
            x, y, width, height = (float(value) for value in numbers[:4])
            points.extend(((x, y), (x + width, y + height)))
        elif operator in {"m", "l"} and len(numbers) >= 2 and all(value is not None for value in numbers[:2]):
            points.append((float(numbers[0]), float(numbers[1])))
        elif operator in {"c", "v", "y"}:
            pairs = zip(numbers[::2], numbers[1::2])
            points.extend((float(x), float(y)) for x, y in pairs if x is not None and y is not None)
    if not points:
        return None
    xs, ys = zip(*points)
    return {"x": min(xs), "y": min(ys), "w": max(xs) - min(xs), "h": max(ys) - min(ys)}


def _ownership(ordinals: list[int], owners: dict[int, set[str]]) -> tuple[str, list[str]]:
    owner_sets = [owners.get(ordinal, set()) for ordinal in ordinals]
    all_owner_ids = sorted(set().union(*owner_sets)) if owner_sets else []
    if not all(owner_sets):
        return ("partially_owned" if all_owner_ids else "unowned", all_owner_ids)
    return "owned", all_owner_ids


def _item(
    page: dict[str, Any],
    operations: list[dict[str, Any]],
    owners: dict[int, set[str]],
    states: dict[int, dict[str, Any]],
    kind: str,
    classification: str,
) -> dict[str, Any]:
    ordinals = [int(operation["ordinal"]) for operation in operations]
    ownership, owner_ids = _ownership(ordinals, owners)
    first_state = states.get(ordinals[0]) if ordinals else None
    result: dict[str, Any] = {
        "kind": kind,
        "classification": classification,
        "ownership": ownership,
        "owner_ids": owner_ids,
        "source_operation_ordinals": ordinals,
        "operators": [str(operation.get("operator")) for operation in operations],
    }
    bbox = _bbox(operations)
    if bbox is not None:
        result["bbox_pdf"] = bbox
    graphics = _graphics_state(first_state)
    if graphics is not None:
        result["graphics_state"] = graphics
    return result


def _path_classification(operations: list[dict[str, Any]], paint_operator: str, options: dict[str, Any]) -> str:
    operators = [operation.get("operator") for operation in operations]
    if paint_operator in {"S", "s"}:
        if len([operator for operator in operators if operator in {"m", "l", "c", "v", "y"}]) <= 2:
            return "radical_or_geometry_stroke_candidate"
        return "geometry_stroke_candidate"
    if paint_operator in _PATH_PAINT:
        bbox = _bbox(operations)
        if bbox is not None:
            width = bbox["w"]
            height = bbox["h"]
            minimum = float(options.get("thin_rule_min_length", 6.0))
            maximum = float(options.get("thin_rule_max_thickness", 2.5))
            if max(width, height) >= minimum and min(width, height) <= maximum:
                return "thin_rule_or_fraction_bar_candidate"
        return "filled_geometry_candidate"
    return "draw_operation_candidate"


def _visual_items(
    page: dict[str, Any], options: dict[str, Any], owners: dict[int, set[str]], states: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    operations = [operation for operation in page.get("operations", []) if isinstance(operation, dict)]
    items: list[dict[str, Any]] = []
    path: list[dict[str, Any]] = []
    text: list[dict[str, Any]] = []
    include_text = bool(options.get("include_text", True))

    def add(item: dict[str, Any]) -> None:
        if item["ownership"] != "owned":
            items.append(item)

    for operation in operations:
        operator = operation.get("operator")
        if operator == "BT":
            text = [operation]
            path = []
            continue
        if text:
            text.append(operation)
            if operator == "ET":
                if include_text:
                    add(_item(page, text, owners, states, "text_span", "text_content_candidate"))
                text = []
            continue
        if operator in _PATH_CONSTRUCTORS:
            path.append(operation)
            continue
        if operator in _PATH_PAINT:
            if path:
                add(_item(page, path + [operation], owners, states, "path_span", _path_classification(path, operator, options)))
            path = []
            continue
        if operator == "Do":
            add(_item(page, [operation], owners, states, "image_operation", "embedded_image_candidate"))
        elif operator == "sh":
            add(_item(page, [operation], owners, states, "shading_operation", "shading_candidate"))
        elif operator in {"BMC", "BDC", "EMC", "n"}:
            path = []
        elif operator not in {"q", "Q", "W", "W*"} and path:
            path = []

    return items


def _page_report(page: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    owners = _owner_ids(page)
    states = _state_by_ordinal(page)
    items = _visual_items(page, options, owners, states)
    counts = Counter(item["classification"] for item in items)
    return {
        "schema": "pdf-training-unprocessed-items-v1",
        "transform": TRANSFORM_ID,
        "policy": {
            "ownership": "source_operation_ordinals claimed by an operation group",
            "include_text": bool(options.get("include_text", True)),
            "thin_rule_min_length": float(options.get("thin_rule_min_length", 6.0)),
            "thin_rule_max_thickness": float(options.get("thin_rule_max_thickness", 2.5)),
        },
        "items": items,
        "counts": dict(sorted(counts.items())),
        "unprocessed_item_count": len(items),
        "owned_operation_count": sum(1 for ordinal in owners if owners[ordinal]),
    }


def identify_unprocessed_items(document: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Report unowned visual spans while preserving all renderable document data."""
    if not isinstance(document, dict):
        raise TypeError("unprocessed item diagnostics expects a document object")
    options = dict(options or {})
    result = deepcopy(document)
    page_reports: list[dict[str, Any]] = []
    for page in result.get("pages", []):
        if not isinstance(page, dict):
            continue
        report = _page_report(page, options)
        page["unprocessed_items"] = report
        page_reports.append(
            {
                "page_id": page.get("id"),
                "unprocessed_item_count": report["unprocessed_item_count"],
                "counts": report["counts"],
                "owned_operation_count": report["owned_operation_count"],
            }
        )

    diagnostics = dict(result.get("diagnostics") or {})
    diagnostics["unprocessed_items"] = {
        "schema": "pdf-training-unprocessed-items-document-v1",
        "transform": TRANSFORM_ID,
        "pages": page_reports,
        "unprocessed_item_count": sum(item["unprocessed_item_count"] for item in page_reports),
    }
    result["diagnostics"] = diagnostics
    return result
