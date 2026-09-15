import pikepdf

from tools.render_pdf_ir import Resolver, render_recursive_operation_groups


def test_context_stack_closes_for_page_owned_sparse_gaps():
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(100, 100))
    operations = [
        {"ordinal": 0, "operator": "m", "operands": [0, 0]},
        {"ordinal": 1, "operator": "m", "operands": [1, 1]},
        {"ordinal": 2, "operator": "l", "operands": [2, 2]},
        {"ordinal": 3, "operator": "c", "operands": [3, 3, 3, 3, 3, 3]},
        {"ordinal": 4, "operator": "v", "operands": [4, 4, 4, 4]},
    ]
    groups = [{
        "id": "outer",
        "type": "group",
        "operation_ordinals": [1, 4],
        "children": [{"id": "inner", "type": "group", "operation_ordinals": [3]}],
    }]

    stream, rendered = render_recursive_operation_groups(pdf, page, Resolver(pdf, {}), operations, groups)
    operators = [str(instruction.operator) for instruction in pikepdf.parse_content_stream(stream)]

    assert rendered == 2
    assert operators == ["m", "q", "m", "Q", "l", "q", "q", "c", "Q", "v", "Q"]
