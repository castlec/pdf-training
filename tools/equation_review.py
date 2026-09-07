#!/usr/bin/env python3
"""Build and merge equation review datasets from renderable metadata.

This tool is intentionally manifest-driven. Private book projects provide page
images, equation bboxes, model endpoints, and correction files; this repository
keeps only the reusable review workflow.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
import subprocess
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageEnhance


SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")


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

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_id(value: str) -> str:
    return SAFE_ID.sub("-", value)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def box_from_dict(value: dict[str, Any]) -> Box:
    return Box(int(value["x"]), int(value["y"]), int(value["w"]), int(value["h"]))


def clamp_box(box: Box, width: int, height: int) -> Box:
    x = max(0, min(box.x, max(0, width - 1)))
    y = max(0, min(box.y, max(0, height - 1)))
    x2 = max(x + 1, min(box.x2, width))
    y2 = max(y + 1, min(box.y2, height))
    return Box(x, y, x2 - x, y2 - y)


def inflate(box: Box, padding: int, width: int, height: int) -> Box:
    return clamp_box(Box(box.x - padding, box.y - padding, box.w + 2 * padding, box.h + 2 * padding), width, height)


def intersection_area(a: Box, b: Box) -> int:
    x1 = max(a.x, b.x)
    y1 = max(a.y, b.y)
    x2 = min(a.x2, b.x2)
    y2 = min(a.y2, b.y2)
    return max(0, x2 - x1) * max(0, y2 - y1)


def iou(a: Box, b: Box) -> float:
    hit = intersection_area(a, b)
    union = a.w * a.h + b.w * b.h - hit
    return hit / union if union else 0.0


def resolve_path(value: str | Path, base: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


def relative_path(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def extract_equation_items(
    source: dict[str, Any],
    *,
    source_base: Path,
    page_images: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return normalized equation review items.

    Accepted inputs:
    - ``items[]`` manifests with explicit ``source_image`` and ``bbox``.
    - renderable page JSON with ``nodes[]`` containing equation nodes.
    """

    if isinstance(source.get("items"), list):
        items = []
        for index, item in enumerate(source["items"], start=1):
            if not item.get("id"):
                item = {**item, "id": f"equation-{index:04d}"}
            items.append(item)
        return items

    page_id = str(source.get("id") or "page")
    source_image = source.get("source_image") or (page_images or {}).get(page_id)
    if not source_image:
        raise ValueError(f"no source image supplied for page {page_id!r}")

    items = []
    for index, node in enumerate(source.get("nodes") or [], start=1):
        if node.get("type") != "equation":
            continue
        node_id = str(node.get("id") or f"{page_id}-eq-{index:04d}")
        items.append(
            {
                "id": node_id,
                "page_id": page_id,
                "source_image": source_image,
                "bbox": node["bbox"],
                "latex": node.get("latex") or "",
                "render_policy": node.get("render_policy"),
                "content_source": node.get("content_source"),
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


def save_crops(
    item: dict[str, Any],
    *,
    source_base: Path,
    report_root: Path,
    contrast: float,
    sharpness: float,
    context_padding: int,
) -> tuple[dict[str, Any], Box, Box]:
    source_path = resolve_path(item["source_image"], source_base)
    with Image.open(source_path).convert("RGB") as source:
        width, height = source.size
        box = clamp_box(box_from_dict(item["bbox"]), width, height)
        context_box = inflate(box, context_padding, width, height)
        crop = preprocess_crop(source.crop((box.x, box.y, box.x2, box.y2)), contrast=contrast, sharpness=sharpness)
        context = preprocess_crop(
            source.crop((context_box.x, context_box.y, context_box.x2, context_box.y2)),
            contrast=contrast,
            sharpness=sharpness,
        )

    crop_rel = Path("assets") / "equations" / f"{safe_id(str(item['id']))}.png"
    context_rel = Path("assets") / "contexts" / f"{safe_id(str(item['id']))}.png"
    crop_path = report_root / crop_rel
    context_path = report_root / context_rel
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(crop_path)
    context.save(context_path)
    assets = {
        "equation_crop": crop_rel.as_posix(),
        "context_crop": context_rel.as_posix(),
        "source_image": relative_path(source_path, source_base),
    }
    return assets, box, context_box


def tesseract_ocr(image_path: Path, *, lang: str, psm: int, timeout: int) -> dict[str, Any]:
    cmd = ["tesseract", str(image_path), "stdout", "--psm", str(psm), "--dpi", "300", "-l", lang]
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, check=False, timeout=timeout)
    except FileNotFoundError:
        return {"text": "", "error": "tesseract executable not found", "command": cmd}
    except Exception as exc:
        return {"text": "", "error": str(exc), "command": cmd}
    return {
        "text": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "returncode": proc.returncode,
        "command": cmd,
    }


def image_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def parse_sectioned_response(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        match = re.fullmatch(r"([A-Z_ ]{3,}):\s*(.*)", line)
        if match:
            current = match.group(1).strip().lower().replace(" ", "_")
            sections[current] = []
            if match.group(2):
                sections[current].append(match.group(2).strip())
            continue
        if current:
            sections[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def default_prompt(item: dict[str, Any], ocr_text: str) -> str:
    hint = ocr_text.strip() or "(empty)"
    return f"""
You are transcribing one exact crop from a technical PDF page.
The attached image is the entire authority. Do not infer or copy anything from outside it.

Transcribe every visible mathematical expression in this exact crop as LaTeX.
Include units and equation numbers only if visibly inside the crop.
Do not include surrounding prose that is not visibly inside the crop.

Item id: {item.get("id")}
OCR hint, possibly wrong:
{hint}

Rules:
- Output only math visibly present in the crop.
- If the crop contains multiple visible equation lines, preserve line breaks in LATEX.
- Do not include neighboring equations that are not visible.
- Do not wrap the result in `$`, `$$`, `\\[`, or `\\]`.
- If the image shows an equals sign with a dot above it, output `\\doteq`; do not output `\\div`.
- If the crop is blank or not math, leave LATEX empty and explain in NOTES.

Output format:
SCOPE_COMPLETE: yes|no|unclear
CONTAINS_PROSE: yes|no|unclear
BBOX_ADJUSTMENT: none|manual_review
EQUATION_NUMBER: equation number or none

LATEX:
raw LaTeX only

NOTES:
brief notes
""".strip()


def ollama_chat(
    *,
    host: str,
    model: str,
    prompt: str,
    images: list[Path],
    timeout: int,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [image_b64(path) for path in images],
            }
        ],
    }
    req = urllib.request.Request(
        host.rstrip("/") + "/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


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
    prompt = default_prompt(item, ocr_text)
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
    ocr_lang: str,
    tesseract: bool,
    psm: int,
    timeout: int,
    contrast: float,
    sharpness: float,
    context_padding: int,
    limit: int,
    force: bool,
) -> dict[str, Any]:
    source = load_json(input_path)
    items = extract_equation_items(source, source_base=source_base)
    if limit:
        items = items[:limit]
    results = []
    for index, item in enumerate(items, start=1):
        print(f"{index}/{len(items)} {item['id']}", flush=True)
        item_json = report_root / "items" / f"{safe_id(str(item['id']))}.json"
        if item_json.exists() and not force:
            results.append(load_json(item_json))
            continue
        assets, box, context_box = save_crops(
            item,
            source_base=source_base,
            report_root=report_root,
            contrast=contrast,
            sharpness=sharpness,
            context_padding=context_padding,
        )
        crop_path = report_root / assets["equation_crop"]
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
            "schema": "pdf-training-equation-review-item-v1",
            "id": item["id"],
            "page_id": item.get("page_id"),
            "bbox": box.as_dict(),
            "context_bbox": context_box.as_dict(),
            "assets": assets,
            "source_latex": item.get("latex") or "",
            "render_policy": item.get("render_policy"),
            "ocr": ocr,
            "vision": vision,
            "latex": {
                "vision_value": vision.get("latex") or "",
                "correction": item.get("correction"),
                "status": "needs_review",
            },
            "processed_at": now_iso(),
        }
        write_json(item_json, result)
        results.append(result)
    summary = {
        "schema": "pdf-training-equation-review-v1",
        "created_at": now_iso(),
        "input": str(input_path),
        "total": len(items),
        "processed": len(results),
        "counts": {
            "vision_latex": sum(1 for item in results if item.get("vision", {}).get("latex")),
            "vision_error": sum(1 for item in results if item.get("vision", {}).get("error")),
            "ocr_text": sum(1 for item in results if item.get("ocr", {}).get("text")),
        },
        "items": [
            {
                "id": item["id"],
                "page_id": item.get("page_id"),
                "bbox": item["bbox"],
                "assets": item["assets"],
                "ocr_text": item.get("ocr", {}).get("text") or "",
                "vision_latex": item.get("vision", {}).get("latex") or "",
                "source_latex": item.get("source_latex") or "",
                "correction": item.get("latex", {}).get("correction"),
                "item_json": f"items/{safe_id(str(item['id']))}.json",
            }
            for item in results
        ],
    }
    write_json(report_root / "summary.json", summary)
    write_review_html(report_root, summary)
    return summary


