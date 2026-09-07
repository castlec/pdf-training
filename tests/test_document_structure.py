import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import document_structure
from document_structure import (
    active_outline_for_page,
    apply_structure,
    build_page_map,
    normalize_text,
    outline_level,
)


class DocumentStructureTests(unittest.TestCase):
    def sample_page(self):
        return {
            "schema": "pdf-training-renderable-page-v1",
            "id": "doc-p001",
            "source_page_index": 0,
            "output_page_index": 0,
            "width": 800,
            "height": 1000,
            "nodes": [
                {
                    "id": "header-ocr",
                    "type": "text_fragment",
                    "class": "body",
                    "bbox": {"x": 60, "y": 40, "w": 300, "h": 30},
                    "text": "OLD HEADER",
                },
                {
                    "id": "heading-candidate",
                    "type": "text_fragment",
                    "class": "body",
                    "bbox": {"x": 80, "y": 120, "w": 420, "h": 50},
                    "text": "1 Introduction",
                },
                {
                    "id": "body",
                    "type": "heading",
                    "class": "heading_level_1",
                    "bbox": {"x": 80, "y": 200, "w": 420, "h": 34},
                    "text": "Normal paragraph",
                },
                {
                    "id": "footer-number",
                    "type": "text_fragment",
                    "class": "body",
                    "bbox": {"x": 380, "y": 930, "w": 40, "h": 30},
                    "text": "7",
                },
            ],
        }

    def sample_rules(self):
        return {
            "schema": "pdf-training-document-rules-v1",
            "id": "synthetic-rules",
            "fallback_running_header": "BOOK",
            "default_exclusion_fraction": 0.35,
            "ocr_exclusion_bands": [
                {"id": "header-band", "bbox": {"x": 0, "y": 0, "w": 800, "h": 90}, "reason": "generated_header"},
                {"id": "footer-band", "bbox": {"x": 0, "y": 900, "w": 800, "h": 100}, "reason": "generated_footer"},
            ],
            "templates": {
                "odd": {
                    "nodes": [
                        {
                            "id": "odd-header-text",
                            "type": "header",
                            "class": "running_header",
                            "text": "{running_header}",
                            "bbox": {"x": 500, "y": 40, "w": 250, "h": 30},
                            "align": "right",
                        },
                        {
                            "id": "odd-footer-number",
                            "type": "page_number",
                            "class": "footer_page_number",
                            "text": "{printed_page}",
                            "bbox": {"x": 700, "y": 930, "w": 40, "h": 30},
                            "align": "center",
                        },
                        {
                            "id": "odd-header-rule",
                            "type": "rule",
                            "class": "header_rule",
                            "bbox": {"x": 50, "y": 80, "w": 700, "h": 2},
                        },
                    ]
                },
                "even": {
                    "nodes": [
                        {
                            "id": "even-header-text",
                            "type": "header",
                            "class": "running_header",
                            "text": "EVEN",
                            "bbox": {"x": 50, "y": 40, "w": 250, "h": 30},
                        }
                    ]
                },
            },
        }

    def sample_outline(self):
        return [
            {"section": "1", "title": "Introduction", "printed_page": 7},
            {"section": "1.1", "title": "Background", "printed_page": 8},
        ]

    def test_normalize_and_outline_level(self):
        self.assertEqual(normalize_text("PŘEDMLUVA"), "PREDMLUVA")
        self.assertEqual(outline_level({"section": "1.2.3"}), 3)
        self.assertEqual(outline_level({"level": 2}), 2)

    def test_active_outline_tracks_current_page(self):
        context = active_outline_for_page(self.sample_outline(), [8])
        self.assertEqual([entry["level"] for entry in context["active"]], [1, 2])
        self.assertEqual(context["starts_on_page"][0]["title"], "Background")

    def test_build_page_map_uses_footer_number(self):
        result = build_page_map(self.sample_page())
        self.assertEqual(result["summary"]["mapped"], 1)
        self.assertEqual(result["by_page_id"]["doc-p001"]["printed_page"], 7)

    def test_apply_structure_generates_header_footer_and_classifies_heading(self):
        page_map = {"entries": [{"page_id": "doc-p001", "printed_page": 7}]}
        updated, report = apply_structure(
            input_data=self.sample_page(),
            outline_entries=self.sample_outline(),
            page_map=page_map,
            rules=self.sample_rules(),
            match_threshold=0.78,
            demote_unmatched=True,
        )
        nodes = {node["id"]: node for node in updated["nodes"]}
        generated = [node for node in updated["nodes"] if node.get("generated_by_document_structure")]

        self.assertEqual(report["heading_matches"], 1)
        self.assertEqual(report["demoted_headings"], 1)
        self.assertEqual(nodes["heading-candidate"]["type"], "heading")
        self.assertEqual(nodes["heading-candidate"]["class"], "heading_level_1")
        self.assertEqual(nodes["body"]["type"], "text_fragment")
        self.assertTrue(nodes["header-ocr"]["render_suppressed"])
        self.assertTrue(nodes["footer-number"]["render_suppressed"])
        self.assertEqual(len(generated), 3)
        self.assertEqual(next(node for node in generated if node["type"] == "header")["text"], "Introduction")
        self.assertEqual(next(node for node in generated if node["type"] == "page_number")["text"], "7")

    def test_cli_page_map_and_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_path = root / "page.json"
            outline_path = root / "outline.json"
            rules_path = root / "rules.json"
            map_path = root / "page-map.json"
            out_path = root / "out.json"
            report_path = root / "report.json"
            html_path = root / "report.html"
            page_path.write_text(json.dumps(self.sample_page()), encoding="utf-8")
            outline_path.write_text(json.dumps({"entries": self.sample_outline()}), encoding="utf-8")
            rules_path.write_text(json.dumps(self.sample_rules()), encoding="utf-8")

            with mock.patch.object(sys, "argv", ["document_structure.py", "page-map", "--input", str(page_path), "--out", str(map_path)]):
                self.assertEqual(document_structure.main(), 0)
            with mock.patch.object(
                sys,
                "argv",
                [
                    "document_structure.py",
                    "apply",
                    "--input",
                    str(page_path),
                    "--outline",
                    str(outline_path),
                    "--page-map",
                    str(map_path),
                    "--rules",
                    str(rules_path),
                    "--out",
                    str(out_path),
                    "--report",
                    str(report_path),
                    "--review-html",
                    str(html_path),
                    "--demote-unmatched",
                ],
            ):
                self.assertEqual(document_structure.main(), 0)

            updated = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertIn("document_structure", updated)
            self.assertIn("Document Structure Report", html_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
