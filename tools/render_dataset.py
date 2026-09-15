#!/usr/bin/env python3
"""Render every page in a pdf-training renderable dataset for review."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_page_html import render_comparison_html, render_page_html  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--show-boxes", action="store_true")
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    links: list[str] = []
    for document in dataset.get("documents", []):
        for page in document.get("pages", []):
            page_id = str(page["id"])
            out = args.out_dir / f"{page_id}.html"
            comparison = args.out_dir / f"{page_id}-comparison.html"
            out.write_text(render_page_html(page, out, show_boxes=args.show_boxes), encoding="utf-8")
            source = page.get("source_page_image")
            if source:
                comparison.write_text(
                    render_comparison_html(page, comparison, source_image=Path(source), show_boxes=args.show_boxes),
                    encoding="utf-8",
                )
                links.append(f'<li><a href="{html.escape(comparison.name)}">{html.escape(page_id)} comparison</a></li>')
            else:
                links.append(f'<li><a href="{html.escape(out.name)}">{html.escape(page_id)}</a></li>')
    index = "<!doctype html><meta charset='utf-8'><title>Dataset render</title><h1>Dataset render</h1><ul>" + "".join(links) + "</ul>"
    (args.out_dir / "index.html").write_text(index, encoding="utf-8")
    print(json.dumps({"pages": len(links), "index": str((args.out_dir / "index.html").resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
