#!/usr/bin/env python3
"""Apply reusable document-structure rules to renderable metadata.

Private projects supply outline entries, printed-page maps, and header/footer
templates. This tool uses those inputs to classify headings and generate
recurring headers/footers without embedding source-book strings in code.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import html
import json
import re
import unicodedata
from pathlib import Path
from typing import Any


TEXT_TYPES = {"text_band", "text_fragment", "caption", "heading", "header", "footer", "page_number"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    no_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    no_marks = no_marks.replace("—", "-").replace("–", "-")
    return re.sub(r"[^0-9A-Z]+", " ", no_marks.upper()).strip()


def node_text(node: dict[str, Any]) -> str:
    for key in ("text", "label", "content"):
        text = str(node.get(key) or "").strip()
        if text:
            return text
    return ""


def outline_level(entry: dict[str, Any]) -> int:
    section = str(entry.get("section") or "").strip()
    if re.fullmatch(r"\d+(?:\.\d+)*", section):
        return section.count(".") + 1
    try:
        return max(1, int(entry.get("level") or 1))
    except (TypeError, ValueError):
        return 1


def outline_label(entry: dict[str, Any], *, include_number: bool = True) -> str:
    section = str(entry.get("section") or "").strip()
    title = str(entry.get("title") or "").strip()
    if include_number and section:
        return f"{section} {title}".strip()
    return title


def outline_candidate_texts(entry: dict[str, Any]) -> set[str]:
    section = str(entry.get("section") or "").strip()
    title = str(entry.get("title") or "").strip()
    candidates = {title}
    if section:
        candidates.add(f"{section} {title}".strip())
        candidates.add(f"{section}. {title}".strip())
    return {normalize_text(candidate) for candidate in candidates if normalize_text(candidate)}


def section_variants(section: str) -> set[str]:
    value = normalize_text(section)
    if not value:
        return set()
    variants = {value, value.rstrip("."), value.replace(".", ""), f"{value}."}
    parts = value.split(".")
    if len(parts) > 2:
        variants.add(f"{parts[0]}.{''.join(parts[1:])}")
    return variants


def outline_match_score(text: str, entry: dict[str, Any]) -> float:
    normalized = normalize_text(text)
    if not normalized:
        return 0.0
    candidates = outline_candidate_texts(entry)
    if normalized in candidates:
        return 1.0
    section = str(entry.get("section") or "").strip()
    title = normalize_text(str(entry.get("title") or ""))
    if section:
        variants = section_variants(section)
        if not any(normalized == variant or normalized.startswith(f"{variant} ") for variant in variants):
            return max((difflib.SequenceMatcher(None, normalized, candidate).ratio() for candidate in candidates), default=0.0)
    if title and len(normalized) >= 10 and normalized in title:
        return 0.79
    return max((difflib.SequenceMatcher(None, normalized, candidate).ratio() for candidate in candidates), default=0.0)


def best_outline_match(text: str, entries: list[dict[str, Any]], *, threshold: float) -> tuple[dict[str, Any], float] | None:
    scored = [(entry, outline_match_score(text, entry)) for entry in entries]
    scored = [(entry, score) for entry, score in scored if score >= threshold]
    if not scored:
        return None
    scored.sort(key=lambda item: (item[1], len(str(item[0].get("section") or ""))), reverse=True)
    return scored[0]


def active_outline_for_page(entries: list[dict[str, Any]], printed_pages: list[int]) -> dict[str, Any]:
    if not printed_pages:
        return {"printed_pages": [], "active": [], "starts_on_page": []}
    targets = sorted(set(int(page) for page in printed_pages if page is not None))
    target = max(targets)
    active_by_level: dict[int, dict[str, Any]] = {}
    starts_on_page = []
    for raw in entries:
        try:
            page = int(raw.get("printed_page"))
        except (TypeError, ValueError):
            continue
        level = outline_level(raw)
        entry = {
            **raw,
            "level": level,
            "label": outline_label(raw),
            "printed_page": page,
        }
        if page == target:
            starts_on_page.append(entry)
        if page <= target:
            active_by_level[level] = entry
            for stale in [key for key in active_by_level if key > level]:
                del active_by_level[stale]
    return {
        "printed_pages": targets,
        "active": [active_by_level[key] for key in sorted(active_by_level)],
        "starts_on_page": starts_on_page,
    }


def box_intersection_fraction(a: dict[str, Any], b: dict[str, Any]) -> float:
    ax1, ay1 = int(a["x"]), int(a["y"])
    ax2, ay2 = ax1 + int(a["w"]), ay1 + int(a["h"])
    bx1, by1 = int(b["x"]), int(b["y"])
    bx2, by2 = bx1 + int(b["w"]), by1 + int(b["h"])
    x1, y1 = max(ax1, bx1), max(ay1, by1)
    x2, y2 = min(ax2, bx2), min(ay2, by2)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    return ((x2 - x1) * (y2 - y1)) / max(1, int(a["w"]) * int(a["h"]))


def page_map_lookup(page: dict[str, Any], page_map: dict[str, Any] | None) -> dict[str, Any]:
    if not page_map:
        return {}
    page_id = str(page.get("id") or "")
    by_page_id = page_map.get("by_page_id") or {}
    if page_id in by_page_id:
        return by_page_id[page_id]
    for entry in page_map.get("entries") or []:
        if str(entry.get("page_id") or "") == page_id:
            return entry
        if page.get("source_page_index") is not None and entry.get("source_page_index") == page.get("source_page_index"):
            return entry
        if page.get("output_page_index") is not None and entry.get("output_page_index") == page.get("output_page_index"):
            return entry
    return {}


def printed_pages_for(page: dict[str, Any], page_map: dict[str, Any] | None) -> list[int]:
    entry = page_map_lookup(page, page_map)
    values = []
    for key in ("printed_page", "printed_pages", "toc_lookup_pages"):
        value = entry.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif value is not None:
            values.append(value)
    if not values and page.get("printed_page") is not None:
        values.append(page["printed_page"])
    out = []
    for value in values:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


def running_header_text(context: dict[str, Any], fallback: str) -> str:
    active = context.get("active") or []
    level1 = [entry for entry in active if int(entry.get("level") or 1) == 1]
    entry = level1[-1] if level1 else (active[-1] if active else None)
    return str((entry or {}).get("running_header") or (entry or {}).get("title") or fallback)


def template_for_page(rules: dict[str, Any], printed_page: int | None) -> dict[str, Any] | None:
    if printed_page is None:
        return None
    templates = rules.get("templates") or {}
    side = "even" if printed_page % 2 == 0 else "odd"
    return templates.get(side) or templates.get("default")


def format_template_text(value: str, *, printed_page: int | None, context: dict[str, Any], fallback_title: str) -> str:
    return (
        value.replace("{printed_page}", "" if printed_page is None else str(printed_page))
        .replace("{running_header}", running_header_text(context, fallback_title))
    )


def generated_header_footer_nodes(
    page: dict[str, Any],
    *,
    printed_page: int | None,
    context: dict[str, Any],
    rules: dict[str, Any],
) -> list[dict[str, Any]]:
    template = template_for_page(rules, printed_page)
    if not template:
        return []
    page_id = str(page.get("id") or "page")
    fallback_title = str(rules.get("fallback_running_header") or "")
    nodes = []
    for index, raw in enumerate(template.get("nodes") or [], start=1):
        node_type = str(raw.get("type") or "")
        node = {
            "id": f"{page_id}-generated-{node_type}-{index:02d}",
            "type": node_type,
            "bbox": raw["bbox"],
            "class": raw.get("class"),
            "producer": "document_structure",
            "source": {"rule": raw.get("id") or f"template-node-{index:02d}"},
            "relations": [],
        }
        if raw.get("text") is not None:
            node["text"] = format_template_text(
                str(raw["text"]),
                printed_page=printed_page,
                context=context,
                fallback_title=fallback_title,
            )
        if raw.get("align"):
            node["align"] = raw["align"]
        nodes.append(node)
    return nodes


def suppress_generated_regions(page: dict[str, Any], rules: dict[str, Any], *, min_fraction: float) -> int:
    bands = rules.get("ocr_exclusion_bands") or []
    suppressed = 0
    for node in page.get("nodes") or []:
        if node.get("type") not in TEXT_TYPES or not node.get("bbox"):
            continue
        for band in bands:
            if box_intersection_fraction(node["bbox"], band["bbox"]) >= float(band.get("min_fraction", min_fraction)):
                node["render_suppressed"] = True
                node["suppress_reason"] = str(band.get("reason") or "generated_header_footer_region")
                suppressed += 1
                break
    return suppressed


def classify_headings(page: dict[str, Any], context: dict[str, Any], *, threshold: float, demote_unmatched: bool) -> dict[str, int]:
    starts = context.get("starts_on_page") or []
    stats = {"matched": 0, "demoted": 0}
    for node in page.get("nodes") or []:
        if node.get("type") not in TEXT_TYPES or not node.get("bbox"):
            continue
        text = node_text(node)
        match = best_outline_match(text, starts, threshold=threshold)
        if match:
            entry, score = match
            level = outline_level(entry)
            node["type"] = "heading"
            node["class"] = f"heading_level_{min(max(level, 1), 6)}"
            node["outline_match"] = {
                "section": entry.get("section"),
                "title": entry.get("title"),
                "level": level,
                "printed_page": entry.get("printed_page"),
                "score": round(score, 4),
            }
            node["producer"] = "document_structure"
            stats["matched"] += 1
        elif demote_unmatched and node.get("type") == "heading":
            node["type"] = "text_fragment"
            node["class"] = "body"
            node["outline_match"] = None
            node["producer"] = "document_structure"
            stats["demoted"] += 1
    return stats


def apply_page_structure(
    page: dict[str, Any],
    *,
    outline_entries: list[dict[str, Any]],
    page_map: dict[str, Any] | None,
    rules: dict[str, Any],
    match_threshold: float,
    demote_unmatched: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    out = copy.deepcopy(page)
    printed_pages = printed_pages_for(out, page_map)
    printed_page = printed_pages[0] if printed_pages else None
    context = active_outline_for_page(outline_entries, printed_pages)
    heading_stats = classify_headings(out, context, threshold=match_threshold, demote_unmatched=demote_unmatched)
    suppressed = suppress_generated_regions(out, rules, min_fraction=float(rules.get("default_exclusion_fraction", 0.35)))
    generated = generated_header_footer_nodes(out, printed_page=printed_page, context=context, rules=rules)
    out["nodes"] = [node for node in out.get("nodes") or [] if not node.get("generated_by_document_structure")]
    for node in generated:
        node["generated_by_document_structure"] = True
    out["nodes"].extend(generated)
    out.setdefault("document_structure", {})["context"] = context
    out["document_structure"]["printed_page"] = printed_page
    out["document_structure"]["ruleset"] = rules.get("id") or rules.get("schema") or "document-structure-rules"
    stats = {
        "page_id": out.get("id"),
        "printed_page": printed_page,
        "heading_matches": heading_stats["matched"],
        "demoted_headings": heading_stats["demoted"],
        "suppressed_nodes": suppressed,
        "generated_nodes": len(generated),
    }
    return out, stats


def pages_from_dataset(data: dict[str, Any]) -> list[tuple[list[Any], dict[str, Any]]]:
    if "nodes" in data:
        return [([], data)]
    pages = []
    for document_index, document in enumerate(data.get("documents") or []):
        for page_index, page in enumerate(document.get("pages") or []):
            pages.append((["documents", document_index, "pages", page_index], page))
    return pages


def replace_at_path(data: dict[str, Any], path: list[Any], value: dict[str, Any]) -> None:
    if not path:
        replacement = copy.deepcopy(value)
        data.clear()
        data.update(replacement)
        return
    target: Any = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value


def apply_structure(
    *,
    input_data: dict[str, Any],
    outline_entries: list[dict[str, Any]],
    page_map: dict[str, Any] | None,
    rules: dict[str, Any],
    match_threshold: float,
    demote_unmatched: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    out = copy.deepcopy(input_data)
    pages = pages_from_dataset(out)
    page_stats = []
    for path, page in pages:
        updated, stats = apply_page_structure(
            page,
            outline_entries=outline_entries,
            page_map=page_map,
            rules=rules,
            match_threshold=match_threshold,
            demote_unmatched=demote_unmatched,
        )
        replace_at_path(out, path, updated)
        page_stats.append(stats)
    summary = {
        "schema": "pdf-training-document-structure-report-v1",
        "pages": len(page_stats),
        "heading_matches": sum(item["heading_matches"] for item in page_stats),
        "demoted_headings": sum(item["demoted_headings"] for item in page_stats),
        "suppressed_nodes": sum(item["suppressed_nodes"] for item in page_stats),
        "generated_nodes": sum(item["generated_nodes"] for item in page_stats),
        "page_stats": page_stats,
    }
    out.setdefault("provenance", {})["document_structure"] = summary
    return out, summary


def extract_footer_page_label(page: dict[str, Any], *, footer_y_fraction: float = 0.82) -> int | None:
    page_h = int(page.get("height") or 0)
    candidates = []
    for node in page.get("nodes") or []:
        if node.get("type") not in TEXT_TYPES:
            continue
        text = node_text(node)
        if not re.fullmatch(r"\d{1,5}", text):
            continue
        box = node.get("bbox") or {}
        if page_h and int(box.get("y") or 0) < page_h * footer_y_fraction:
            continue
        candidates.append((int(box.get("y") or 0), int(box.get("x") or 0), int(text), node.get("id")))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def build_page_map(input_data: dict[str, Any], *, infer_offset: int | None = None) -> dict[str, Any]:
    entries = []
    by_page_id = {}
    for _, page in pages_from_dataset(input_data):
        page_id = str(page.get("id") or "")
        printed = extract_footer_page_label(page)
        source = "footer_text"
        if printed is None and infer_offset is not None and page.get("source_page_index") is not None:
            printed = int(page["source_page_index"]) + infer_offset
            source = "inferred_offset"
        entry = {
            "page_id": page_id,
            "source_page_index": page.get("source_page_index"),
            "output_page_index": page.get("output_page_index"),
            "printed_page": printed,
            "printed_page_source": source if printed is not None else "missing",
        }
        entries.append(entry)
        if page_id:
            by_page_id[page_id] = entry
    return {
        "schema": "pdf-training-page-map-v1",
        "entries": entries,
        "by_page_id": by_page_id,
        "summary": {
            "pages": len(entries),
            "mapped": sum(1 for entry in entries if entry["printed_page"] is not None),
            "missing": sum(1 for entry in entries if entry["printed_page"] is None),
        },
    }


def group_page_by_whitespace(
    page: dict[str, Any],
    *,
    min_gap: int = 48,
    gap_height_factor: float = 3.0,
    min_width_fraction: float = 0.8,
) -> list[dict[str, Any]]:
    """Group page nodes using geometry only and retain every source node."""
    entries = []
    for node in page.get("nodes") or []:
        box = node.get("bbox") or {}
        try:
            x, y = int(box["x"]), int(box["y"])
            w, h = int(box["w"]), int(box["h"])
        except (KeyError, TypeError, ValueError):
            continue
        if w > 0 and h > 0:
            entries.append((x, y, w, h, str(node.get("id") or "")))
    if not entries:
        return []

    heights = sorted(item[3] for item in entries)
    median_height = heights[len(heights) // 2]
    page_x1 = min(item[0] for item in entries)
    page_x2 = max(item[0] + item[2] for item in entries)
    occupied_width = max(1, page_x2 - page_x1)

    bands = []
    for item in sorted(entries, key=lambda value: (value[1], value[0], value[4])):
        x, y, w, h, node_id = item
        if bands and y <= bands[-1]["y2"]:
            band = bands[-1]
            band["y2"] = max(band["y2"], y + h)
            band["x1"] = min(band["x1"], x)
            band["x2"] = max(band["x2"], x + w)
            band["items"].append(item)
        else:
            bands.append({"y1": y, "y2": y + h, "x1": x, "x2": x + w, "items": [item]})

    split_after = set()
    for index in range(len(bands) - 1):
        upper, lower = bands[index], bands[index + 1]
        gap = lower["y1"] - upper["y2"]
        upper_items = [item for band in bands[: index + 1] for item in band["items"]]
        lower_items = [item for band in bands[index + 1 :] for item in band["items"]]
        upper_width = max(item[0] + item[2] for item in upper_items) - min(item[0] for item in upper_items)
        lower_width = max(item[0] + item[2] for item in lower_items) - min(item[0] for item in lower_items)
        neighboring_width = min(upper_width, lower_width)
        if gap >= max(min_gap, median_height * gap_height_factor) and neighboring_width >= occupied_width * min_width_fraction:
            split_after.add(index)

    groups = []
    start = 0
    ends = sorted(split_after)
    if not ends or ends[-1] != len(bands) - 1:
        ends.append(len(bands) - 1)
    for end in ends:
        selected = bands[start : end + 1]
        items = [item for band in selected for item in band["items"]]
        groups.append({
            "id": f"{page.get('id') or 'page'}-structure-group-{len(groups) + 1:02d}",
            "rule": "full_width_vertical_whitespace",
            "bbox": {
                "x": min(item[0] for item in items),
                "y": min(item[1] for item in items),
                "w": max(item[0] + item[2] for item in items) - min(item[0] for item in items),
                "h": max(item[1] + item[3] for item in items) - min(item[1] for item in items),
            },
            "node_ids": [item[4] for item in sorted(items, key=lambda value: (value[1], value[0], value[4]))],
            "parameters": {
                "min_gap": min_gap,
                "gap_height_factor": gap_height_factor,
                "min_width_fraction": min_width_fraction,
            },
        })
        start = end + 1
    return groups


def apply_whitespace_grouping(
    input_data: dict[str, Any],
    *,
    min_gap: int,
    gap_height_factor: float,
    min_width_fraction: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    out = copy.deepcopy(input_data)
    page_stats = []
    for path, page in pages_from_dataset(out):
        groups = group_page_by_whitespace(
            page,
            min_gap=min_gap,
            gap_height_factor=gap_height_factor,
            min_width_fraction=min_width_fraction,
        )
        page["structure_groups"] = groups
        replace_at_path(out, path, page)
        page_stats.append({"page_id": page.get("id"), "groups": len(groups), "group_node_counts": [len(group["node_ids"]) for group in groups]})
    report = {
        "schema": "pdf-training-structural-grouping-report-v1",
        "rule": "full_width_vertical_whitespace",
        "pages": len(page_stats),
        "page_stats": page_stats,
    }
    out.setdefault("provenance", {})["structural_grouping"] = report
    return out, report


def render_report_html(report: dict[str, Any]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('page_id') or ''))}</td>"
        f"<td>{html.escape(str(item.get('printed_page') or ''))}</td>"
        f"<td>{item.get('heading_matches', 0)}</td>"
        f"<td>{item.get('demoted_headings', 0)}</td>"
        f"<td>{item.get('suppressed_nodes', 0)}</td>"
        f"<td>{item.get('generated_nodes', 0)}</td>"
        "</tr>"
        for item in report.get("page_stats") or []
    )
    return f"""<!doctype html>
