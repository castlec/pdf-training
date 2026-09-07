#!/usr/bin/env python3
"""Synthetic tests for render composition and validation."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_tool(name: str):
    path = ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"tool_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


COMPOSE = load_tool("render_compose")
VALIDATE = load_tool("validate_render_dataset")


class RenderComposeTest(unittest.TestCase):
    def layout(self) -> dict:
        return {
            "schema": "pdf-training-layout-primitives-v1",
            "image_size": {"w": 500, "h": 300},
            "regions": [
                {
                    "id": "line-001",
                    "type": "text_band",
                    "class": "body",
                    "bbox": {"x": 40, "y": 80, "w": 360, "h": 24},
                    "internal_line_summary": {"line_count": 1, "same_indent": True},
                }
            ],
            "rules": [{"id": "rule-001", "bbox": {"x": 40, "y": 260, "w": 420, "h": 2}}],
        }

    def test_compose_splits_text_band_around_inline_equation(self) -> None:
        annotations = {
            "page_id": "doc-p001",
            "equations": [{"id": "eq-001", "bbox": {"x": 160, "y": 76, "w": 60, "h": 32}, "latex": "x_T"}],
            "images": [],
        }

        page = COMPOSE.compose_page(self.layout(), annotations, document_id="doc")

        fragments = [node for node in page["nodes"] if node["type"] == "text_fragment"]
        equation = next(node for node in page["nodes"] if node["id"] == "eq-001")
        band = next(node for node in page["nodes"] if node["id"] == "line-001")
        self.assertEqual(len(fragments), 2)
        self.assertEqual(fragments[0]["bbox"], {"x": 40, "y": 80, "w": 120, "h": 24})
        self.assertEqual(fragments[1]["bbox"], {"x": 220, "y": 80, "w": 180, "h": 24})
        self.assertEqual(equation["render_policy"], "text")
        self.assertEqual(equation["relations"], [{"type": "inline_with", "target": "line-001"}])
        self.assertEqual(band["relations"][0]["target"], "eq-001")

    def test_structural_latex_uses_math_policy(self) -> None:
        self.assertEqual(COMPOSE.classify_render_policy(r"\frac{a}{b}", inline=True), "inline_math")
        self.assertEqual(COMPOSE.classify_render_policy(r"\sqrt{x}", inline=False), "display_math")
        self.assertEqual(COMPOSE.classify_render_policy("F_1", inline=True), "text")

    def test_validation_flags_removed_valuable_nodes(self) -> None:
        baseline = {
            "id": "base",
            "nodes": [
                {"id": "line-001", "type": "text_band", "bbox": {"x": 1, "y": 2, "w": 3, "h": 4}},
                {"id": "eq-001", "type": "equation", "bbox": {"x": 5, "y": 6, "w": 7, "h": 8}},
            ],
        }
        candidate = {"id": "cand", "nodes": [{"id": "line-001", "type": "text_band", "bbox": {"x": 1, "y": 2, "w": 3, "h": 4}}]}

        report = VALIDATE.validate(baseline, candidate)

        self.assertEqual(report["summary"]["status"], "review")
        self.assertEqual(report["regressions"][0]["id"], "eq-001")

    def test_validation_flags_class_height_ranges(self) -> None:
        candidate = {"id": "cand", "nodes": [{"id": "line-001", "type": "text_band", "class": "body", "bbox": {"x": 0, "y": 0, "w": 50, "h": 80}}]}

        report = VALIDATE.validate({"id": "base", "nodes": []}, candidate, class_ranges={"body": {"min_h": 8, "max_h": 30}})

        self.assertEqual(report["suspect_nodes"][0]["type"], "too_tall_for_class")

    def test_cli_compose_and_validate_write_reports(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pdf-training-render-test-") as tmp:
            root = Path(tmp)
            layout_path = root / "layout.json"
            annotations_path = root / "annotations.json"
            page_path = root / "page.json"
            validation_path = root / "validation.json"
            layout_path.write_text(json.dumps(self.layout()), encoding="utf-8")
            annotations_path.write_text(
                json.dumps({"equations": [{"id": "eq-001", "bbox": {"x": 160, "y": 76, "w": 60, "h": 32}, "latex": "x_T"}]}),
                encoding="utf-8",
            )

            compose = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "render_compose.py"),
                    "--layout",
                    str(layout_path),
                    "--annotations",
                    str(annotations_path),
                    "--out",
                    str(page_path),
                    "--review-html",
                    str(root / "page.html"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(compose.returncode, 0, compose.stderr)
            self.assertTrue(page_path.exists())
            self.assertTrue((root / "page.html").exists())

            validate = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "validate_render_dataset.py"),
                    "--baseline",
                    str(page_path),
                    "--candidate",
                    str(page_path),
                    "--out",
                    str(validation_path),
                    "--review-html",
                    str(root / "validation.html"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(validate.returncode, 0, validate.stderr)
            self.assertTrue(validation_path.exists())
            self.assertTrue((root / "validation.html").exists())


if __name__ == "__main__":
    unittest.main()
