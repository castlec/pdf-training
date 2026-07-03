#!/usr/bin/env python3
"""Render, deskew, and initialize blank annotation masks for scanned PDFs.

This creates files that can be painted manually in any image editor:

  annotation_root/
    document-name/
      pages/page-001.png
      masks/text/page-001.png
      masks/image/page-001.png
      state/page-001.json

The masks are black PNGs the same size as the page image.
Paint white on the text mask wherever text should be OCR'd.
Paint white on the image mask wherever a region should be extracted as an image.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np


def run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"Required tool not found in PATH: {name}")
    return path


def page_sort_key(path: Path) -> Tuple[int, str]:
    suffix = path.stem.rsplit("-", 1)[-1]
    try:
        return int(suffix), path.stem
    except ValueError:
        return 10**9, path.stem


def render_pdf(pdf_path: Path, out_dir: Path, dpi: int) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / pdf_path.stem
    run(["pdftoppm", "-png", "-r", str(dpi), str(pdf_path), str(prefix)])
    return sorted(out_dir.glob(f"{pdf_path.stem}-*.png"), key=page_sort_key)


def med(values: List[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    if len(values) % 2:
        return float(values[mid])
    return float((values[mid - 1] + values[mid]) / 2.0)


def weighted_median(values: List[float], weights: List[float]) -> float:
    order = np.argsort(values)
    values_arr = np.asarray(values, dtype=float)[order]
    weights_arr = np.asarray(weights, dtype=float)[order]
    cutoff = weights_arr.sum() / 2.0
    idx = int(np.searchsorted(np.cumsum(weights_arr), cutoff, side="left"))
    idx = min(idx, len(values_arr) - 1)
    return float(values_arr[idx])


def collect_horizontal_angles(
    gray: np.ndarray,
    y0: int,
    y1: int,
    x0: int = 0,
    x1: int | None = None,
) -> List[tuple[float, float]]:
    """Collect weighted small angles from horizontal line components within a window."""
    h, w = gray.shape[:2]
    x1 = w if x1 is None else x1
    crop = gray[y0:y1, x0:x1]
    if crop.size == 0:
        return []
    dark = cv2.threshold(crop, 220, 255, cv2.THRESH_BINARY_INV)[1]
    line = cv2.morphologyEx(
        dark,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (120, 1)),
    )
    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(line, connectivity=8)
    results: List[tuple[float, float]] = []
    crop_w = max(1, crop.shape[1])
    for label in range(1, num_labels):
        x, y, ww, hh, area = stats[label]
        if area < 500:
            continue
        if ww < int(crop_w * 0.12):
            continue
        if hh > 80:
            continue
        roi = line[y : y + hh, x : x + ww]
        ys, xs = np.where(roi > 0)
        if len(xs) < 20:
            continue
        design = np.vstack([xs, np.ones_like(xs)]).T
        slope, _ = np.linalg.lstsq(design, ys, rcond=None)[0]
        angle = math.degrees(math.atan2(float(slope), 1.0))
        # Prefer wider components and components with more pixels.
        weight = float(area) + float(ww) * 0.5
        results.append((angle, weight))
    return results


def detect_page_skew(gray: np.ndarray, page_num: int) -> float:
    """Estimate page rotation from horizontal header/footer structures."""
    h, w = gray.shape[:2]
    windows: List[tuple[int, int, int, int]] = [
        (int(h * 0.02), int(h * 0.24), 0, w),
        (int(h * 0.68), int(h * 0.97), 0, w),
    ]
    if page_num % 2 == 1:
        # Odd pages usually place footer/header metadata on the right side.
        windows.extend(
            [
                (int(h * 0.02), int(h * 0.24), int(w * 0.42), w),
                (int(h * 0.68), int(h * 0.97), int(w * 0.34), w),
            ]
        )
    else:
        # Even pages usually place footer/header metadata on the left side.
        windows.extend(
            [
                (int(h * 0.02), int(h * 0.24), 0, int(w * 0.58)),
                (int(h * 0.68), int(h * 0.97), 0, int(w * 0.66)),
            ]
        )

    angles: List[float] = []
    weights: List[float] = []
    for y0, y1, x0, x1 in windows:
        for angle, weight in collect_horizontal_angles(gray, y0, y1, x0, x1):
            angles.append(angle)
            weights.append(weight)

    if not angles:
        return 0.0
    return weighted_median(angles, weights)


def load_frame_model(annotation_root: Path) -> dict[str, float] | None:
    """Load a consensus frame model from saved crop annotations."""
    states = []
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
    left = med([float(s["margins"]["left"]) for s in states if s["margins"].get("left") is not None])
    right = med([float(s["margins"]["right"]) for s in states if s["margins"].get("right") is not None])
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
    if None in (top, bottom, left, right, frame_w, frame_h, source_w, source_h):
        return None
    return {
        "top": float(top),
        "bottom": float(bottom),
        "left": float(left),
        "right": float(right),
        "frame_w": float(frame_w),
        "frame_h": float(frame_h),
        "source_w": float(source_w),
        "source_h": float(source_h),
    }


def scale_value(value: float, current: float, reference: float) -> int:
    if reference <= 0:
      return int(round(value))
    return int(round(value * (current / reference)))


def detect_page_skew_with_model(
    gray: np.ndarray,
    page_num: int,
    model: dict[str, float] | None,
    skip_pages: set[int],
) -> float:
    if page_num in skip_pages:
        return 0.0
    if model is None:
        return detect_page_skew(gray, page_num)
    h, w = gray.shape[:2]
    top = scale_value(model["top"], h, model["source_h"])
    bottom = scale_value(model["bottom"], h, model["source_h"])
    left = scale_value(model["left"], w, model["source_w"])
    right = scale_value(model["right"], w, model["source_w"])
    pad_y = max(120, int(h * 0.08))
    side_pad_x = max(140, int(w * 0.12))
    windows: List[tuple[int, int, int, int]] = [
        (max(0, top - pad_y), min(h, top + pad_y), 0, w),
        (max(0, h - bottom - pad_y), min(h, h - bottom + pad_y), 0, w),
    ]
    if page_num % 2 == 1:
        windows.extend(
            [
                (max(0, top - pad_y), min(h, top + pad_y), max(0, w - right - side_pad_x), w),
                (max(0, h - bottom - pad_y), min(h, h - bottom + pad_y), max(0, w - right - side_pad_x), w),
            ]
        )
    else:
        windows.extend(
            [
                (max(0, top - pad_y), min(h, top + pad_y), 0, min(w, left + side_pad_x)),
                (max(0, h - bottom - pad_y), min(h, h - bottom + pad_y), 0, min(w, left + side_pad_x)),
            ]
        )
    angles: List[float] = []
    weights: List[float] = []
    for y0, y1, x0, x1 in windows:
        for angle, weight in collect_horizontal_angles(gray, y0, y1, x0, x1):
            angles.append(angle)
            weights.append(weight)
    if not angles:
        return detect_page_skew(gray, page_num)
    return weighted_median(angles, weights)


def rotate_page(img: np.ndarray, angle_deg: float) -> np.ndarray:
    if abs(angle_deg) < 0.01:
        return img
    h, w = img.shape[:2]
    center = (w / 2.0, h / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    cos = abs(matrix[0, 0])
    sin = abs(matrix[0, 1])
    new_w = int(round((h * sin) + (w * cos)))
    new_h = int(round((h * cos) + (w * sin)))
    matrix[0, 2] += (new_w / 2.0) - center[0]
    matrix[1, 2] += (new_h / 2.0) - center[1]
    border_value = (255, 255, 255) if img.ndim == 3 else 255
    return cv2.warpAffine(
        img,
        matrix,
        (new_w, new_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border_value,
    )


def trim_page(img: np.ndarray, footer_bbox: tuple[int, int, int, int] | None) -> np.ndarray:
    # Preserve the full page. The footer is part of the document content and
    # the annotation masks should stay aligned to the original page extents.
    return img


def preprocess_page(
    img: np.ndarray,
    page_num: int,
    model: dict[str, float] | None,
    skip_pages: set[int],
) -> np.ndarray:
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    angle = detect_page_skew_with_model(gray, page_num, model, skip_pages)
    rotated = rotate_page(img, angle)
    return trim_page(rotated, None)


def load_mask_shape(path: Path) -> tuple[int, int] | None:
    if not path.exists():
        return None
    mask = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        return None
    return mask.shape[:2]


def init_pdf(
    pdf_path: Path,
    annotation_root: Path,
    dpi: int,
    model: dict[str, float] | None,
    skip_pages: set[int],
) -> None:
    pdf_out = annotation_root / pdf_path.stem
    pages_dir = pdf_out / "pages"
    masks_text_dir = pdf_out / "masks" / "text"
    masks_image_dir = pdf_out / "masks" / "image"
    state_dir = pdf_out / "state"
    pages_dir.mkdir(parents=True, exist_ok=True)
    masks_text_dir.mkdir(parents=True, exist_ok=True)
    masks_image_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    page_images = render_pdf(pdf_path, pages_dir, dpi)
    for idx, page_image in enumerate(page_images, start=1):
        img = cv2.imread(str(page_image), cv2.IMREAD_COLOR)
        if img is None:
            raise SystemExit(f"Could not read rendered page: {page_image}")
        processed = preprocess_page(img, idx, model, skip_pages)
        cv2.imwrite(str(page_image), processed)
        img = processed
        blank = img[:, :, 0] * 0
        text_mask_path = masks_text_dir / f"page-{idx:03d}.png"
        image_mask_path = masks_image_dir / f"page-{idx:03d}.png"
        if load_mask_shape(text_mask_path) != img.shape[:2]:
            cv2.imwrite(str(text_mask_path), blank)
        if load_mask_shape(image_mask_path) != img.shape[:2]:
            cv2.imwrite(str(image_mask_path), blank)
        state_path = state_dir / f"page-{idx:03d}.json"
        if not state_path.exists():
            state_path.write_text(
                json.dumps(
                    {"pdf": pdf_path.stem, "page": idx, "mode": None, "updated_at": None},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        print(f"{pdf_path.name}: initialized page {idx:03d}")


def main() -> int:
    require_tool("pdftoppm")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "pdfs",
        nargs="*",
        type=Path,
        default=sorted(Path("pdfs").glob("*.pdf")),
        help="PDF files to initialize (default: pdfs/*.pdf)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("pages"),
        help="Annotation root directory",
    )
    parser.add_argument(
        "--model-root",
        type=Path,
        default=Path("crop-annotations"),
        help="Root directory containing saved frame/margin model state",
    )
    parser.add_argument("--dpi", type=int, default=300, help="Render DPI")
    parser.add_argument(
        "--skip-deskew-pages",
        default="1",
        help="Comma-separated page numbers to render without deskewing; empty means none.",
    )
    args = parser.parse_args()

    if not args.pdfs:
        raise SystemExit("No PDFs found.")

    args.output.mkdir(parents=True, exist_ok=True)
    skip_pages = {
        int(value.strip())
        for value in args.skip_deskew_pages.split(",")
        if value.strip()
    }
    model = load_frame_model(args.model_root)
    if model:
        print(
            "Loaded frame model:",
            f"top={model['top']:.1f}",
            f"bottom={model['bottom']:.1f}",
            f"left={model['left']:.1f}",
            f"right={model['right']:.1f}",
            f"frame={model['frame_w']:.1f}x{model['frame_h']:.1f}",
        )
    for pdf in args.pdfs:
        if not pdf.exists():
            raise SystemExit(f"Missing PDF: {pdf}")
        init_pdf(pdf, args.output, args.dpi, model, skip_pages)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
