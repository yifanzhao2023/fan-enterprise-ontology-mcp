from pathlib import Path

import pytest

from feomcp.config import Settings


def test_defaults_when_env_empty():
    s = Settings.from_env({})
    assert s.store == "neo4j"
    assert s.neo4j_uri == "bolt://localhost:7687"
    assert s.ontology_dir == Path("ontology")
    assert s.query_timeout_ms == 10_000
    assert s.max_rows == 50
    assert s.max_query_length == 8192
    assert s.max_batch == 500
    assert s.redact_fields == ()


def test_env_overrides():
    s = Settings.from_env({
        "FEOMCP_QUERY_TIMEOUT_MS": "2500",
        "FEOMCP_MAX_ROWS": "7",
        "FEOMCP_MAX_QUERY_LENGTH": "128",
        "FEOMCP_MAX_BATCH": "10",
        "FEOMCP_ONTOLOGY_DIR": "data/onto",
        "FEOMCP_REDACT_FIELDS": "amount, owner",
    })
    assert s.query_timeout_ms == 2500
    assert s.max_rows == 7
    assert s.max_query_length == 128
    assert s.max_batch == 10
    assert s.ontology_dir == Path("data/onto")
    assert s.redact_fields == ("amount", "owner")


def test_write_account_falls_back_to_read_account():
    s = Settings.from_env({"FEOMCP_NEO4J_USER": "reader", "FEOMCP_NEO4J_PASSWORD": "pw"})
    assert s.neo4j_user == "reader"
    assert s.neo4j_write_user == "reader"
    assert s.neo4j_write_password == "pw"


def test_non_positive_limits_are_rejected_at_startup():
    for key in ["FEOMCP_QUERY_TIMEOUT_MS", "FEOMCP_MAX_ROWS",
                "FEOMCP_MAX_QUERY_LENGTH", "FEOMCP_MAX_BATCH"]:
        with pytest.raises(ValueError):
            Settings.from_env({key: "0"})


def test_negative_limit_is_rejected():
    with pytest.raises(ValueError):
        Settings.from_env({"FEOMCP_MAX_ROWS": "-1"})


def test_malformed_uri_is_rejected():
    with pytest.raises(ValueError):
        Settings.from_env({"FEOMCP_NEO4J_URI": "not-a-uri"})


def test_bolt_scheme_is_accepted():
    s = Settings.from_env({"FEOMCP_NEO4J_URI": "bolt://db:7687", "FEOMCP_NEO4J_PASSWORD": "pw"})
    assert s.neo4j_uri == "bolt://db:7687"


# --- Task 6: base IRI for generated entity identifiers -----------------------

def test_base_iri_default():
    assert Settings.from_env({}).base_iri == "https://example.org/id"


def test_base_iri_override():
    s = Settings.from_env({"FEOMCP_BASE_IRI": "https://data.example.com/onto"})
    assert s.base_iri == "https://data.example.com/onto"
