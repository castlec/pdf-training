#!/usr/bin/env python3
"""Synthetic HTTP smoke tests for the annotation and reconstruction web apps."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def load_server_module():
    path = Path(__file__).resolve().parents[1] / "tools" / "annotation_server.py"
    spec = importlib.util.spec_from_file_location("annotation_server", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SERVER = load_server_module()


def png_data_url(kind: str) -> str:
    image = Image.new("L", (320, 450), 0)
    boxes = {
        "text": (10, 10, 250, 80),
        "image": (10, 90, 250, 220),
        "equation": (10, 50, 250, 100),
    }
    ImageDraw.Draw(image).rectangle(boxes[kind], fill=255)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


class AnnotationServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="pdf-training-web-test-")
        root = Path(self.temp.name)
        pages = root / "pages" / "synthetic-book" / "pages"
        renders = root / "renders" / "synthetic-book"
        pages.mkdir(parents=True)
        renders.mkdir(parents=True)

        page = Image.new("RGB", (320, 450), "white")
        ImageDraw.Draw(page).text((20, 20), "Synthetic page", fill="black")
        page.save(pages / "synthetic-book-01.png")
        page.save(renders / "page-001-rerender.png")
        page.save(renders / "page-001-comparison.png")
        (renders / "page-001-rerender.json").write_text(
            json.dumps({"accepted_lines": 1, "rejected_lines": 0, "equation_crops": []}),
            encoding="utf-8",
        )

        store = SERVER.AnnotationStore(
            None,
            root / "annotations",
            300,
            root / "pages",
            root / "renders",
        )
        SERVER.Handler.store = store
        self.server = SERVER.ThreadingHTTPServer(("127.0.0.1", 0), SERVER.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.root = root

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def get(self, path: str) -> bytes:
        with urllib.request.urlopen(self.base_url + path) as response:
            self.assertEqual(response.status, 200)
            return response.read()

    def test_editor_viewer_routes_and_mask_persistence(self) -> None:
        self.assertIn(b"PDF Annotation Editor", self.get("/"))
        self.assertIn(b"PDF Reconstruction Viewer", self.get("/?view=rerender"))

        index = json.loads(self.get("/api/index"))
        self.assertEqual(index["pageCount"], 1)
        self.assertEqual(index["pdfs"][0]["name"], "synthetic-book")

        before = json.loads(self.get("/api/page?pdf=synthetic-book&page=1"))
        self.assertFalse(before["saved"])
        self.assertEqual(Image.open(io.BytesIO(self.get(before["pageImageUrl"]))).size, (320, 450))

        rerender = json.loads(self.get("/api/rerender-page?pdf=synthetic-book&page=1"))
        self.assertEqual(rerender["acceptedLines"], 1)
        self.assertEqual(Image.open(io.BytesIO(self.get(rerender["rerenderUrl"]))).size, (320, 450))

        payload = {
            "pdf": "synthetic-book",
            "page": 1,
            "mode": "text",
            "layoutRole": "body",
            "textMask": png_data_url("text"),
            "imageMask": png_data_url("image"),
            "equationMask": png_data_url("equation"),
        }
        request = urllib.request.Request(
            self.base_url + "/api/save",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            self.assertEqual(json.loads(response.read()), {"ok": True})

        after = json.loads(self.get("/api/page?pdf=synthetic-book&page=1"))
        self.assertTrue(after["saved"])
        self.assertEqual(after["mode"], "text")
        for layer in ("text", "image", "equation"):
            mask = Image.open(
                self.root / "annotations" / "synthetic-book" / "masks" / layer / "page-001.png"
            )
            self.assertGreater(np.count_nonzero(np.array(mask)), 0)


if __name__ == "__main__":
    unittest.main()
