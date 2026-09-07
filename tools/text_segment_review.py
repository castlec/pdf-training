#!/usr/bin/env python3
"""Build and merge text-segment review datasets from renderable metadata.

Private projects provide page images and renderable metadata. This tool crops
only text fragments or derived text-band segments, optionally runs OCR and/or a
vision model on those exact crop images, and merges reviewed text back into the
renderable dataset.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance

sys.path.insert(0, str(Path(__file__).resolve().parent))
from equation_review import (  # noqa: E402
    Box,
    box_from_dict,
    clamp_box,
    iou,
    load_json,
    now_iso,
    ollama_chat,
    parse_sectioned_response,
    relative_path,
    resolve_path,
    safe_id,
    tesseract_ocr,
    write_json,
)


def intersection(a: Box, b: Box) -> Box | None:
    x1 = max(a.x, b.x)
    y1 = max(a.y, b.y)
    x2 = min(a.x2, b.x2)
    y2 = min(a.y2, b.y2)
    if x2 <= x1 or y2 <= y1:
        return None
    return Box(x1, y1, x2 - x1, y2 - y1)


def vertical_overlap_ratio(a: Box, b: Box) -> float:
    overlap = max(0, min(a.y2, b.y2) - max(a.y, b.y))
    return overlap / max(1, min(a.h, b.h))


def union_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def page_source_image(page: dict[str, Any], page_images: dict[str, str] | None = None) -> str:
    page_id = str(page.get("id") or "page")
    image = page.get("source_image") or (page_images or {}).get(page_id)
    if not image:
        raise ValueError(f"no source image supplied for page {page_id!r}")
    return str(image)


def obstacle_nodes(page: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        node
        for node in page.get("nodes") or []
        if node.get("type") in {"equation", "image", "diagram_component", "table"} and node.get("bbox")
    ]


def mask_refs_for_box(
    box: Box,
    obstacles: list[dict[str, Any]],
    *,
    min_vertical_overlap_ratio: float,
) -> list[dict[str, Any]]:
    refs = []
    for node in obstacles:
        obstacle_box = box_from_dict(node["bbox"])
        hit = intersection(box, obstacle_box)
        if not hit:
            continue
        ratio = vertical_overlap_ratio(box, obstacle_box)
        if ratio < min_vertical_overlap_ratio:
            continue
        refs.append(
            {
                "kind": node.get("type"),
                "id": node.get("id"),
                "bbox_page": hit.as_dict(),
                "bbox_segment": Box(hit.x - box.x, hit.y - box.y, hit.w, hit.h).as_dict(),
                "vertical_overlap_ratio": round(ratio, 4),
            }
        )
    return sorted(refs, key=lambda item: (item["bbox_page"]["x"], item["bbox_page"]["y"]))


def segment_boxes_from_band(
    band: Box,
    refs: list[dict[str, Any]],
    *,
    min_width: int,
    gutter: int,
) -> list[Box]:
    blocked = []
    for ref in refs:
        hit = box_from_dict(ref["bbox_page"])
        start = max(band.x, hit.x - gutter)
        end = min(band.x2, hit.x2 + gutter)
        if end > start:
            blocked.append((start, end))
    if not blocked:
        return [band] if band.w >= min_width else []

    segments = []
    cursor = band.x
    for start, end in union_ranges(blocked):
        if start - cursor >= min_width:
            segments.append(Box(cursor, band.y, start - cursor, band.h))
        cursor = max(cursor, end)
    if band.x2 - cursor >= min_width:
        segments.append(Box(cursor, band.y, band.x2 - cursor, band.h))
    return segments


def extract_text_items(
    page: dict[str, Any],
    *,
    source_base: Path,
    min_width: int,
    gutter: int,
    min_vertical_overlap_ratio: float,
) -> list[dict[str, Any]]:
    source_image = page_source_image(page)
    page_id = str(page.get("id") or "page")
    obstacles = obstacle_nodes(page)
    items = []

    text_fragments = [node for node in page.get("nodes") or [] if node.get("type") == "text_fragment" and node.get("bbox")]
    if text_fragments:
        for index, node in enumerate(text_fragments, start=1):
            box = box_from_dict(node["bbox"])
            refs = mask_refs_for_box(box, obstacles, min_vertical_overlap_ratio=min_vertical_overlap_ratio)
            items.append(
                {
                    "id": str(node.get("id") or f"{page_id}-seg-{index:04d}"),
                    "page_id": page_id,
                    "source_image": source_image,
                    "bbox": box.as_dict(),
                    "source_text": node.get("text") or "",
                    "target_node": node.get("id"),
                    "mask_refs": refs,
                    "source_base": str(source_base),
                }
            )
        return items

    bands = [node for node in page.get("nodes") or [] if node.get("type") == "text_band" and node.get("bbox")]
    for band_index, node in enumerate(bands, start=1):
        band = box_from_dict(node["bbox"])
        refs = mask_refs_for_box(band, obstacles, min_vertical_overlap_ratio=min_vertical_overlap_ratio)
        for seg_index, segment in enumerate(segment_boxes_from_band(band, refs, min_width=min_width, gutter=gutter), start=1):
            seg_refs = mask_refs_for_box(segment, obstacles, min_vertical_overlap_ratio=min_vertical_overlap_ratio)
            items.append(
                {
                    "id": f"{node.get('id') or f'{page_id}-line-{band_index:04d}'}-seg-{seg_index:02d}",
                    "page_id": page_id,
                    "source_image": source_image,
                    "bbox": segment.as_dict(),
                    "source_text": "",
                    "target_node": node.get("id"),
                    "parent_band": node.get("id"),
                    "mask_refs": seg_refs,
                    "source_base": str(source_base),
                }
            )
    return items


def preprocess_crop(image: Image.Image, *, contrast: float, sharpness: float) -> Image.Image:
    crop = image.convert("L")
    if contrast != 1:
        crop = ImageEnhance.Contrast(crop).enhance(contrast)
    if sharpness != 1:
        crop = ImageEnhance.Sharpness(crop).enhance(sharpness)
    return crop.convert("RGB")


def mask_crop_obstacles(crop: Image.Image, refs: list[dict[str, Any]], *, placeholder: str | None) -> Image.Image:
    if not refs:
        return crop
    out = crop.copy()
    draw = ImageDraw.Draw(out)
    for ref in refs:
        local = box_from_dict(ref["bbox_segment"])
        draw.rectangle((local.x, local.y, local.x2, local.y2), fill="white")
        if placeholder and ref.get("kind") == "equation":
            repeated = placeholder
            while len(repeated) * 10 < max(1, local.w):
                repeated += "-" + placeholder
            draw.text((local.x + 1, local.y + max(0, local.h // 4)), repeated, fill="black")
    return out


def save_segment_crop(
    item: dict[str, Any],
    *,
    source_base: Path,
    report_root: Path,
    contrast: float,
    sharpness: float,
    placeholder: str | None,
) -> tuple[dict[str, Any], Box]:
    source_path = resolve_path(item["source_image"], source_base)
    with Image.open(source_path).convert("RGB") as page_image:
        box = clamp_box(box_from_dict(item["bbox"]), page_image.width, page_image.height)
        crop = page_image.crop((box.x, box.y, box.x2, box.y2))
    crop = preprocess_crop(crop, contrast=contrast, sharpness=sharpness)
    crop = mask_crop_obstacles(crop, item.get("mask_refs") or [], placeholder=placeholder)
    crop_rel = Path("assets") / "segments" / f"{safe_id(str(item['id']))}.png"
    crop_path = report_root / crop_rel
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(crop_path)
    return {"segment_crop": crop_rel.as_posix(), "source_image": relative_path(source_path, source_base)}, box


def segment_prompt(item: dict[str, Any], ocr_text: str) -> str:
    source_hint = str(item.get("source_text") or "").strip() or "(empty)"
    refs = item.get("mask_refs") or []
    return f"""
