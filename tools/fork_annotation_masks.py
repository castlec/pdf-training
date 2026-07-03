#!/usr/bin/env python3
"""Fork page annotations while promoting equation regions into OCR text masks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

import cv2
import numpy as np


DEFAULT_SOURCE = Path("annotations")
DEFAULT_OUTPUT = Path("annotations-ocr")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def equation_stats(mask: np.ndarray) -> tuple[int, int]:
    binary = (mask > 0).astype(np.uint8)
    components, _ = cv2.connectedComponents(binary)
    return int(np.count_nonzero(binary)), max(0, components - 1)


def copy_or_link(source: Path, output: Path, *, link: bool) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if link:
        try:
            os.link(source, output)
            return
        except OSError:
            pass
    shutil.copy2(source, output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"Missing annotation set: {source}")
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    if source == output or source in output.parents:
        raise ValueError("Output must be a sibling of the source annotation set.")

    report: dict[str, object] = {
        "source": str(source),
        "output": str(output),
        "strategy": {
            "pages": "hard-linked when supported; otherwise copied",
            "state_image": "independent copies",
            "text": "source text unioned with equations; blank source text becomes full-page include when equations existed",
            "equation": "same-size blank masks",
        },
        "documents": {},
        "totals": {
            "pages": 0,
            "equation_masks": 0,
            "nonempty_equation_masks_removed": 0,
            "equation_components_removed": 0,
            "equation_pixels_removed": 0,
            "equation_pixels_promoted_to_text": 0,
            "text_masks_promoted_to_full_page": 0,
        },
    }
    totals = report["totals"]
    assert isinstance(totals, dict)

    try:
        for doc_dir in sorted(path for path in source.iterdir() if path.is_dir()):
            doc_report = {
                "pages": 0,
                "equation_masks": 0,
                "nonempty_equation_masks_removed": 0,
                "equation_components_removed": 0,
                "equation_pixels_removed": 0,
                "equation_pixels_promoted_to_text": 0,
                "text_masks_promoted_to_full_page": 0,
            }
            for path in sorted(item for item in doc_dir.rglob("*") if item.is_file()):
                relative = path.relative_to(source)
                destination = output / relative
                parts = relative.parts
                is_page = len(parts) >= 3 and parts[1] == "pages"
                is_equation = len(parts) >= 4 and parts[1:3] == ("masks", "equation")
                is_text = len(parts) >= 4 and parts[1:3] == ("masks", "text")

                if is_equation:
                    mask = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                    if mask is None:
                        raise RuntimeError(f"Cannot read equation mask: {path}")
                    pixels, components = equation_stats(mask)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if not cv2.imwrite(str(destination), np.zeros_like(mask)):
                        raise RuntimeError(f"Cannot write blank equation mask: {destination}")
                    doc_report["equation_masks"] += 1
                    doc_report["equation_pixels_removed"] += pixels
                    doc_report["equation_components_removed"] += components
                    doc_report["nonempty_equation_masks_removed"] += int(pixels > 0)
                elif is_text:
                    text_mask = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                    equation_path = path.parent.parent / "equation" / path.name
                    equation_mask = cv2.imread(str(equation_path), cv2.IMREAD_GRAYSCALE)
                    if text_mask is None or equation_mask is None:
                        raise RuntimeError(f"Cannot read text/equation masks for: {path}")
                    if text_mask.shape != equation_mask.shape:
                        raise RuntimeError(f"Text/equation mask shape mismatch: {path}")
                    if np.count_nonzero(text_mask) == 0 and np.count_nonzero(equation_mask) > 0:
                        output_text = np.full_like(text_mask, 255)
                        doc_report["text_masks_promoted_to_full_page"] += 1
                    else:
                        output_text = cv2.bitwise_or(text_mask, equation_mask)
                    promoted = np.count_nonzero((output_text > 0) & (text_mask == 0))
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if not cv2.imwrite(str(destination), output_text):
                        raise RuntimeError(f"Cannot write promoted text mask: {destination}")
                    doc_report["equation_pixels_promoted_to_text"] += int(promoted)
                else:
                    copy_or_link(path, destination, link=is_page)
                    if is_page:
                        doc_report["pages"] += 1

            report["documents"][doc_dir.name] = doc_report
            for key in doc_report:
                totals[key] += doc_report[key]

        report_path = output / "fork-report.json"
        report["verification"] = {
            "source_equation_sha256": {
                str(path.relative_to(source)): sha256(path)
                for path in sorted(source.glob("*/masks/equation/*.png"))
            }
        }
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise

    print(json.dumps(totals, indent=2))
    print(f"Fork: {output}")
    print(f"Report: {output / 'fork-report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
