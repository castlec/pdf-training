#!/usr/bin/env python3
"""Render a grouped page while resolving generic container decorations."""
from __future__ import annotations

import argparse
import json
import unicodedata
from pathlib import Path
from typing import Any

import pikepdf
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from geometry_grouping import layout_page

TRANSLATED_FONT_SCALE = 0.84


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def wrap_text_around_obstacles(page: dict[str, Any]) -> dict[str, Any]:
    """Wrap translated prose before local opaque drawing obstacles."""
    result = json.loads(json.dumps(page))
    px_to_pt = 595.32 / float(page["width"])
    for region in result.get("children", []):
        if region.get("type") != "region":
            continue
        region_box = region.get("bbox") or {}
        obstacles = (region.get("layout") or {}).get("obstacles") or []
        for node in region.get("children") or []:
            if node.get("type") != "text_fragment" or node.get("translation_status") != "translated":
                continue
            box = node.get("bbox") or {}
            for obstacle in obstacles:
                obstacle_box = obstacle.get("bbox") or {}
                obstacle_page = {
                    "x": int(region_box.get("x", 0)) + int(obstacle_box.get("x", 0)),
                    "y": int(region_box.get("y", 0)) + int(obstacle_box.get("y", 0)),
                    "w": int(obstacle_box.get("w", 0)),
                    "h": int(obstacle_box.get("h", 0)),
                }
                if int(box.get("x", 0)) >= obstacle_page["x"]:
                    continue
                if int(box.get("y", 0)) + int(box.get("h", 0)) <= obstacle_page["y"] or int(box.get("y", 0)) >= obstacle_page["y"] + obstacle_page["h"]:
                    continue
                available_px = obstacle_page["x"] - int(box.get("x", 0)) - 8
                if available_px < 120:
                    continue
                text = str(node.get("text", "")).strip()
                words = text.split()
                font_name = "Helvetica-Bold" if int(node.get("font_weight", 400)) >= 700 else "Helvetica"
                font_size_pt = float(node.get("font_size_px", box.get("h", 12))) * px_to_pt
                max_width_pt = available_px * px_to_pt
                lines: list[str] = []
                current = ""
                for word in words:
                    candidate = f"{current} {word}".strip()
                    if current and stringWidth(candidate, font_name, font_size_pt) > max_width_pt:
                        lines.append(current)
                        current = word
                    else:
                        current = candidate
                if current:
                    lines.append(current)
                if len(lines) > 1:
                    node["text"] = "\n".join(lines)
                    node["bbox"]["w"] = min(int(box.get("w", available_px)), available_px)
                    original_height = int(box.get("h", 0))
                    node["bbox"]["h"] = max(original_height, round(len(lines) * float(node.get("font_size_px", box.get("h", 12))) * 1.15))
                    growth = node["bbox"]["h"] - original_height
                    if growth:
                        node_bottom = int(box.get("y", 0)) + original_height
                        for later in region.get("children") or []:
                            later_box = later.get("bbox") or {}
                            if later is not node and later.get("type") == "text_fragment" and int(later_box.get("x", 0)) < obstacle_page["x"] and int(later_box.get("y", 0)) >= node_bottom:
                                later_box["y"] = int(later_box.get("y", 0)) + growth
                break
    return result


def merge_translations(page: dict[str, Any], translations: dict[str, Any]) -> dict[str, Any]:
    translated = {node.get("id"): node for node in translations.get("nodes", []) if node.get("id")}

    def visit(node: dict[str, Any]) -> None:
        replacement = translated.get(node.get("id"))
        if replacement:
            for key in ("text", "bbox", "font_size_px", "font_weight", "translation_status"):
                if key in replacement:
                    node[key] = replacement[key]
            if replacement.get("translation_status", "translated") == "translated":
                base_size = replacement.get("font_size_px", node.get("font_size_px"))
                if base_size is not None:
                    node["font_size_px"] = float(base_size) * TRANSLATED_FONT_SCALE
                node["translation_status"] = "translated"
        for child in node.get("children") or []:
            visit(child)

    result = json.loads(json.dumps(page))
    for node in result.get("children") or []:
        visit(node)
    return layout_page(wrap_text_around_obstacles(result))


def iter_text_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("type") == "text_fragment":
            result.append(node)
        result.extend(iter_text_nodes(node.get("children") or []))
    return result


