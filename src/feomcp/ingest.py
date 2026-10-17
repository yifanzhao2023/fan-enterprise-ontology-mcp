"""The import bridge: adapter records in, one WriteBatch out.

Record-level failures (bad key, unknown type, unmapped code, coercion) are
skipped and counted so one dirty row cannot block an ERP extract. Anything
that would leave a half-written batch is a batch-level failure: nothing is
written and an :IngestJob(failed) records why.
"""

import hashlib
from dataclasses import dataclass, replace
from typing import Any, Mapping
from uuid import uuid4

from .adapter import SourceAdapter
from .errors import WriteRejected
from .mapping import Irules, Mapping as MappingArtifact, RecordError, apply_record
from .ports import Entity, Relation, WriteBatch

_MAX_ERROR_DETAILS = 50


@dataclass(frozen=True)
class IngestReport:
    job_id: str
    total: int
    succeeded: int
    failed: int
    error_details: tuple[Mapping[str, Any], ...]


def _input_hash(
    snapshot_source: str, snapshot_time: str, mapping: MappingArtifact, irules: Irules
) -> str:
    blob = "|".join(
        [snapshot_source, snapshot_time, mapping.digest, irules.digest]
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def run_ingest(
    store,
    adapter: SourceAdapter,
    mapping: MappingArtifact,
    irules: Irules,
    *,
    actor: str,
    max_batch: int,
) -> IngestReport:
    from .ports import IngestJobMeta

    job_id = "ingest-" + uuid4().hex[:12]
    try:
        snapshot = adapter.snapshot()
    except Exception as exc:
        job = IngestJobMeta(
            job_id=job_id,
            input_hash="",
            source_id="",
            source_version=None,
            snapshot_time="",
            actor=actor,
            total=0,
            succeeded=0,
            failed=0,
            error_details=(),
            mapping_ref=mapping.ref,
        )
        store.record_ingest_failure(job, error=str(exc)[:300])
        raise

    entities: list[Entity] = []
    relations: list[Relation] = []
    code_nodes: dict[str, Entity] = {}
    details: list[Mapping[str, Any]] = []
    total = succeeded = failed = 0
    batch = WriteBatch()

    job = IngestJobMeta(
        job_id=job_id,
        input_hash=_input_hash(
            snapshot.source_id, snapshot.snapshot_time, mapping, irules
        ),
        source_id=snapshot.source_id,
        source_version=snapshot.version,
        snapshot_time=snapshot.snapshot_time,
        actor=actor,
        total=0,
        succeeded=0,
        failed=0,
        error_details=(),
        mapping_ref=mapping.ref,
    )

    try:
        for record in adapter.iter_records():
            total += 1
            try:
                entity, rels, nodes = apply_record(mapping, irules, record)
            except RecordError as exc:
                failed += 1
                if len(details) < _MAX_ERROR_DETAILS:
                    details.append(
                        {"record_type": record.record_type, "error": str(exc)}
                    )
                continue
            entities.append(entity)
            relations.extend(rels)
            for node in nodes:
                code_nodes.setdefault(node.iri, node)
            succeeded += 1
            if len(entities) + len(relations) + len(code_nodes) > max_batch:
                job = replace(
                    job, total=total, succeeded=succeeded, failed=failed,
                    error_details=tuple(details),
                )
                raise WriteRejected(
                    f"import produces {len(entities) + len(relations) + len(code_nodes)} "
                    f"writes, exceeding limit {max_batch}",
                    hint="raise FEOMCP_MAX_BATCH or narrow the adapter's extract",
                    details={"limit": max_batch},
                )

        if len(details) == _MAX_ERROR_DETAILS:
            details.append({"record_type": "*", "error": "additional failures omitted"})

        batch = WriteBatch(
            entities=entities + list(code_nodes.values()), relations=relations
        )
        job = IngestJobMeta(
            job_id=job_id,
            input_hash=job.input_hash,
            source_id=snapshot.source_id,
            source_version=snapshot.version,
            snapshot_time=snapshot.snapshot_time,
            actor=actor,
            total=total,
            succeeded=succeeded,
            failed=failed,
            error_details=tuple(details),
            mapping_ref=mapping.ref,
        )
        store.ingest(batch, job=job)
    except Exception as exc:
        store.record_ingest_failure(job, error=str(exc)[:300])
        raise

    return IngestReport(
        job_id=job_id,
        total=total,
        succeeded=succeeded,
        failed=failed,
        error_details=tuple(details),
    )
