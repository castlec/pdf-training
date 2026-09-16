from tools.operation_grouping import apply_intrinsic_container_layout
from tools.render_pdf_ir import operation_tree_groups


def test_intrinsic_layout_grows_cell_and_replaces_frame_operations():
    document = {
        "schema": "pdf-training-pdf-ir-v1",
        "pages": [{
            "media_box": [0, 0, 100, 100],
            "operation_groups": [{
                "id": "table",
                "type": "group",
                "layout_kind": "table",
                "bbox": {"x": 10, "y": 10, "w": 20, "h": 20},
                "children": [{
                    "id": "cell",
                    "type": "group",
                    "layout_kind": "cell",
                    "bbox": {"x": 10, "y": 10, "w": 20, "h": 20},
                    "children": [{
                        "id": "frame-op",
                        "type": "group",
                        "role": "container_border_operation",
                        "bbox": {"x": 10, "y": 10, "w": 20, "h": 20},
                        "operation_ordinals": [1],
                    }, {
                        "id": "content",
                        "type": "content",
                        "bbox": {"x": 10, "y": 10, "w": 40, "h": 50},
                    }],
                }],
            }],
        }],
    }

    result = apply_intrinsic_container_layout(document)
    cell = result["pages"][0]["operation_groups"][0]["children"][0]
    assert cell["layout"]["sizing"] == "intrinsic"
    assert cell["layout"]["overflow"] == "grow"
    assert cell["layout"]["overlap"] == "allowed"
    assert cell["layout"]["growth"]["before"] == {"x": 10, "y": 10, "w": 20, "h": 20}
    assert cell["layout"]["growth"]["after"] == {"x": 10.0, "y": 10.0, "w": 40.0, "h": 50.0}
    assert cell["layout"]["growth"]["delta"]["bottom"] == 30.0
    assert cell["bbox"] == {"x": 10.0, "y": 10.0, "w": 40.0, "h": 50.0}
    assert cell["children"][0]["role"] == "container_border"
    assert cell["children"][0]["source_operation_ordinals"] == [1]
    assert cell["children"][0]["render_operations"][3]["operator"] == "re"
    assert cell["children"][0]["render_operations"][3]["operands"] == [10.0, 40.0, 40.0, 50.0]


def test_operation_tree_references_do_not_become_owners():
    groups = operation_tree_groups({
        "children": [{
            "type": "operation_group",
            "group": {"id": "cell", "type": "group"},
            "children": [{"type": "operation", "source_ordinal": 4}],
            "references": [{"source_operation_ordinals": [8, 9]}],
        }],
    })

    assert groups[0]["operation_ordinals"] == [4]
    assert groups[0]["source_operation_ordinals"] == []
    assert groups[0]["operation_references"] == [{"source_operation_ordinals": [8, 9]}]