def render_overlay(page: dict[str, Any], frames: dict[str, Any], path: Path, coordinate_mode: str = "auto") -> None:
    if coordinate_mode not in {"auto", "absolute", "relative"}:
        raise ValueError(f"unsupported coordinate mode: {coordinate_mode}")

    relative_boxes: dict[str, dict[str, Any]] = {}
    relative_group_boxes: dict[str, dict[str, Any]] = {}
    declared_boxes: dict[str, dict[str, Any]] = {}

    def collect_declared(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("id") and value.get("layout_box"):
                declared_boxes[str(value["id"])] = value
            for child in value.values():
                collect_declared(child)
        elif isinstance(value, list):
            for child in value:
                collect_declared(child)

    collect_declared(page)

    def resolve_declared(node_id: str, seen: set[str] | None = None) -> dict[str, Any] | None:
        seen = set() if seen is None else seen
        if node_id in seen or node_id not in declared_boxes:
            return None
        seen.add(node_id)
        box = declared_boxes[node_id].get("layout_box") or {}
        if not all(key in box for key in ("x", "y", "w", "h")):
            return None
        origin_id = str(box.get("origin") or "")
        origin_box = resolve_declared(origin_id, seen) if origin_id else None
        origin_x = float(origin_box["x"]) if origin_box else 0.0
        origin_y = float(origin_box["y"]) if origin_box else 0.0
        return {"x": origin_x + float(box["x"]), "y": origin_y + float(box["y"]), "w": float(box["w"]), "h": float(box["h"])}

    def collect_relative(group: dict[str, Any]) -> None:
        group_id = str(group.get("id") or "")
        group_layout = group.get("layout_position") or {}
        group_local = group_layout.get("bbox") or {}
        group_origin = (group.get("coordinate_space") or {}).get("origin_page") or {"x": 0, "y": 0}
        if group_id and all(key in group_local for key in ("x", "y", "w", "h")):
            relative_group_boxes[group_id] = {
                "x": float(group_origin.get("x", 0)) + float(group_local["x"]),
                "y": float(group_origin.get("y", 0)) + float(group_local["y"]),
                "w": float(group_local["w"]),
                "h": float(group_local["h"]),
            }
        for node_id, layout in (group.get("node_layout") or {}).items():
            if layout.get("relative_bbox"):
                origin = (group.get("coordinate_space") or {}).get("origin_page") or {"x": 0, "y": 0}
                local = layout["relative_bbox"]
                relative_boxes[str(node_id)] = {
                    "x": float(origin.get("x", 0)) + float(local["x"]),
                    "y": float(origin.get("y", 0)) + float(local["y"]),
                    "w": float(local["w"]),
                    "h": float(local["h"]),
                }
        for child in group.get("children") or []:
            collect_relative(child)

    if coordinate_mode in {"auto", "relative"}:
        for group in page.get("group_tree") or []:
            collect_relative(group)

    def normalize_math_letters(value: str) -> str:
        normalized: list[str] = []
        for char in value:
            name = unicodedata.name(char, "")
            prefix = "MATHEMATICAL ITALIC "
            if name.startswith(prefix):
                suffix = name[len(prefix):]
                if suffix.startswith("SMALL ") and suffix[-1:].isalpha():
                    char = suffix[-1].lower()
                elif suffix.startswith("CAPITAL ") and suffix[-1:].isalpha():
                    char = suffix[-1]
            normalized.append(char)
        return "".join(normalized).replace("$", "")

    # Frame sidecar coordinates are native PDF points, not extracted image pixels.
    width, height = 595.32, 841.92
    canvas = Canvas(str(path), pagesize=(width, height))
    scale_x = scale_y = 1.0
    # Erase only the source container frames; all other source artwork stays black.
    canvas.setStrokeColorRGB(1, 1, 1)
    canvas.setLineWidth(4.0)
    for frame in frames.values():
        box = frame["bbox"]
        canvas.rect(box["x"], box["y"], box["w"], box["h"], stroke=1, fill=0)

    def draw_group(group: dict[str, Any], depth: int = 0) -> None:
        group_box = group.get("bbox") or {}
        if coordinate_mode in {"auto", "relative"}:
            group_box = resolve_declared(str(group.get("id"))) or relative_group_boxes.get(str(group.get("id")), group_box)
        if not all(key in group_box for key in ("x", "y", "w", "h")):
            return
        page_scale_x = width / float(page["width"])
        page_scale_y = height / float(page["height"])
        x = float(group_box["x"]) * page_scale_x
        y = height - (float(group_box["y"]) + float(group_box["h"])) * page_scale_y
        w = float(group_box["w"]) * page_scale_x
        h = float(group_box["h"]) * page_scale_y
        # Diagnostic groups are translucent so source artwork remains inspectable.
        shade = min(0.9, 0.08 * (depth + 1))
        canvas.setFillColorRGB(shade, shade, shade)
        try:
            canvas.setFillAlpha(shade)
        except AttributeError:
            pass
        canvas.rect(x, y, w, h, stroke=0, fill=1)
        try:
            canvas.setFillAlpha(1)
        except AttributeError:
            pass
        canvas.setStrokeColorRGB(0.2, 0.2, 0.2)
        canvas.setLineWidth(0.8)
        canvas.setDash(3, 2)
        canvas.rect(x, y, w, h, stroke=1, fill=0)
        canvas.setDash()
        for child in group.get("children") or []:
            draw_group(child, depth + 1)

    for group in page.get("group_tree") or []:
        draw_group(group)

    region_number = 0
    for region in page.get("children", []):
        decoration = region.get("decoration") or {}
        frame_id = decoration.get("frame")
        frame = frames.get(frame_id)
        if not frame:
            continue
        region_number += 1
        frame_box = frame["bbox"]
        region_box = region.get("bbox") or {}
        if all(key in region_box for key in ("x", "y", "w", "h")):
            page_scale_x = width / float(page["width"])
            page_scale_y = height / float(page["height"])
            box = {
                "x": float(region_box["x"]) * page_scale_x,
                "y": height - (float(region_box["y"]) + float(region_box["h"])) * page_scale_y,
                "w": float(region_box["w"]) * page_scale_x,
                "h": float(region_box["h"]) * page_scale_y,
            }
        else:
            box = {"x": frame_box["x"], "y": frame_box["y"], "w": frame_box["w"], "h": frame_box["h"]}
        x = box["x"] * scale_x
        y = box["y"] * scale_y
        canvas.setLineWidth(0.72)
        canvas.setStrokeColorRGB(0, 0, 0)
        canvas.rect(x, y, box["w"] * scale_x, box["h"] * scale_y, stroke=1, fill=0)

    canvas.setFillColorRGB(0, 0, 0)
    for node in iter_text_nodes(page.get("children") or []):
            if node.get("render_source"):
                continue
            node_box = node.get("bbox") or {}
            if coordinate_mode in {"auto", "relative"}:
                node_box = resolve_declared(str(node.get("id"))) or relative_boxes.get(str(node.get("id")), node_box)
            node_scale_x = width / float(page["width"])
            node_scale_y = height / float(page["height"])
            x = float(node_box.get("x", 0)) * node_scale_x
            top = float(node_box.get("y", 0)) * node_scale_y
            font_size = max(6.0, float(node.get("font_size_px", node_box.get("h", 12))) * node_scale_y)
            original_text = str(node.get("text", ""))
            text_value = normalize_math_letters(original_text)
            if original_text.strip() == "𝑥":
                font_name = "Helvetica-Oblique"
            else:
                font_name = "Helvetica-Bold" if int(node.get("font_weight", 400)) >= 700 else "Helvetica"
            available_width = max(24.0, width - x - 14.0)
            for line_number, line in enumerate(text_value.splitlines() or [""]):
                line_size = font_size
                if line:
                    line_width = pdfmetrics.stringWidth(line, font_name, line_size)
                    if line_width > available_width:
                        line_size = max(6.0, line_size * available_width / line_width)
                canvas.setFont(font_name, line_size)
                line_height = line_size * 1.15
                y = height - top - line_size - line_number * line_height
                canvas.drawString(x, y, line.rstrip())
    canvas.save()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--grouped", required=True, type=Path)
    parser.add_argument("--sidecar", required=True, type=Path)
    parser.add_argument("--background", required=True, type=Path)
    parser.add_argument("--translations", type=Path)
    parser.add_argument("--coordinate-mode", choices=("auto", "absolute", "relative"), default="auto")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    page = load(args.grouped)
    if args.translations:
        page = merge_translations(page, load(args.translations))
    sidecar = load(args.sidecar)
    frames = {frame["id"]: frame for frame in sidecar.get("frames", [])}
    overlay = args.out.with_suffix(".overlay.pdf")
    render_overlay(page, frames, overlay, coordinate_mode=args.coordinate_mode)
    with pikepdf.Pdf.open(args.background) as background, pikepdf.Pdf.open(overlay) as overlay_pdf:
        background.pages[0].add_overlay(overlay_pdf.pages[0])
        args.out.parent.mkdir(parents=True, exist_ok=True)
        background.save(args.out)
    print(json.dumps({"regions": sum(1 for node in page.get("children", []) if node.get("type") == "region"), "frames": len(frames), "output": str(args.out)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
