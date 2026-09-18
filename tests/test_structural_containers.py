import json

import pikepdf
import pytest

from tools.extract_pdf_ir import extract
from tools.render_pdf_ir import render


def test_extract_creates_document_and_page_containers(tmp_path):
    source = tmp_path / "source.pdf"
    extracted = tmp_path / "document.json"
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(10, 10))
    pdf.save(source)

    result = extract(source, extracted)

    assert result["type"] == "document"
    assert result["role"] == "document_container"
    assert result["pages"][0]["type"] == "page"
    assert result["pages"][0]["role"] == "page_container"


def test_renderer_requires_explicit_document_and_page_containers(tmp_path):
    input_path = tmp_path / "document.json"
    output_path = tmp_path / "rendered.pdf"
    document = {
        "schema": "pdf-training-pdf-ir-v1",
        "type": "document",
        "role": "document_container",
        "pages": [
            {
                "type": "page",
                "role": "page_container",
                "media_box": [0, 0, 10, 10],
                "crop_box": None,
                "rotate": 0,
                "user_unit": 1,
                "operations": [],
            }
        ],
        "objects": {},
    }
    input_path.write_text(json.dumps(document), encoding="utf-8")

    report = render(input_path, output_path)

    assert report["pages"] == 1
    assert output_path.exists()

    document["role"] = "not_a_document_container"
    input_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="document_container"):
        render(input_path, tmp_path / "invalid.pdf")
