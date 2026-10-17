"""Reference SourceAdapter over an Odoo snapshot.

This file is the proof that the adapter boundary is implementable outside
`feomcp`: it only fetches and flattens. A live deployment swaps the JSON read
for Odoo's XML-RPC `execute_kw` (or the JSON-2 REST endpoint) and keeps the same
two methods.
"""

import json
from pathlib import Path
from typing import Iterator

# Imported lazily from the project package when run through `onto import`.
from feomcp.adapter import Record, SourceSnapshot


class OdooSnapshotAdapter:
    def __init__(self, path: Path) -> None:
        self._path = path
        data = json.loads(path.read_text(encoding="utf-8"))
        self._meta = data
        self._records = data["records"]

    def snapshot(self) -> SourceSnapshot:
        return SourceSnapshot(
            source_id="odoo",
            snapshot_time=self._meta["snapshot_time"],
            version=self._meta.get("version"),
        )

    def iter_records(self) -> Iterator[Record]:
        for row in self._records:
            yield Record(record_type=row["record_type"], fields=row["fields"])


def build_adapter(path: Path | None = None) -> OdooSnapshotAdapter:
    return OdooSnapshotAdapter(path or Path(__file__).with_name("odoo_records.json"))
