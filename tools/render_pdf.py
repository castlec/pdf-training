#!/usr/bin/env python3
"""Print renderable pages as clean, one-page-per-page PDFs."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_page_html import render_page_html  # noqa: E402


def printable_html(page: dict, html_path: Path) -> str:
    rendered = render_page_html(page, html_path, show_boxes=False)
    match = re.search(r'<section class="page"[^>]*>.*?</section>', rendered, flags=re.S)
    if not match:
        raise ValueError(f"rendered page is missing page section: {page.get('id')}")
    return rendered.replace(
        "<body>",
        "<body style=\"margin:0; padding:0; background:white;\"><style>@page { size: A4; margin: 0; } body { overflow: hidden; } .page { margin: 0 !important; }</style>",
    ).replace(
        rendered[rendered.find('<div class="toolbar"'):rendered.find('<main class="page-wrap">')],
        "",
    ).replace(
        '<main class="page-wrap">',
        '<main class="page-wrap" style="padding:0; margin:0;">',
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    work_dir = args.work_dir or args.out.parent / "pdf-pages-clean"
    work_dir.mkdir(parents=True, exist_ok=True)
    page_paths: list[Path] = []
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for document in dataset.get("documents", []):
            for page_data in document.get("pages", []):
                page_id = str(page_data["id"])
                html_path = work_dir / f"{page_id}.html"
                pdf_path = work_dir / f"{page_id}.pdf"
                html_path.write_text(printable_html(page_data, html_path), encoding="utf-8")
                page = browser.new_page(viewport={"width": 892, "height": 1262})
                page.goto(html_path.as_uri())
                page.pdf(path=str(pdf_path), format="A4", scale=0.889, print_background=True,
                         margin={"top": "0in", "right": "0in", "bottom": "0in", "left": "0in"})
                page.close()
                page_paths.append(pdf_path)
        browser.close()
    subprocess.run(["pdfunite", *(str(path) for path in page_paths), str(args.out)], check=True)
    print(json.dumps({"pages": len(page_paths), "out": str(args.out.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
