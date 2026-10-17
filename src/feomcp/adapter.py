"""The external-system boundary.

An adapter is the only per-source code. It fetches and flattens; every
ontology decision (types, predicates, coercion, IRI) belongs to the bridge.
Keeping it this dumb is what lets the mapping live in Git and be replayed.
"""

from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Protocol


@dataclass(frozen=True)
class Record:
    record_type: str
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class SourceSnapshot:
    source_id: str
    snapshot_time: str
    version: str | None = None


class SourceAdapter(Protocol):
    def snapshot(self) -> SourceSnapshot: ...
    def iter_records(self) -> Iterator[Record]: ...
