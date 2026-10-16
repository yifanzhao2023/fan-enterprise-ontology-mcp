from feomcp.playbook import QUERY_PLAYBOOK, WRITE_PLAYBOOK


def test_query_playbook_states_the_order_of_operations():
    order = ["search_schema", "list_queries", "run_query", "query_raw"]
    positions = [QUERY_PLAYBOOK.index(name) for name in order]
    assert positions == sorted(positions), "the playbook must read schema first, raw cypher last"


def test_query_playbook_says_not_to_guess_field_names():
    assert "search_schema" in QUERY_PLAYBOOK
    assert "guess" in QUERY_PLAYBOOK.lower()


def test_query_playbook_documents_retryable_semantics():
    assert "retryable" in QUERY_PLAYBOOK
    assert "identical request" in QUERY_PLAYBOOK
    assert "must change" in QUERY_PLAYBOOK


def test_query_playbook_names_every_error_code():
    for code in [
        "SCHEMA_NOT_FOUND", "QUERY_SYNTAX", "QUERY_REJECTED", "QUERY_LIMIT",
        "VALIDATION_FAILED", "WRITE_REJECTED", "BACKEND_UNAVAILABLE",
    ]:
        assert code in QUERY_PLAYBOOK, code


def test_query_playbook_states_the_guard_rules():
    for rule in ["read-only", "APOC", "LIMIT", "parameters"]:
        assert rule.lower() in QUERY_PLAYBOOK.lower(), rule


def test_write_playbook_requires_relations_to_be_co_supplied():
    assert "relations" in WRITE_PLAYBOOK
    assert "belongsToCustomer" in WRITE_PLAYBOOK or "minCount" in WRITE_PLAYBOOK


def test_write_playbook_states_batch_atomicity():
    assert "atomic" in WRITE_PLAYBOOK.lower()
    assert "FEOMCP_MAX_BATCH" in WRITE_PLAYBOOK


def test_write_playbook_limits_write_time_validation_to_core():
    for kind in ["minCount", "maxCount", "datatype", "class"]:
        assert kind in WRITE_PLAYBOOK, kind
    assert "Core" in WRITE_PLAYBOOK


def test_write_playbook_warns_derived_facts_stay_stale():
    assert "INFERRED" in WRITE_PLAYBOOK
    assert "stale" in WRITE_PLAYBOOK.lower()


def test_write_playbook_says_to_verify_by_reading_back():
    assert "read" in WRITE_PLAYBOOK.lower()
    assert "applied" in WRITE_PLAYBOOK
