#!/usr/bin/env python3
"""Synthetic regression tests for reusable deskew and crop tooling."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def load_tool(name: str):
    path = ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


INITIALIZE = load_tool("initialize_pages")
APPLY_ANNOTATIONS = load_tool("apply_crop_annotations")
APPLY_ORIGINS = load_tool("apply_crop_origins")
CROP_SERVER = load_tool("crop_annotation_server")
ORIGIN_SERVER = load_tool("crop_origin_server")
COMPARE_SERVER = load_tool("crop_compare_server")


def write_page(path: Path, width: int = 800, height: int = 1000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.full((height, width, 3), 255, dtype=np.uint8)
    cv2.line(image, (80, 100), (width - 80, 100), (0, 0, 0), 5)
    cv2.line(image, (80, height - 150), (width - 80, height - 150), (0, 0, 0), 5)
    if not cv2.imwrite(str(path), image):
        raise RuntimeError(f"Could not write {path}")


def write_crop_state(root: Path) -> None:
    state_path = root / "synthetic-book" / "state" / "page-002.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "pdf": "synthetic-book",
                "page": 2,
                "crop": {"x": 50, "y": 20, "w": 700, "h": 900},
                "margins": {"left": 50, "right": 50, "top": 20, "bottom": 80},
                "updated_at": "synthetic",
            }
        ),
        encoding="utf-8",
    )


class FakeCropStore:
    def __init__(self, source_root: Path) -> None:
        self.source_root = source_root
        self.saved = None
        page = source_root / "synthetic-book" / "pages" / "synthetic-book-01.png"
        self.doc = SimpleNamespace(
            name="synthetic-book",
            page_count=1,
            pages=[SimpleNamespace(angle_deg=0.0, candidate=True)],
        )
        self.page = page

    def index(self):
        return {"pageCount": 1, "pdfs": [{"name": "synthetic-book"}]}

    def page_paths(self, pdf, page):
        return {"page_image": self.page}

    def get_state(self, pdf, page):
        return {"crop": None, "updated_at": None}

    def get_doc(self, pdf):
        return self.doc

    def save_page(self, payload):
        self.saved = payload


class CropWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="pdf-training-crop-test-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_frame_model_dimensions_are_inferred(self) -> None:
        annotations = self.root / "crop-annotations"
        write_crop_state(annotations)

        init_model = INITIALIZE.load_frame_model(annotations)
        crop_model = APPLY_ANNOTATIONS.load_frame_model(annotations)

        self.assertEqual((init_model["source_w"], init_model["source_h"]), (800.0, 1000.0))
        self.assertEqual((crop_model["source_w"], crop_model["source_h"]), (800.0, 1000.0))
        self.assertEqual(crop_model["x_bias"], 0.0)
        self.assertEqual(crop_model["outer_trim"], 0.0)
        self.assertEqual(crop_model["scale_x"], 1.0)

    def test_detected_skew_is_corrected(self) -> None:
        page = np.full((1000, 800), 255, dtype=np.uint8)
        for y in (100, 750, 850):
            cv2.line(page, (80, y), (720, y), 0, 5)
        skewed = INITIALIZE.rotate_page(page, -2.0)

        detected = INITIALIZE.detect_page_skew(skewed, 2)
        corrected = INITIALIZE.rotate_page(skewed, detected)
        residual = INITIALIZE.detect_page_skew(corrected, 2)

        self.assertAlmostEqual(detected, 2.0, delta=0.35)
        self.assertLess(abs(residual), 0.35)

    def test_saved_deskew_matches_editor_rotation_direction(self) -> None:
        page = np.full((300, 300), 255, dtype=np.uint8)
        cv2.circle(page, (250, 150), 8, 0, -1)
        state = {
            "deskew_enabled": True,
            "deskew_deg": 90,
            "deskew_origin": {"x": 150, "y": 150},
        }

        rotated = APPLY_ORIGINS.apply_deskew(page, state)

        self.assertLess(int(rotated[245:256, 145:156].mean()), 40)
        self.assertGreater(int(rotated[145:156, 245:256].mean()), 240)

    def test_crop_web_apps_serve_synthetic_data(self) -> None:
        source = self.root / "pages"
        page_path = source / "synthetic-book" / "pages" / "synthetic-book-01.png"
        write_page(page_path)
        annotations = self.root / "crop-annotations"
        write_crop_state(annotations)

        fake_store = FakeCropStore(source)
        CROP_SERVER.Handler.store = fake_store
        self._assert_server(
            CROP_SERVER.ThreadingHTTPServer,
            CROP_SERVER.Handler,
            b"PDF Anchor Model Tool",
            "/api/index",
        )

        COMPARE_SERVER.Handler.root_dir = source
        COMPARE_SERVER.Handler.model_root = annotations
        payload = self._assert_server(
            COMPARE_SERVER.ThreadingHTTPServer,
            COMPARE_SERVER.Handler,
            b"Crop Compare",
            "/api/index",
        )
        self.assertEqual(payload["model"]["source_w"], 800.0)

        dataset = self.root / "crop-origin-annotations"
        seed = self.root / "cropped-pages"
        write_crop_state(seed)
        store = ORIGIN_SERVER.CropOriginStore(source, dataset, seed)
        ORIGIN_SERVER.Handler.store = store
        payload = self._assert_server(
            ORIGIN_SERVER.ThreadingHTTPServer,
            ORIGIN_SERVER.Handler,
            b"Crop Origin",
            "/api/index",
        )
        self.assertEqual(payload["pdfs"][0]["name"], "synthetic-book")

    def _assert_server(self, server_class, handler_class, page_marker: bytes, api_path: str):
        server = server_class(("127.0.0.1", 0), handler_class)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/") as response:
                self.assertIn(page_marker, response.read())
            with urllib.request.urlopen(base + api_path) as response:
                return json.loads(response.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
