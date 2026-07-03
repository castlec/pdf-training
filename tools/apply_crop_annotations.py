#!/usr/bin/env python3
"""Apply saved representative crop annotations to rendered page images.

This reads page PNGs from a rendered source root, then applies the saved crop
box for each page from a crop-annotation root. If a crop box would extend
outside the current canvas, the page is padded with white before cropping so
the requested margins are preserved.

Pages without a saved crop are copied through unchanged.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

import cv2
import numpy as np


def page_sort_key(path: Path) -> Tuple[int, str]:
    suffix = path.stem.rsplit("-", 1)[-1]
    try:
        return int(suffix), path.stem
    except ValueError:
        return 10**9, path.stem


def load_state(state_path: Path) -> Dict[str, Any]:
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text(encoding="utf-8"))


def page_number_from_stem(stem: str) -> int:
    suffix = stem.rsplit("-", 1)[-1]
    try:
        return int(suffix)
    except ValueError:
        raise ValueError(f"Could not parse page number from {stem}")


def med(values: list[float]) -> Optional[float]:
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    if len(values) % 2:
        return float(values[mid])
    return float((values[mid - 1] + values[mid]) / 2.0)


def percentile(values: Iterable[float], pct: float) -> Optional[float]:
    values = sorted(values)
    if not values:
        return None
    if pct <= 0:
        return float(values[0])
    if pct >= 100:
        return float(values[-1])
    if len(values) == 1:
        return float(values[0])
    rank = (len(values) - 1) * (pct / 100.0)
    lo = int(rank)
    hi = min(len(values) - 1, lo + 1)
    frac = rank - lo
    return float(values[lo] * (1.0 - frac) + values[hi] * frac)


def load_frame_model(annotation_root: Path, page_number_offset: int = 0) -> Optional[Dict[str, float]]:
    states: list[Dict[str, Any]] = []
    for state_path in annotation_root.glob("*/state/page-*.json"):
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("crop") and data.get("margins"):
            states.append(data)
    if not states:
        return None
    top = med([float(s["margins"]["top"]) for s in states if s["margins"].get("top") is not None])
    bottom = med([float(s["margins"]["bottom"]) for s in states if s["margins"].get("bottom") is not None])
    frame_w = med([float(s["crop"]["w"]) for s in states if s["crop"].get("w") is not None])
    frame_h = med([float(s["crop"]["h"]) for s in states if s["crop"].get("h") is not None])
    source_w = med(
        [
            float(s["margins"]["left"]) + float(s["crop"]["w"]) + float(s["margins"]["right"])
            for s in states
        ]
    )
    source_h = med(
        [
            float(s["margins"]["top"]) + float(s["crop"]["h"]) + float(s["margins"]["bottom"])
            for s in states
        ]
    )
    outer_margins: list[float] = []
    for state in states:
        margins = state.get("margins") or {}
        page_num = int(state.get("page") or 0)
        page_side = infer_page_side(page_num, page_number_offset)
        if page_side == "right":
            value = margins.get("right")
        else:
            value = margins.get("left")
        if value is not None:
            outer_margins.append(float(value))
    edge_margin = med(outer_margins)
    if None in (top, bottom, frame_w, frame_h, edge_margin, source_w, source_h):
        return None
    return {
        "anchor_edge": "footer",
        "top": float(top),
        "bottom": float(bottom),
        "edge_margin": float(edge_margin),
        "x_bias": 0.0,
        "outer_trim": 0.0,
        "frame_w": float(frame_w),
        "frame_h": float(frame_h),
        "scale_x": 1.0,
        "scale_y": 1.0,
        "source_w": float(source_w),
        "source_h": float(source_h),
    }


def scale_value(value: float, current: float, reference: float) -> int:
    if reference <= 0:
        return int(round(value))
    return int(round(value * (current / reference)))


def pad_for_crop(
    img: np.ndarray,
    crop: Dict[str, int],
    extra_padding: int = 0,
) -> tuple[np.ndarray, Dict[str, int]]:
    h, w = img.shape[:2]
    x = int(crop["x"]) - extra_padding
    y = int(crop["y"]) - extra_padding
    cw = int(crop["w"]) + extra_padding * 2
    ch = int(crop["h"]) + extra_padding * 2

    left_pad = max(0, -x)
    top_pad = max(0, -y)
    right_pad = max(0, x + cw - w)
    bottom_pad = max(0, y + ch - h)

    if left_pad or top_pad or right_pad or bottom_pad:
        border_value = (255, 255, 255) if img.ndim == 3 else 255
        img = cv2.copyMakeBorder(
            img,
            top_pad,
            bottom_pad,
            left_pad,
            right_pad,
            borderType=cv2.BORDER_CONSTANT,
            value=border_value,
        )
        x += left_pad
        y += top_pad

    return img, {"x": x, "y": y, "w": cw, "h": ch}


def apply_crop(img: np.ndarray, crop: Dict[str, int], extra_padding: int = 0) -> np.ndarray:
    padded, crop_rect = pad_for_crop(img, crop, extra_padding=extra_padding)
    x = crop_rect["x"]
    y = crop_rect["y"]
    w = crop_rect["w"]
    h = crop_rect["h"]
    return padded[y : y + h, x : x + w]


def model_crop_for_page(
    img: np.ndarray,
    model: Dict[str, float],
    page_side: str,
) -> Dict[str, int]:
    h, w = img.shape[:2]
    scale_x = float(model.get("scale_x", 1.0))
    scale_y = float(model.get("scale_y", 1.0))
    frame_w = max(1, scale_value(model["frame_w"], w, model["source_w"]))
    frame_h = max(1, scale_value(model["frame_h"], h, model["source_h"]))
    frame_w = max(1, int(round(frame_w * scale_x)))
    frame_h = max(1, int(round(frame_h * scale_y)))
    bottom_margin = scale_value(model["bottom"], h, model["source_h"])
    top = max(0, h - bottom_margin - frame_h)
    edge_margin = scale_value(model["edge_margin"], w, model["source_w"])
    x_bias = scale_value(model.get("x_bias", 0.0), w, model["source_w"])
    outer_trim = scale_value(model.get("outer_trim", 0.0), w, model["source_w"])
    # Place the common frame so the spine-side margin is preserved.
    # Right-hand pages keep the left/spine margin; left-hand pages keep the
    # right/spine margin.
    if page_side == "right":
        left = max(0, edge_margin)
        frame_w = max(1, frame_w - outer_trim)
    else:
        left = max(0, w - frame_w - edge_margin)
        left = max(0, left + outer_trim)
        frame_w = max(1, frame_w - outer_trim)
    left = max(0, left + x_bias)
    return {"x": left, "y": top, "w": frame_w, "h": frame_h}


def infer_page_side(page_num: int, page_number_offset: int = 0) -> str:
    return "right" if (page_num + page_number_offset) % 2 == 1 else "left"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path("deskewed-pages"),
        help="Root containing rendered, deskewed page PNGs",
    )
    parser.add_argument(
        "--crop-root",
        type=Path,
        default=Path("crop-annotations"),
        help="Root containing saved crop annotation state",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("cropped-pages"),
        help="Destination for cropped pages",
    )
    parser.add_argument(
        "--extra-padding",
        type=int,
        default=0,
        help="Extra white padding to add around each crop before extraction",
    )
    parser.add_argument(
        "--pdf",
        action="append",
        default=None,
        help="Only process the named PDF directory (repeatable)",
    )
    parser.add_argument(
        "--pages",
        default=None,
        help="Comma-separated page numbers to process, for example 16,26",
    )
    parser.add_argument(
        "--page-number-offset",
        type=int,
        default=1,
        help="Offset applied to the scan index before determining left/right folio parity",
    )
    parser.add_argument("--x-bias", type=float, default=0.0, help="Horizontal model adjustment in source pixels")
    parser.add_argument("--outer-trim", type=float, default=0.0, help="Pixels to trim from each outer edge")
    parser.add_argument("--scale-x", type=float, default=1.0, help="Horizontal crop scale")
    parser.add_argument("--scale-y", type=float, default=1.0, help="Vertical crop scale")
    parser.add_argument(
        "--skip-model-pages",
        default="1",
        help="Comma-separated pages that require explicit annotations instead of the inferred model; empty means none",
    )
    args = parser.parse_args()

    if not args.source_root.exists():
        raise SystemExit(f"Missing source root: {args.source_root}")
    if not args.crop_root.exists():
        raise SystemExit(f"Missing crop root: {args.crop_root}")

    args.output_root.mkdir(parents=True, exist_ok=True)
    model = load_frame_model(args.crop_root, page_number_offset=args.page_number_offset)
    if model is not None:
        model["x_bias"] = args.x_bias
        model["outer_trim"] = args.outer_trim
        model["scale_x"] = args.scale_x
        model["scale_y"] = args.scale_y
    skip_model_pages = {
        int(value.strip())
        for value in args.skip_model_pages.split(",")
        if value.strip()
    }
    page_filter = None
    if args.pages:
        page_filter = {
            int(part.strip())
            for part in args.pages.split(",")
            if part.strip()
        }

    pdf_dirs = [p for p in sorted(args.source_root.iterdir()) if p.is_dir()]
    if args.pdf:
        wanted = set(args.pdf)
        pdf_dirs = [p for p in pdf_dirs if p.name in wanted]
    if not pdf_dirs:
        raise SystemExit(f"No PDF directories found under {args.source_root}")

    for pdf_dir in pdf_dirs:
        src_pages_dir = pdf_dir / "pages"
        if not src_pages_dir.exists():
            continue
        state_dir = args.crop_root / pdf_dir.name / "state"
        out_pages_dir = args.output_root / pdf_dir.name / "pages"
        out_state_dir = args.output_root / pdf_dir.name / "state"
        out_pages_dir.mkdir(parents=True, exist_ok=True)
        out_state_dir.mkdir(parents=True, exist_ok=True)

        for page_path in sorted(src_pages_dir.glob("*.png"), key=page_sort_key):
            page_num = page_number_from_stem(page_path.stem)
            if page_filter is not None and page_num not in page_filter:
                continue
            state_path = state_dir / f"page-{page_num:03d}.json"
            state = load_state(state_path)
            img = cv2.imread(str(page_path), cv2.IMREAD_COLOR)
            if img is None:
                raise SystemExit(f"Could not read page image: {page_path}")
            crop = state.get("crop")
            if crop is None and model is not None and page_num not in skip_model_pages:
                page_side = infer_page_side(page_num, args.page_number_offset)
                crop = model_crop_for_page(img, model, page_side)
            if crop is not None:
                cropped = apply_crop(img, crop, extra_padding=args.extra_padding)
            else:
                cropped = img
            page_side = infer_page_side(page_num, args.page_number_offset)
            margins = None
            if crop is not None:
                margins = {
                    "top": int(round(crop["y"])),
                    "bottom": int(round(img.shape[0] - crop["y"] - crop["h"])),
                    "left": int(round(crop["x"])),
                    "right": int(round(img.shape[1] - crop["x"] - crop["w"])),
                }
            out_path = out_pages_dir / page_path.name
            cv2.imwrite(str(out_path), cropped)
            out_state_path = out_state_dir / f"{page_path.stem}.json"
            out_state_path.write_text(
                json.dumps(
                    {
                        "pdf": pdf_dir.name,
                        "page": page_num,
                        "source_page": str(page_path),
                        "crop_applied": bool(crop),
                        "model_applied": bool(
                            crop is not None
                            and state.get("crop") is None
                            and model is not None
                            and page_num not in skip_model_pages
                        ),
                        "extra_padding": args.extra_padding,
                        "crop": crop,
                        "page_side": page_side,
                        "anchor_edge": state.get("anchor_edge") or "footer",
                        "side_hint": page_side,
                        "margins": margins,
                        "frame_model": model,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"{pdf_dir.name}: wrote {out_path.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
