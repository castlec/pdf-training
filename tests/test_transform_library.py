from __future__ import annotations

from tools.transform_library import IDENTITY_ID, TRANSFORMS, apply_transform, identity, resolve


def test_identity_replaces_top_level_and_nested_nodes() -> None:
    document = {
        "schema": "pdf-training-renderable-document-v1",
        "pages": [{
            "id": "page-1",
            "nodes": [{
                "id": "container-1",
                "type": "container",
                "realization": {"local_transform": {"a": 1}},
                "children": [{"id": "text-1", "type": "text", "text": "A"}],
            }],
        }],
        "resources": {"xobjects": {"X1": {"data": "embedded"}}},
    }
    result = identity(document)
    assert result == document
    source_node = document["pages"][0]["nodes"][0]
    result_node = result["pages"][0]["nodes"][0]
    assert result_node is not source_node
    assert result_node["children"][0] is not source_node["children"][0]
    assert result_node["realization"] is not source_node["realization"]
    assert result["resources"] is not document["resources"]


def test_identity_is_registered_by_stable_id() -> None:
    assert resolve(IDENTITY_ID) is identity
    assert apply_transform({"nodes": []}, IDENTITY_ID) == {"nodes": []}


def test_transform_options_are_passed_only_to_option_aware_transforms() -> None:
    received = {}

    def configured(document: dict, *, options: dict) -> dict:
        received.update(options)
        return document

    transform_id = "test.configured-options.v1"
    TRANSFORMS[transform_id] = configured
    try:
        document = {"nodes": []}
        assert apply_transform(document, transform_id, {"collision_span": {"left": 40}}) is document
        assert received == {"collision_span": {"left": 40}}
    finally:
        TRANSFORMS.pop(transform_id, None)