You are transcribing one exact text-segment crop from a technical PDF page.
The attached image is the authority. Return only text visibly present in the crop.

The page language may be non-English. Preserve diacritics, punctuation, capitalization, and visible leader dots.
If the crop is blank, noise, a page-number fragment, or not meaningful text, set ACTION to delete and leave TEXT empty.
If artificial placeholders such as ZXO, ZXO-ZXO, or repeated placeholder-like tokens appear, omit them from TEXT.
If an equation placeholder corresponds to one of the MASK_REFS, place [[REF:id]] where that object belongs in TEXT_WITH_REFS.

Item id: {item.get("id")}
Source text hint, possibly stale:
{source_hint}
OCR hint, possibly wrong:
{ocr_text or "(empty)"}
MASK_REFS:
{json.dumps(refs, ensure_ascii=False)}

Output format:
ACTION: transcribe|delete|human_review
CONFIDENCE: high|medium|low

TEXT:
plain visible text only

TEXT_WITH_REFS:
visible text with [[REF:id]] anchors where applicable

NOTES:
brief visual reason
""".strip()


def query_vision(
    item: dict[str, Any],
    *,
    crop_path: Path,
    provider: str,
    host: str,
    model: str,
    timeout: int,
    ocr_text: str,
) -> dict[str, Any]:
    if provider == "none":
        return {"skipped": True}
    prompt = segment_prompt(item, ocr_text)
    try:
        if provider == "ollama-chat":
            data = ollama_chat(host=host, model=model, prompt=prompt, images=[crop_path], timeout=timeout)
            content = (data.get("message") or {}).get("content") or ""
            parsed = parse_sectioned_response(content)
            parsed["raw_response"] = content
            parsed["provider"] = provider
            parsed["model"] = model
            return parsed
    except Exception as exc:
        return {"error": str(exc), "provider": provider, "model": model}
    return {"error": f"unsupported provider: {provider}", "provider": provider, "model": model}


def build_review(
    *,
    input_path: Path,
    report_root: Path,
    source_base: Path,
    provider: str,
    host: str,
    model: str,
    tesseract: bool,
    ocr_lang: str,
    psm: int,
    timeout: int,
    contrast: float,
    sharpness: float,
    placeholder: str | None,
    min_width: int,
    gutter: int,
    min_vertical_overlap_ratio: float,
    limit: int,
    force: bool,
) -> dict[str, Any]:
    page = load_json(input_path)
    items = extract_text_items(
        page,
        source_base=source_base,
        min_width=min_width,
        gutter=gutter,
        min_vertical_overlap_ratio=min_vertical_overlap_ratio,
    )
    if limit:
        items = items[:limit]
    results = []
    for index, item in enumerate(items, start=1):
        print(f"{index}/{len(items)} {item['id']}", flush=True)
        item_json = report_root / "items" / f"{safe_id(str(item['id']))}.json"
        if item_json.exists() and not force:
            results.append(load_json(item_json))
            continue
        assets, box = save_segment_crop(
            item,
            source_base=source_base,
            report_root=report_root,
            contrast=contrast,
            sharpness=sharpness,
            placeholder=placeholder,
        )
        crop_path = report_root / assets["segment_crop"]
        ocr = tesseract_ocr(crop_path, lang=ocr_lang, psm=psm, timeout=timeout) if tesseract else {"skipped": True, "text": ""}
        vision = query_vision(
            item,
            crop_path=crop_path,
            provider=provider,
            host=host,
            model=model,
            timeout=timeout,
            ocr_text=str(ocr.get("text") or ""),
        )
        result = {
            "schema": "pdf-training-text-segment-review-item-v1",
            "id": item["id"],
            "page_id": item.get("page_id"),
            "target_node": item.get("target_node"),
            "parent_band": item.get("parent_band"),
            "bbox": box.as_dict(),
            "assets": assets,
            "source_text": item.get("source_text") or "",
            "mask_refs": item.get("mask_refs") or [],
            "ocr": ocr,
            "vision": vision,
            "text": {
                "vision_value": vision.get("text") or "",
                "vision_value_with_refs": vision.get("text_with_refs") or "",
                "correction": None,
                "status": "needs_review",
            },
            "processed_at": now_iso(),
        }
        write_json(item_json, result)
        results.append(result)
    summary = {
        "schema": "pdf-training-text-segment-review-v1",
        "created_at": now_iso(),
        "input": str(input_path),
        "total": len(items),
        "processed": len(results),
        "counts": {
            "vision_text": sum(1 for item in results if item.get("vision", {}).get("text")),
            "vision_error": sum(1 for item in results if item.get("vision", {}).get("error")),
            "ocr_text": sum(1 for item in results if item.get("ocr", {}).get("text")),
            "has_mask_refs": sum(1 for item in results if item.get("mask_refs")),
        },
        "items": [
            {
                "id": item["id"],
                "page_id": item.get("page_id"),
                "target_node": item.get("target_node"),
                "bbox": item["bbox"],
                "assets": item["assets"],
                "source_text": item.get("source_text") or "",
                "ocr_text": item.get("ocr", {}).get("text") or "",
                "vision_text": item.get("vision", {}).get("text") or "",
                "vision_text_with_refs": item.get("vision", {}).get("text_with_refs") or "",
                "correction": item.get("text", {}).get("correction"),
                "mask_refs": item.get("mask_refs") or [],
                "item_json": f"items/{safe_id(str(item['id']))}.json",
            }
            for item in results
        ],
    }
    write_json(report_root / "summary.json", summary)
    write_review_html(report_root, summary)
    return summary


def write_review_html(report_root: Path, summary: dict[str, Any]) -> None:
    rows = []
    for item in summary.get("items") or []:
        proposed = item.get("vision_text_with_refs") or item.get("vision_text") or item.get("ocr_text") or item.get("source_text") or ""
        rows.append(
            f"""
