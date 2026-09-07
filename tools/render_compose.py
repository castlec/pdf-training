#!/usr/bin/env python3
"""Compose renderable page metadata from layout primitives and annotations."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layout_detect import Box, intersection  # noqa: E402


STRUCTURAL_LATEX = re.compile(
    r"\\(?:frac|sqrt|sum|prod|int|begin|overline|underline|overset|underset|vec|hat|bar|left|right)|[_^].*[_^]"
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def box_from_dict(data: dict[str, Any]) -> Box:
    return Box(int(data["x"]), int(data["y"]), int(data["w"]), int(data["h"]))


def vertical_overlap(a: Box, b: Box) -> int:
    return max(0, min(a.y2, b.y2) - max(a.y, b.y))


def classify_render_policy(latex: str, *, inline: bool) -> str:
    text = latex.strip()
    if not text:
        return "inline_math" if inline else "display_math"
    if STRUCTURAL_LATEX.search(text):
        return "inline_math" if inline else "display_math"
    if len(text) <= 12 and "\\\\" not in text:
        return "text"
    return "inline_math" if inline else "display_math"


def interval_union(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def split_band_around_obstacles(band: Box, obstacles: list[dict[str, Any]]) -> list[Box]:
    blocked: list[tuple[int, int]] = []
    for item in obstacles:
        box = box_from_dict(item["bbox"])
        hit = intersection(band, box)
        if not hit:
            continue
        blocked.append((max(band.x, hit.x), min(band.x2, hit.x2)))
    blocked = interval_union(blocked)

    fragments: list[Box] = []
    cursor = band.x
    for start, end in blocked:
        if start > cursor:
            fragments.append(Box(cursor, band.y, start - cursor, band.h))
        cursor = max(cursor, end)
    if cursor < band.x2:
        fragments.append(Box(cursor, band.y, band.x2 - cursor, band.h))
    return [fragment for fragment in fragments if fragment.w > 0 and fragment.h > 0]


def inline_obstacles_for_band(
    band: Box,
    equations: list[dict[str, Any]],
    images: list[dict[str, Any]],
    *,
    min_vertical_overlap_ratio: float,
) -> list[dict[str, Any]]:
    obstacles: list[dict[str, Any]] = []
    for kind, items in (("equation", equations), ("image", images)):
        for item in items:
            box = box_from_dict(item["bbox"])
            hit = intersection(band, box)
            if not hit:
                continue
            ratio = vertical_overlap(band, box) / max(1, min(band.h, box.h))
            if ratio >= min_vertical_overlap_ratio:
                obstacles.append({"kind": kind, **item, "vertical_overlap_ratio": round(ratio, 4)})
    return sorted(obstacles, key=lambda item: (item["bbox"]["x"], item["bbox"]["y"]))


def compose_page(
    layout: dict[str, Any],
    annotations: dict[str, Any] | None = None,
    *,
    document_id: str = "document",
    page_id: str | None = None,
    min_vertical_overlap_ratio: float = 0.45,
) -> dict[str, Any]:
    annotations = annotations or {}
    page_size = layout.get("image_size") or annotations.get("image_size") or {}
    width = int(page_size.get("w", 0))
    height = int(page_size.get("h", 0))
    page_id = page_id or str(annotations.get("page_id") or layout.get("page_id") or f"{document_id}-p001")
    equations = [dict(item, bbox=item["bbox"]) for item in annotations.get("equations", [])]
    images = [dict(item, bbox=item["bbox"]) for item in annotations.get("images", [])]

    nodes: list[dict[str, Any]] = []
    for region in layout.get("regions") or []:
        if region.get("type") != "text_band":
            continue
        band = box_from_dict(region["bbox"])
        band_id = str(region.get("id") or f"band-{len(nodes) + 1:04d}")
        obstacles = inline_obstacles_for_band(
            band,
            equations,
            images,
            min_vertical_overlap_ratio=min_vertical_overlap_ratio,
        )
        fragment_boxes = split_band_around_obstacles(band, obstacles)
        fragment_ids = [f"{band_id}-frag-{idx:02d}" for idx, _ in enumerate(fragment_boxes, start=1)]
        nodes.append(
            {
                "id": band_id,
                "type": "text_band",
                "class": region.get("class", "body"),
                "bbox": band.to_dict(),
                "producer": region.get("producer") or layout.get("schema") or "layout_detect",
                "line_metrics": region.get("internal_line_summary") or {},
                "fragments": fragment_ids,
                "relations": [
                    {"type": "intersects", "target": item["id"], "kind": item["kind"]}
                    for item in obstacles
                ],
                "source": {"bbox": band.to_dict()},
            }
        )
        for fragment_id, fragment_box in zip(fragment_ids, fragment_boxes):
            nodes.append(
                {
                    "id": fragment_id,
                    "type": "text_fragment",
                    "class": region.get("class", "body"),
                    "bbox": fragment_box.to_dict(),
                    "text": "",
                    "content_source": None,
                    "parent_band": band_id,
                    "relations": [{"type": "part_of", "target": band_id}],
                    "source": {"bbox": fragment_box.to_dict()},
                }
            )

    for idx, equation in enumerate(equations, start=1):
        box = box_from_dict(equation["bbox"])
        related_bands = []
        for node in nodes:
            if node["type"] != "text_band":
                continue
            band = box_from_dict(node["bbox"])
            if intersection(band, box) and vertical_overlap(band, box) / max(1, min(band.h, box.h)) >= min_vertical_overlap_ratio:
                related_bands.append(node["id"])
        inline = bool(related_bands)
        nodes.append(
            {
                "id": str(equation.get("id") or f"{page_id}-eq-{idx:04d}"),
                "type": "equation",
                "bbox": box.to_dict(),
                "latex": str(equation.get("latex") or ""),
                "render_policy": str(equation.get("render_policy") or classify_render_policy(str(equation.get("latex") or ""), inline=inline)),
                "crop_asset": equation.get("crop_asset"),
                "content_source": equation.get("content_source"),
                "relations": [{"type": "inline_with" if inline else "anchored_on_page", "target": target} for target in related_bands],
                "source": {"bbox": box.to_dict()},
            }
        )

    for idx, image in enumerate(images, start=1):
        box = box_from_dict(image["bbox"])
        nodes.append(
            {
                "id": str(image.get("id") or f"{page_id}-img-{idx:04d}"),
                "type": "image",
                "bbox": box.to_dict(),
                "render_layer": image.get("render_layer", "background"),
                "transparent_background": bool(image.get("transparent_background", True)),
                "component_of": image.get("component_of"),
                "relations": [],
                "source": {"bbox": box.to_dict()},
            }
        )

    for idx, rule in enumerate(layout.get("rules") or [], start=1):
        box = box_from_dict(rule["bbox"])
        nodes.append(
            {
                "id": str(rule.get("id") or f"{page_id}-rule-{idx:04d}"),
                "type": "rule",
                "bbox": box.to_dict(),
                "class": rule.get("class", "horizontal_rule"),
                "relations": [],
                "source": {"bbox": box.to_dict()},
            }
        )

    nodes.sort(key=lambda item: (item["bbox"]["y"], item["bbox"]["x"], item["type"], item["id"]))
    return {
        "schema": "pdf-training-renderable-page-v1",
        "id": page_id,
        "document_id": document_id,
        "source_page_index": annotations.get("source_page_index", layout.get("source_page_index")),
        "output_page_index": annotations.get("output_page_index", layout.get("output_page_index")),
        "width": width,
        "height": height,
        "nodes": nodes,
        "summary": {
            "nodes": len(nodes),
            "text_bands": sum(1 for node in nodes if node["type"] == "text_band"),
            "text_fragments": sum(1 for node in nodes if node["type"] == "text_fragment"),
            "equations": sum(1 for node in nodes if node["type"] == "equation"),
            "images": sum(1 for node in nodes if node["type"] == "image"),
            "rules": sum(1 for node in nodes if node["type"] == "rule"),
        },
    }


def render_review_html(page: dict[str, Any]) -> str:
    width = int(page.get("width") or 1)
    height = int(page.get("height") or 1)
    colors = {
        "text_band": "#0ea5e9",
        "text_fragment": "#22c55e",
        "equation": "#f97316",
        "image": "#84cc16",
        "rule": "#ef4444",
    }
    overlays = []
    rows = []
    for node in page.get("nodes") or []:
        box = node["bbox"]
        color = colors.get(node["type"], "#a855f7")
        title = html.escape(json.dumps(node, ensure_ascii=False))
        label = html.escape(f"{node['id']} {node['type']}")
        overlays.append(
            f'<div class="box" title="{title}" style="left:{box["x"]}px;top:{box["y"]}px;'
            f'width:{box["w"]}px;height:{box["h"]}px;border-color:{color};background:{color}18">'
            f"<span>{label}</span></div>"
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(node['id'])}</td>"
            f"<td>{html.escape(node['type'])}</td>"
            f"<td><code>{html.escape(json.dumps(box))}</code></td>"
            f"<td><code>{html.escape(json.dumps(node.get('relations') or []))}</code></td>"
            "</tr>"
        )
    return f"""<!doctype html>
