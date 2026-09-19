from __future__ import annotations

import base64

import pikepdf

from tools.extract_pdf_ir import scalar, structured_operations


def op(ordinal: int, operator: str, operands: list[object] | None = None) -> dict[str, object]:
    return {"ordinal": ordinal, "operator": operator, "operands": operands or []}


def test_structured_operations_preserves_syntactic_nesting_and_ordinals() -> None:
    tree = structured_operations([
        op(0, "q"),
        op(1, "cm", [1, 0, 0, 1, 5, 7]),
        op(2, "BT"),
        op(3, "Tm", [1, 0, 0, 1, 10, 20]),
        op(4, "TJ"),
        op(5, "ET"),
        op(6, "Q"),
    ])

    assert tree["schema"] == "pdf-training-structured-operations-v1"
    graphics = tree["children"][0]
    assert graphics["scope"] == "graphics_state"
    assert graphics["open_ordinal"] == 0
    assert graphics["close_ordinal"] == 6
    text = graphics["children"][1]
    assert text["scope"] == "text_state"
    assert text["open_ordinal"] == 2
    assert text["close_ordinal"] == 5
    assert [item["source_ordinal"] for item in text["children"]] == [3, 4]
    assert text["children"][0]["effective_state"]["text"]["in_text"] is True
    assert text["children"][0]["effective_state"]["text"]["text_matrix"] == [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    assert text["children"][1]["effective_state"]["text"]["text_matrix"] == [1.0, 0.0, 0.0, 1.0, 10.0, 20.0]
    assert text["children"][1]["effective_state"]["text"]["font"] is None
    assert tree["diagnostics"] == []


def test_structured_operations_retains_unmatched_delimiters_as_operations() -> None:
    tree = structured_operations([op(0, "ET"), op(1, "BT")])

    assert [item["operator"] for item in tree["children"] if item["type"] == "operation"] == ["ET"]
    assert tree["children"][1]["open_ordinal"] == 1
    assert tree["diagnostics"] == [{"kind": "unmatched_scope_close", "operator": "ET", "ordinal": 0}, {
        "kind": "unclosed_scope", "scope": "text_state", "open_operator": "BT", "open_ordinal": 1,
    }]


def test_structured_operations_have_exact_raw_parity() -> None:
    from tools.extract_pdf_ir import flatten_structured_operations, validate_operation_parity

    operations = [op(0, "q"), op(1, "BT"), op(2, "TJ"), op(3, "ET"), op(4, "Q")]
    tree = structured_operations(operations)

    assert flatten_structured_operations(tree, operations) == [0, 1, 2, 3, 4]
    assert validate_operation_parity(tree, operations) == []


def test_scalar_preserves_raw_pdf_string_bytes() -> None:
    value = scalar(pikepdf.String(b"\x01\x1b"))
    assert value["value"] == "\x01˙"
    assert base64.b64decode(value["raw_bytes_b64"]) == b"\x01\x1b"


def test_operation_parity_detects_missing_and_reordered_ordinals() -> None:
    from tools.extract_pdf_ir import validate_operation_parity

    operations = [op(0, "q"), op(1, "m"), op(2, "S"), op(3, "Q")]
    tree = structured_operations(operations)
    tree["children"][0]["children"].reverse()
    tree["children"][0]["close_ordinal"] = 2

    errors = validate_operation_parity(tree, operations)
    assert any("operation order mismatch" in error for error in errors)
    assert any("same ordinals" in error for error in errors)
