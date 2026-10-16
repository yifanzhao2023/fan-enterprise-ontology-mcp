from pathlib import Path

import pytest

from beomcp.catalog import Catalog
from beomcp.config import Settings
from beomcp.neo4j_store import Neo4jStore
from beomcp.ports import Entity, Relation, WriteBatch

TBox = "https://example.org/onto#"
ID = "https://example.org/id/"


@pytest.fixture(scope="module")
def store():
    settings = Settings.from_env()
    s = Neo4jStore(settings)
    yield s
    s.close()


@pytest.fixture(scope="module")
def seeded(store):
    """Load the sample ABox. Cleared first so a re-run against a persistent
    volume still compares against the manifest's sample_result values."""
    with store._write.session() as session:
        session.run("MATCH (n) DETACH DELETE n").consume()
    batch = WriteBatch(
        entities=[
            Entity(ID + "customerA", (TBox + "Customer",), {"hasName": "客户A"}),
            Entity(ID + "departmentSales", (TBox + "Department",), {}),
            Entity(ID + "employeeZhang", (TBox + "Employee",), {}),
            Entity(ID + "contract001", (TBox + "Contract",),
                   {"hasAmount": 1200000, "hasStatus": "draft", "archived": False}),
            Entity(ID + "wo001", (TBox + "WorkOrder",), {}),
        ],
        relations=[
            Relation(ID + "contract001", TBox + "belongsToCustomer", ID + "customerA"),
            Relation(ID + "departmentSales", TBox + "hasHead", ID + "employeeZhang"),
            Relation(ID + "employeeZhang", TBox + "worksFor", ID + "departmentSales"),
            Relation(ID + "wo001", TBox + "belongsToDepartment", ID + "departmentSales"),
            Relation(ID + "wo001", TBox + "hasOwner", ID + "employeeZhang"),
        ],
    )
    store.upsert(batch)
    return True


def test_every_cq_matches_its_sample_result(store, seeded):
    cat = Catalog.load(Path("ontology"))
    for d in cat.list():
        rows = cat.run(store, d.name, d.sample_params)
        assert list(rows) == list(d.sample_result), f"{d.cqid} {d.name}"


def test_projection_gate_is_clean():
    import json
    import subprocess
    import sys

    before = Path("ontology/schema.projection.json").read_text(encoding="utf-8")
    subprocess.run([sys.executable, "-m", "beomcp.cli", "project"], check=True)
    after = Path("ontology/schema.projection.json").read_text(encoding="utf-8")
    assert before == after, "schema.projection.json is out of date — run `onto project` and commit it"
    assert json.loads(after)["version"] == 1
