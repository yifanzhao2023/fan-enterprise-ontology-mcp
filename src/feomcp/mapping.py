"""Mapping artifacts: the data contract between a source and the ontology.

Two YAML files, both in Git and both reviewed like code: one maps a source's
fields to ontology terms, the other fixes how entity IRIs are built. Nothing
here knows which ERP it is talking to.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping as TypingMapping

import yaml

from .schema import SchemaCatalog


@dataclass(frozen=True)
class PropertyMap:
    field: str
    predicate: str
    datatype: str | None = None
    format: str | None = None


@dataclass(frozen=True)
class RelationMap:
    field: str
    predicate: str
    code_table: str


@dataclass(frozen=True)
class RecordMap:
    class_: str | None
    class_by_field: str | None
    class_values: TypingMapping[str, str]
    iri_keys: tuple[str, ...]
    properties: tuple[PropertyMap, ...]
    relations: tuple[RelationMap, ...]


@dataclass(frozen=True)
class CodeTable:
    class_: str
    values: TypingMapping[str, str]


@dataclass(frozen=True)
class Mapping:
    name: str
    source: str
    version: int
    records: TypingMapping[str, RecordMap]
    code_tables: TypingMapping[str, CodeTable]
    digest: str

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True)
class Irules:
    base: str
    template: str
    key_join: str
    digest: str


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def load_mapping(path: Path) -> Mapping:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    records = {}
    for record_type, spec in (raw.get("records") or {}).items():
        iri = spec.get("iri") or {}
        records[record_type] = RecordMap(
            class_=spec.get("class"),
            class_by_field=spec.get("class_by_field"),
            class_values=dict(spec.get("class_values") or {}),
            iri_keys=tuple(iri.get("keys") or ()),
            properties=tuple(
                PropertyMap(
                    field=p["field"],
                    predicate=p["predicate"],
                    datatype=p.get("datatype"),
                    format=p.get("format"),
                )
                for p in (spec.get("properties") or ())
            ),
            relations=tuple(
                RelationMap(
                    field=r["field"],
                    predicate=r["predicate"],
                    code_table=r["code_table"],
                )
                for r in (spec.get("relations") or ())
            ),
        )
    code_tables = {
        name: CodeTable(class_=spec["class"], values=dict(spec.get("values") or {}))
        for name, spec in (raw.get("code_tables") or {}).items()
    }
    return Mapping(
        name=path.name,
        source=raw["source"],
        version=int(raw["version"]),
        records=records,
        code_tables=code_tables,
        digest=_digest(path),
    )


def load_irules(path: Path) -> Irules:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Irules(
        base=raw["base"],
        template=raw["template"],
        key_join=raw.get("key_join", "-"),
        digest=_digest(path),
    )


def validate_mapping(mapping: Mapping, schema: SchemaCatalog) -> list[str]:
    """Terms the mapping references that the TBox no longer defines.

    A dangling mapping would only surface as a silent dropped relation, so it
    is refused before the first record is read.
    """
    referenced: set[str] = set()
    for record in mapping.records.values():
        if record.class_:
            referenced.add(record.class_)
        referenced.update(record.class_values.values())
        referenced.update(p.predicate for p in record.properties)
        referenced.update(r.predicate for r in record.relations)
    for table in mapping.code_tables.values():
        referenced.add(table.class_)
    known = {c["iri"] for c in schema._classes.values()}
    known |= {p["iri"] for p in schema._properties.values()}
    return sorted(referenced - known)
