from feomcp.redact import redact_rows


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


def test_nested_keys_are_masked():
    rows = [{"c": {"uri": "x", "hasAmount": 1200000}}, {"hasAmount": 5}]
    out = redact_rows(rows, ["hasAmount"])
    assert out[0]["c"]["hasAmount"] == "***"
    assert out[0]["c"]["uri"] == "x"
    assert out[1]["hasAmount"] == "***"


def test_keys_inside_lists_are_masked():
    rows = [{"items": [{"hasAmount": 1}, {"hasAmount": 2}]}]
    out = redact_rows(rows, ["hasAmount"])
    assert [i["hasAmount"] for i in out[0]["items"]] == ["***", "***"]
