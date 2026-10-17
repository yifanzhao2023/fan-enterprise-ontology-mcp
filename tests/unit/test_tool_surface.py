"""The tool surface is a stability promise: names and order are contract."""

from feomcp.server import TOOL_NAMES


def test_ten_tools_are_registered_in_spec_order():
    assert TOOL_NAMES == (
        "search_schema",
        "get_schema",
        "get_constraints",
        "list_queries",
        "run_query",
        "query_raw",
        "upsert_entities",
        "upsert_relations",
        "get_inference_status",
        "list_violations",
    )


def test_inference_and_violation_tools_sit_last():
    # Tools 1-6 and 9-10 are read-only; 7-8 write. The order documents that.
    assert TOOL_NAMES.index("get_inference_status") == 8
    assert TOOL_NAMES.index("list_violations") == 9
