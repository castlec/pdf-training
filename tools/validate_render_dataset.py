#!/usr/bin/env python3
"""Validate renderable datasets against a baseline."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


VALUABLE_TYPES = {"text_band", "text_fragment", "equation", "image", "caption", "heading", "table"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def node_map(page_or_dataset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    if "nodes" in page_or_dataset:
        for node in page_or_dataset.get("nodes") or []:
            nodes[str(node["id"])] = node
        return nodes
    for document in page_or_dataset.get("documents") or []:
        for page in document.get("pages") or []:
            for node in page.get("nodes") or []:
                nodes[str(node["id"])] = node
    return nodes


def bbox_delta(a: dict[str, Any], b: dict[str, Any]) -> int:
    return max(abs(int(a.get(key, 0)) - int(b.get(key, 0))) for key in ("x", "y", "w", "h"))


def class_range_findings(nodes: dict[str, dict[str, Any]], class_ranges: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for node in nodes.values():
        cls = node.get("class")
        if not cls or cls not in class_ranges:
            continue
        height = int((node.get("bbox") or {}).get("h", 0))
        spec = class_ranges[cls] or {}
        min_h = spec.get("min_h")
        max_h = spec.get("max_h")
        if min_h is not None and height < int(min_h):
            findings.append({"id": node["id"], "type": "too_short_for_class", "class": cls, "height": height, "min_h": int(min_h)})
        if max_h is not None and height > int(max_h):
            findings.append({"id": node["id"], "type": "too_tall_for_class", "class": cls, "height": height, "max_h": int(max_h)})
    return findings


def validate(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    bbox_tolerance: int = 2,
    class_ranges: dict[str, Any] | None = None,
) -> dict[str, Any]:
    baseline_nodes = node_map(baseline)
    candidate_nodes = node_map(candidate)

    removed = [node for node_id, node in baseline_nodes.items() if node_id not in candidate_nodes]
    added = [node for node_id, node in candidate_nodes.items() if node_id not in baseline_nodes]
    changed = []
    for node_id, old in baseline_nodes.items():
        new = candidate_nodes.get(node_id)
        if not new:
            continue
        changes: dict[str, Any] = {}
        if old.get("type") != new.get("type"):
            changes["type"] = {"old": old.get("type"), "new": new.get("type")}
        if old.get("class") != new.get("class"):
            changes["class"] = {"old": old.get("class"), "new": new.get("class")}
        old_bbox = old.get("bbox") or {}
        new_bbox = new.get("bbox") or {}
        if bbox_delta(old_bbox, new_bbox) > bbox_tolerance:
            changes["bbox"] = {"old": old_bbox, "new": new_bbox, "delta": bbox_delta(old_bbox, new_bbox)}
        if changes:
            changed.append({"id": node_id, "changes": changes})

    regressions = [
        {"id": node["id"], "type": "removed_valuable_node", "node_type": node.get("type"), "bbox": node.get("bbox")}
        for node in removed
        if node.get("type") in VALUABLE_TYPES
    ]
    suspect_nodes = class_range_findings(candidate_nodes, class_ranges or {})

    return {
        "schema": "pdf-training-validation-report-v1",
        "baseline": baseline.get("id") or baseline.get("name"),
        "candidate": candidate.get("id") or candidate.get("name"),
        "summary": {
            "baseline_nodes": len(baseline_nodes),
            "candidate_nodes": len(candidate_nodes),
            "removed_nodes": len(removed),
            "added_nodes": len(added),
            "changed_nodes": len(changed),
            "suspect_nodes": len(suspect_nodes),
            "regressions": len(regressions),
            "status": "pass" if not regressions and not suspect_nodes else "review",
        },
        "removed_nodes": removed,
        "added_nodes": added,
        "changed_nodes": changed,
        "suspect_nodes": suspect_nodes,
        "regressions": regressions,
    }


def render_review_html(report: dict[str, Any]) -> str:
    def table_rows(items: list[dict[str, Any]]) -> str:
        return "".join(
            "<tr>"
            f"<td>{html.escape(str(item.get('id', '')))}</td>"
            f"<td><code>{html.escape(json.dumps(item, ensure_ascii=False))}</code></td>"
            "</tr>"
            for item in items
        )

    return f"""<!doctype html>
<meta charset="utf-8">
<title>Renderable Dataset Validation</title>
<style>
body {{ margin:0; font-family: Georgia, 'Times New Roman', serif; background:#f6efe2; color:#1d160f; }}
header {{ background:#271b10; color:#fff4dd; padding:12px 18px; }}
main {{ padding:18px; }}
table {{ border-collapse:collapse; width:100%; margin:16px 0; background:#fffaf0; }}
td, th {{ border:1px solid #d7c199; padding:6px 8px; vertical-align:top; }}
th {{ background:#efe1c4; text-align:left; }}
code {{ white-space:pre-wrap; }}
</style>
<header><h1>Validation: {html.escape(report['summary']['status'])}</h1></header>
<main>
<p><code>{html.escape(json.dumps(report['summary'], sort_keys=True))}</code></p>
<h2>Regressions</h2><table><thead><tr><th>ID</th><th>Data</th></tr></thead><tbody>{table_rows(report['regressions'])}</tbody></table>
<h2>Suspect Nodes</h2><table><thead><tr><th>ID</th><th>Data</th></tr></thead><tbody>{table_rows(report['suspect_nodes'])}</tbody></table>
<h2>Changed Nodes</h2><table><thead><tr><th>ID</th><th>Data</th></tr></thead><tbody>{table_rows(report['changed_nodes'])}</tbody></table>
</main>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--review-html", type=Path)
    parser.add_argument("--bbox-tolerance", type=int, default=2)
    parser.add_argument("--class-ranges", type=Path)
    args = parser.parse_args()

    ranges = load_json(args.class_ranges) if args.class_ranges else {}
    report = validate(
        load_json(args.baseline),
        load_json(args.candidate),
        bbox_tolerance=args.bbox_tolerance,
        class_ranges=ranges,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.review_html:
        args.review_html.parent.mkdir(parents=True, exist_ok=True)
        args.review_html.write_text(render_review_html(report), encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))
    return 1 if report["regressions"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
