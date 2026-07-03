#!/usr/bin/env python3
"""Apply a shared crop model and saved per-page origins to page images.

This renderer uses only the stored dataset:
- one shared crop size
- one shared margin model
- one per-page origin
- one per-page left/right side

There is no detection or inference during rendering. If the dataset is
incomplete, the script fails so the missing pages can be fixed in the editor.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import cv2
import numpy as np


def page_sort_key(path: Path) -> Tuple[int, str]:
    suffix = path.stem.rsplit("-", 1)[-1]
    try:
        return int(suffix), path.stem
    except ValueError:
        return 10**9, path.stem


def page_number_from_stem(stem: str) -> int:
    suffix = stem.rsplit("-", 1)[-1]
    try:
        return int(suffix)
    except ValueError:
        raise ValueError(f"Could not parse page number from {stem}")


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def pad_for_crop(img, crop: Dict[str, int], extra_padding: int = 0, border_value=None):
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
        if border_value is None:
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


def apply_crop(img, crop: Dict[str, int], extra_padding: int = 0, border_value=None):
    padded, crop_rect = pad_for_crop(img, crop, extra_padding=extra_padding, border_value=border_value)
    x = crop_rect["x"]
    y = crop_rect["y"]
    w = crop_rect["w"]
    h = crop_rect["h"]
    return padded[y : y + h, x : x + w]


def apply_deskew(img: np.ndarray, page_state: Dict[str, Any]) -> np.ndarray:
    """Rotate the page around the editor's saved deskew origin."""
    if not page_state.get("deskew_enabled"):
        return img
    angle = float(page_state.get("deskew_deg") or 0)
    if abs(angle) < 0.001:
        return img
    origin = page_state.get("deskew_origin") or page_state.get("origin") or {}
    center = (
        float(origin.get("x", img.shape[1] / 2.0)),
        float(origin.get("y", img.shape[0] / 2.0)),
    )
    matrix = cv2.getRotationMatrix2D(center, -angle, 1.0)
    border_value = (255, 255, 255) if img.ndim == 3 else 255
    return cv2.warpAffine(
        img,
        matrix,
        (img.shape[1], img.shape[0]),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border_value,
    )


