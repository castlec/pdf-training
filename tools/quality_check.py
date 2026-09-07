#!/usr/bin/env python3
"""Run reusable quality checks for renderable page datasets.

This is the canonical "run all checks" entry point for reusable pipeline
changes. It validates page-internal geometry/content invariants and can include
a baseline-vs-candidate comparison using ``validate_render_dataset``.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layout_detect import Box, intersection_area  # noqa: E402
from validate_render_dataset import validate as validate_baseline  # noqa: E402


TEXT_TYPES = {"text_band", "text_fragment", "caption", "heading", "header", "footer", "page_number"}
TEXT_FIELD_RE = re.compile(r"(^|\.)(text|source_text|text_with_refs|raw_content|reason|latex)$")
OBSTACLE_TYPES = {"equation", "image", "diagram_component", "table"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def box_from_dict(value: dict[str, Any]) -> Box:
    return Box(int(value["x"]), int(value["y"]), int(value["w"]), int(value["h"]))


def pages_from_object(data: dict[str, Any], *, path: Path) -> list[dict[str, Any]]:
    if "nodes" in data:
        return [{**data, "_source_path": str(path)}]
    pages: list[dict[str, Any]] = []
    for document in data.get("documents") or []:
        for page in document.get("pages") or []:
            pages.append({**page, "_source_path": str(path), "_document_id": document.get("id")})
    return pages


def load_pages(path: Path) -> list[dict[str, Any]]:
    if path.is_file():
        return pages_from_object(load_json(path), path=path)
    pages = []
    for file_path in sorted(path.rglob("*.json")):
        data = load_json(file_path)
        pages.extend(pages_from_object(data, path=file_path))
    return pages


def resolve_path(value: str | None, base: Path) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else base / path


def finding(
    *,
    check: str,
    severity: str,
    message: str,
    page: dict[str, Any] | None = None,
    node: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "check": check,
        "severity": severity,
        "message": message,
    }
    if page is not None:
        out["page_id"] = page.get("id")
        out["source_page_index"] = page.get("source_page_index")
        out["output_page_index"] = page.get("output_page_index")
    if node is not None:
        out["node_id"] = node.get("id")
        out["node_type"] = node.get("type")
        out["bbox"] = node.get("bbox")
    if data:
        out.update(data)
    return out


def check_bbox_bounds(page: dict[str, Any]) -> list[dict[str, Any]]:
    width = int(page.get("width") or 0)
    height = int(page.get("height") or 0)
    findings = []
    for node in page.get("nodes") or []:
        box_data = node.get("bbox")
        if not box_data:
            findings.append(
                finding(check="bbox_presence", severity="error", message="node has no bbox", page=page, node=node)
            )
            continue
        box = box_from_dict(box_data)
        if box.w <= 0 or box.h <= 0:
            findings.append(
                finding(check="bbox_size", severity="error", message="node bbox has non-positive size", page=page, node=node)
            )
        if width and height and (box.x < 0 or box.y < 0 or box.x2 > width or box.y2 > height):
            findings.append(
                finding(check="bbox_bounds", severity="error", message="node bbox exceeds page bounds", page=page, node=node)
            )
    return findings


def check_class_ranges(page: dict[str, Any], class_ranges: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for node in page.get("nodes") or []:
        cls = node.get("class")
        if not cls or cls not in class_ranges or not node.get("bbox"):
            continue
        spec = class_ranges[cls] or {}
        height = int(node["bbox"].get("h") or 0)
        min_h = spec.get("min_h")
        max_h = spec.get("max_h")
        if min_h is not None and height < int(min_h):
            findings.append(
                finding(
                    check="class_height",
                    severity="review",
                    message="node is too short for its class",
                    page=page,
                    node=node,
                    data={"class": cls, "height": height, "min_h": int(min_h)},
                )
            )
        if max_h is not None and height > int(max_h):
            findings.append(
                finding(
                    check="class_height",
                    severity="review",
                    message="node is too tall for its class",
                    page=page,
                    node=node,
                    data={"class": cls, "height": height, "max_h": int(max_h)},
                )
            )
    return findings


def walk_strings(value: Any, path: str = "", context_id: str | None = None):
    if isinstance(value, dict):
        current_id = value.get("id") if isinstance(value.get("id"), str) else context_id
        for key, item in value.items():
            yield from walk_strings(item, f"{path}.{key}" if path else str(key), current_id)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_strings(item, f"{path}[{index}]", context_id)
    elif isinstance(value, str):
        yield path, value, context_id


def check_line_break_fields(page: dict[str, Any], *, include_raw: bool) -> list[dict[str, Any]]:
    findings = []
    for field_path, value, context_id in walk_strings(page):
        if not TEXT_FIELD_RE.search(field_path):
            continue
        if not include_raw and field_path.endswith("raw_content"):
            continue
        actual = "\n" in value
        literal = "\\n" in value
        if not actual and not literal:
            continue
        severity = "review" if ".latex" in field_path or field_path.endswith("raw_content") else "error"
        findings.append(
            finding(
                check="line_break_field",
                severity=severity,
                message="text-like field contains actual or literal newline",
                page=page,
                data={
                    "context_id": context_id,
                    "field": field_path,
                    "actual_newline": actual,
                    "literal_backslash_n": literal,
                    "preview": value[:220].replace("\n", "\\n"),
                },
            )
        )
    return findings


def row_bands(crop: Image.Image, *, threshold: int, min_row_fraction: float, merge_gap: int) -> tuple[float, int, int, list[dict[str, int]]]:
    gray = crop.convert("L")
    width, height = gray.size
    pix = gray.load()
    active_rows: list[int] = []
    dark_total = 0
    for y in range(height):
        dark = sum(1 for x in range(width) if pix[x, y] < threshold)
        dark_total += dark
        if dark / max(1, width) >= min_row_fraction:
            active_rows.append(y)
    if not active_rows:
        return 0.0, 0, height, []
    bands: list[dict[str, int]] = []
    start = previous = active_rows[0]
    for y in active_rows[1:]:
        if y > previous + merge_gap + 1:
            bands.append({"y1": start, "y2": previous + 1, "h": previous + 1 - start})
            start = y
        previous = y
    bands.append({"y1": start, "y2": previous + 1, "h": previous + 1 - start})
    density = dark_total / max(1, width * height)
    return density, active_rows[0], height - 1 - active_rows[-1], bands


def check_density(
    page: dict[str, Any],
    *,
    source_base: Path,
    threshold: int,
    min_row_fraction: float,
    merge_gap: int,
    major_band_min_height: int,
    tall_line_height: int,
    low_density: float,
    large_pad: int,
) -> list[dict[str, Any]]:
    image_path = resolve_path(str(page.get("source_image") or ""), source_base)
    if not image_path or not image_path.exists():
        return [
            finding(
                check="density_image",
                severity="review",
                message="source image unavailable; density checks skipped",
                page=page,
                data={"source_image": page.get("source_image")},
            )
        ]
    findings = []
    with Image.open(image_path).convert("L") as image:
        for node in page.get("nodes") or []:
            if node.get("type") not in TEXT_TYPES or not node.get("bbox"):
                continue
            box = box_from_dict(node["bbox"])
            box = Box(max(0, box.x), max(0, box.y), max(1, min(box.x2, image.width) - max(0, box.x)), max(1, min(box.y2, image.height) - max(0, box.y)))
            crop = image.crop((box.x, box.y, box.x2, box.y2))
            density, top_pad, bottom_pad, bands = row_bands(crop, threshold=threshold, min_row_fraction=min_row_fraction, merge_gap=merge_gap)
            major_bands = [band for band in bands if int(band["h"]) >= major_band_min_height]
            flags = []
            if box.h >= tall_line_height and len(major_bands) >= 2:
                flags.append("multi_dense_band_tall_text_node")
            if box.h >= tall_line_height and density <= low_density:
                flags.append("low_density_tall_text_node")
            if box.h >= tall_line_height and (top_pad >= large_pad or bottom_pad >= large_pad):
                flags.append("large_vertical_padding")
            if not bands and node.get("type") not in {"footer", "page_number"}:
                flags.append("no_detected_ink")
            if flags:
                findings.append(
                    finding(
                        check="density_geometry",
                        severity="review",
                        message="text node density or row-band metrics are suspicious",
                        page=page,
                        node=node,
                        data={
                            "flags": flags,
                            "metrics": {
                                "density": round(density, 5),
                                "top_pad": top_pad,
                                "bottom_pad": bottom_pad,
                                "bands": bands,
                                "major_band_count": len(major_bands),
                            },
                        },
                    )
                )
    return findings


def related_targets(node: dict[str, Any]) -> set[str]:
    return {str(rel.get("target")) for rel in node.get("relations") or [] if rel.get("target")}


def check_mask_intersections(page: dict[str, Any], *, min_area_ratio: float) -> list[dict[str, Any]]:
    findings = []
    obstacles = [node for node in page.get("nodes") or [] if node.get("type") in OBSTACLE_TYPES and node.get("bbox")]
    text_nodes = [node for node in page.get("nodes") or [] if node.get("type") in TEXT_TYPES and node.get("bbox")]
    for node in text_nodes:
        node_box = box_from_dict(node["bbox"])
        node_area = max(1, node_box.w * node_box.h)
        targets = related_targets(node)
        for obstacle in obstacles:
            area = intersection_area(node_box, box_from_dict(obstacle["bbox"]))
            if not area:
                continue
            ratio = area / node_area
            if ratio < min_area_ratio:
                continue
            if str(obstacle.get("id")) in targets:
                continue
            findings.append(
                finding(
                    check="mask_intersection",
                    severity="review",
                    message="text node intersects equation/image/table without an explicit relation",
                    page=page,
                    node=node,
                    data={"obstacle_id": obstacle.get("id"), "obstacle_type": obstacle.get("type"), "area_ratio": round(ratio, 4)},
                )
            )
    return findings


def check_duplicate_bboxes(page: dict[str, Any], *, iou_threshold: float) -> list[dict[str, Any]]:
    findings = []
    nodes = [node for node in page.get("nodes") or [] if node.get("bbox") and node.get("type") in TEXT_TYPES | OBSTACLE_TYPES]
    for index, left in enumerate(nodes):
        left_box = box_from_dict(left["bbox"])
        for right in nodes[index + 1 :]:
            if left.get("type") != right.get("type"):
                continue
            score = box_iou(left_box, box_from_dict(right["bbox"]))
            if score >= iou_threshold:
                findings.append(
                    finding(
                        check="duplicate_bbox",
                        severity="review",
                        message="same-type nodes have nearly duplicate bboxes",
                        page=page,
                        node=left,
                        data={"other_node_id": right.get("id"), "iou": round(score, 4)},
                    )
                )
    return findings


def box_iou(a: Box, b: Box) -> float:
    hit = intersection_area(a, b)
    union = a.w * a.h + b.w * b.h - hit
    return hit / union if union else 0.0


def class_ranges_from_candidate(candidate: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    if override:
        return override
    if isinstance(candidate.get("style"), dict) and isinstance(candidate["style"].get("class_ranges"), dict):
        return candidate["style"]["class_ranges"]
    ranges: dict[str, Any] = {}
    for document in candidate.get("documents") or []:
        style = document.get("style") if isinstance(document.get("style"), dict) else {}
        for key, value in (style.get("class_ranges") or {}).items():
            ranges[key] = value
    return ranges


def validate_quality(
    candidate: dict[str, Any],
    *,
    candidate_path: Path,
    source_base: Path,
    baseline: dict[str, Any] | None = None,
    class_ranges: dict[str, Any] | None = None,
    include_raw: bool = False,
    density: bool = False,
    threshold: int = 190,
    min_row_fraction: float = 0.006,
    merge_gap: int = 3,
    major_band_min_height: int = 18,
    tall_line_height: int = 86,
    low_density: float = 0.045,
    large_pad: int = 22,
    intersection_ratio: float = 0.05,
    duplicate_iou: float = 0.98,
    bbox_tolerance: int = 2,
) -> dict[str, Any]:
    pages = pages_from_object(candidate, path=candidate_path)
    effective_ranges = class_ranges_from_candidate(candidate, class_ranges)
    findings: list[dict[str, Any]] = []
    for page in pages:
        findings.extend(check_bbox_bounds(page))
        findings.extend(check_class_ranges(page, effective_ranges))
        findings.extend(check_line_break_fields(page, include_raw=include_raw))
        findings.extend(check_mask_intersections(page, min_area_ratio=intersection_ratio))
        findings.extend(check_duplicate_bboxes(page, iou_threshold=duplicate_iou))
        if density:
            findings.extend(
                check_density(
                    page,
                    source_base=source_base,
                    threshold=threshold,
                    min_row_fraction=min_row_fraction,
                    merge_gap=merge_gap,
                    major_band_min_height=major_band_min_height,
                    tall_line_height=tall_line_height,
                    low_density=low_density,
                    large_pad=large_pad,
                )
            )

    baseline_report = None
    if baseline is not None:
        baseline_report = validate_baseline(
            baseline,
            candidate,
            bbox_tolerance=bbox_tolerance,
            class_ranges=effective_ranges,
        )
        for item in baseline_report.get("regressions") or []:
            findings.append(
                {
                    "check": "baseline_regression",
                    "severity": "error",
                    "message": "baseline comparison reported removed valuable content",
                    **item,
                }
            )

    severity_counts = Counter(item["severity"] for item in findings)
    check_counts = Counter(item["check"] for item in findings)
    status = "fail" if severity_counts.get("error", 0) else ("review" if findings else "pass")
    return {
        "schema": "pdf-training-quality-report-v1",
        "candidate": candidate.get("id") or candidate.get("name") or str(candidate_path),
        "created_at": now_iso(),
        "summary": {
            "status": status,
            "pages": len(pages),
            "findings": len(findings),
            "errors": severity_counts.get("error", 0),
            "review": severity_counts.get("review", 0),
            "checks": dict(sorted(check_counts.items())),
        },
        "baseline_report": baseline_report,
        "findings": findings,
    }


def render_review_html(report: dict[str, Any]) -> str:
    rows = []
    for item in report.get("findings") or []:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('severity')))}</td>"
            f"<td>{html.escape(str(item.get('check')))}</td>"
            f"<td>{html.escape(str(item.get('page_id') or ''))}</td>"
            f"<td>{html.escape(str(item.get('node_id') or item.get('id') or ''))}</td>"
            f"<td>{html.escape(str(item.get('message') or ''))}</td>"
            f"<td><code>{html.escape(json.dumps(item, ensure_ascii=False))}</code></td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="und">
<head>
<meta charset="utf-8">
<title>Quality Check: {html.escape(str(report['summary']['status']))}</title>
<style>
body {{ margin:0; font-family:Georgia,'Times New Roman',serif; background:#f4ead8; color:#1f180f; }}
header {{ background:#291f14; color:#fff3d8; padding:14px 18px; }}
main {{ padding:18px; }}
.summary {{ display:flex; flex-wrap:wrap; gap:10px; }}
.pill {{ border:1px solid #9d783d; border-radius:999px; padding:4px 9px; background:#3b2b1a; }}
table {{ border-collapse:collapse; width:100%; background:#fffaf0; }}
th, td {{ border:1px solid #cdb583; padding:6px 8px; vertical-align:top; }}
th {{ background:#ead8b4; position:sticky; top:0; }}
code {{ white-space:pre-wrap; }}
</style>
</head>
<body>
<header>
  <h1>Quality Check: {html.escape(str(report['summary']['status']))}</h1>
  <div class="summary">
    <span class="pill">pages {report['summary']['pages']}</span>
    <span class="pill">findings {report['summary']['findings']}</span>
    <span class="pill">errors {report['summary']['errors']}</span>
    <span class="pill">review {report['summary']['review']}</span>
  </div>
</header>
<main>
<table>
<thead><tr><th>Severity</th><th>Check</th><th>Page</th><th>Node</th><th>Message</th><th>Data</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
</main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--review-html", type=Path)
    parser.add_argument("--source-base", type=Path, default=Path("."))
    parser.add_argument("--class-ranges", type=Path)
    parser.add_argument("--include-raw", action="store_true")
    parser.add_argument("--density", action="store_true", help="Run image-density checks; requires source_image paths")
    parser.add_argument("--threshold", type=int, default=190)
    parser.add_argument("--min-row-fraction", type=float, default=0.006)
    parser.add_argument("--merge-gap", type=int, default=3)
    parser.add_argument("--major-band-min-height", type=int, default=18)
    parser.add_argument("--tall-line-height", type=int, default=86)
    parser.add_argument("--low-density", type=float, default=0.045)
    parser.add_argument("--large-pad", type=int, default=22)
    parser.add_argument("--intersection-ratio", type=float, default=0.05)
    parser.add_argument("--duplicate-iou", type=float, default=0.98)
    parser.add_argument("--bbox-tolerance", type=int, default=2)
    args = parser.parse_args()

    candidate = load_json(args.candidate)
    report = validate_quality(
        candidate,
        candidate_path=args.candidate,
        source_base=args.source_base,
        baseline=load_json(args.baseline) if args.baseline else None,
        class_ranges=load_json(args.class_ranges) if args.class_ranges else None,
        include_raw=args.include_raw,
        density=args.density,
        threshold=args.threshold,
        min_row_fraction=args.min_row_fraction,
        merge_gap=args.merge_gap,
        major_band_min_height=args.major_band_min_height,
        tall_line_height=args.tall_line_height,
        low_density=args.low_density,
        large_pad=args.large_pad,
        intersection_ratio=args.intersection_ratio,
        duplicate_iou=args.duplicate_iou,
        bbox_tolerance=args.bbox_tolerance,
    )
    write_json(args.out, report)
    if args.review_html:
        args.review_html.parent.mkdir(parents=True, exist_ok=True)
        args.review_html.write_text(render_review_html(report), encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))
    return 1 if report["summary"]["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
