import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import quality_check
from quality_check import validate_quality


class QualityCheckTests(unittest.TestCase):
    def make_page(self, root: Path) -> tuple[Path, dict]:
        image_path = root / "page.png"
        image = Image.new("RGB", (400, 260), "white")
        draw = ImageDraw.Draw(image)
        draw.text((50, 80), "first line", fill="black")
        draw.text((50, 132), "second line", fill="black")
        image.save(image_path)
        page = {
            "schema": "pdf-training-renderable-page-v1",
            "id": "quality-p001",
            "source_image": "page.png",
            "width": 400,
            "height": 260,
            "style": {"class_ranges": {"body": {"min_h": 20, "max_h": 44}}},
            "nodes": [
                {
                    "id": "frag-001",
                    "type": "text_fragment",
                    "class": "body",
                    "bbox": {"x": 48, "y": 74, "w": 130, "h": 32},
                    "text": "first line",
                },
                {
                    "id": "eq-001",
                    "type": "equation",
                    "bbox": {"x": 220, "y": 72, "w": 60, "h": 36},
                    "latex": "x_T",
                },
            ],
        }
        page_path = root / "page.json"
        page_path.write_text(json.dumps(page), encoding="utf-8")
        return page_path, page

    def test_clean_page_passes_without_density(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_path, page = self.make_page(root)
            report = validate_quality(page, candidate_path=page_path, source_base=root)
            self.assertEqual(report["summary"]["status"], "pass")
            self.assertEqual(report["summary"]["findings"], 0)

    def test_flags_bounds_linebreak_and_class_height(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_path, page = self.make_page(root)
            page["nodes"][0]["bbox"]["h"] = 80
            page["nodes"][0]["text"] = "first\\nline"
            page["nodes"].append(
                {
                    "id": "bad-bounds",
                    "type": "text_fragment",
                    "bbox": {"x": 390, "y": 250, "w": 40, "h": 20},
                    "text": "bad",
                }
            )
            report = validate_quality(page, candidate_path=page_path, source_base=root)
            checks = {item["check"] for item in report["findings"]}
            self.assertEqual(report["summary"]["status"], "fail")
            self.assertIn("bbox_bounds", checks)
            self.assertIn("line_break_field", checks)
            self.assertIn("class_height", checks)

    def test_flags_unrelated_text_obstacle_intersection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_path, page = self.make_page(root)
            page["nodes"][0]["bbox"] = {"x": 48, "y": 72, "w": 230, "h": 36}
            report = validate_quality(page, candidate_path=page_path, source_base=root)
            self.assertIn("mask_intersection", {item["check"] for item in report["findings"]})

    def test_allows_related_text_obstacle_intersection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_path, page = self.make_page(root)
            page["nodes"][0]["bbox"] = {"x": 48, "y": 72, "w": 230, "h": 36}
            page["nodes"][0]["relations"] = [{"type": "intersects", "target": "eq-001"}]
            report = validate_quality(page, candidate_path=page_path, source_base=root)
            self.assertNotIn("mask_intersection", {item["check"] for item in report["findings"]})

    def test_density_flags_tall_multiline_text_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_path, page = self.make_page(root)
            page["nodes"][0]["bbox"] = {"x": 48, "y": 70, "w": 150, "h": 90}
            report = validate_quality(
                page,
                candidate_path=page_path,
                source_base=root,
                density=True,
                tall_line_height=70,
                min_row_fraction=0.002,
                major_band_min_height=3,
            )
            self.assertIn("density_geometry", {item["check"] for item in report["findings"]})

    def test_baseline_removed_node_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_path, baseline = self.make_page(root)
            candidate = {**baseline, "nodes": baseline["nodes"][:1]}
            report = validate_quality(candidate, candidate_path=page_path, source_base=root, baseline=baseline)
            self.assertEqual(report["summary"]["status"], "fail")
            self.assertIn("baseline_regression", {item["check"] for item in report["findings"]})

    def test_cli_writes_json_and_html_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_path, _ = self.make_page(root)
            out_path = root / "quality.json"
            html_path = root / "quality.html"
            argv = [
                "quality_check.py",
                "--candidate",
                str(page_path),
                "--source-base",
                str(root),
                "--out",
                str(out_path),
                "--review-html",
                str(html_path),
            ]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(quality_check.main(), 0)
            report = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(report["schema"], "pdf-training-quality-report-v1")
            self.assertIn("Quality Check", html_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
