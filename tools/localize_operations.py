"""Convert selected draw-operation spans into explicit group-local coordinates."""

from __future__ import annotations

import copy
from typing import Any

TRANSFORM_ID = "geometry.localize-operation-groups.v1"
_PATH_COORDS = {"m": 2, "l": 2, "c": 6, "re": 4}


def _localize(operation: dict[str, Any], origin: dict[str, float], size: dict[str, float]) -> dict[str, Any]:
    """Convert path coordinates to a top-left, group-local coordinate space."""
    result = copy.deepcopy(operation)
    operator = result.get("operator")
    values = result.get("operands") or []
    if operator not in _PATH_COORDS or len(values) < _PATH_COORDS[operator]:
        return result
    numeric = list(values)
    if operator == "re":
        numeric[0] = float(numeric[0]) - origin["x"]
        numeric[1] = float(size["h"]) - (float(numeric[1]) - origin["y"]) - float(numeric[3])
    else:
        for index in range(0, _PATH_COORDS[operator], 2):
            numeric[index] = float(numeric[index]) - origin["x"]
            numeric[index + 1] = float(size["h"]) - (float(numeric[index + 1]) - origin["y"])
    result["operands"] = numeric
    return result


def apply_local_operation_coordinates(input_data: dict[str, Any]) -> dict[str, Any]:
    """Localize operation coordinates and retain originals for audit."""
    out = copy.deepcopy(input_data)
    for page in out.get("pages") or []:
        operations = page.get("operations") or []
        by_ordinal = {int(item["ordinal"]): index for index, item in enumerate(operations)}
        def localize_group(group: dict[str, Any]) -> None:
            source = group.get("source_bbox") or {}
            ordinals = [int(value) for value in group.get("operation_ordinals") or []]
            if source and ordinals:
                group["coordinate_space"] = {"name": "group-local", "origin": group.get("id"), "origin_convention": "top-left"}
                group["local_origin"] = {"x": 0, "y": 0}
                group["local_size"] = {"w": float(source.get("w", 0)), "h": float(source.get("h", 0))}
                group["source_origin_pdf"] = {"x": source["x"], "y": source["y"]}
                group["source_operations"] = copy.deepcopy([operations[by_ordinal[value]] for value in ordinals if value in by_ordinal])
                for ordinal in ordinals:
                    index = by_ordinal.get(ordinal)
                    if index is not None:
                        operations[index] = _localize(operations[index], source, group["local_size"])
                group["operations_localized"] = True
            for child in (group.get("children") or []) + (group.get("operation_groups") or []):
                if isinstance(child, dict):
                    localize_group(child)

        for group in page.get("operation_groups") or []:
            localize_group(group)
    out.setdefault("provenance", {})["local_operation_coordinates"] = {"transform": TRANSFORM_ID}
    return out
