import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from equation_review import build_review, merge_review, parse_sectioned_response


class EquationReviewTests(unittest.TestCase):
    def make_page(self, root: Path) -> tuple[Path, Path]:
        image_path = root / "page.png"
        image = Image.new("RGB", (320, 220), "white")
        draw = ImageDraw.Draw(image)
        draw.text((52, 82), "x_T = 12", fill="black")
        image.save(image_path)

        page = {
            "schema": "pdf-training-renderable-page-v1",
            "id": "synthetic-p001",
            "source_image": "page.png",
            "width": 320,
            "height": 220,
            "nodes": [
                {
                    "id": "synthetic-p001-eq-0001",
                    "type": "equation",
                    "bbox": {"x": 50, "y": 78, "w": 96, "h": 30},
                    "latex": "x_T = 12",
                    "render_policy": "text",
                }
            ],
        }
        page_path = root / "page.json"
        page_path.write_text(json.dumps(page), encoding="utf-8")
        return image_path, page_path

    def test_parse_sectioned_response_extracts_latex(self):
        parsed = parse_sectioned_response(
            """SCOPE_COMPLETE: yes
CONTAINS_PROSE: no

LATEX:
x_T = 12

NOTES:
ok"""
        )
        self.assertEqual(parsed["scope_complete"], "yes")
        self.assertEqual(parsed["latex"], "x_T = 12")
        self.assertEqual(parsed["notes"], "ok")

    def test_build_review_from_renderable_page_without_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, page_path = self.make_page(root)
            report_root = root / "report"

            summary = build_review(
                input_path=page_path,
                report_root=report_root,
                source_base=root,
                provider="none",
                host="http://127.0.0.1:11434",
                model="",
                ocr_lang="eng",
                tesseract=False,
                psm=6,
                timeout=10,
                contrast=1.0,
                sharpness=1.0,
                context_padding=5,
                limit=0,
                force=True,
            )

            self.assertEqual(summary["processed"], 1)
            self.assertTrue((report_root / "assets/equations/synthetic-p001-eq-0001.png").exists())
            self.assertTrue((report_root / "summary.json").exists())
            review_html = (report_root / "review.html").read_text(encoding="utf-8")
            self.assertIn("synthetic-p001-eq-0001", review_html)
            self.assertIn("x_T = 12", review_html)

    def test_merge_review_prefers_correction_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, page_path = self.make_page(root)
            report_root = root / "report"
            build_review(
                input_path=page_path,
                report_root=report_root,
                source_base=root,
                provider="none",
                host="http://127.0.0.1:11434",
                model="",
                ocr_lang="eng",
                tesseract=False,
                psm=6,
                timeout=10,
                contrast=1.0,
                sharpness=1.0,
                context_padding=5,
                limit=0,
                force=True,
            )
            corrections = report_root / "corrections"
            corrections.mkdir()
            (corrections / "synthetic-p001-eq-0001.json").write_text(
                json.dumps({"id": "synthetic-p001-eq-0001", "correction": r"F_1 = 12"}),
                encoding="utf-8",
            )
            out_path = root / "merged.json"
            result = merge_review(
                metadata_path=page_path,
                summary_path=report_root / "summary.json",
                corrections_root=corrections,
                out_path=out_path,
                min_iou=0.8,
            )
            merged = json.loads(out_path.read_text(encoding="utf-8"))

            self.assertEqual(result["matched"], 1)
            self.assertEqual(merged["nodes"][0]["latex"], r"F_1 = 12")
            self.assertEqual(merged["nodes"][0]["content_source"], "equation_review")


if __name__ == "__main__":
    unittest.main()
