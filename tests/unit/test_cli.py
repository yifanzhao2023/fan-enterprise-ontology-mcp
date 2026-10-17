"""`onto import`: wiring only. The bridge itself is tested in test_ingest.py."""
import json
from pathlib import Path

import pytest

import feomcp.cli as cli
import feomcp.ingest as ingest_mod
import feomcp.neo4j_store as store_mod
from feomcp.ingest import IngestReport
from feomcp.mapping import Irules, Mapping, load_mapping
from feomcp.config import Settings


MAPPING = """
source: erp
version: 1
records:
  customer:
    class: "https://example.org/onto/Customer"
    iri: {keys: [customer_id]}
    properties:
      - {field: customer_name, predicate: "https://example.org/onto/hasName", datatype: "xsd:string"}
code_tables: {}
"""

IRULES = """
base: "https://example.org/id"
template: "{base}/{source}/{record_type}/{key}"
key_join: "-"
"""


class FakeAdapter:
    def snapshot(self): ...
    def iter_records(self): ...


class FakeStore:
    closed = False

    def __init__(self, settings):
        self.settings = settings

    def close(self):
        FakeStore.closed = True


@pytest.fixture
def ontology_dir(tmp_path, monkeypatch):
    d = tmp_path / "ontology"
    (d / "mapping").mkdir(parents=True)
    (d / "mapping" / "erp.yaml").write_text(MAPPING, encoding="utf-8")
    (d / "mapping" / "irules.yaml").write_text(IRULES, encoding="utf-8")
    (d / "schema.projection.json").write_text(
        json.dumps({
            "classes": [
                {"name": "Customer", "iri": "https://example.org/onto/Customer"},
            ],
            "properties": [
                {"name": "hasName", "iri": "https://example.org/onto/hasName",
                 "kind": "datatype"},
            ],
        }),
        encoding="utf-8",
    )
    return d


def test_cli_import_runs_bridge(monkeypatch, ontology_dir):
    called = {}

    def fake_run_ingest(store, adapter, mapping, irules, *, actor, max_batch):
        called.update(
            adapter=adapter, mapping=mapping, irules=irules,
            actor=actor, max_batch=max_batch,
        )
        return IngestReport(
            job_id="ingest-x", total=1, succeeded=1, failed=0, error_details=()
        )

    monkeypatch.setattr(ingest_mod, "run_ingest", fake_run_ingest)
    monkeypatch.setattr(store_mod, "Neo4jStore", FakeStore)
    monkeypatch.setattr(cli, "_load_adapter", lambda spec: FakeAdapter())

    rc = cli.main([
        "import", "--source", "erp", "--adapter", "fake.mod",
        "--ontology-dir", str(ontology_dir), "--actor", "alice",
    ])
    assert rc == 0
    assert isinstance(called["adapter"], FakeAdapter)
    assert called["mapping"].source == "erp"
    assert called["actor"] == "alice"
    assert FakeStore.closed is True


def test_cli_import_rejects_dangling_mapping(monkeypatch, ontology_dir):
    (ontology_dir / "mapping" / "erp.yaml").write_text(
        MAPPING.replace("Customer\"", "Gone\""), encoding="utf-8"
    )
    monkeypatch.setattr(store_mod, "Neo4jStore", FakeStore)
    monkeypatch.setattr(cli, "_load_adapter", lambda spec: FakeAdapter())
    rc = cli.main([
        "import", "--source", "erp", "--adapter", "fake.mod",
        "--ontology-dir", str(ontology_dir),
    ])
    assert rc == 2


def test_cli_lint_mapping_ok(ontology_dir):
    assert cli.main(["lint-mapping", "--ontology-dir", str(ontology_dir)]) == 0


def test_cli_import_applies_env_base_iri(monkeypatch, ontology_dir):
    from dataclasses import replace

    called = {}

    def fake_run_ingest(store, adapter, mapping, irules, *, actor, max_batch):
        called["irules"] = irules
        return IngestReport(
            job_id="ingest-x", total=0, succeeded=0, failed=0, error_details=()
        )

    monkeypatch.setenv("FEOMCP_BASE_IRI", "https://env.example/id")
    settings = replace(Settings.from_env({}), ontology_dir=ontology_dir)
    monkeypatch.setattr(ingest_mod, "run_ingest", fake_run_ingest)
    monkeypatch.setattr(store_mod, "Neo4jStore", FakeStore)
    monkeypatch.setattr(cli, "_load_adapter", lambda spec: FakeAdapter())
    monkeypatch.setattr(cli, "_settings", lambda args: settings)

    rc = cli.main([
        "import", "--source", "erp", "--adapter", "fake.mod",
        "--ontology-dir", str(ontology_dir),
    ])
    assert rc == 0
    assert called["irules"].base == "https://env.example/id"
