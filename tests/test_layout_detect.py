#!/usr/bin/env python3
"""Synthetic tests for reusable layout primitive detection."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def load_tool(name: str):
    path = ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"tool_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


LAYOUT = load_tool("layout_detect")


class LayoutDetectTest(unittest.TestCase):
    def test_detects_rows_rules_and_internal_indents(self) -> None:
        page = np.full((300, 500), 255, dtype=np.uint8)
        cv2.line(page, (40, 30), (460, 30), 0, 2)
        cv2.rectangle(page, (50, 70), (430, 78), 0, -1)
        cv2.rectangle(page, (50, 95), (420, 103), 0, -1)
        cv2.rectangle(page, (90, 120), (360, 128), 0, -1)
        cv2.line(page, (40, 270), (460, 270), 0, 2)

        report = LAYOUT.analyze_image(page)

        self.assertEqual(report["summary"]["rules"], 2)
        self.assertGreaterEqual(report["summary"]["regions"], 3)
        row_three = report["regions"][2]
        self.assertGreater(row_three["bbox"]["x"], report["regions"][0]["bbox"]["x"])

    def test_edge_expansion_captures_subscript_and_superscript_marks(self) -> None:
        page = np.full((120, 260), 255, dtype=np.uint8)
        cv2.rectangle(page, (40, 50), (200, 60), 0, -1)
        cv2.circle(page, (198, 65), 3, 0, -1)
        cv2.circle(page, (42, 46), 3, 0, -1)

        rows = [LAYOUT.Box(40, 50, 161, 11)]
        expanded, details = LAYOUT.expand_row_edges(rows, page, max_expand=12)

        self.assertEqual(len(rows), 1)
        self.assertGreater(details[0]["top"], 0)
        self.assertGreater(details[0]["bottom"], 0)
        self.assertLessEqual(expanded[0].y, 46)
        self.assertGreaterEqual(expanded[0].y2, 68)

    def test_short_artifact_cleanup_is_audited(self) -> None:
        rows = [
            LAYOUT.Box(20, 20, 180, 12),
            LAYOUT.Box(20, 33, 180, 3),
            LAYOUT.Box(20, 50, 180, 12),
        ]

        cleaned, actions = LAYOUT.cleanup_short_artifact_rows(rows, image_shape=(200, 300))

        self.assertEqual(len(cleaned), 2)
        self.assertEqual(actions[0]["action"], "merge_adjacent_short_row")

    def test_mask_intersections_are_reported_without_deleting_rows(self) -> None:
        page = np.full((120, 300), 255, dtype=np.uint8)
        cv2.rectangle(page, (20, 50), (280, 60), 0, -1)
        mask = np.zeros_like(page)
        cv2.rectangle(mask, (120, 45), (170, 65), 255, -1)

        report = LAYOUT.analyze_image(page, masks={"equation": mask})

        self.assertEqual(report["summary"]["regions"], 1)
        hit = report["regions"][0]["mask_intersections"]["equation"]
        self.assertGreater(hit["overlap_area"], 0)

    def test_cli_writes_json_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pdf-training-layout-test-") as tmp:
            root = Path(tmp)
            image = root / "page.png"
            out = root / "layout.json"
            page = np.full((80, 160), 255, dtype=np.uint8)
            cv2.rectangle(page, (10, 30), (120, 38), 0, -1)
            self.assertTrue(cv2.imwrite(str(image), page))

            rc = LAYOUT.main.__globals__["main"] if False else None
            self.assertIsNone(rc)
            import subprocess

            proc = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "layout_detect.py"), "--image", str(image), "--out", str(out)],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
