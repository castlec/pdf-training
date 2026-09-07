import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from text_segment_review import build_review, extract_text_items, merge_review


class TextSegmentReviewTests(unittest.TestCase):
    def make_page(self, root: Path, *, fragments: bool = True) -> Path:
        image_path = root / "page.png"
        image = Image.new("RGB", (420, 240), "white")
        draw = ImageDraw.Draw(image)
        draw.text((40, 78), "left text", fill="black")
        draw.text((230, 78), "right text", fill="black")
        image.save(image_path)

        text_nodes = (
            [
                {
                    "id": "frag-left",
                    "type": "text_fragment",
                    "bbox": {"x": 38, "y": 72, "w": 120, "h": 34},
                    "text": "left text",
                },
                {
                    "id": "frag-right",
                    "type": "text_fragment",
                    "bbox": {"x": 230, "y": 72, "w": 130, "h": 34},
                    "text": "right text",
                },
            ]
            if fragments
            else [
                {
                    "id": "band-001",
                    "type": "text_band",
                    "bbox": {"x": 38, "y": 72, "w": 322, "h": 34},
                }
            ]
        )
        page = {
            "schema": "pdf-training-renderable-page-v1",
            "id": "synthetic-p001",
            "source_image": "page.png",
            "width": 420,
            "height": 240,
            "nodes": [
                *text_nodes,
                {
                    "id": "eq-001",
                    "type": "equation",
                    "bbox": {"x": 170, "y": 70, "w": 40, "h": 38},
                    "latex": "x_T",
                },
            ],
        }
        page_path = root / ("page-fragments.json" if fragments else "page-band.json")
        page_path.write_text(json.dumps(page), encoding="utf-8")
        return page_path

    def test_extract_uses_existing_text_fragments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page = json.loads(self.make_page(root, fragments=True).read_text(encoding="utf-8"))
            items = extract_text_items(
                page,
                source_base=root,
                min_width=16,
                gutter=3,
                min_vertical_overlap_ratio=0.45,
            )
            self.assertEqual([item["id"] for item in items], ["frag-left", "frag-right"])
            self.assertEqual(items[0]["source_text"], "left text")

    def test_extract_splits_text_band_around_equation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page = json.loads(self.make_page(root, fragments=False).read_text(encoding="utf-8"))
            items = extract_text_items(
                page,
                source_base=root,
                min_width=16,
                gutter=3,
                min_vertical_overlap_ratio=0.45,
            )
            self.assertEqual(len(items), 2)
            self.assertLess(items[0]["bbox"]["x"] + items[0]["bbox"]["w"], 170)
            self.assertGreater(items[1]["bbox"]["x"], 210)

    def test_build_review_writes_segment_crops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_path = self.make_page(root, fragments=True)
            report_root = root / "report"
            summary = build_review(
                input_path=page_path,
                report_root=report_root,
                source_base=root,
                provider="none",
                host="http://127.0.0.1:11434",
                model="",
                tesseract=False,
                ocr_lang="eng",
                psm=7,
                timeout=10,
                contrast=1.0,
                sharpness=1.0,
                placeholder=None,
                min_width=16,
                gutter=3,
                min_vertical_overlap_ratio=0.45,
                limit=0,
                force=True,
            )
            self.assertEqual(summary["processed"], 2)
            self.assertTrue((report_root / "assets/segments/frag-left.png").exists())
            self.assertIn("Text Segment Review", (report_root / "review.html").read_text(encoding="utf-8"))

    def test_merge_review_prefers_correction_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_path = self.make_page(root, fragments=True)
            report_root = root / "report"
            build_review(
                input_path=page_path,
                report_root=report_root,
                source_base=root,
                provider="none",
                host="http://127.0.0.1:11434",
                model="",
                tesseract=False,
                ocr_lang="eng",
                psm=7,
                timeout=10,
                contrast=1.0,
                sharpness=1.0,
                placeholder=None,
                min_width=16,
                gutter=3,
                min_vertical_overlap_ratio=0.45,
                limit=0,
                force=True,
            )
            corrections = report_root / "corrections"
            corrections.mkdir()
            (corrections / "frag-left.json").write_text(
                json.dumps({"id": "frag-left", "text": "corrected left"}),
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
            node = next(node for node in merged["nodes"] if node["id"] == "frag-left")
            self.assertGreaterEqual(result["matched"], 1)
            self.assertEqual(node["text"], "corrected left")
            self.assertEqual(node["content_source"], "text_segment_review")


if __name__ == "__main__":
    unittest.main()
