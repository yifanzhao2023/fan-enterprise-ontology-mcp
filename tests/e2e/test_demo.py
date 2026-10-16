"""End-to-end: seed the sample ABox, run the chain, check every CQ.

This is the integration gate. `make demo` runs it against a live Neo4j.
"""
from pathlib import Path

import pytest

from feomcp.catalog import Catalog
from feomcp.config import Settings
from feomcp.neo4j_store import Neo4jStore
from feomcp.seed import load_abox

EX = "https://example.org/id/"
TBox = "https://example.org/onto#"


@pytest.fixture(scope="module")
def store():
    s = Settings.from_env()
    st = Neo4jStore(s)
    st.upsert(load_abox(Path("ontology/abox.ttl")))
    yield st
    st.close()


@pytest.fixture(scope="module")
def catalog():
    return Catalog.load(Path("ontology"))


def _norm(value):
    """Neo4j hands back typed temporal objects; the manifest records strings."""
    return value if isinstance(value, (str, int, float, bool, type(None))) else str(value)


def _norm_rows(rows):
    return [{k: _norm(v) for k, v in dict(r).items()} for r in rows]


def test_every_cq_matches_its_sample_result(store, catalog):
    for d in catalog.list():
        if d.sample_result is None:
            continue  # e.g. a query over the run-timestamped change log
        params = {p.name: p.default for p in d.params if not p.required}
        params.update(d.sample_params)
        rows = _norm_rows(catalog.run(store, d.name, params))
        assert rows == _norm_rows(d.sample_result), f"{d.cqid} {d.name}"


def test_inference_derives_key_contract_and_the_dept_head_owner(store):
    """What the two business rules promise, checked against the live graph."""
    rows = store.submit("MATCH (c:KeyContract) RETURN c.uri AS u ORDER BY u", {})
    assert {r["u"] for r in rows} == {
        EX + "contract001", EX + "contract004", EX + "contract005",
    }

    rows = store.submit(
        "MATCH (w:WorkOrder)-[r:hasOwner]->(e) WHERE r.inferred = true "
        "RETURN w.uri AS w, e.uri AS e ORDER BY w, e",
        {},
    )
    # Only edges the ABox does not already assert can be marked inferred:
    #   wo001's derived owner is employeeZhang, which the ABox already says -> absorbed
    #   wo004's derived owner is employeeLi, which the ABox already says  -> absorbed
    #   wo002/wo003 gain employeeZhang as a second owner -> new derived edges
    assert {(r["w"], r["e"]) for r in rows} == {
        (EX + "wo002", EX + "employeeZhang"),
        (EX + "wo003", EX + "employeeZhang"),
    }


def test_rebuild_keeps_the_abox_labels(store):
    """A derived type is an annotation. Rebuilding must not strip what was
    asserted, or the next export loses it and the layer oscillates."""
    rows = store.submit(
        "MATCH (n {uri: $u}) RETURN labels(n) AS ls", {"u": EX + "contract001"},
    )
    labels = set(rows[0]["ls"])
    assert "Contract" in labels          # asserted
    assert "KeyContract" in labels       # derived
    assert "INFERRED" in labels          # marker

    rows = store.submit(
        "MATCH (n {uri: $u}) RETURN n.archived AS a", {"u": EX + "contract001"},
    )
    assert rows[0]["a"] is False
