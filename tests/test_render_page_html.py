import json
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from render_page_html import latex_text_fragment, latex_to_mathml, render_comparison_html, render_page_html


class RenderPageHtmlTests(unittest.TestCase):
    def sample_page(self):
        return {
            "schema": "pdf-training-renderable-page-v1",
            "id": "synthetic-p001",
            "width": 800,
            "height": 1000,
            "style": {"fonts": {"body": "Georgia, 'Times New Roman', serif"}},
            "nodes": [
                {
                    "id": "line-001-frag-01",
                    "type": "text_fragment",
                    "class": "body",
                    "bbox": {"x": 80, "y": 120, "w": 500, "h": 36},
                    "text": "Known body text",
                },
                {
                    "id": "eq-001",
                    "type": "equation",
                    "bbox": {"x": 250, "y": 180, "w": 180, "h": 50},
                    "latex": r"\frac{a}{b}",
                    "render_policy": "inline_math",
                },
                {
                    "id": "eq-002",
                    "type": "equation",
                    "bbox": {"x": 450, "y": 180, "w": 80, "h": 36},
                    "latex": r"x_T",
                    "render_policy": "text",
                },
                {
                    "id": "img-001",
                    "type": "image",
                    "bbox": {"x": 100, "y": 300, "w": 400, "h": 260},
                    "transparent_background": True,
                },
                {
                    "id": "rule-001",
                    "type": "rule",
                    "bbox": {"x": 80, "y": 900, "w": 640, "h": 2},
                },
            ],
        }

    def test_latex_text_fragment_preserves_scripts_and_symbols(self):
        rendered = latex_text_fragment(r"\measuredangle \alpha + x_T + \vec{F}")
        self.assertIn("∡", rendered)
        self.assertIn("α", rendered)
        self.assertIn("<sub>T</sub>", rendered)
        self.assertIn('class="math-vec"', rendered)

    def test_latex_to_mathml_handles_common_structure(self):
        rendered = latex_to_mathml(r"F_1 = \frac{a}{\sqrt{b}}")
        self.assertIn("<math", rendered)
        self.assertIn("<msub>", rendered)
        self.assertIn("<mfrac>", rendered)
        self.assertIn("<msqrt>", rendered)
        self.assertIn("<mi>F</mi>", rendered)

    def test_render_page_contains_fixed_page_and_layers(self):
        html = render_page_html(self.sample_page(), Path("render.html"), show_boxes=True)
        self.assertIn("--page-w: 800px", html)
        self.assertIn("--page-h: 1000px", html)
        self.assertIn("Known body text", html)
        self.assertIn("<mfrac>", html)
        self.assertIn("<sub>T</sub>", html)
        self.assertIn("missing-asset", html)
        self.assertIn("review-box", html)

    def test_render_comparison_contains_original_and_rendered_panels(self):
        html = render_comparison_html(
            self.sample_page(),
            Path("/tmp/out/comparison.html"),
            source_image=Path("/tmp/out/original.png"),
        )
        self.assertIn("<h2>Original</h2>", html)
        self.assertIn("<h2>Rendered</h2>", html)
        self.assertIn('src="original.png"', html)
        self.assertIn("Known body text", html)

    def test_cli_writes_render_and_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page_path = root / "page.json"
            out_path = root / "render.html"
            comparison_path = root / "comparison.html"
            source_path = root / "original.png"
            page_path.write_text(json.dumps(self.sample_page()), encoding="utf-8")
            source_path.write_bytes(b"not-a-real-png")

            import render_page_html as module

            old_argv = sys.argv
            try:
                sys.argv = [
                    "render_page_html.py",
                    "--page",
                    str(page_path),
                    "--out",
                    str(out_path),
                    "--comparison-html",
                    str(comparison_path),
                    "--source-image",
                    str(source_path),
                    "--show-boxes",
                ]
                self.assertEqual(module.main(), 0)
            finally:
                sys.argv = old_argv

            self.assertIn("Known body text", out_path.read_text(encoding="utf-8"))
            self.assertIn("<h2>Original</h2>", comparison_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
