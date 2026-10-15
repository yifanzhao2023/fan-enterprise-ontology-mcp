import pytest

from beomcp.errors import QueryLimitExceeded, QueryRejected
from beomcp.guard import check_cypher


def test_plain_match_is_allowed():
    assert check_cypher("MATCH (n:Contract) RETURN n.uri AS uri LIMIT 5") is None


def test_blank_query_rejected_by_guard():
    with pytest.raises(QueryLimitExceeded):
        check_cypher("   \n\t  ")


@pytest.mark.parametrize(
    "q",
    [
        "CREATE (n:Contract {uri: 'x'})",
        "MERGE (n:Contract {uri: 'x'})",
        "MATCH (n) DELETE n",
        "MATCH (n) DETACH DELETE n",
        "MATCH (n) SET n.uri = 'x'",
        "MATCH (n) REMOVE n.uri",
        "DROP CONSTRAINT ON (n:Resource) ASSERT n.uri IS UNIQUE",
        "LOAD CSV FROM 'file:///x.csv' AS row RETURN row",
        "CALL apoc.cypher.run('MATCH (n) RETURN n', {}) YIELD value RETURN value",
        "CALL apoc.periodic.iterate('MATCH (n) RETURN n', 'DELETE n', {})",
        "CALL dbms.security.listUsers() YIELD username RETURN username",
    ],
)
def test_dangerous_queries_are_rejected(q):
    with pytest.raises(QueryRejected):
        check_cypher(q)


def test_whitelisted_read_only_procedure_is_allowed():
    assert check_cypher("CALL db.labels() YIELD label RETURN label LIMIT 5") is None


def test_non_whitelisted_call_is_rejected():
    with pytest.raises(QueryRejected):
        check_cypher("CALL db.idx.fulltext.queryNodes('x', 'y') YIELD node RETURN node")


def test_query_too_long_raises_limit():
    with pytest.raises(QueryLimitExceeded):
        check_cypher("MATCH (n) RETURN n " + " " * 100, max_length=20)


def test_error_carries_a_hint():
    with pytest.raises(QueryRejected) as exc:
        check_cypher("CREATE (n)")
    assert exc.value.hint
    assert exc.value.details["rule"]
