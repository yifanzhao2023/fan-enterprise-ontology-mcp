from pathlib import Path

import pytest

from beomcp.catalog import Catalog
from beomcp.config import Settings
from beomcp.neo4j_store import Neo4jStore
from beomcp.seed import load_abox

@pytest.fixture(scope="module")
def store():
    settings = Settings.from_env()
    s = Neo4jStore(settings)
    yield s
    s.close()


@pytest.fixture(scope="module")
def seeded(store):
    """Load the sample ABox from ontology/abox.ttl — the same file `make seed`
    writes, so the demo and the suite cannot drift apart. Cleared first so a
    re-run against a persistent volume still matches the manifest samples."""
    with store._write.session() as session:
        session.run("MATCH (n) DETACH DELETE n").consume()
    store.upsert(load_abox(Path("ontology/abox.ttl")))
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
