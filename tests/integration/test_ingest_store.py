"""Ingest against a real Neo4j: ABox + :IngestJob in one transaction.

The provenance rule is the point: a successful import leaves both; a failed
one leaves the row and no ABox change.
"""
from datetime import datetime

import pytest

from feomcp.errors import ValidationFailed
from feomcp.ports import Entity, IngestJobMeta, Relation, WriteBatch

ONTO = "https://example.org/onto#"


def _job(**kw):
    base = dict(
        job_id="ingest-abc123",
        input_hash="h",
        source_id="erp",
        source_version="1",
        snapshot_time="2026-10-17T00:00:00Z",
        actor="alice",
        total=1,
        succeeded=1,
        failed=0,
        error_details=(),
        mapping_ref="erp.yaml@1",
    )
    base.update(kw)
    return IngestJobMeta(**base)


def _customer(iri):
    return Entity(
        iri=iri,
        types=(ONTO + "Customer",),
        properties={"hasName": "ACME"},
    )


def test_ingest_writes_abox_and_job(store):
    iri = "https://example.org/id/erp/customer/1"
    store.ingest(WriteBatch(entities=[_customer(iri)]), job=_job())
    job = store._read_rows(
        "MATCH (j:IngestJob {job_id: $id}) RETURN j.status AS s, j.total AS t,"
        " j.actor AS a, j.source_id AS src",
        {"id": "ingest-abc123"},
    )
    assert job == [{"s": "ok", "t": 1, "a": "alice", "src": "erp"}]
    assert store._read_rows(
        "MATCH (n {uri: $u}) RETURN count(n) AS c", {"u": iri}
    ) == [{"c": 1}]
    # the field-level change log points back at the job
    changes = store._read_rows(
        "MATCH (e:ChangeEvent) WHERE e.subject = $u RETURN e.actor AS a",
        {"u": iri},
    )
    assert changes and changes[0]["a"] == "ingest:ingest-abc123"


def test_validation_failure_leaves_no_abox_but_a_failed_job(store):
    iri = "https://example.org/id/erp/customer/bad"
    # CustomerShape requires hasName; an empty properties map violates minCount
    with pytest.raises(ValidationFailed):
        store.ingest(
            WriteBatch(entities=[Entity(iri=iri, types=(ONTO + "Customer",))]),
            job=_job(job_id="ingest-bad", total=1, succeeded=0, failed=1),
        )
    assert store._read_rows(
        "MATCH (n {uri: $u}) RETURN count(n) AS c", {"u": iri}
    ) == [{"c": 0}]
    rows = store._read_rows(
        "MATCH (j:IngestJob {job_id: 'ingest-bad'}) RETURN j.status AS s",
        {},
    )
    assert rows == [{"s": "failed"}]


def test_duplicate_iri_is_idempotent(store):
    iri = "https://example.org/id/erp/customer/dup"
    store.ingest(WriteBatch(entities=[_customer(iri)]), job=_job(job_id="ingest-a"))
    store.ingest(WriteBatch(entities=[_customer(iri)]), job=_job(job_id="ingest-b"))
    assert store._read_rows(
        "MATCH (n {uri: $u}) RETURN count(n) AS c", {"u": iri}
    ) == [{"c": 1}]
    # both attempts are audited
    assert store._read_rows(
        "MATCH (j:IngestJob) WHERE j.job_id IN ['ingest-a','ingest-b'] "
        "RETURN count(j) AS c",
        {},
    ) == [{"c": 2}]


def test_datetime_roundtrip(store, tmp_path):
    customer = "https://example.org/id/erp/customer/dt"
    contract = "https://example.org/id/erp/contract/dt"
    store.ingest(
        WriteBatch(
            entities=[
                _customer(customer),
                Entity(
                    iri=contract,
                    types=(ONTO + "Contract",),
                    properties={
                        "hasAmount": 10.0,
                        "hasStatus": "active",
                        "hasEndDate": datetime(2027, 1, 1, 9, 30),  # bridge produces naive datetimes
                    },
                ),
            ],
            relations=[Relation(contract, ONTO + "belongsToCustomer", customer)],
        ),
        job=_job(job_id="ingest-dt"),
    )
    dest = tmp_path / "abox.ttl"
    store.materialize_rdf(dest)
    text = dest.read_text(encoding="utf-8")
    assert "hasEndDate" in text
    assert "dateTime" in text
