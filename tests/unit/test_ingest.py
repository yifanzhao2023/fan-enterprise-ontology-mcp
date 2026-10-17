"""The ingest bridge: adapter records -> mapping -> one WriteBatch.

The store is faked so the record-level vs batch-level failure split can be
tested without a database: a bad row is skipped and counted, a failed batch
records an IngestJob(failed) and writes nothing.
"""
from pathlib import Path

import pytest

from feomcp.adapter import Record, SourceSnapshot
from feomcp.errors import WriteRejected
from feomcp.ingest import run_ingest
from feomcp.mapping import Irules, load_mapping

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

IRULES = Irules(
    base="https://example.org/id",
    template="{base}/{source}/{record_type}/{key}",
    key_join="-",
    digest="rules",
)


class FakeStore:
    def __init__(self, *, fail_ingest: bool = False):
        self.ingested = None
        self.failed = None
        self.fail_ingest = fail_ingest

    def ingest(self, batch, *, job):
        if self.fail_ingest:
            raise RuntimeError("store exploded")
        self.ingested = (batch, job)

        class Receipt:
            written_iris = ()
            skipped = ()
            validation = {"violations": []}
            applied = True

        return Receipt()

    def record_ingest_failure(self, job, *, error):
        self.failed = (job, error)


class FakeAdapter:
    def __init__(self, records, *, fail_snapshot: bool = False):
        self._records = records
        self._fail_snapshot = fail_snapshot

    def snapshot(self):
        if self._fail_snapshot:
            raise RuntimeError("source unreachable")
        return SourceSnapshot("erp", "2026-10-17T00:00:00Z", "1")

    def iter_records(self):
        return iter(self._records)


@pytest.fixture
def mapping(tmp_path: Path):
    p = tmp_path / "erp.yaml"
    p.write_text(MAPPING, encoding="utf-8")
    return load_mapping(p)


def _good(n: int) -> Record:
    return Record("customer", {"customer_id": str(n), "customer_name": f"ACME {n}"})


def test_success_and_record_level_skip(mapping):
    store = FakeStore()
    adapter = FakeAdapter([_good(1), Record("widget", {"id": "x"}), _good(2)])
    report = run_ingest(
        store, adapter, mapping, IRULES, actor="alice", max_batch=100
    )
    assert report.total == 3
    assert report.succeeded == 2
    assert report.failed == 1
    assert report.error_details[0]["record_type"] == "widget"
    batch, job = store.ingested
    assert job.job_id.startswith("ingest-")
    assert job.input_hash and job.actor == "alice"
    assert job.total == 3 and job.succeeded == 2 and job.failed == 1
    assert job.source_id == "erp" and job.snapshot_time == "2026-10-17T00:00:00Z"
    assert job.mapping_ref == "erp.yaml@1"
    assert len(batch.entities) == 2
    assert store.failed is None


def test_unknown_record_type_skipped(mapping):
    store = FakeStore()
    adapter = FakeAdapter([Record("widget", {"id": "x"})])
    report = run_ingest(
        store, adapter, mapping, IRULES, actor="alice", max_batch=100
    )
    assert report.succeeded == 0 and report.failed == 1
    assert store.ingested[0].size() == 0  # all rows skipped, audit row still written


def test_snapshot_failure_is_audited_and_reraised(mapping):
    store = FakeStore()
    adapter = FakeAdapter([], fail_snapshot=True)
    with pytest.raises(RuntimeError, match="source unreachable"):
        run_ingest(store, adapter, mapping, IRULES, actor="alice", max_batch=100)
    job, error = store.failed
    assert job.job_id.startswith("ingest-")
    assert "source unreachable" in error
    assert store.ingested is None


def test_store_failure_is_audited_and_reraised(mapping):
    store = FakeStore(fail_ingest=True)
    with pytest.raises(RuntimeError, match="store exploded"):
        run_ingest(store, FakeAdapter([_good(1)]), mapping, IRULES,
                   actor="alice", max_batch=100)
    _, error = store.failed
    assert "store exploded" in error


def test_over_max_batch_writes_nothing_and_is_audited(mapping):
    store = FakeStore()
    adapter = FakeAdapter([_good(1), _good(2)])
    with pytest.raises(WriteRejected):
        run_ingest(store, adapter, mapping, IRULES, actor="alice", max_batch=1)
    assert store.ingested is None
    assert store.failed is not None
