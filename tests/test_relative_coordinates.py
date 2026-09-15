from tools.relative_coordinates import TRANSFORM_ID, apply_relative_coordinates


def test_relative_coordinates_preserve_page_bbox_and_add_local_layout():
    data = {
        "pages": [{
            "id": "p1",
            "width": 100,
            "height": 100,
            "nodes": [{"id": "n1", "bbox": {"x": 15, "y": 25, "w": 10, "h": 5}}],
            "group_tree": [{
                "id": "g1",
                "type": "group",
                "bbox": {"x": 10, "y": 20, "w": 80, "h": 40},
                "node_ids": ["n1"],
                "children": [{
                    "id": "content",
                    "type": "content",
                    "bbox": {"x": 15, "y": 25, "w": 10, "h": 5},
                    "node_ids": ["n1"],
                    "children": [],
                }],
            }],
        }],
    }
    result = apply_relative_coordinates(data)
    group = result["pages"][0]["group_tree"][0]
    assert group["bbox"] == {"x": 10, "y": 20, "w": 80, "h": 40}
    assert group["coordinate_space"]["origin_page"] == {"x": 10.0, "y": 20.0}
    assert group["layout_box"] == {"space": "parent-local", "origin": "p1", "x": 10.0, "y": 20.0, "w": 80.0, "h": 40.0}
    assert group["node_layout"]["n1"]["relative_bbox"] == {"x": 5.0, "y": 5.0, "w": 10.0, "h": 5.0}
    assert result["pages"][0]["nodes"][0]["layout_box"]["origin"] == "content"
    assert group["children"][0]["layout_position"]["bbox"]["x"] == 5.0
    assert data["pages"][0]["nodes"][0]["bbox"]["x"] == 15


def test_relative_transform_is_registered():
    from tools.transform_library import apply_transform

    data = {"pages": [{"id": "p1", "width": 10, "height": 10, "nodes": [], "group_tree": []}]}
    assert apply_transform(data, TRANSFORM_ID)["pages"][0]["coordinate_system"]["name"] == "page"
