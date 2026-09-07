#!/usr/bin/env python3
"""Detect reusable scanned-PDF layout primitives from page images.

This module intentionally uses page geometry and ink density only. OCR text and
vision-model output may populate content later, but they should not decide
whether a layout band exists, is split, removed, or classified.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def area(self) -> int:
        return self.w * self.h

    def scale(self, sx: float, sy: float) -> "Box":
        return Box(
            round(self.x * sx),
            round(self.y * sy),
            max(1, round(self.w * sx)),
            max(1, round(self.h * sy)),
        )

    def clamp(self, width: int, height: int) -> "Box | None":
        x1 = min(width, max(0, self.x))
        y1 = min(height, max(0, self.y))
        x2 = min(width, max(0, self.x2))
        y2 = min(height, max(0, self.y2))
        if x2 <= x1 or y2 <= y1:
            return None
        return Box(x1, y1, x2 - x1, y2 - y1)

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def union_box(boxes: list[Box]) -> Box:
    x1 = min(box.x for box in boxes)
    y1 = min(box.y for box in boxes)
    x2 = max(box.x2 for box in boxes)
    y2 = max(box.y2 for box in boxes)
    return Box(x1, y1, x2 - x1, y2 - y1)


def intersection(a: Box, b: Box) -> Box | None:
    x1 = max(a.x, b.x)
    y1 = max(a.y, b.y)
    x2 = min(a.x2, b.x2)
    y2 = min(a.y2, b.y2)
    if x2 <= x1 or y2 <= y1:
        return None
    return Box(x1, y1, x2 - x1, y2 - y1)


def intersection_area(a: Box, b: Box) -> int:
    hit = intersection(a, b)
    return hit.area if hit else 0


def horizontal_overlap_width(a: Box, b: Box) -> int:
    return max(0, min(a.x2, b.x2) - max(a.x, b.x))


def contains_box(outer: Box, inner: Box, *, pad: int = 0) -> bool:
    return (
        outer.x - pad <= inner.x
        and outer.y - pad <= inner.y
        and outer.x2 + pad >= inner.x2
        and outer.y2 + pad >= inner.y2
    )


def bands_from_projection(active: np.ndarray, *, min_height: int, merge_gap: int) -> list[tuple[int, int]]:
    bands: list[tuple[int, int]] = []
    start: int | None = None
    for idx, value in enumerate(active):
        if value and start is None:
            start = idx
        if (not value or idx == len(active) - 1) and start is not None:
            end = idx if not value else idx + 1
            if end - start >= min_height:
                bands.append((start, end))
            start = None

    merged: list[tuple[int, int]] = []
    for start, end in bands:
        if merged and start - merged[-1][1] <= merge_gap:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def load_grayscale(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(f"Could not read image: {path}")
    return image


def ink_mask(gray: np.ndarray, *, threshold: int = 210) -> np.ndarray:
    return gray < threshold


def gradient_mask(gray: np.ndarray) -> np.ndarray:
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    gx = cv2.Sobel(blurred, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blurred, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = cv2.magnitude(gx, gy)
    threshold = max(18.0, float(np.percentile(magnitude, 92)))
    return magnitude >= threshold


def x_extent(mask: np.ndarray, y1: int, y2: int, *, min_col_pixels: int = 1) -> tuple[int, int] | None:
    crop = mask[y1:y2, :]
    if crop.size == 0:
        return None
    cols = np.where(crop.sum(axis=0) >= min_col_pixels)[0]
    if cols.size == 0:
        return None
    return int(cols[0]), int(cols[-1]) + 1


def detect_content_bbox(mask: np.ndarray) -> Box:
    ys, xs = np.where(mask)
    if xs.size == 0:
        return Box(0, 0, mask.shape[1], mask.shape[0])
    return Box(int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))


def detect_rows(line_mask: np.ndarray, *, min_width: int = 8) -> list[Box]:
    height, width = line_mask.shape
    projection = line_mask.sum(axis=1)
    active = projection > max(2, int(width * 0.01))
    bands = bands_from_projection(active, min_height=2, merge_gap=max(1, round(height * 0.004)))
    rows: list[Box] = []
    for y1, y2 in bands:
        extent = x_extent(line_mask, y1, y2)
        if not extent:
            continue
        x1, x2 = extent
        if x2 - x1 < min_width:
            continue
        rows.append(Box(x1, y1, x2 - x1, y2 - y1))
    return rows


def split_tall_rows(rows: list[Box], line_mask: np.ndarray) -> list[Box]:
    height, width = line_mask.shape
    split_rows: list[Box] = []
    for row in rows:
        if row.h < max(36, round(height * 0.045)):
            split_rows.append(row)
            continue
        crop = line_mask[row.y : row.y2, :]
        projection = crop.sum(axis=1)
        active = projection > max(10, int(width * 0.02))
        bands = bands_from_projection(active, min_height=2, merge_gap=1)
        candidates: list[Box] = []
        for local_y1, local_y2 in bands:
            y1 = row.y + local_y1
            y2 = row.y + local_y2
            extent = x_extent(line_mask, y1, y2)
            if not extent:
                continue
            x1, x2 = extent
            if x2 - x1 >= 8:
                candidates.append(Box(x1, y1, x2 - x1, y2 - y1))
        split_rows.extend(candidates if len(candidates) >= 2 else [row])
    return split_rows


def cleanup_short_artifact_rows(rows: list[Box], *, image_shape: tuple[int, int]) -> tuple[list[Box], list[dict[str, Any]]]:
    """Remove or merge pathological short row slices.

    This does not discard normal narrow glyphs. Only very short rows that are
    contained in or directly adjacent to compatible bands are changed, and every
    action is returned for validation/audit.
    """
    if not rows:
        return [], []

    height, width = image_shape
    short_h = max(5, round(height * 0.007))
    adjacent_gap = max(2, round(height * 0.004))
    wide_artifact_w = max(90, round(width * 0.18))
    actions: list[dict[str, Any]] = []

    current = list(rows)
    removed: set[int] = set()
    for idx, row in enumerate(current):
        if row.h > short_h:
            continue
        for other_idx, other in enumerate(current):
            if idx == other_idx or other_idx in removed or other.h <= row.h:
                continue
            if contains_box(other, row, pad=1):
                removed.add(idx)
                actions.append(
                    {
                        "action": "remove_contained_short_row",
                        "row_index": idx + 1,
                        "row_bbox": row.to_dict(),
                        "container_index": other_idx + 1,
                        "container_bbox": other.to_dict(),
                        "reason": "short row fully contained in taller row",
                    }
                )
                break

    current = [row for idx, row in enumerate(current) if idx not in removed]
    changed = True
    while changed:
        changed = False
        next_rows: list[Box] = []
        idx = 0
        while idx < len(current):
            row = current[idx]
            if row.h > short_h:
                next_rows.append(row)
                idx += 1
                continue

            candidates: list[tuple[int, Box, int, int]] = []
            if next_rows:
                prev = next_rows[-1]
                gap = row.y - prev.y2
                if 0 <= gap <= adjacent_gap:
                    candidates.append((-1, prev, gap, horizontal_overlap_width(row, prev)))
            if idx + 1 < len(current):
                nxt = current[idx + 1]
                gap = nxt.y - row.y2
                if 0 <= gap <= adjacent_gap:
                    candidates.append((1, nxt, gap, horizontal_overlap_width(row, nxt)))

            target: tuple[int, Box, int, int] | None = None
            for direction, candidate, gap, overlap in sorted(candidates, key=lambda item: (item[2], -item[3], item[0])):
                overlap_fraction = overlap / max(1, min(row.w, candidate.w))
                similar_left = abs(row.x - candidate.x) <= max(4, round(width * 0.015))
                similar_right = abs(row.x2 - candidate.x2) <= max(4, round(width * 0.015))
                wide_slice = row.w >= wide_artifact_w
                narrow_touching = row.w < wide_artifact_w and gap <= 1 and overlap_fraction >= 0.35
                compatible = overlap_fraction >= 0.18 or similar_left or similar_right
                if compatible and (wide_slice or narrow_touching):
                    target = (direction, candidate, gap, overlap)
                    break

            if target is None:
                next_rows.append(row)
                idx += 1
                continue

            direction, candidate, gap, overlap = target
            merged = union_box([row, candidate])
            if direction < 0:
                previous = next_rows.pop()
                next_rows.append(merged)
                target_index = len(next_rows)
                target_bbox = previous.to_dict()
            else:
                current[idx + 1] = merged
                target_index = idx + 2
                target_bbox = candidate.to_dict()
            actions.append(
                {
                    "action": "merge_adjacent_short_row",
                    "row_index": idx + 1,
                    "row_bbox": row.to_dict(),
                    "target_index": target_index,
                    "target_bbox": target_bbox,
                    "merged_bbox": merged.to_dict(),
                    "gap": gap,
                    "horizontal_overlap": overlap,
                    "reason": "short row adjacent to compatible text band",
                }
            )
            changed = True
            idx += 1
        current = sorted(next_rows, key=lambda box: (box.y, box.x))

    return current, actions


def edge_expansion(
    box: Box,
    *,
    weak_mask: np.ndarray,
    edge_mask: np.ndarray,
    direction: str,
    probe_pad_x: int = 2,
    probe_height: int = 4,
    max_expand: int = 10,
) -> int:
    height, width = weak_mask.shape
    if direction not in {"up", "down"}:
        raise ValueError("direction must be 'up' or 'down'")
    expanded_to = box.y if direction == "up" else box.y2

    while True:
        if direction == "down":
            if expanded_to - box.y2 >= max_expand or expanded_to >= height:
                break
            probe_y1 = expanded_to
            probe_y2 = min(height, probe_y1 + probe_height)
        else:
            if box.y - expanded_to >= max_expand or expanded_to <= 0:
                break
            probe_y2 = expanded_to
            probe_y1 = max(0, probe_y2 - probe_height)
        x1 = max(0, box.x - probe_pad_x)
        x2 = min(width, box.x2 + probe_pad_x)
        weak_support = weak_mask[probe_y1:probe_y2, x1:x2]
        edge_support = edge_mask[probe_y1:probe_y2, x1:x2]
        if weak_support.size == 0:
            break
        useful: list[int] = []
        for support in (weak_support, edge_support):
            if int(np.count_nonzero(support)) < 3:
                continue
            count, _, stats, _ = cv2.connectedComponentsWithStats(support.astype(np.uint8), connectivity=8)
            for label in range(1, count):
                sx, sy, sw, sh, area = [int(v) for v in stats[label]]
                if area < 3 or sw > (x2 - x1) * 0.85:
                    continue
                useful.append(probe_y1 + sy if direction == "up" else probe_y1 + sy + sh)
            if useful:
                break
        if not useful:
            break
        new_expanded_to = max(0, min(useful)) if direction == "up" else min(height, max(useful))
        if direction == "up" and new_expanded_to >= expanded_to:
            break
        if direction == "down" and new_expanded_to <= expanded_to:
            break
        expanded_to = new_expanded_to

    return min(max_expand, max(0, box.y - expanded_to if direction == "up" else expanded_to - box.y2))


def expand_row_edges(rows: list[Box], gray: np.ndarray, *, max_expand: int = 10) -> tuple[list[Box], list[dict[str, int]]]:
    weak = ink_mask(gray, threshold=235)
    edges = gradient_mask(gray)
    expanded: list[Box] = []
    details: list[dict[str, int]] = []
    height, width = gray.shape
    for row in rows:
        top = edge_expansion(row, weak_mask=weak, edge_mask=edges, direction="up", max_expand=max_expand)
        bottom = edge_expansion(row, weak_mask=weak, edge_mask=edges, direction="down", max_expand=max_expand)
        new = Box(row.x, max(0, row.y - top), row.w, min(height, row.y2 + bottom) - max(0, row.y - top))
        expanded.append(new.clamp(width, height) or row)
        details.append({"top": top, "bottom": bottom})
    return expanded, details


def line_candidates_from_projection(
    row: Box,
    *,
    split_mask: np.ndarray,
    extent_mask: np.ndarray,
    active_threshold: int,
    merge_gap: int,
) -> list[dict[str, Any]]:
    projection = split_mask[row.y : row.y2, :].sum(axis=1)
    active = projection > active_threshold
    bands = bands_from_projection(active, min_height=2, merge_gap=merge_gap)
    lines: list[dict[str, Any]] = []
    for line_index, (local_y1, local_y2) in enumerate(bands, start=1):
        y1 = row.y + local_y1
        y2 = row.y + local_y2
        extent = x_extent(extent_mask, y1, y2)
        if not extent:
            continue
        x1, x2 = extent
        lines.append({"line_index": line_index, "bbox": Box(x1, y1, x2 - x1, y2 - y1).to_dict()})
    return lines


def annotate_line_indents(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not lines:
        return lines
    left = min(line["bbox"]["x"] for line in lines)
    previous_x: int | None = None
    run = 1
    run_x: int | None = None
    for line in lines:
        x = int(line["bbox"]["x"])
        if previous_x is None:
            previous_delta = None
            same_previous = None
            run_x = x
        else:
            previous_delta = x - previous_x
            same_previous = abs(previous_delta) <= 2
            if not same_previous:
                run += 1
                run_x = x
        line["indent_from_region"] = x - left
        line["previous_indent_delta"] = previous_delta
        line["same_as_previous_line"] = same_previous
        line["indent_run"] = run
        line["indent_run_x"] = run_x
        previous_x = x
    return lines


def detect_internal_lines(row: Box, *, split_mask: np.ndarray, extent_mask: np.ndarray) -> list[dict[str, Any]]:
    height, width = split_mask.shape
    lines = line_candidates_from_projection(
        row,
        split_mask=split_mask,
        extent_mask=extent_mask,
        active_threshold=max(2, int(width * 0.01)),
        merge_gap=0,
    )
    if len(lines) <= 1 and row.h >= max(28, round(height * 0.04)) and row.w >= width * 0.72:
        stricter = line_candidates_from_projection(
            row,
            split_mask=split_mask,
            extent_mask=extent_mask,
            active_threshold=max(10, int(width * 0.035)),
            merge_gap=0,
        )
        if len(stricter) > len(lines):
            lines = stricter
    if not lines:
        lines = [{"line_index": 1, "bbox": row.to_dict()}]
    return annotate_line_indents(lines)


def summarize_internal_lines(lines: list[dict[str, Any]]) -> dict[str, Any]:
    xs = [line["bbox"]["x"] for line in lines]
    first_x = xs[0] if xs else 0
    following_x = round(median(xs[1:])) if len(xs) > 1 else first_x
    return {
        "line_count": len(lines),
        "first_line_x": first_x,
        "following_line_x": following_x,
        "same_indent": (max(xs) - min(xs) <= 2) if xs else True,
        "left_edge_variance": (max(xs) - min(xs)) if xs else 0,
    }


def merge_collinear_rules(boxes: list[Box], *, image_width: int) -> list[Box]:
    groups: list[list[Box]] = []
    for box in sorted(boxes, key=lambda item: (item.y, item.x)):
        cy = box.y + box.h / 2
        for group in groups:
            gy = sum(item.y + item.h / 2 for item in group) / len(group)
            if abs(cy - gy) <= 4:
                group.append(box)
                break
        else:
            groups.append([box])

    merged: list[Box] = []
    for group in groups:
        current = sorted(group, key=lambda item: item.x)[0]
        for box in sorted(group, key=lambda item: item.x)[1:]:
            if box.x - current.x2 <= image_width * 0.08:
                current = union_box([current, box])
            else:
                merged.append(current)
                current = box
        merged.append(current)
    return sorted(merged, key=lambda box: (box.y, box.x))


def detect_horizontal_rules(gray: np.ndarray, *, target_size: tuple[int, int] | None = None) -> list[Box]:
    dark = ink_mask(gray, threshold=180).astype(np.uint8)
    height, width = dark.shape
    min_rule_width = max(40, round(width * 0.045))
    kernel_width = max(40, round(width * 0.02))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, 1))
    horizontal = cv2.morphologyEx(dark, cv2.MORPH_OPEN, kernel)
    horizontal = cv2.dilate(horizontal, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 1)), iterations=1)
    count, _, stats, _ = cv2.connectedComponentsWithStats(horizontal, connectivity=8)
    candidates: list[Box] = []
    for label in range(1, count):
        x, y, w, h, area = [int(v) for v in stats[label]]
        if w < min_rule_width or h > max(8, round(height * 0.008)) or area < w * 0.55:
            continue
        candidates.append(Box(x, y, w, h))

    sx = sy = 1.0
    if target_size is not None:
        sx = target_size[1] / width
        sy = target_size[0] / height
    return [box.scale(sx, sy) for box in merge_collinear_rules(candidates, image_width=width)]


def mask_components(mask: np.ndarray, *, target_size: tuple[int, int] | None = None, min_area: int = 20) -> list[dict[str, Any]]:
    binary = (mask > 0).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    sx = sy = 1.0
    if target_size is not None:
        sx = target_size[1] / mask.shape[1]
        sy = target_size[0] / mask.shape[0]
    components: list[dict[str, Any]] = []
    for label in range(1, count):
        x, y, w, h, area = [int(v) for v in stats[label]]
        if area < min_area:
            continue
        original = Box(x, y, w, h)
        scaled = original.scale(sx, sy)
        components.append({"id": f"c{len(components) + 1:03d}", "bbox": scaled.to_dict(), "source_bbox": original.to_dict(), "area": area})
    return sorted(components, key=lambda item: (item["bbox"]["y"], item["bbox"]["x"]))


def mask_intersections(box: Box, components: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    matches = []
    total = 0
    for component in components:
        component_box = Box(**component["bbox"])
        area = intersection_area(box, component_box)
        if area <= 0:
            continue
        total += area
        matches.append(
            {
                "id": component["id"],
                "overlap_area": area,
                "region_fraction": round(area / max(1, box.area), 4),
                "component_fraction": round(area / max(1, component_box.area), 4),
                "bbox": component["bbox"],
            }
        )
    matches.sort(key=lambda item: item["overlap_area"], reverse=True)
    return {"kind": kind, "overlap_area": total, "region_fraction": round(total / max(1, box.area), 4), "components": matches}


def analyze_image(gray: np.ndarray, *, masks: dict[str, np.ndarray] | None = None) -> dict[str, Any]:
    dark = ink_mask(gray)
    rows = detect_rows(dark)
    rows = split_tall_rows(rows, dark)
    rows, cleanup_actions = cleanup_short_artifact_rows(rows, image_shape=gray.shape)
    rows, edge_details = expand_row_edges(rows, gray)
    rules = detect_horizontal_rules(gray)
    components = {kind: mask_components(mask, target_size=gray.shape) for kind, mask in (masks or {}).items()}

    regions = []
    for index, row in enumerate(rows, start=1):
        lines = detect_internal_lines(row, split_mask=dark, extent_mask=dark)
        region = {
            "id": f"r{index:03d}",
            "type": "text_band",
            "bbox": row.to_dict(),
            "edge_expansion": edge_details[index - 1],
            "internal_lines": lines,
            "internal_line_summary": summarize_internal_lines(lines),
            "mask_intersections": {kind: mask_intersections(row, items, kind) for kind, items in components.items()},
        }
        regions.append(region)

    return {
        "schema": "pdf-training-layout-primitives-v1",
        "image_size": {"w": gray.shape[1], "h": gray.shape[0]},
        "content_bbox": detect_content_bbox(dark).to_dict(),
        "regions": regions,
        "rules": [{"id": f"rule-{idx:03d}", "type": "rule", "bbox": box.to_dict()} for idx, box in enumerate(rules, start=1)],
        "mask_components": components,
        "row_cleanup_actions": cleanup_actions,
        "summary": {"regions": len(regions), "rules": len(rules), "row_cleanup_actions": len(cleanup_actions)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mask", action="append", default=[], help="Named mask in the form kind=path")
    args = parser.parse_args()

    masks: dict[str, np.ndarray] = {}
    for item in args.mask:
        if "=" not in item:
            raise SystemExit(f"Mask must be kind=path: {item}")
        kind, value = item.split("=", 1)
        masks[kind] = load_grayscale(Path(value))

    report = analyze_image(load_grayscale(args.image), masks=masks)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