<meta charset="utf-8">
<title>Document Structure Report</title>
<style>
body {{ margin:0; font-family:Georgia,'Times New Roman',serif; background:#f4ead8; color:#1f180f; }}
header {{ background:#291f14; color:#fff3d8; padding:14px 18px; }}
main {{ padding:18px; }}
table {{ border-collapse:collapse; width:100%; background:#fffaf0; }}
th, td {{ border:1px solid #cdb583; padding:6px 8px; text-align:left; }}
th {{ background:#ead8b4; }}
code {{ background:#eee0bd; padding:2px 4px; }}
</style>
<header><h1>Document Structure Report</h1></header>
<main>
<p><code>{html.escape(json.dumps({k: v for k, v in report.items() if k != 'page_stats'}, ensure_ascii=False, sort_keys=True))}</code></p>
<table>
<thead><tr><th>Page</th><th>Printed</th><th>Heading Matches</th><th>Demoted</th><th>Suppressed</th><th>Generated</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</main>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    page_map_parser = subparsers.add_parser("page-map", help="Build printed-page map from footer text nodes")
    page_map_parser.add_argument("--input", required=True, type=Path)
    page_map_parser.add_argument("--out", required=True, type=Path)
    page_map_parser.add_argument("--infer-offset", type=int)

    apply_parser = subparsers.add_parser("apply", help="Apply outline heading and header/footer rules")
    apply_parser.add_argument("--input", required=True, type=Path)
    apply_parser.add_argument("--outline", required=True, type=Path)
    apply_parser.add_argument("--page-map", type=Path)
    apply_parser.add_argument("--rules", required=True, type=Path)
    apply_parser.add_argument("--out", required=True, type=Path)
    apply_parser.add_argument("--report", type=Path)
    apply_parser.add_argument("--review-html", type=Path)
    apply_parser.add_argument("--match-threshold", type=float, default=0.78)
    apply_parser.add_argument("--demote-unmatched", action="store_true")

    group_parser = subparsers.add_parser("group", help="Group page nodes using geometry-only rules")
    group_parser.add_argument("--input", required=True, type=Path)
    group_parser.add_argument("--out", required=True, type=Path)
    group_parser.add_argument("--report", type=Path)
    group_parser.add_argument("--min-gap", type=int, default=48)
    group_parser.add_argument("--gap-height-factor", type=float, default=3.0)
    group_parser.add_argument("--min-width-fraction", type=float, default=0.8)

    args = parser.parse_args()
    if args.command == "page-map":
        result = build_page_map(load_json(args.input), infer_offset=args.infer_offset)
        write_json(args.out, result)
        print(json.dumps(result["summary"], sort_keys=True))
        return 0

    if args.command == "group":
        updated, report = apply_whitespace_grouping(
            load_json(args.input),
            min_gap=args.min_gap,
            gap_height_factor=args.gap_height_factor,
            min_width_fraction=args.min_width_fraction,
        )
        write_json(args.out, updated)
        if args.report:
            write_json(args.report, report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0

    outline_data = load_json(args.outline)
    outline_entries = outline_data.get("entries") if isinstance(outline_data, dict) else outline_data
    if not isinstance(outline_entries, list):
        raise SystemExit("outline must be a list or an object with entries[]")
    page_map = load_json(args.page_map) if args.page_map else None
    updated, report = apply_structure(
        input_data=load_json(args.input),
        outline_entries=outline_entries,
        page_map=page_map,
        rules=load_json(args.rules),
        match_threshold=args.match_threshold,
        demote_unmatched=args.demote_unmatched,
    )
    write_json(args.out, updated)
    if args.report:
        write_json(args.report, report)
    if args.review_html:
        args.review_html.parent.mkdir(parents=True, exist_ok=True)
        args.review_html.write_text(render_report_html(report), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("pages", "heading_matches", "generated_nodes")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
