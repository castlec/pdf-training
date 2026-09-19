import json

import pikepdf
import pytest

from tools.extract_pdf_ir import extract
from tools.native_table_structure import _marked_content_ranges
from tools.operation_grouping import _operation_bbox, apply_operation_grouping
from tools.render_pdf_ir import _group_transform, render
from tools.relative_coordinates import apply_relative_coordinates
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


def test_operation_bbox_applies_graphics_state_transforms():
    operations = [
        {"operator": "q", "operands": []},
        {"operator": "cm", "operands": [1, 0, 0, 1, 401.6000061, 249.9700012]},
        {"operator": "m", "operands": [0, 0]},
        {"operator": "l", "operands": [15.87, -15.87]},
        {"operator": "Q", "operands": []},
    ]

    bbox = _operation_bbox(operations)

    assert bbox is not None
    assert bbox["x"] == pytest.approx(401.6000061)
    assert bbox["y"] == pytest.approx(234.1000012)
    assert bbox["w"] == pytest.approx(15.87)
    assert bbox["h"] == pytest.approx(15.87)


def test_page_container_owns_page_level_geometry_once():
    document = {
        "schema": "pdf-training-pdf-ir-v1",
        "type": "document",
        "role": "document_container",
        "pages": [
            {
                "type": "page",
                "role": "page_container",
                "id": "page-001",
                "media_box": [0, 0, 100, 200],
                "operations": [
                    {"ordinal": 0, "operator": "BDC", "operands": [{"type": "name", "value": "/P"}, {"/MCID": 1}]},
                    {"ordinal": 1, "operator": "m", "operands": [0, 0]},
                    {"ordinal": 2, "operator": "l", "operands": [10, 10]},
                    {"ordinal": 3, "operator": "EMC", "operands": []},
                    {"ordinal": 4, "operator": "BDC", "operands": [{"type": "name", "value": "/P"}, {"/MCID": 2}]},
                    {"ordinal": 5, "operator": "m", "operands": [20, 0]},
                    {"ordinal": 6, "operator": "l", "operands": [30, 10]},
                    {"ordinal": 7, "operator": "EMC", "operands": []},
                ],
            }
        ],
    }

    result = apply_operation_grouping(document)
    page = result["pages"][0]

    assert page["bbox"] == {"x": 0.0, "y": 0.0, "w": 100.0, "h": 200.0}
    assert len(page["operation_groups"]) == 1
    group = page["operation_groups"][0]
    assert group["parent_context_bbox"] == page["bbox"]
    assert group["parent_context_bbox_pdf"] == {"x": 0.0, "y": 0.0, "w": 100.0, "h": 200.0}


def test_marked_content_ownership_includes_enclosing_graphics_context():
    operations = [
        {"ordinal": 0, "operator": "q", "operands": []},
        {"ordinal": 1, "operator": "re", "operands": [1, 2, 3, 4]},
        {"ordinal": 2, "operator": "W*", "operands": []},
        {"ordinal": 3, "operator": "n", "operands": []},
        {"ordinal": 4, "operator": "BDC", "operands": [{"/MCID": 7}]},
        {"ordinal": 5, "operator": "TJ", "operands": [{"type": "string", "value": "cell"}]},
        {"ordinal": 6, "operator": "Q", "operands": []},
        {"ordinal": 7, "operator": "EMC", "operands": []},
    ]

    assert _marked_content_ranges(operations)[7] == list(range(8))


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
                    {"ordinal": 1, "operator": "re", "operands": [1, 2, 3, 4]},
                    {"ordinal": 2, "operator": "TJ", "operands": [{"type": "string", "value": "outer"}]},
                    {"ordinal": 3, "operator": "EMC", "operands": []},
                    {"ordinal": 4, "operator": "BDC", "operands": [{"type": "name", "value": "/P"}, {"/MCID": 2}]},
                    {"ordinal": 5, "operator": "re", "operands": [5, 6, 2, 2]},
                    {"ordinal": 6, "operator": "TJ", "operands": [{"type": "string", "value": "nested"}]},
                    {"ordinal": 7, "operator": "EMC", "operands": []},
                    {"ordinal": 8, "operator": "BMC", "operands": [{"type": "name", "value": "/Artifact"}]},
                    {"ordinal": 9, "operator": "re", "operands": [0, 1, 8, 8]},
                    {"ordinal": 10, "operator": "EMC", "operands": []},
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
    table_paint = [child for child in outer_table["children"] if child.get("role") == "table_paint"]
    assert len(table_paint) == 1
    assert table_paint[0]["source_operation_ordinals"] == [8, 9, 10]
    assert outer_cell["source_operation_ordinals"] == [0, 1, 2, 3]
    assert outer_cell["source_frame_pdf"] == {"x": 1.0, "y": 2.0, "w": 6.0, "h": 6.0}
    assert outer_cell["coordinate_space"]["name"] == "parent-relative-pdf"
    assert nested_table["layout_kind"] == "table"
    assert nested_cell["source_operation_ordinals"] == [4, 5, 6, 7]
    assert nested_cell["source_frame_pdf"] == {"x": 5.0, "y": 6.0, "w": 2.0, "h": 2.0}

    after_relative = apply_relative_coordinates(result)
    assert after_relative["pages"][0]["operation_groups"][0]["layout_position"]["offset"] == {"x": 0.0, "y": 1.0}

    root = {
        "coordinate_space": {"name": "parent-relative-pdf"},
        "source_frame_pdf": {"x": 10.0, "y": 10.0},
        "layout_position": {"offset": {"x": 20.0, "y": 30.0}},
    }
    root_transform = _group_transform(None, root)
    assert root_transform == [1, 0, 0, 1, 10.0, 20.0]
    child = {
        "coordinate_space": {"name": "parent-relative-pdf"},
        "source_frame_pdf": {"x": 12.0, "y": 14.0},
        "layout_position": {"offset": {"x": 2.0, "y": 4.0}},
    }
    child_transform = _group_transform(
        None,
        child,
        current_translation={"x": 10.0, "y": 20.0},
        parent_target_origin={"x": 20.0, "y": 30.0},
    )
    assert child_transform == [1, 0, 0, 1, 0.0, 0.0]

    serialized = json.dumps(result)
    assert all(role not in serialized for role in ("/Table", "/TR", "/TH", "/TD"))
