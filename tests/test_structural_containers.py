import json

import pikepdf
import pytest

from tools.extract_pdf_ir import extract
from tools.render_pdf_ir import render
from tools.transform_library import apply_transform


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


def test_native_table_transform_materializes_recursive_containers_and_removes_pdf_roles():
    document = {
        "schema": "pdf-training-pdf-ir-v1",
        "type": "document",
        "role": "document_container",
        "pages": [
            {
                "type": "page",
                "role": "page_container",
                "id": "page-001",
                "page_ref": "1 0",
                "media_box": [0, 0, 10, 10],
                "operations": [
                    {"ordinal": 0, "operator": "BDC", "operands": [{"type": "name", "value": "/P"}, {"/MCID": 1}]},
                    {"ordinal": 1, "operator": "TJ", "operands": [{"type": "string", "value": "outer"}]},
                    {"ordinal": 2, "operator": "EMC", "operands": []},
                    {"ordinal": 3, "operator": "BDC", "operands": [{"type": "name", "value": "/P"}, {"/MCID": 2}]},
                    {"ordinal": 4, "operator": "TJ", "operands": [{"type": "string", "value": "nested"}]},
                    {"ordinal": 5, "operator": "EMC", "operands": []},
                ],
            }
        ],
        "objects": {},
        "native_structure": {
            "schema": "pdf-training-pdf-structure-v1",
            "type": "pdf_structure_root",
            "children": [
                {
                    "type": "pdf_struct_element",
                    "role": "/Table",
                    "page_ref": "1 0",
                    "children": [
                        {
                            "type": "pdf_struct_element",
                            "role": "/TR",
                            "children": [
                                {
                                    "type": "pdf_struct_element",
                                    "role": "/TD",
                                    "children": [
                                        {"type": "pdf_marked_content", "mcid": 1},
                                        {
                                            "type": "pdf_struct_element",
                                            "role": "/Table",
                                            "page_ref": "1 0",
                                            "children": [
                                                {
                                                    "type": "pdf_struct_element",
                                                    "role": "/TR",
                                                    "children": [
                                                        {
                                                            "type": "pdf_struct_element",
                                                            "role": "/TD",
                                                            "children": [{"type": "pdf_marked_content", "mcid": 2}],
                                                        }
                                                    ],
                                                }
                                            ],
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    }

    result = apply_transform(document, "structure.materialize-native-tables.v1")
    outer_table = result["pages"][0]["operation_groups"][0]
    outer_cell = outer_table["children"][0]["children"][0]
    nested_table = outer_cell["children"][0]
    nested_cell = nested_table["children"][0]["children"][0]

    assert "native_structure" not in result
    assert outer_table["layout_kind"] == "table"
    assert outer_cell["source_operation_ordinals"] == [0, 1, 2]
    assert nested_table["layout_kind"] == "table"
    assert nested_cell["source_operation_ordinals"] == [3, 4, 5]
    serialized = json.dumps(result)
    assert all(role not in serialized for role in ("/Table", "/TR", "/TH", "/TD"))
