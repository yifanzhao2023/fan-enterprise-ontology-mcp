from beomcp.redact import redact_rows


def test_redacted_field_is_masked():
    out = redact_rows([{"contract_iri": "x", "amount": 1200000}], ["amount"])
    assert out == [{"contract_iri": "x", "amount": "***"}]


def test_empty_field_list_is_a_no_op():
    rows = [{"a": 1}]
    assert redact_rows(rows, []) == rows


def test_input_rows_are_not_mutated():
    rows = [{"amount": 1}]
    redact_rows(rows, ["amount"])
    assert rows == [{"amount": 1}]
