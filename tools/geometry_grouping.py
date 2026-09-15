#!/usr/bin/env python3
"""Detect structural boxes and rules from painted PDF geometry."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import pikepdf


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def number(value: Any) -> float:
    return float(value)


def merge_intervals(items: list[dict[str, float]], *, coordinate: str, tolerance: float) -> list[dict[str, float]]:
    merged: list[dict[str, float]] = []
    for item in sorted(items, key=lambda value: (value[coordinate], value["start"])):
        if merged and item[coordinate] == merged[-1][coordinate] and item["start"] <= merged[-1]["end"] + tolerance:
            merged[-1]["end"] = max(merged[-1]["end"], item["end"])
        else:
            merged.append(dict(item))
    return merged


def painted_rectangles(pdf_path: Path, page_number: int) -> list[dict[str, float]]:
    pdf = pikepdf.Pdf.open(pdf_path)
    operations = pikepdf.parse_content_stream(pdf.pages[page_number - 1])
    rectangles: list[dict[str, float]] = []
    for index, operation in enumerate(operations):
        if str(operation.operator) != "re" or len(operation.operands) < 4:
            continue
        if index + 1 >= len(operations) or str(operations[index + 1].operator) not in {"f", "f*", "B", "B*", "b", "b*", "S"}:
            continue
        x, y, width, height = (number(value) for value in operation.operands[:4])
        if width <= 0 or height <= 0:
            continue
        # Page-sized rectangles and clipping/background scaffolding are not layout geometry.
        if width > 0.95 * float(pdf.pages[page_number - 1].MediaBox[2]) and height > 0.95 * float(pdf.pages[page_number - 1].MediaBox[3]):
            continue
        rectangles.append({"index": float(index), "x": x, "y": y, "w": width, "h": height})
    return rectangles


def vector_path_bbox(pdf_path: Path, page_number: int) -> dict[str, float] | None:
    """Return the bounds of page path geometry, excluding text and rectangle frames."""
    with pikepdf.Pdf.open(pdf_path) as pdf:
        operations = pikepdf.parse_content_stream(pdf.pages[page_number - 1])
    points: list[tuple[float, float]] = []
    for operation in operations:
        operator = str(operation.operator)
        if operator in {"m", "l"} and len(operation.operands) >= 2:
            points.append((number(operation.operands[0]), number(operation.operands[1])))
        elif operator == "c" and len(operation.operands) >= 6:
            values = [number(value) for value in operation.operands[:6]]
            points.extend(zip(values[0::2], values[1::2]))
    if not points:
        return None
    xs, ys = zip(*points)
    return {"x": min(xs), "y": min(ys), "w": max(xs) - min(xs), "h": max(ys) - min(ys)}


def detect_geometry(pdf_path: Path, page_number: int, *, thickness: float = 2.0, tolerance: float = 1.5) -> dict[str, Any]:
    rectangles = painted_rectangles(pdf_path, page_number)
    horizontal = [r for r in rectangles if r["h"] <= thickness and r["w"] >= 100]
    vertical = [r for r in rectangles if r["w"] <= thickness and r["h"] >= 5]
    horizontal = sorted(horizontal, key=lambda r: (r["y"], r["x"]))

    vertical_groups: dict[tuple[int, int], list[dict[str, float]]] = {}
    for rect in vertical:
        key = (round(rect["x"] / tolerance), round(rect["w"] / tolerance))
        vertical_groups.setdefault(key, []).append({"coordinate": rect["x"], "start": rect["y"], "end": rect["y"] + rect["h"], "x": rect["x"], "w": rect["w"], "index": rect["index"]})
    vertical_runs = []
    for fragments in vertical_groups.values():
        for run in merge_intervals(fragments, coordinate="x", tolerance=tolerance):
            if run["end"] - run["start"] >= 20:
                vertical_runs.append(run)

    boxes = []
    used_horizontal: set[int] = set()
    for top in horizontal:
        lower = [candidate for candidate in horizontal if candidate["y"] < top["y"] - tolerance and candidate["w"] >= top["w"] - 4]
        for bottom in sorted(lower, key=lambda candidate: top["y"] - candidate["y"]):
            if any(candidate["y"] < top["y"] - tolerance and candidate["y"] > bottom["y"] + tolerance for candidate in horizontal):
                continue
            left_candidates = [v for v in vertical_runs if abs(v["x"] - top["x"]) <= 2 and v["start"] <= top["y"] + tolerance and v["end"] >= bottom["y"] - tolerance]
            right_candidates = [v for v in vertical_runs if abs((v["x"] + v["w"]) - (top["x"] + top["w"])) <= 2 and v["start"] <= top["y"] + tolerance and v["end"] >= bottom["y"] - tolerance]
            if not left_candidates or not right_candidates:
                continue
            if any(box["bbox"]["y"] == top["y"] and box["bbox"]["x"] == top["x"] for box in boxes):
                break
            boxes.append({"type": "box", "id": f"box-{len(boxes) + 1:02d}", "bbox": {"x": top["x"], "y": bottom["y"], "w": top["w"], "h": top["y"] - bottom["y"] + top["h"]}, "border_operations": {"top": [int(top["index"])], "bottom": [int(bottom["index"])], "left": [int(v.get("index", -1)) for v in left_candidates], "right": [int(v.get("index", -1)) for v in right_candidates]}})
            used_horizontal.update({int(top["index"]), int(bottom["index"])})
            break

    rules = [{"type": "rule", "id": f"rule-{index:02d}", "bbox": {"x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"]}, "operations": [int(r["index"])]} for index, r in enumerate(horizontal, start=1) if int(r["index"]) not in used_horizontal]
    path_bbox = vector_path_bbox(pdf_path, page_number)
    drawings = []
    if path_bbox and path_bbox["w"] > 20 and path_bbox["h"] > 20:
        drawings.append({"id": "source-drawing-01", "type": "opaque_drawing", "bbox": path_bbox, "render_ref": "source-vector-asset"})
    return {"schema": "pdf-training-geometry-v1", "page": page_number, "painted_rectangles": rectangles, "boxes": boxes, "rules": rules, "drawings": drawings}


def pdf_bbox_to_page_bbox(box: dict[str, float], page: dict[str, Any], media_width: float, media_height: float) -> dict[str, int]:
    scale_x = float(page["width"]) / media_width
    scale_y = float(page["height"]) / media_height
    return {"x": round(box["x"] * scale_x), "y": round((media_height - box["y"] - box["h"]) * scale_y), "w": round(box["w"] * scale_x), "h": round(box["h"] * scale_y)}


def group_page(page: dict[str, Any], geometry: dict[str, Any], media_width: float, media_height: float) -> dict[str, Any]:
    """Attach detected geometry as rendering decoration on generic containers."""
    tree = copy.deepcopy(page)
    source_nodes = tree.pop("nodes", [])
    tree["schema"] = "pdf-training-geometry-grouped-page-v2"
    children: list[dict[str, Any]] = []
    assigned: set[str] = set()
    for index, detected in enumerate(sorted(geometry["boxes"], key=lambda item: item["bbox"]["y"], reverse=True), start=1):
        page_box = pdf_bbox_to_page_bbox(detected["bbox"], page, media_width, media_height)
        members = []
        expanded = {"x": page_box["x"] - 3, "y": page_box["y"] - 3, "w": page_box["w"] + 6, "h": page_box["h"] + 6}
        for node in source_nodes:
            node_box = node.get("bbox") or {}
            center_x = int(node_box.get("x", 0)) + int(node_box.get("w", 0)) / 2
            center_y = int(node_box.get("y", 0)) + int(node_box.get("h", 0)) / 2
            if expanded["x"] <= center_x <= expanded["x"] + expanded["w"] and expanded["y"] <= center_y <= expanded["y"] + expanded["h"]:
                members.append(copy.deepcopy(node))
                assigned.add(str(node.get("id")))
        for member in members:
            source_box = member["bbox"]
            member["layout_position"] = {"space": "container-local", "bbox": {"x": int(source_box["x"]) - page_box["x"], "y": int(source_box["y"]) - page_box["y"], "w": int(source_box["w"]), "h": int(source_box["h"])}}
        obstacles = []
        for drawing in geometry.get("drawings", []):
            drawing_box = pdf_bbox_to_page_bbox(drawing["bbox"], page, media_width, media_height)
            if drawing_box["x"] < page_box["x"] + page_box["w"] and drawing_box["x"] + drawing_box["w"] > page_box["x"] and drawing_box["y"] < page_box["y"] + page_box["h"] and drawing_box["y"] + drawing_box["h"] > page_box["y"]:
                obstacles.append({"id": drawing["id"], "render_ref": drawing["render_ref"], "bbox": {"x": drawing_box["x"] - page_box["x"], "y": drawing_box["y"] - page_box["y"], "w": drawing_box["w"], "h": drawing_box["h"]}})
        for obstacle in obstacles:
            fixed_boxes = []
            obstacle_page = {"x": page_box["x"] + obstacle["bbox"]["x"], "y": page_box["y"] + obstacle["bbox"]["y"], "w": obstacle["bbox"]["w"], "h": obstacle["bbox"]["h"]}
            for member in members:
                member_box = member.get("bbox") or {}
                center_x = int(member_box.get("x", 0)) + int(member_box.get("w", 0)) / 2
                center_y = int(member_box.get("y", 0)) + int(member_box.get("h", 0)) / 2
                diagram_margin = 60
                if member.get("type") == "image" or (member.get("type") == "text_fragment" and obstacle_page["x"] - diagram_margin <= center_x <= obstacle_page["x"] + obstacle_page["w"] + diagram_margin and obstacle_page["y"] - diagram_margin <= center_y <= obstacle_page["y"] + obstacle_page["h"] + diagram_margin):
                    fixed_boxes.append(member_box)
            if fixed_boxes:
                x1 = min(obstacle["bbox"]["x"], min(int(box.get("x", 0)) - page_box["x"] for box in fixed_boxes))
                y1 = min(obstacle["bbox"]["y"], min(int(box.get("y", 0)) - page_box["y"] for box in fixed_boxes))
                x2 = max(obstacle["bbox"]["x"] + obstacle["bbox"]["w"], max(int(box.get("x", 0)) + int(box.get("w", 0)) - page_box["x"] for box in fixed_boxes))
                y2 = max(obstacle["bbox"]["y"] + obstacle["bbox"]["h"], max(int(box.get("y", 0)) + int(box.get("h", 0)) - page_box["y"] for box in fixed_boxes))
                obstacle["bbox"] = {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}

        children.append({
            "type": "region",
            "id": f"region-{index:02d}",
            "bbox": page_box,
            "layout": {"sizing": "intrinsic-height", "padding": {"top": 0, "bottom": 0}, "obstacles": obstacles},
            "decoration": {"frame": f"geometry-frame-{index:02d}", "frame_style": {"stroke": "source", "width": 0.72, "sides": ["top", "right", "bottom", "left"]}},
            "children": sorted(members, key=lambda n: (int(n["bbox"]["y"]), int(n["bbox"]["x"]), str(n.get("id")))),
        })
    for node in source_nodes:
        if str(node.get("id")) not in assigned:
            children.append(copy.deepcopy(node))
    tree["children"] = sorted(children, key=lambda n: (int((n.get("bbox") or {}).get("y", 0)), int((n.get("bbox") or {}).get("x", 0)), str(n.get("id"))))
    return layout_page(tree)


def _shift_node(node: dict[str, Any], delta_y: int) -> None:
    box = node.get("bbox")
    if isinstance(box, dict) and "y" in box:
        box["y"] = int(box["y"]) + delta_y
    for child in node.get("children") or []:
        _shift_node(child, delta_y)


def layout_page(tree: dict[str, Any]) -> dict[str, Any]:
    """Grow intrinsic-height regions downward and shift later siblings."""
    children = tree.get("children") or []
    accumulated_shift = 0
    for index, node in enumerate(children):
        if accumulated_shift:
            _shift_node(node, accumulated_shift)
        if node.get("type") != "region" or (node.get("layout") or {}).get("sizing") != "intrinsic-height":
            continue
        box = node.get("bbox") or {}
        child_boxes = [(child.get("bbox") or {}) for child in node.get("children") or []]
        if not child_boxes:
            continue
        bottom = max(int(child.get("y", 0)) + int(child.get("h", 0)) for child in child_boxes)
        padding = int(((node.get("layout") or {}).get("padding") or {}).get("bottom", 0))
        required_height = max(int(box.get("h", 0)), bottom - int(box.get("y", 0)) + padding)
        growth = required_height - int(box.get("h", 0))
        if growth > 0:
            box["h"] = required_height
            accumulated_shift += growth
    return tree


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--page", type=int, default=4)
    args = parser.parse_args()
    geometry = detect_geometry(args.pdf, args.page)
    ordered_boxes = sorted(geometry["boxes"], key=lambda item: item["bbox"]["y"], reverse=True)
    geometry["frames"] = [
        {"id": f"geometry-frame-{index:02d}", "bbox": detected["bbox"], "border_operations": detected["border_operations"]}
        for index, detected in enumerate(ordered_boxes, start=1)
    ]
    page = load_json(args.input)
    with pikepdf.Pdf.open(args.pdf) as pdf:
        media = pdf.pages[args.page - 1].MediaBox
        grouped = group_page(page, geometry, float(media[2]), float(media[3]))
    write_json(args.report, geometry)
    write_json(args.out, grouped)
    print(json.dumps({"boxes": len(geometry["boxes"]), "rules": len(geometry["rules"]), "source_nodes": len(page.get("nodes", [])), "grouped_children": len(grouped["children"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