<section class="card" id="{html.escape(str(item['id']))}">
  <h2>{html.escape(str(item['id']))}</h2>
  <p><b>Page:</b> {html.escape(str(item.get('page_id') or ''))}
  <b>Target:</b> {html.escape(str(item.get('target_node') or ''))}
  <b>Box:</b> <code>{html.escape(json.dumps(item['bbox']))}</code></p>
  <div class="grid">
    <div>
      <img src="{html.escape(item['assets']['segment_crop'])}" alt="text segment crop">
      <h3>Mask Refs</h3>
      <pre>{html.escape(json.dumps(item.get('mask_refs') or [], ensure_ascii=False, indent=2))}</pre>
    </div>
    <div>
      <h3>Source Text</h3>
      <pre>{html.escape(item.get('source_text') or '')}</pre>
      <h3>OCR</h3>
      <pre>{html.escape(item.get('ocr_text') or '')}</pre>
    </div>
    <div>
      <h3>Vision Text</h3>
      <textarea readonly>{html.escape(item.get('vision_text') or '')}</textarea>
      <h3>Correction</h3>
      <textarea>{html.escape(proposed)}</textarea>
      <p class="hint">Save corrections as <code>corrections/{safe_id(str(item['id']))}.json</code>.</p>
    </div>
  </div>
