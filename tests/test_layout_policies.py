import pytest

from tools.operation_grouping import apply_intrinsic_container_layout


def base_document(layout=None):
    return {
        "schema": "pdf-training-pdf-ir-v1",
        "pages": [{
            "media_box": [0, 0, 100, 100],
            "operation_groups": [{
                "id": "cell",
                "type": "group",
                "layout_kind": "cell",
                "layout": layout or {},
                "bbox": {"x": 10, "y": 10, "w": 20, "h": 20},
                "children": [{
                    "id": "child",
                    "type": "content",
                    "bbox": {"x": 10, "y": 10, "w": 40, "h": 50},
                }],
            }],
        }],
    }


def test_visible_overflow_does_not_grow_container():
    result = apply_intrinsic_container_layout(base_document({"sizing": "fixed", "overflow": "visible"}))
    assert result["pages"][0]["operation_groups"][0]["bbox"] == {"x": 10, "y": 10, "w": 20, "h": 20}


def test_reject_overflow_fails():
    with pytest.raises(ValueError, match="overflows fixed bounds"):
        apply_intrinsic_container_layout(base_document({"sizing": "fixed", "overflow": "reject"}))


def test_forbidden_overlap_fails():
    document = base_document({"sizing": "intrinsic", "overflow": "grow", "overlap": "forbidden"})
    document["pages"][0]["operation_groups"][0]["children"].append({
        "id": "overlap",
        "type": "content",
        "bbox": {"x": 20, "y": 20, "w": 10, "h": 10},
    })
    with pytest.raises(ValueError, match="overlapping children are forbidden"):
        apply_intrinsic_container_layout(document)
