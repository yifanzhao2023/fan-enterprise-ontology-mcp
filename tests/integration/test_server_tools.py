import inspect

from beomcp import server


def test_tool_registry_has_eight_tools():
    names = set(server.TOOL_NAMES)
    assert names == {
        "search_schema", "get_schema", "get_constraints",
        "list_queries", "run_query", "query_raw",
        "upsert_entities", "upsert_relations",
    }


def test_no_load_ontology_tool():
    assert "load_ontology" not in server.TOOL_NAMES


def test_main_is_callable():
    assert inspect.isfunction(server.main)


def test_result_envelope_shape_is_stable():
    env = server._envelope([{"a": 1}], truncated=True)
    assert env["@context"]
    assert env["truncated"] is True
    assert env["row_count"] == 1
    assert env["rows"] == [{"a": 1}]


def test_build_server_accepts_principal():
    sig = inspect.signature(server.build_server)
    assert "principal" in sig.parameters


def test_envelope_row_count_counts_before_redaction():
    rows = server._envelope([{"amount": 1, "uri": "x"}], truncated=False)
    assert rows["row_count"] == 1


def test_unexpected_exception_becomes_opaque_payload():
    def boom():
        raise RuntimeError("secret detail /home/user/.ssh/id_rsa")

    out = server._call(
        tool="run_query",
        params={"name": "x"},
        principal="agent",
        audit=server.AuditLogger(sink=lambda r: None),
        fields=(),
        fn=boom,
    )
    assert out["code"] == "BACKEND_UNAVAILABLE"
    assert out["retryable"] is True
    assert "id_rsa" not in out["message"]
    assert "id_rsa" not in out["hint"]
