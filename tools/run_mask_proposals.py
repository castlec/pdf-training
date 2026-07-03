#!/usr/bin/env python3
"""Run page-mask proposals with Codex CLI using ChatGPT sign-in.

This wrapper does not use an API key. It prepares a review image for each page
by overlaying the current masks on top of the page image, then runs
`codex exec` with that image attached.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image


DEFAULT_REPO_ROOT = Path.cwd()
DEFAULT_ANNOTATION_ROOT = Path("annotations")
DEFAULT_OUTPUT_ROOT = Path("mask-suggestions")
DEFAULT_CLI = "codex"
DEFAULT_MODEL = "gpt-5.5"

SYSTEM_PROMPT = """You are reviewing a scanned technical-book page.

The attached image is a diagnostic composite:
- the underlying page image is visible
- text mask regions are overlaid in blue
- equation mask regions are overlaid in red
- figure/image mask regions are overlaid in green

Your job:
- propose the best mask regions for the page
- return JSON only
- do not edit files
- prefer slightly over-marking rather than missing content
- keep regions practical and reviewable

Output JSON schema:
{
  "source_image": "<filename>",
  "regions": [
    {
      "kind": "equation" | "figure" | "uncertain",
      "bbox": {"x": 0, "y": 0, "w": 0, "h": 0},
      "confidence": 0.0,
      "notes": "short reason"
    }
  ],
  "summary": "one short sentence"
}
"""


def parse_page_spec(spec: str) -> list[int]:
    pages: set[int] = set()
    for chunk in spec.split(","):
        part = chunk.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start = int(start_s)
            end = int(end_s)
            if end < start:
                raise ValueError(f"Invalid page range: {part}")
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    if not pages:
        raise ValueError("No pages selected")
    return sorted(pages)


def page_image_path(source_root: Path, pdf: str, page: int) -> Path:
    candidates = [
        source_root / pdf / "pages" / f"{pdf}-{page:02d}.png",
        source_root / pdf / "pages" / f"{pdf}-{page}.png",
        source_root / pdf / "pages" / f"page-{page:03d}.png",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def mask_path(annotation_root: Path, pdf: str, kind: str, page: int) -> Path:
    return annotation_root / pdf / "masks" / kind / f"page-{page:03d}.png"


def overlay_mask(base: Image.Image, mask: Image.Image, rgba: tuple[int, int, int, int]) -> Image.Image:
    base_rgba = base.convert("RGBA")
    mask_rgba = mask.convert("RGBA")
    overlay = Image.new("RGBA", base_rgba.size, (0, 0, 0, 0))
    overlay_px = overlay.load()
    mask_px = mask_rgba.load()
    for y in range(base_rgba.height):
        for x in range(base_rgba.width):
            r, g, b, a = mask_px[x, y]
            if a and (r, g, b) != (0, 0, 0):
                overlay_px[x, y] = rgba
    return Image.alpha_composite(base_rgba, overlay)


def build_composite(
    page_image: Path,
    text_mask: Path | None,
    equation_mask: Path | None,
    image_mask: Path | None,
    output_path: Path,
) -> None:
    base = Image.open(page_image).convert("RGBA")
    if text_mask is not None and text_mask.exists():
        base = overlay_mask(base, Image.open(text_mask), (0, 128, 255, 80))
    if equation_mask is not None and equation_mask.exists():
        base = overlay_mask(base, Image.open(equation_mask), (255, 70, 70, 80))
    if image_mask is not None and image_mask.exists():
        base = overlay_mask(base, Image.open(image_mask), (40, 200, 120, 80))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    base.save(output_path)


def write_schema(schema_path: Path) -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["source_image", "regions", "summary"],
        "properties": {
            "source_image": {"type": "string"},
            "regions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "bbox", "confidence", "notes"],
                    "properties": {
                        "kind": {"type": "string", "enum": ["equation", "figure", "uncertain"]},
                        "bbox": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["x", "y", "w", "h"],
                            "properties": {
                                "x": {"type": "integer"},
                                "y": {"type": "integer"},
                                "w": {"type": "integer"},
                                "h": {"type": "integer"},
                            },
                        },
                        "confidence": {"type": "number"},
                        "notes": {"type": "string"},
                    },
                },
            },
            "summary": {"type": "string"},
        },
    }
    schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")


def codex_command(
    cli: str,
    prompt: str,
    image_path: Path,
    schema_path: Path,
    output_path: Path,
    model: str,
    repo_root: Path,
) -> list[str]:
    command = [
        cli,
        "exec",
        "--cd",
        str(repo_root),
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--image",
        str(image_path),
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
    ]
    command.extend(["--model", model])
    command.append(prompt)
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, help="PDF/document stem, e.g. volume-1")
    parser.add_argument("--pages", required=True, help="Page list or range, e.g. 31 or 1-3,7,9")
    parser.add_argument(
        "--annotation-root",
        type=Path,
        default=DEFAULT_ANNOTATION_ROOT,
        help="Root containing the active annotation cache, including page PNGs and masks",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT, help="Root for proposal JSON output")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Codex model for this proposal run")
    parser.add_argument("--codex-bin", default=DEFAULT_CLI, help="Codex CLI binary")
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--dry-run", action="store_true", help="Print the resolved command and exit")
    args = parser.parse_args()

    try:
        pages = parse_page_spec(args.pages)
    except ValueError as exc:
        raise SystemExit(str(exc))

    output_dir = args.output_root / args.pdf
    composite_dir = output_dir / "composites"
    schema_path = output_dir / "mask-proposal.schema.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    write_schema(schema_path)

    page_results: list[dict[str, Any]] = []
    for page in pages:
        page_img = page_image_path(args.annotation_root, args.pdf, page)
        text_mask = mask_path(args.annotation_root, args.pdf, "text", page)
        equation_mask = mask_path(args.annotation_root, args.pdf, "equation", page)
        image_mask = mask_path(args.annotation_root, args.pdf, "image", page)
        if not page_img.exists():
            raise SystemExit(f"Missing page image: {page_img}")
        if not text_mask.exists():
            text_mask = None
        if not equation_mask.exists():
            equation_mask = None
        if not image_mask.exists():
            image_mask = None

        composite_path = composite_dir / f"{page_img.stem}-composite.png"
        proposal_path = output_dir / f"page-{page:03d}.mask-proposal.json"
        build_composite(page_img, text_mask, equation_mask, image_mask, composite_path)

        prompt = (
            SYSTEM_PROMPT
            + f"\nSource page: {page_img.name}\n"
            + "Return only JSON that matches the schema."
        )
        command = codex_command(
            args.codex_bin,
            prompt,
            composite_path,
            schema_path,
            proposal_path,
            args.model,
            args.repo_root,
        )

        page_results.append(
            {
                "page": page,
                "page_image": str(page_img),
                "text_mask": str(text_mask) if text_mask else None,
                "equation_mask": str(equation_mask) if equation_mask else None,
                "image_mask": str(image_mask) if image_mask else None,
                "composite": str(composite_path),
                "proposal": str(proposal_path),
                "command": command,
            }
        )

        if args.dry_run:
            print(json.dumps(page_results[-1], ensure_ascii=False, indent=2))
            continue

        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            return result.returncode

    summary = {
        "pdf": args.pdf,
        "pages": pages,
        "annotation_root": str(args.annotation_root),
        "output_dir": str(output_dir),
        "results": page_results,
    }
    (output_dir / f"page-set-{pages[0]:03d}-{pages[-1]:03d}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
