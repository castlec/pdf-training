#!/usr/bin/env python3
"""Audit native-PDF extraction coverage and produce browser-review artifacts."""

from __future__ import annotations

import argparse
import html
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


def numbers(value: str) -> list[float]:
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", value)]


def path_bbox(path: ET.Element, page_height: int) -> dict[str, int] | None:
    values = numbers(path.attrib.get("d", ""))
    if len(values) < 4:
        return None
    xs, ys = values[0::2], values[1::2]
    if not xs or not ys:
        return None
    transform = path.attrib.get("transform", "")
    y0, y1 = min(ys), max(ys)
    if ",0,0,-" in transform:
        y0, y1 = page_height - y1, page_height - y0
    return {"x": round(min(xs)), "y": round(y0), "w": max(1, round(max(xs) - min(xs))), "h": max(1, round(y1 - y0))}


def audit_page(page_json: Path, vector_path: Path, overlay_path: Path) -> dict[str, Any]:
    page = json.loads(page_json.read_text(encoding="utf-8"))
    svg = ET.parse(vector_path).getroot()
    vector_elements: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    def visit(element: ET.Element, in_defs: bool = False) -> None:
        kind = element.tag.rsplit("}", 1)[-1]
        child_in_defs = in_defs or kind == "defs"
        if kind in {"path", "use", "image", "rect", "line", "polyline", "polygon"}:
            counts[kind] = counts.get(kind, 0) + 1
            box = path_bbox(element, int(page["height"])) if kind == "path" else None
            vector_elements.append({"type": kind, "id": element.attrib.get("id"), "bbox_estimate": box, "in_defs": in_defs})
        for child in element:
            visit(child, child_in_defs)
    visit(svg)

    image = Image.open(page["source_page_image"]).convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    colors = {"text_fragment": (35, 120, 255, 150), "image": (20, 180, 80, 180), "rule": (240, 120, 20, 210)}
    for node in page.get("nodes", []):
        box = node.get("bbox", {})
        color = colors.get(node.get("type"), (150, 70, 200, 150))
        xy = (box.get("x", 0), box.get("y", 0), box.get("x", 0) + box.get("w", 0), box.get("y", 0) + box.get("h", 0))
        draw.rectangle(xy, outline=color, width=2)
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(overlay_path)
    return {
        "page_id": page["id"], "width": page["width"], "height": page["height"],
        "renderable_nodes": page.get("summary", {}), "svg_elements": counts,
        "svg_element_total": len(vector_elements), "vector_elements": vector_elements,
        "source_page_image": page["source_page_image"], "overlay": str(overlay_path.resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--vectors", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pages: list[dict[str, Any]] = []
    links: list[str] = []
    for document in dataset.get("documents", []):
        for page in document.get("pages", []):
            page_id = str(page["id"])
            page_json = args.dataset.parent / "renderable" / f"page-{page_id.rsplit('-p', 1)[-1]}.json"
            vector_path = args.vectors / f"page-{page_id.rsplit('-p', 1)[-1]}.svg"
            overlay = args.out_dir / f"{page_id}-overlay.png"
            report = audit_page(page_json, vector_path, overlay)
            report_path = args.out_dir / f"{page_id}.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            pages.append(report)
            links.append(f'<li><a href="{html.escape(report_path.name)}">{html.escape(page_id)} manifest</a> | <a href="{html.escape(overlay.name)}">overlay</a></li>')
    summary = {"schema": "pdf-training-native-pdf-audit-v1", "pages": pages}
    (args.out_dir / "audit.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    index = "<!doctype html><meta charset='utf-8'><title>Native PDF audit</title><h1>Native PDF audit</h1><p>Blue=text, green=image, orange=rule, purple=other renderable node.</p><ul>" + "".join(links) + "</ul>"
    (args.out_dir / "index.html").write_text(index, encoding="utf-8")
    print(json.dumps({"pages": len(pages), "out_dir": str(args.out_dir.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
