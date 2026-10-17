from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, Sequence

@dataclass
class Entity:
    iri: str
    types: tuple[str, ...]
    properties: Mapping[str, Any] = field(default_factory=dict)

@dataclass
class Relation:
    source_iri: str
    predicate_iri: str
    target_iri: str

@dataclass
class WriteBatch:
    entities: Sequence[Entity] = ()
    relations: Sequence[Relation] = ()
    def size(self) -> int:
        return len(self.entities) + len(self.relations)

@dataclass
class IngestJobMeta:
    """One append-only audit row per import attempt.

    job_id identifies the attempt; input_hash identifies the inputs. Keeping
    them separate lets a retry of the same snapshot add history instead of
    overwriting the failed attempt.
    """
    job_id: str
    input_hash: str
    source_id: str
    source_version: str | None
    snapshot_time: str
    actor: str
    total: int
    succeeded: int
    failed: int
    error_details: tuple[Mapping[str, Any], ...]
    mapping_ref: str


@dataclass
class WriteReceipt:
    written_iris: tuple[str, ...]
    skipped: tuple[str, ...]
    validation: Mapping[str, Any]
    applied: bool

class QueryEngine(Protocol):
    def submit(
        self,
        query: str,
        params: Mapping[str, Any],
        *,
        lang: Literal["cypher", "sparql"] = "cypher",
    ) -> Sequence[Mapping[str, Any]]: ...

class FactStore(Protocol):
    def upsert(self, batch: WriteBatch, *, actor: str = "system") -> WriteReceipt: ...
    # Ingest owns the audit row: the ABox write and :IngestJob(ok) share one
    # transaction, so an import can never leave data without its provenance.
    def ingest(self, batch: WriteBatch, *, job: IngestJobMeta) -> WriteReceipt: ...
    def record_ingest_failure(self, job: IngestJobMeta, *, error: str) -> None: ...
    def materialize_rdf(self, dest: Path) -> None: ...
    def replace_derived(
        self, inferred: Path, violations: Path, *,
        fingerprint: Mapping[str, Any] | None = None, job_id: str | None = None,
    ) -> WriteReceipt: ...
    # Derived-layer bookkeeping. replace_derived already owns that layer, so its
    # failure and freshness siblings live here too rather than in a second port.
    def record_inference_failure(
        self, job_id: str, error: str, **fingerprint: Any
    ) -> None: ...
    def derived_layer_matches(self, **fingerprint: Any) -> bool: ...
    def abox_writes(self) -> int: ...
