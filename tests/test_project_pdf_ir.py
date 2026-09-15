from __future__ import annotations

from tools.project_pdf_ir import project


def test_project_preserves_raw_page_realization_without_semantic_nodes() -> None:
    raw = {
        "schema": "pdf-training-pdf-ir-v1",
        "source": {"path": "source.pdf", "sha256": "abc"},
        "objects": {"1 0": {"kind": "stream", "raw_bytes_b64": "AA=="}},
        "pages": [{
            "id": "page-001",
            "index": 0,
            "media_box": [0, 0, 100, 200],
            "crop_box": [0, 0, 100, 200],
            "operations": [{"ordinal": 0, "operator": "Do", "operands": [{"type": "name", "value": "/Image1"}]}],
            "resources_ref": "2 0",
            "contents_ref": "3 0",
        }],
    }
    result = project(raw)
    page = result["documents"][0]["pages"][0]
    assert result["schema"] == "pdf-training-renderable-dataset-v1"
    assert page["nodes"] == []
    assert page["realization"]["operations"] == raw["pages"][0]["operations"]
    assert result["realization_objects"] is not raw["objects"]
    assert result["realization_objects"] == raw["objects"]
