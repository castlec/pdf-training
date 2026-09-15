#!/usr/bin/env python3
"""Overlay positioned renderable text nodes onto a PDF artwork layer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pikepdf
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas


SCALE = 1 / 1.5  # native extraction canvas is pdftohtml zoom 1.5
FALLBACK_SIZE_SCALE = 0.885  # match DejaVu x-height to the source Myriad Pro subset
REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
ITALIC = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def font_paths(font_dir: Path | None) -> dict[str, str]:
    if not font_dir:
        return {"regular": REGULAR, "bold": BOLD, "italic": ITALIC, "math": MATH}
    return {
        "regular": str(font_dir / "MyriadPro-Regular.ttf"),
        "bold": str(font_dir / "MyriadPro-Bold.ttf"),
        "italic": str(font_dir / "MyriadPro-It.ttf"),
        "math": str(font_dir / "CambriaMath.ttf"),
    }


def select_font(node: dict, registered: dict[str, str]) -> str:
    family = str(node.get("font_family") or "")
    prefix = "fallback_" if node.get("translation_status") == "translated" else "source_"
    if "CambriaMath" in family:
        return registered["fallback_math"]
    if "MyriadPro-It" in family or str(node.get("font_style")) == "italic":
        return registered[prefix + "italic"]
    if "MyriadPro-Bold" in family or str(node.get("font_weight")) == "700":
        return registered[prefix + "bold"]
    return registered[prefix + "regular"]


def build_overlay(dataset: dict, path: Path, *, font_dir: Path | None = None) -> None:
    paths = font_paths(font_dir)
    pdfmetrics.registerFont(TTFont("OverlaySourceRegular", paths["regular"]))
    pdfmetrics.registerFont(TTFont("OverlaySourceBold", paths["bold"]))
    pdfmetrics.registerFont(TTFont("OverlaySourceItalic", paths["italic"]))
    pdfmetrics.registerFont(TTFont("OverlaySourceMath", paths["math"]))
    pdfmetrics.registerFont(TTFont("OverlayFallbackRegular", REGULAR))
    pdfmetrics.registerFont(TTFont("OverlayFallbackBold", BOLD))
    pdfmetrics.registerFont(TTFont("OverlayFallbackItalic", ITALIC))
    pdfmetrics.registerFont(TTFont("OverlayFallbackMath", MATH))
    registered = {
        "source_regular": "OverlaySourceRegular",
        "source_bold": "OverlaySourceBold",
        "source_italic": "OverlaySourceItalic",
        "source_math": "OverlaySourceMath",
        "fallback_regular": "OverlayFallbackRegular",
        "fallback_bold": "OverlayFallbackBold",
        "fallback_italic": "OverlayFallbackItalic",
        "fallback_math": "OverlayFallbackMath",
    }
    canvas = Canvas(str(path), pagesize=(595.32, 841.92), pageCompression=1)
    for document in dataset.get("documents", []):
        for page in document.get("pages", []):
            for node in page.get("nodes", []):
                if node.get("type") != "text_fragment":
                    continue
                box = node["bbox"]
                text = str(node.get("text") or "").replace("\r", "").replace("\n", "").replace(chr(0x1D465), "x")
                if not text:
                    continue
                font = select_font(node, registered)
                size = float(node.get("font_size_px") or 16) * SCALE
                if node.get("translation_status") == "translated":
                    size *= FALLBACK_SIZE_SCALE
                x = float(box["x"]) * SCALE
                y = 841.92 - (float(box["y"]) + float(box["h"])) * SCALE + size * 0.12
                canvas.setFont(font, size)
                canvas.setFillColorRGB(0, 0, 0)
                canvas.drawString(x, y, text)
            canvas.showPage()
    canvas.save()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--artwork", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--overlay-pdf", type=Path)
    parser.add_argument("--font-dir", type=Path, help="Directory containing extracted source font subsets")
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    overlay_path = args.overlay_pdf or args.out.with_name(args.out.stem + "-text-overlay.pdf")
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    build_overlay(dataset, overlay_path, font_dir=args.font_dir)
    with pikepdf.Pdf.open(args.artwork) as artwork, pikepdf.Pdf.open(overlay_path) as overlay:
        for target, layer in zip(artwork.pages, overlay.pages):
            target.add_overlay(layer, push_stack=True, shrink=False, expand=False)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        artwork.save(args.out)
    print(json.dumps({"output": str(args.out.resolve()), "overlay": str(overlay_path.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