</section>
"""
        )
    html_text = f"""<!doctype html>
<html lang="und">
<head>
<meta charset="utf-8">
<title>Text Segment Review</title>
<style>
body {{ margin:0; background:#1f1a14; color:#231d15; font-family:Georgia,'Times New Roman',serif; }}
header {{ position:sticky; top:0; z-index:2; background:#2b2116; color:#fff5df; padding:14px 18px; }}
main {{ display:grid; gap:16px; padding:18px; }}
.summary {{ display:flex; flex-wrap:wrap; gap:10px; }}
.pill {{ border:1px solid #99763c; border-radius:999px; padding:4px 9px; background:#3a2c1c; }}
.card {{ background:#fff7e6; border:1px solid #d2b982; padding:14px; box-shadow:0 8px 24px #0007; }}
.grid {{ display:grid; grid-template-columns:minmax(260px,.8fr) minmax(260px,1fr) minmax(260px,1fr); gap:16px; }}
img {{ max-width:100%; background:white; border:1px solid #c7ae78; }}
textarea {{ width:100%; min-height:82px; font:14px/1.35 ui-monospace, SFMono-Regular, Menlo, monospace; }}
pre {{ white-space:pre-wrap; background:#211b13; color:#f8ead0; padding:10px; border-radius:8px; overflow:auto; }}
.hint {{ color:#6a5a45; }}
code {{ background:#eee0bd; padding:2px 4px; }}
@media (max-width: 1000px) {{ .grid {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<header>
  <h1>Text Segment Review</h1>
  <div class="summary">
    <span class="pill">processed {summary['processed']}/{summary['total']}</span>
    {''.join(f'<span class="pill">{html.escape(k)}: {html.escape(str(v))}</span>' for k, v in summary.get('counts', {}).items())}
  </div>
</header>
<main>
{''.join(rows)}
</main>
</body>
</html>
"""
    (report_root / "review.html").write_text(html_text, encoding="utf-8")


def load_corrections(corrections_root: Path) -> dict[str, dict[str, Any]]:
    corrections = {}
    if not corrections_root.exists():
        return corrections
    for path in sorted(corrections_root.glob("*.json")):
        data = load_json(path)
        corrections[str(data.get("id") or path.stem)] = data
    return corrections


def best_text(item: dict[str, Any], corrections: dict[str, dict[str, Any]]) -> str:
    correction = corrections.get(str(item.get("id"))) or corrections.get(safe_id(str(item.get("id"))))
    if correction and str(correction.get("text") or correction.get("correction") or "").strip():
        return str(correction.get("text") or correction.get("correction")).strip()
    text = item.get("text") or {}
    if str(text.get("correction") or "").strip():
        return str(text["correction"]).strip()
    if str(item.get("vision", {}).get("text_with_refs") or "").strip():
        return str(item["vision"]["text_with_refs"]).strip()
    if str(item.get("vision", {}).get("text") or "").strip():
        return str(item["vision"]["text"]).strip()
    if str(item.get("ocr", {}).get("text") or "").strip():
        return str(item["ocr"]["text"]).strip()
    return str(item.get("source_text") or "").strip()


def find_node(page: dict[str, Any], item: dict[str, Any], *, min_iou: float) -> dict[str, Any] | None:
    target = item.get("target_node")
    if target:
        for node in page.get("nodes") or []:
            if node.get("id") == target:
                return node
    item_box = box_from_dict(item["bbox"])
    best: tuple[float, dict[str, Any]] | None = None
    for node in page.get("nodes") or []:
        if node.get("type") not in {"text_fragment", "text_band", "caption", "heading", "header", "footer"}:
            continue
        if not node.get("bbox"):
            continue
        score = iou(item_box, box_from_dict(node["bbox"]))
        if best is None or score > best[0]:
            best = (score, node)
    if best and best[0] >= min_iou:
        return best[1]
    return None


def merge_review(
    *,
    metadata_path: Path,
    summary_path: Path,
    corrections_root: Path,
    out_path: Path,
    min_iou: float,
) -> dict[str, Any]:
    metadata = load_json(metadata_path)
    summary = load_json(summary_path)
    corrections = load_corrections(corrections_root)
    review_items = []
    for item in summary.get("items") or []:
        item_path = summary_path.parent / str(item.get("item_json") or "")
        review_items.append(load_json(item_path) if item_path.exists() else item)

    matched = 0
    for item in review_items:
        text = best_text(item, corrections)
        if not text:
            continue
        node = find_node(metadata, item, min_iou=min_iou)
        if not node:
            continue
        node["text"] = text
        node["content_source"] = "text_segment_review"
        node.setdefault("review", {})["text_segment_review_item"] = item["id"]
        node["review"]["merged_at"] = now_iso()
        matched += 1
    metadata.setdefault("provenance", {})["text_segment_review_merge"] = {
        "summary": str(summary_path),
        "corrections": str(corrections_root),
        "merged_at": now_iso(),
        "matched": matched,
    }
    write_json(out_path, metadata)
    return {"matched": matched, "out": str(out_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build text segment crops and review HTML")
    build.add_argument("--input", required=True, type=Path, help="Renderable page JSON")
    build.add_argument("--report-root", required=True, type=Path)
    build.add_argument("--source-base", type=Path, default=Path("."))
    build.add_argument("--provider", choices=["none", "ollama-chat"], default="none")
    build.add_argument("--host", default="http://127.0.0.1:11434")
    build.add_argument("--model", default="")
    build.add_argument("--tesseract", action="store_true")
    build.add_argument("--ocr-lang", default="eng")
    build.add_argument("--psm", type=int, default=7)
    build.add_argument("--timeout", type=int, default=120)
    build.add_argument("--contrast", type=float, default=1.8)
    build.add_argument("--sharpness", type=float, default=1.3)
    build.add_argument("--placeholder", default=None, help="Optional equation placeholder text written into masked regions")
    build.add_argument("--min-width", type=int, default=16)
    build.add_argument("--gutter", type=int, default=3)
    build.add_argument("--min-vertical-overlap-ratio", type=float, default=0.45)
    build.add_argument("--limit", type=int, default=0)
    build.add_argument("--force", action="store_true")

    merge = subparsers.add_parser("merge", help="Merge reviewed text back into renderable metadata")
    merge.add_argument("--metadata", required=True, type=Path)
    merge.add_argument("--summary", required=True, type=Path)
    merge.add_argument("--corrections-root", required=True, type=Path)
    merge.add_argument("--out", required=True, type=Path)
    merge.add_argument("--min-iou", type=float, default=0.80)

    args = parser.parse_args()
    if args.command == "build":
        summary = build_review(
            input_path=args.input,
            report_root=args.report_root,
            source_base=args.source_base,
            provider=args.provider,
            host=args.host,
            model=args.model,
            tesseract=args.tesseract,
            ocr_lang=args.ocr_lang,
            psm=args.psm,
            timeout=args.timeout,
            contrast=args.contrast,
            sharpness=args.sharpness,
            placeholder=args.placeholder,
            min_width=args.min_width,
            gutter=args.gutter,
            min_vertical_overlap_ratio=args.min_vertical_overlap_ratio,
            limit=args.limit,
            force=args.force,
        )
        print(json.dumps({"report": str(args.report_root), "processed": summary["processed"]}, sort_keys=True))
        return 0
    result = merge_review(
        metadata_path=args.metadata,
        summary_path=args.summary,
        corrections_root=args.corrections_root,
        out_path=args.out,
        min_iou=args.min_iou,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
