"""Neo4jStore.ingest failure reporting, without a database.

The ABox transaction rolled back, so the audit row must not claim anything
was written.
"""
import pytest

from feomcp.neo4j_store import Neo4jStore
from feomcp.ports import IngestJobMeta, WriteBatch


def _job(**kw):
    base = dict(
        job_id="ingest-x", input_hash="h", source_id="erp", source_version="1",
        snapshot_time="2026-10-17T00:00:00Z", actor="alice",
        total=3, succeeded=2, failed=1, error_details=(), mapping_ref="erp@1",
    )
    base.update(kw)
    return IngestJobMeta(**base)


def test_failed_ingest_reports_nothing_committed(monkeypatch):
    store = object.__new__(Neo4jStore)
    captured = {}

    def boom(batch, *, actor, job_meta=None):
        raise RuntimeError("transaction failed")

    monkeypatch.setattr(store, "_upsert", boom)
    monkeypatch.setattr(
        store, "record_ingest_failure",
        lambda job, *, error: captured.update(job=job, error=error),
    )
    with pytest.raises(RuntimeError, match="transaction failed"):
        store.ingest(WriteBatch(), job=_job())
    assert captured["job"].succeeded == 0
    assert captured["job"].failed == 3
    assert captured["job"].total == 3
