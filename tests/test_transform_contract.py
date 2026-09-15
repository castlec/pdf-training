import pytest

from tools.transform_contract import TransformContractError, validate_transform_result


def document(groups):
    return {
        "schema": "pdf-training-pdf-ir-v1",
        "pages": [{
            "operations": [
                {"ordinal": 0, "kind": "save"},
                {"ordinal": 1, "kind": "restore"},
            ],
            "operation_groups": groups,
        }],
    }


def test_sparse_groups_are_valid_and_do_not_claim_intervening_operations():
    validate_transform_result(document([
        {"id": "g", "type": "group", "operation_ordinals": [1]},
    ]))


def test_duplicate_active_ownership_is_rejected():
    with pytest.raises(TransformContractError, match="multiple active owners"):
        validate_transform_result(document([
            {"id": "a", "type": "group", "operation_ordinals": [0]},
            {"id": "b", "type": "group", "operation_ordinals": [0]},
        ]))


def test_metadata_only_ownership_is_rejected():
    with pytest.raises(TransformContractError, match="metadata-only ownership"):
        validate_transform_result(document([
            {"id": "g", "type": "group", "owned_operation_ordinals": [0]},
        ]))


def test_table_requires_cell_children():
    with pytest.raises(TransformContractError, match="table must contain"):
        validate_transform_result(document([
            {"id": "table", "type": "group", "layout_kind": "table", "children": []},
        ]))