<meta charset="utf-8">
<title>Renderable Page Review</title>
<style>
body {{ margin:0; font-family: Georgia, 'Times New Roman', serif; background:#f6efe2; color:#1d160f; }}
header {{ background:#271b10; color:#fff4dd; padding:12px 18px; }}
main {{ padding:18px; }}
.stage {{ position:relative; width:{width}px; height:{height}px; background:white; border:1px solid #d7c199; }}
.box {{ position:absolute; border:2px solid; box-sizing:border-box; }}
.box span {{ position:absolute; left:0; top:-15px; font:11px/1.1 ui-monospace,Consolas,monospace; background:#fff9; white-space:nowrap; }}
table {{ border-collapse:collapse; width:100%; margin-top:20px; background:#fffaf0; }}
td, th {{ border:1px solid #d7c199; padding:6px 8px; vertical-align:top; }}
th {{ background:#efe1c4; text-align:left; }}
</style>
<header><h1>Renderable Page Review: {html.escape(str(page.get("id")))}</h1></header>
<main>
<div class="stage">{''.join(overlays)}</div>
<table><thead><tr><th>ID</th><th>Type</th><th>Bbox</th><th>Relations</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
</main>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", required=True, type=Path)
    parser.add_argument("--annotations", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--review-html", type=Path)
    parser.add_argument("--document-id", default="document")
    parser.add_argument("--page-id")
    args = parser.parse_args()

    page = compose_page(
        load_json(args.layout),
        load_json(args.annotations) if args.annotations else {},
        document_id=args.document_id,
        page_id=args.page_id,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(page, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.review_html:
        args.review_html.parent.mkdir(parents=True, exist_ok=True)
        args.review_html.write_text(render_review_html(page), encoding="utf-8")
    print(json.dumps(page["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
