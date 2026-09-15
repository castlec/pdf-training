#!/usr/bin/env python3
"""Grammar-backed, geometry-only transformations for renderable PDF pages."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any


TEXT_TERMINALS = {
    "text_fragment",
    "text_band",
    "caption",
    "heading",
    "header",
    "footer",
    "page_number",
}
GRAPHIC_TERMINALS = {
    "image",
    "rule",
    "path",
    "vector",
    "equation",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def terminal_kind(node: dict[str, Any]) -> str:
    node_type = str(node.get("type") or "")
    if node_type in TEXT_TERMINALS:
        return "text"
    if node_type in GRAPHIC_TERMINALS:
        return "graphic"
    return "opaque"


def validate_source_page(page: dict[str, Any]) -> list[str]:
    errors = []
    if page.get("type") not in (None, "page"):
        errors.append("source page has an unexpected type")
    nodes = page.get("nodes")
    if not isinstance(nodes, list):
        return ["source page must contain a flat nodes list"]
    seen = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"source node {index} is not an object")
            continue
        node_id = node.get("id")
        if not node_id:
            errors.append(f"source node {index} has no id")
        elif node_id in seen:
            errors.append(f"duplicate source node id: {node_id}")
        seen.add(node_id)
        if not isinstance(node.get("bbox"), dict):
            errors.append(f"source node {node_id or index} has no bbox")
    return errors


def validate_structured_page(tree: dict[str, Any]) -> list[str]:
    errors = []
    if tree.get("type") != "page":
        errors.append("structured root must have type page")
    regions = tree.get("children")
    if not isinstance(regions, list) or not regions:
        errors.append("structured page must contain one or more regions")
        return errors
    seen = set()
    for region_index, region in enumerate(regions):
        if region.get("type") != "region":
            errors.append(f"child {region_index} is not a region")
            continue
        items = region.get("children")
        if not isinstance(items, list) or not items:
            errors.append(f"region {region.get('id') or region_index} is empty")
            continue
        for item in items:
            node_id = item.get("id")
            if node_id in seen:
                errors.append(f"duplicate structured node id: {node_id}")
            seen.add(node_id)
            if not isinstance(item.get("bbox"), dict):
                errors.append(f"structured node {node_id} has no bbox")
    return errors


def _bands(page: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int, int]:
    entries = []
    for node in page.get("nodes") or []:
        box = node.get("bbox") or {}
        try:
            x, y = int(box["x"]), int(box["y"])
            w, h = int(box["w"]), int(box["h"])
        except (KeyError, TypeError, ValueError):
            continue
        if w > 0 and h > 0:
            entries.append((x, y, w, h, node))
    if not entries:
        return [], 1, 1, 1
    bands = []
    for entry in sorted(entries, key=lambda value: (value[1], value[0], str(value[4].get("id") or ""))):
        x, y, w, h, node = entry
        if bands and y <= bands[-1]["y2"]:
            band = bands[-1]
            band["y2"] = max(band["y2"], y + h)
            band["x1"] = min(band["x1"], x)
            band["x2"] = max(band["x2"], x + w)
            band["items"].append(entry)
        else:
            bands.append({"y1": y, "y2": y + h, "x1": x, "x2": x + w, "items": [entry]})
    heights = sorted(entry[3] for entry in entries)
    median_height = heights[len(heights) // 2]
    occupied_width = max(1, max(entry[0] + entry[2] for entry in entries) - min(entry[0] for entry in entries))
    return bands, median_height, occupied_width, len(entries)


def split_by_full_width_vertical_whitespace(
    page: dict[str, Any],
    *,
    min_gap: int = 48,
    gap_height_factor: float = 3.0,
    min_width_fraction: float = 0.8,
) -> tuple[list[list[dict[str, Any]]], list[dict[str, Any]]]:
    """Return ordered item groups and optional rule diagnostics."""
    bands, median_height, occupied_width, _ = _bands(page)
    if not bands:
        return [], []
    split_after = set()
    candidates = []
    for index in range(len(bands) - 1):
        upper, lower = bands[index], bands[index + 1]
        gap = lower["y1"] - upper["y2"]
        upper_items = [item for band in bands[: index + 1] for item in band["items"]]
        lower_items = [item for band in bands[index + 1 :] for item in band["items"]]
        upper_width = max(item[0] + item[2] for item in upper_items) - min(item[0] for item in upper_items)
        lower_width = max(item[0] + item[2] for item in lower_items) - min(item[0] for item in lower_items)
        width_fraction = min(upper_width, lower_width) / max(1, occupied_width)
        accepted = gap >= max(min_gap, median_height * gap_height_factor) and width_fraction >= min_width_fraction
        candidates.append({"after_band": index, "gap": gap, "width_fraction": round(width_fraction, 4), "accepted": accepted})
        if accepted:
            split_after.add(index)
    groups = []
    start = 0
    ends = sorted(split_after)
    if not ends or ends[-1] != len(bands) - 1:
        ends.append(len(bands) - 1)
    for end in ends:
        items = [entry[4] for band in bands[start : end + 1] for entry in band["items"]]
        groups.append(sorted(items, key=lambda node: (int(node["bbox"]["y"]), int(node["bbox"]["x"]), str(node.get("id") or ""))))
        start = end + 1
    return groups, candidates


def transform_page(
    page: dict[str, Any],
    *,
    min_gap: int = 48,
    gap_height_factor: float = 3.0,
    min_width_fraction: float = 0.8,
    trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source_errors = validate_source_page(page)
    if source_errors:
        raise ValueError("invalid source page: " + "; ".join(source_errors))
    groups, candidates = split_by_full_width_vertical_whitespace(
        page,
        min_gap=min_gap,
        gap_height_factor=gap_height_factor,
        min_width_fraction=min_width_fraction,
    )
    page_id = str(page.get("id") or "page")
    tree = copy.deepcopy(page)
    tree.pop("nodes", None)
    tree["schema"] = "pdf-training-structured-page-v1"
    tree["type"] = "page"
    tree["children"] = [
        {
            "type": "region",
            "id": f"{page_id}-region-{index:02d}",
            "children": [copy.deepcopy(node) for node in items],
        }
        for index, items in enumerate(groups, start=1)
    ]
    errors = validate_structured_page(tree)
    if errors:
        raise ValueError("invalid transformed page: " + "; ".join(errors))
    if trace is not None:
        trace.append({"transform": "split_by_full_width_vertical_whitespace", "candidates": candidates, "groups": len(groups)})
    return tree


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--min-gap", type=int, default=48)
    parser.add_argument("--gap-height-factor", type=float, default=3.0)
    parser.add_argument("--min-width-fraction", type=float, default=0.8)
    args = parser.parse_args()
    trace = [] if args.trace else None
    tree = transform_page(
        load_json(args.input),
        min_gap=args.min_gap,
        gap_height_factor=args.gap_height_factor,
        min_width_fraction=args.min_width_fraction,
        trace=trace,
    )
    write_json(args.out, tree)
    if args.trace:
        write_json(args.trace, {"schema": "pdf-training-transform-trace-v1", "events": trace})
    print(json.dumps({"regions": len(tree["children"]), "items": sum(len(region["children"]) for region in tree["children"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
