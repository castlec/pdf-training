#!/usr/bin/env python3
"""Extract a native PDF into pdf-training renderable pages.

Poppler supplies the positioned text and embedded raster objects. The output
also retains per-page PNG and SVG provenance assets for visual comparison and
future vector extraction. This intentionally does not infer vector rules or
diagrams from a raster image.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from PIL import Image


def run(command: list[str]) -> None:
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def bbox(element: ET.Element) -> dict[str, int]:
    x = float(element.attrib.get("left", 0))
    y = float(element.attrib.get("top", 0))
    right = float(element.attrib.get("left", 0)) + float(element.attrib.get("width", 0))
    bottom = float(element.attrib.get("top", 0)) + float(element.attrib.get("height", 0))
    return {"x": round(x), "y": round(y), "w": max(1, round(right - x)), "h": max(1, round(bottom - y))}


def text_value(element: ET.Element) -> str:
    return "".join(element.itertext()).replace("\xa0", " ")


def font_family(value: str) -> str:
    value = re.sub(r"^[A-Z]{6}\\+", "", value)
    return f"'{value}', Arial, sans-serif"


def decoded_asset_is_zero(path: Path) -> bool:
    """Detect image exports with no visible decoded samples."""
    try:
        with Image.open(path) as image:
            extrema = image.convert("RGBA").getextrema()
    except (OSError, ValueError):
        return False
    color_extrema = extrema[:3] if len(extrema) > 2 else extrema
    return all(low == 0 and high == 0 for low, high in color_extrema)


def classify_text(node: ET.Element, font: dict[str, str], page_height: int) -> str:
    y = float(node.attrib.get("top", 0))
    size = float(font.get("size", node.attrib.get("height", 16)))
    bold = any(child.tag.rsplit("}", 1)[-1] == "b" for child in node.iter())
    if y > page_height - 90:
        return "page_number" if size >= 17 else "footer"
    if bold and (size >= 20 or y < 220):
        return "heading"
    if y < 220:
        return "header"
    return "body"


def extract_horizontal_rules(svg_path: Path, scale: float) -> list[dict[str, Any]]:
    """Extract direct SVG filled rectangles that represent horizontal rules."""
    root = ET.parse(svg_path).getroot()
    rules: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int, int]] = set()
    for path in root.iter():
        if path.tag.rsplit("}", 1)[-1] != "path":
            continue
        style = path.attrib.get("style", "")
        if "fill:rgb" not in style or "stroke:none" not in style:
            continue
        points = [(float(x), float(y)) for x, y in re.findall(
            r"(?<![A-Za-z])(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)", path.attrib.get("d", "")
        )]
        unique: list[tuple[float, float]] = []
        for point in points:
            if point not in unique:
                unique.append(point)
        if len(unique) != 4 or len(points) < 4:
            continue
        xs = [x for x, _ in unique]
        ys = [y for _, y in unique]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        # Glyph definitions and tiny punctuation live near the SVG origin.
        if min(xs) < 20 or min(ys) < 20 or width <= 5 or height > 2.5:
            continue
        box = (round(min(xs) * scale), round(min(ys) * scale), round(width * scale), max(1, round(height * scale)))
        if box in seen:
            continue
        seen.add(box)
        rules.append({"bbox": {"x": box[0], "y": box[1], "w": box[2], "h": box[3]}, "source": {"svg": str(svg_path), "bbox": {"x": box[0], "y": box[1], "w": box[2], "h": box[3]}}})
    return rules



def native_text_pages(pdf: Path) -> list[list[ET.Element]]:
    """Extract ordered text lines from the PDF content stream via pdfminer."""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTChar, LTTextContainer

    pages: list[list[ET.Element]] = []
    for layout in extract_pages(str(pdf)):
        page_items: list[ET.Element] = []

        def visit(item: object) -> None:
            if isinstance(item, LTTextContainer):
                for line in item:
                    if not isinstance(line, LTTextContainer):
                        continue
                    chars = [child for child in line if isinstance(child, LTChar)]
                    value = line.get_text().rstrip("\\n")
                    if not value.strip() or not chars:
                        continue
                    x0, y0, x1, y1 = line.bbox
                    scale_x = 892.0 / layout.width
                    scale_y = 1262.0 / layout.height
                    math_symbols = set("√−=⋅()")
                    has_math_operator = any(char.get_text().strip() in math_symbols for char in chars)
                    baseline = max(char.y0 for char in chars)
                    has_vertical_script = any(abs(char.y0 - baseline) > 0.8 for char in chars)
                    preserve_positions = has_math_operator and (
                        len(value.strip()) < 40 or any(symbol in value for symbol in ("=", "√", "⋅"))
                    )
                    source_chars = chars if preserve_positions else [None]
                    for source_char in source_chars:
                        if source_char is None:
                            char_text = value
                            char_x0, char_y0, char_x1, char_y1 = x0, y0, x1, y1
                            char_font = chars[0]
                        else:
                            char_text = source_char.get_text()
                            if not char_text.strip():
                                continue
                            char_x0, char_y0, char_x1, char_y1 = source_char.bbox
                            char_font = source_char
                        node = ET.Element("text", {
                            "top": str(round((layout.height - char_y1) * scale_y)),
                            "left": str(round(char_x0 * scale_x)),
                            "width": str(max(1, round((char_x1 - char_x0) * scale_x))),
                            "height": str(max(1, round((char_y1 - char_y0) * scale_y))),
                            "font": "native",
                        })
                        node.text = char_text
                        if preserve_positions:
                            node.attrib["render_source"] = "true"
                        if "bold" in str(getattr(char_font, "fontname", "")).lower():
                            ET.SubElement(node, "b")
                        node.attrib["native_font"] = str(getattr(char_font, "fontname", "Arial"))
                        node.attrib["native_size"] = str(round(char_font.size * scale_y, 2))
                        page_items.append(node)
                return
            for child in getattr(item, "_objs", []):
                visit(child)

        for item in layout:
            visit(item)
        page_items.sort(key=lambda item: (float(item.attrib["top"]), float(item.attrib["left"])))
        pages.append(page_items)
    return pages


def extract(pdf: Path, out_dir: Path, document_id: str) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    assets = out_dir / "assets"
    pages_dir = out_dir / "pages"
    vectors_dir = out_dir / "vectors"
    assets.mkdir(exist_ok=True)
    pages_dir.mkdir(exist_ok=True)
    vectors_dir.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="native-pdf-") as tmp_name:
        tmp = Path(tmp_name)
        prefix = tmp / "source"
        run(["pdftohtml", "-xml", "-hidden", "-noroundcoord", "-zoom", "1.5", "-q", str(pdf), str(prefix)])
        root = ET.parse(tmp / "source.xml").getroot()
        native_pages = native_text_pages(pdf)
        pages: list[dict[str, Any]] = []
        for page_index, page in enumerate(root.findall("page")):
            page_id = f"{document_id}-p{page_index + 1:03d}"
            width = int(round(float(page.attrib["width"])))
            height = int(round(float(page.attrib["height"])))
            page_png = pages_dir / f"page-{page_index + 1:03d}.png"
            run(["pdftoppm", "-f", str(page_index + 1), "-l", str(page_index + 1), "-png", "-singlefile",
                 "-scale-to-x", str(width), "-scale-to-y", str(height), str(pdf), str(page_png.with_suffix(""))])
            page_svg = vectors_dir / f"page-{page_index + 1:03d}.svg"
            run(["pdftocairo", "-f", str(page_index + 1), "-l", str(page_index + 1), "-svg", str(pdf), str(page_svg.with_suffix(""))])
            generated_svg = page_svg.with_suffix("")
            if generated_svg.exists():
                generated_svg.rename(page_svg)

            fonts = {item.attrib["id"]: item.attrib for item in page.findall("fontspec")}
            nodes: list[dict[str, Any]] = []
            for image_index, image in enumerate(page.findall("image"), start=1):
                source = Path(image.attrib["src"])
                asset = assets / f"page-{page_index + 1:03d}-image-{image_index:03d}{source.suffix.lower()}"
                shutil.copy2(source, asset)
                zero_decoded = decoded_asset_is_zero(asset)
                nodes.append({
                    "id": f"{page_id}-image-{image_index:03d}", "type": "image", "class": "image",
                    "bbox": bbox(image), "asset": str(asset.resolve()), "transparent_background": True,
                    "render_suppressed": zero_decoded,
                    "suppress_reason": "zero_decoded_image_asset" if zero_decoded else None,
                    "source": {"bbox": bbox(image), "asset": str(asset.resolve())},
                })
            for text_index, text_node in enumerate(native_pages[page_index], start=1):
                value = text_value(text_node)
                if not value.strip():
                    continue
                font = fonts.get(text_node.attrib.get("font", ""), {})
                if text_node.attrib.get("font") == "native":
                    font = {"family": text_node.attrib.get("native_font", "Arial"), "size": text_node.attrib.get("native_size", text_node.attrib.get("height", "16"))}
                style_class = classify_text(text_node, font, height)
                is_bold = any(child.tag.rsplit("}", 1)[-1] == "b" for child in text_node.iter())
                nodes.append({
                    "id": f"{page_id}-text-{text_index:04d}", "type": "text_fragment", "class": style_class,
                    "bbox": bbox(text_node), "text": value, "content_source": "native_pdf",
                    "render_source": text_node.attrib.get("render_source") == "true",
                    "font_size_px": float(font.get("size", text_node.attrib.get("height", 16))),
                    "font_family": font_family(font.get("family", "Arial")),
                    "font_weight": "700" if is_bold else "400",
                    "source": {"bbox": bbox(text_node), "font": font},
                })
            for rule_index, rule in enumerate(extract_horizontal_rules(page_svg, width / 595.32), start=1):
                nodes.append({
                    "id": f"{page_id}-rule-{rule_index:03d}", "type": "rule", "class": "horizontal_rule",
                    "bbox": rule["bbox"], "source": rule["source"],
                })
            nodes.sort(key=lambda item: (item["bbox"]["y"], item["bbox"]["x"], item["id"]))
            pages.append({
                "schema": "pdf-training-renderable-page-v1", "id": page_id, "document_id": document_id,
                "source_page_index": page_index, "output_page_index": page_index, "width": width, "height": height,
                "source_page_image": str(page_png.resolve()), "source_vector_asset": str(page_svg.resolve()),
                "nodes": nodes, "summary": {"nodes": len(nodes), "text_fragments": sum(n["type"] == "text_fragment" for n in nodes),
                                             "images": sum(n["type"] == "image" for n in nodes), "rules": sum(n["type"] == "rule" for n in nodes)},
            })
            (out_dir / "renderable").mkdir(exist_ok=True)
            (out_dir / "renderable" / f"page-{page_index + 1:03d}.json").write_text(json.dumps(pages[-1], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    dataset = {"schema": "pdf-training-renderable-dataset-v1", "name": document_id, "private_source": True, "source_pdf": str(pdf.resolve()), "documents": [{"id": document_id, "pages": pages}]}
    (out_dir / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--document-id", default="native-pdf")
    args = parser.parse_args()
    dataset = extract(args.pdf, args.out_dir, args.document_id)
    print(json.dumps({"pages": len(dataset["documents"][0]["pages"]), "out_dir": str(args.out_dir.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