def latex_preview(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return '<span class="empty">No LaTeX</span>'
    return "\\[" + html.escape(text) + "\\]"


def write_review_html(report_root: Path, summary: dict[str, Any]) -> None:
    rows = []
    for item in summary["items"]:
        correction = item.get("correction") or item.get("vision_latex") or item.get("source_latex") or ""
        rows.append(
            f"""
<section class="card" id="{html.escape(str(item['id']))}">
  <h2>{html.escape(str(item['id']))}</h2>
  <p><b>Page:</b> {html.escape(str(item.get('page_id') or ''))}
  <b>Box:</b> <code>{html.escape(json.dumps(item['bbox']))}</code></p>
  <div class="grid">
    <div>
      <img src="{html.escape(item['assets']['equation_crop'])}" alt="equation crop">
      <h3>OCR</h3>
      <pre>{html.escape(item.get('ocr_text') or '')}</pre>
      <h3>Vision Render</h3>
      <div class="math">{latex_preview(item.get('vision_latex') or '')}</div>
    </div>
    <div>
      <h3>Vision LaTeX</h3>
      <textarea readonly>{html.escape(item.get('vision_latex') or '')}</textarea>
      <h3>LaTeX Correction</h3>
      <textarea>{html.escape(correction)}</textarea>
      <h3>Correction Render</h3>
      <div class="math">{latex_preview(correction)}</div>
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
<title>Equation Review</title>
<script>
window.MathJax = {{tex: {{displayMath: [['\\\\[', '\\\\]']]}}}};
</script>
<script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
<style>
body {{ margin:0; background:#1f1a14; color:#231d15; font-family:Georgia,'Times New Roman',serif; }}
header {{ position:sticky; top:0; z-index:2; background:#2b2116; color:#fff5df; padding:14px 18px; }}
main {{ display:grid; gap:16px; padding:18px; }}
.summary {{ display:flex; flex-wrap:wrap; gap:10px; }}
.pill {{ border:1px solid #99763c; border-radius:999px; padding:4px 9px; background:#3a2c1c; }}
.card {{ background:#fff7e6; border:1px solid #d2b982; padding:14px; box-shadow:0 8px 24px #0007; }}
.grid {{ display:grid; grid-template-columns:minmax(260px, .9fr) minmax(260px, 1.1fr); gap:16px; }}
img {{ max-width:100%; background:white; border:1px solid #c7ae78; }}
textarea {{ width:100%; min-height:82px; font:14px/1.35 ui-monospace, SFMono-Regular, Menlo, monospace; }}
pre {{ white-space:pre-wrap; background:#211b13; color:#f8ead0; padding:10px; border-radius:8px; }}
.math {{ background:white; border:1px solid #d2c29e; min-height:45px; padding:8px; overflow-x:auto; }}
.hint {{ color:#6a5a45; }}
.empty {{ color:#7b6a52; font-style:italic; }}
code {{ background:#eee0bd; padding:2px 4px; }}
@media (max-width: 900px) {{ .grid {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<header>
  <h1>Equation Review</h1>
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
    corrections: dict[str, dict[str, Any]] = {}
    if not corrections_root.exists():
        return corrections
    for path in sorted(corrections_root.glob("*.json")):
        data = load_json(path)
        item_id = str(data.get("id") or path.stem)
        corrections[item_id] = data
    return corrections


def correction_value(item_id: str, review_item: dict[str, Any], corrections: dict[str, dict[str, Any]]) -> str:
    correction = corrections.get(item_id) or corrections.get(safe_id(item_id))
    if correction and str(correction.get("correction") or "").strip():
        return str(correction["correction"]).strip()
    latex = review_item.get("latex") or {}
    if str(latex.get("correction") or "").strip():
        return str(latex["correction"]).strip()
    if str(review_item.get("vision", {}).get("latex") or "").strip():
        return str(review_item["vision"]["latex"]).strip()
    return str(review_item.get("source_latex") or "").strip()


def find_review_match(
    node: dict[str, Any],
    review_items: list[dict[str, Any]],
    *,
    min_iou: float,
) -> dict[str, Any] | None:
    node_id = str(node.get("id") or "")
    for item in review_items:
        if str(item.get("id") or "") == node_id:
            return item
    if "bbox" not in node:
        return None
    node_box = box_from_dict(node["bbox"])
    best: tuple[float, dict[str, Any]] | None = None
    for item in review_items:
        if item.get("page_id") and node.get("page_id") and item.get("page_id") != node.get("page_id"):
            continue
        score = iou(node_box, box_from_dict(item["bbox"]))
        if best is None or score > best[0]:
            best = (score, item)
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
    review_items = []
    for item in summary.get("items") or []:
        item_path = summary_path.parent / str(item.get("item_json") or "")
        review_items.append(load_json(item_path) if item_path.exists() else item)
    corrections = load_corrections(corrections_root)

    matched = 0
    for node in metadata.get("nodes") or []:
        if node.get("type") != "equation":
            continue
        match = find_review_match(node, review_items, min_iou=min_iou)
        if not match:
            continue
        latex = correction_value(str(match["id"]), match, corrections)
        if not latex:
            continue
        node["latex"] = latex
        node["content_source"] = "equation_review"
        node.setdefault("review", {})["equation_review_item"] = match["id"]
        node["review"]["merged_at"] = now_iso()
        matched += 1
    metadata.setdefault("provenance", {})["equation_review_merge"] = {
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

    build = subparsers.add_parser("build", help="Build equation crops, OCR/vision output, and review HTML")
    build.add_argument("--input", required=True, type=Path, help="Manifest or renderable page JSON")
    build.add_argument("--report-root", required=True, type=Path)
    build.add_argument("--source-base", type=Path, default=Path("."))
    build.add_argument("--provider", choices=["none", "ollama-chat"], default="none")
    build.add_argument("--host", default="http://127.0.0.1:11434")
    build.add_argument("--model", default="")
    build.add_argument("--tesseract", action="store_true")
    build.add_argument("--ocr-lang", default="eng")
    build.add_argument("--psm", type=int, default=6)
    build.add_argument("--timeout", type=int, default=120)
    build.add_argument("--contrast", type=float, default=1.8)
    build.add_argument("--sharpness", type=float, default=1.3)
    build.add_argument("--context-padding", type=int, default=28)
    build.add_argument("--limit", type=int, default=0)
    build.add_argument("--force", action="store_true")

    merge = subparsers.add_parser("merge", help="Merge reviewed equation LaTeX back into renderable metadata")
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
            ocr_lang=args.ocr_lang,
            tesseract=args.tesseract,
            psm=args.psm,
            timeout=args.timeout,
            contrast=args.contrast,
            sharpness=args.sharpness,
            context_padding=args.context_padding,
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