def sample_page_color(img, crop: Dict[str, int], side: str, model: Dict[str, Any]) -> Tuple[int, int, int]:
    """Sample a stable background color from just inside the margins."""
    h, w = img.shape[:2]
    margins = model.get("margins") or {}
    top = int(round(float(margins.get("top", 0))))
    bottom = int(round(float(margins.get("bottom", 0))))
    inner = int(round(float(margins.get("inner", 0))))
    outer = int(round(float(margins.get("outer", 0))))
    left_margin = inner if side == "right" else outer
    right_margin = outer if side == "right" else inner
    crop_x = int(crop["x"])
    crop_y = int(crop["y"])
    crop_w = int(crop["w"])
    crop_h = int(crop["h"])

    edge_skip = max(8, min(24, min(w, h) // 120))
    band = max(12, min(40, min(w, h) // 80))

    samples: list[np.ndarray] = []

    def add_sample(x1: int, y1: int, x2: int, y2: int) -> None:
        x1 = max(0, min(w, x1))
        y1 = max(0, min(h, y1))
        x2 = max(0, min(w, x2))
        y2 = max(0, min(h, y2))
        if x2 <= x1 or y2 <= y1:
            return
        patch = img[y1:y2, x1:x2]
        if patch.size:
            if patch.ndim == 2:
                patch = patch[:, :, None]
            samples.append(patch.reshape(-1, patch.shape[-1]))

    # Sample from just inside each margin band, away from the noisy outer edge.
    add_sample(crop_x + edge_skip, crop_y + edge_skip, crop_x + crop_w - edge_skip, min(h, crop_y + min(top, edge_skip + band)))
    add_sample(crop_x + edge_skip, max(0, crop_y + crop_h - min(bottom, edge_skip + band)), crop_x + crop_w - edge_skip, crop_y + crop_h - edge_skip)
    add_sample(max(0, crop_x + edge_skip), crop_y + edge_skip, min(w, crop_x + min(left_margin, edge_skip + band)), crop_y + crop_h - edge_skip)
    add_sample(max(0, crop_x + crop_w - min(right_margin, edge_skip + band)), crop_y + edge_skip, min(w, crop_x + crop_w - edge_skip), crop_y + crop_h - edge_skip)

    if not samples:
        patch = img[max(0, edge_skip): min(h, edge_skip + band), max(0, edge_skip): min(w, edge_skip + band)]
        if patch.size:
            if patch.ndim == 2:
                patch = patch[:, :, None]
            samples.append(patch.reshape(-1, patch.shape[-1]))

    if not samples:
        return (255, 255, 255)

    stacked = np.concatenate(samples, axis=0)
    med = np.median(stacked[:, :3], axis=0)
    return tuple(int(max(0, min(255, round(v)))) for v in med)


def fill_margin_bands(img, side: str, model: Dict[str, Any], color: Tuple[int, int, int]):
    """Overwrite the margin bands with a stable page background color."""
    h, w = img.shape[:2]
    margins = model.get("margins") or {}
    top = int(round(float(margins.get("top", 0))))
    bottom = int(round(float(margins.get("bottom", 0))))
    inner = int(round(float(margins.get("inner", 0))))
    outer = int(round(float(margins.get("outer", 0))))
    left_margin = inner if side == "right" else outer
    right_margin = outer if side == "right" else inner
    if top > 0:
        img[0:min(h, top), :] = color
    if bottom > 0:
        img[max(0, h - bottom):h, :] = color
    if left_margin > 0:
        img[:, 0:min(w, left_margin)] = color
    if right_margin > 0:
        img[:, max(0, w - right_margin):w] = color
    return img


def load_model(model_path: Path) -> Dict[str, Any]:
    model = load_json(model_path)
    crop = model.get("crop") or {}
    margins = model.get("margins") or {}
    required_crop = ("w", "h")
    required_margins = ("top", "bottom", "inner", "outer")
    if any(c not in crop for c in required_crop):
      raise ValueError(f"Model is missing crop dimensions: {model_path}")
    if any(m not in margins for m in required_margins):
      raise ValueError(f"Model is missing margin fields: {model_path}")
    return model


def crop_rect_for_page(page_state: Dict[str, Any], model: Dict[str, Any]) -> Dict[str, int]:
    origin = page_state.get("origin") or {}
    side = str(page_state.get("side") or page_state.get("page_side") or "")
    if side not in {"left", "right"}:
        raise ValueError(f"Missing side for page {page_state.get('page')}")
    return {
        "x": int(round(float(origin.get("x", 0)))),
        "y": int(round(float(origin.get("y", 0)))),
        "w": int(round(float((model.get("crop") or {}).get("w", 0)))),
        "h": int(round(float((model.get("crop") or {}).get("h", 0)))),
    }


def margins_for_crop(page_img, crop: Dict[str, int], side: str, model: Dict[str, Any]) -> Dict[str, int]:
    h, w = page_img.shape[:2]
    margins = model.get("margins") or {}
    top = int(round(float(margins["top"])))
    bottom = int(round(float(margins["bottom"])))
    inner = int(round(float(margins["inner"])))
    outer = int(round(float(margins["outer"])))
    left = inner if side == "right" else outer
    right = outer if side == "right" else inner
    return {
        "top": top,
        "bottom": bottom,
        "inner": inner,
        "outer": outer,
        "left": left,
        "right": right,
        "page_left": int(round(crop["x"])),
        "page_top": int(round(crop["y"])),
        "page_bottom": int(round(h - crop["y"] - crop["h"])),
        "page_right": int(round(w - crop["x"] - crop["w"])),
    }


def passthrough_margins(page_img) -> Dict[str, int]:
    h, w = page_img.shape[:2]
    return {
        "top": 0,
        "bottom": 0,
        "inner": 0,
        "outer": 0,
        "left": 0,
        "right": 0,
        "page_left": 0,
        "page_top": 0,
        "page_bottom": 0,
        "page_right": 0,
        "page_width": int(w),
        "page_height": int(h),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("deskewed-pages"))
    parser.add_argument("--dataset-root", type=Path, default=Path("crop-origin-annotations"))
    parser.add_argument("--output-root", type=Path, default=Path("origin-cropped-pages"))
    parser.add_argument("--extra-padding", type=int, default=0)
    parser.add_argument("--pdf", action="append", default=None)
    parser.add_argument("--pages", default=None)
    args = parser.parse_args()

    if not args.source_root.exists():
        raise SystemExit(f"Missing source root: {args.source_root}")
    if not args.dataset_root.exists():
        raise SystemExit(f"Missing dataset root: {args.dataset_root}")

    model = load_model(args.dataset_root / "model.json")
    page_filter = None
    if args.pages:
        page_filter = {
            int(part.strip())
            for part in args.pages.split(",")
            if part.strip()
        }

    args.output_root.mkdir(parents=True, exist_ok=True)
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
        state_dir = args.dataset_root / pdf_dir.name / "state"
        out_pages_dir = args.output_root / pdf_dir.name / "pages"
        out_state_dir = args.output_root / pdf_dir.name / "state"
        out_pages_dir.mkdir(parents=True, exist_ok=True)
        out_state_dir.mkdir(parents=True, exist_ok=True)

        for page_path in sorted(src_pages_dir.glob("*.png"), key=page_sort_key):
            page_num = page_number_from_stem(page_path.stem)
            if page_filter is not None and page_num not in page_filter:
                continue
            state_path = state_dir / f"page-{page_num:03d}.json"
            page_state = load_json(state_path)
            if not page_state:
                raise SystemExit(f"Missing page state: {state_path}")
            origin = page_state.get("origin") or {}
            side = str(page_state.get("side") or page_state.get("page_side") or "")
            if side not in {"left", "right"}:
                raise SystemExit(f"Missing page side for {pdf_dir.name} page {page_num}")
            img = cv2.imread(str(page_path), cv2.IMREAD_COLOR)
            if img is None:
                raise SystemExit(f"Could not read page image: {page_path}")
            img = apply_deskew(img, page_state)
            passthrough = bool(page_state.get("passthrough"))
            crop = None
            if passthrough:
                cropped = img
            else:
                crop = crop_rect_for_page(page_state, model)
                bg_color = sample_page_color(img, crop, side, model)
                cropped = apply_crop(img, crop, extra_padding=args.extra_padding, border_value=bg_color)
                cropped = fill_margin_bands(cropped, side, model, bg_color)
            out_path = out_pages_dir / page_path.name
            cv2.imwrite(str(out_path), cropped)
            out_state_path = out_state_dir / f"{page_path.stem}.json"
            write_json(
                out_state_path,
                {
                    "pdf": pdf_dir.name,
                    "page": page_num,
                    "source_page": str(page_path),
                    "crop_applied": not passthrough,
                    "passthrough": passthrough,
                    "crop": crop,
                    "origin": page_state.get("origin"),
                    "page_side": side,
                    "side": side,
                    "reacquire": False,
                    "reacquire_source": str(page_state.get("reacquire_source") or "source-pdf"),
                    "needs_rotation": bool(page_state.get("deskew_enabled") or page_state.get("needs_rotation")),
                    "deskew_enabled": bool(page_state.get("deskew_enabled")),
                    "deskew_deg": float(page_state.get("deskew_deg") or 0),
                    "deskew_origin": page_state.get("deskew_origin") or {"x": int(round(float(origin.get("x", 0)))), "y": int(round(float(origin.get("y", 0))))},
                    "deskew_step": int(page_state.get("deskew_step") or 100),
                    "margins": passthrough_margins(cropped) if passthrough else margins_for_crop(cropped, {"x": 0, "y": 0, "w": cropped.shape[1], "h": cropped.shape[0]}, side, model),
                    "model": model,
                    "extra_padding": args.extra_padding,
                },
            )
            print(f"{pdf_dir.name}: wrote {out_path.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
