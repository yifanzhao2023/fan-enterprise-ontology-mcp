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
