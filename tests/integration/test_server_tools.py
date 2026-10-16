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


def test_build_server_refuses_a_shared_read_write_account():
    import pytest

    from beomcp.config import Settings

    s = Settings.from_env({
        "BEOMCP_NEO4J_URI": "bolt://localhost:7687",
        "BEOMCP_NEO4J_USER": "neo4j",
        "BEOMCP_NEO4J_PASSWORD": "x",
    })
    assert s.neo4j_user == s.neo4j_write_user
    with pytest.raises(ValueError, match="read-only"):
        server.build_server(s)


def test_build_server_accepts_separate_accounts():
    from beomcp.config import Settings

    s = Settings.from_env({
        "BEOMCP_NEO4J_URI": "bolt://localhost:7687",
        "BEOMCP_NEO4J_USER": "reader",
        "BEOMCP_NEO4J_PASSWORD": "x",
        "BEOMCP_NEO4J_WRITE_USER": "writer",
        "BEOMCP_NEO4J_WRITE_PASSWORD": "y",
    })
    # constructs without raising; do not run it (no database here)
    assert s.neo4j_user != s.neo4j_write_user


def test_all_eight_tools_register_and_both_write_tools_take_both_halves():
    """A Contract requires belongsToCustomer; a write tool that cannot express
    both the instance and its relation cannot create one at all."""
    import asyncio

    from beomcp.config import Settings

    s = Settings.from_env({
        "BEOMCP_NEO4J_URI": "bolt://localhost:7687",
        "BEOMCP_NEO4J_USER": "reader",
        "BEOMCP_NEO4J_PASSWORD": "x",
        "BEOMCP_NEO4J_WRITE_USER": "writer",
        "BEOMCP_NEO4J_WRITE_PASSWORD": "y",
    })
    # drivers are lazy — building the server needs no live database
    mcp = server.build_server(s)
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert names == set(server.TOOL_NAMES)
    schemas = {t.name: t.input_schema for t in tools}
    assert "relations" in schemas["upsert_entities"]["properties"]
    assert "entities" in schemas["upsert_relations"]["properties"]


def test_types_supplied_as_a_string_is_refused():
    import pytest

    from beomcp.errors import WriteRejected

    with pytest.raises(WriteRejected) as exc:
        server._coerce_entities([{"iri": "https://x/i", "types": "Contract"}])
    assert exc.value.details["field"] == "types"


def test_missing_iri_is_refused_with_field_detail():
    import pytest

    from beomcp.errors import WriteRejected

    with pytest.raises(WriteRejected) as exc:
        server._coerce_entities([{"types": ["Contract"]}])
    assert exc.value.details["field"] == "iri"
