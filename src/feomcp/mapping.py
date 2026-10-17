"""Mapping artifacts: the data contract between a source and the ontology.

Two YAML files, both in Git and both reviewed like code: one maps a source's
fields to ontology terms, the other fixes how entity IRIs are built. Nothing
here knows which ERP it is talking to.
"""

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping as TypingMapping, Sequence
from urllib.parse import quote

import yaml

from .adapter import Record
from .ports import Entity, Relation
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
    # exactly one: a code table (enum individual) or another record type
    # whose IRI is built from this field (a foreign key)
    code_table: str | None = None
    target_record: str | None = None


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
                    code_table=r.get("code_table"),
                    target_record=r.get("target_record"),
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


# --- record application ------------------------------------------------------

class RecordError(Exception):
    """One record could not be mapped. The bridge skips it and counts it; the
    batch keeps going. Never used for a batch-level failure."""


XSD_PREFIX = "http://www.w3.org/2001/XMLSchema#"


def _local(iri: str) -> str:
    text = str(iri)
    # mapping authors write `xsd:decimal`; the SHACL check speaks full IRIs
    if text.startswith("xsd:"):
        return text[len("xsd:"):]
    return text.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def coerce_value(value: Any, datatype: str | None, fmt: str | None = None) -> Any:
    """The only place raw source strings become typed values.

    Agent upserts never pass through here: they must send correctly typed
    values. A conversion failure is a record-level error, never a silent
    fallback to the raw string.
    """
    if datatype is None:
        return value
    if value is None or value == "":
        raise RecordError(f"empty value for {datatype}")
    name = _local(datatype)
    try:
        if name == "string":
            return str(value)
        if name == "integer":
            if isinstance(value, bool):
                raise ValueError("bool is not an integer here")
            return int(value)
        if name == "decimal":
            if isinstance(value, bool):
                raise ValueError("bool is not a decimal here")
            return float(value)
        if name == "boolean":
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in ("true", "1", "yes"):
                return True
            if text in ("false", "0", "no"):
                return False
            raise ValueError("not a boolean")
        if name == "dateTime":
            if isinstance(value, datetime):
                return value
            return (datetime.strptime(str(value), fmt) if fmt
                    else datetime.fromisoformat(str(value)))
        if name == "date":
            if isinstance(value, date) and not isinstance(value, datetime):
                return value
            if fmt:
                return datetime.strptime(str(value), fmt).date()
            return date.fromisoformat(str(value))
    except (ValueError, TypeError) as exc:
        raise RecordError(f"cannot coerce {value!r} to {name}: {exc}") from exc
    raise RecordError(f"unsupported datatype {datatype!r}")


def build_iri(
    irules: Irules,
    *,
    source: str,
    record_type: str,
    key_values: Sequence[str],
) -> str:
    if not key_values or any(v is None or str(v) == "" for v in key_values):
        raise RecordError(f"{record_type}: missing IRI key value")
    key = irules.key_join.join(quote(str(v), safe="") for v in key_values)
    return irules.template.format(
        base=irules.base, source=source, record_type=record_type, key=key
    )


def apply_record(
    mapping: Mapping, irules: Irules, record: Record
) -> tuple[Entity, list[Relation], list[Entity]]:
    """Map one raw record into an entity, its relations and any code-table nodes.

    Returns code-table individuals referenced by this record; the caller appends
    them to the batch so a relation never points at a node that does not exist.
    """
    spec = mapping.records.get(record.record_type)
    if spec is None:
        raise RecordError(f"unknown record_type {record.record_type!r}")

    if spec.class_ is not None:
        class_iri = spec.class_
    else:
        if spec.class_by_field is None:
            raise RecordError(f"{record.record_type}: no class or class_by_field")
        raw_class = record.fields.get(spec.class_by_field)
        if raw_class is None:
            raise RecordError(
                f"{record.record_type}: missing class field {spec.class_by_field!r}"
            )
        class_iri = spec.class_values.get(str(raw_class))
        if class_iri is None:
            raise RecordError(
                f"{record.record_type}: {spec.class_by_field}={raw_class!r} "
                "has no class mapping"
            )

    key_values = []
    for key in spec.iri_keys:
        if key not in record.fields:
            raise RecordError(f"{record.record_type}: missing IRI key {key!r}")
        key_values.append(record.fields[key])
    iri = build_iri(
        irules, source=mapping.source, record_type=record.record_type,
        key_values=key_values,
    )

    properties: dict[str, Any] = {}
    for prop in spec.properties:
        if prop.field not in record.fields:
            raise RecordError(
                f"{record.record_type}: missing field {prop.field!r}"
            )
        properties[_local(prop.predicate)] = coerce_value(
            record.fields[prop.field], prop.datatype, prop.format
        )

    relations: list[Relation] = []
    code_nodes: dict[str, Entity] = {}
    for rel in spec.relations:
        if rel.field not in record.fields:
            raise RecordError(f"{record.record_type}: missing field {rel.field!r}")
        raw = record.fields[rel.field]
        if rel.code_table is not None:
            table = mapping.code_tables.get(rel.code_table)
            if table is None:
                raise RecordError(
                    f"{record.record_type}: unknown code_table {rel.code_table!r}"
                )
            target = table.values.get(str(raw))
            if target is None:
                raise RecordError(
                    f"{record.record_type}: {rel.code_table}={raw!r} has no mapping"
                )
            relations.append(Relation(iri, rel.predicate, target))
            code_nodes.setdefault(target, Entity(iri=target, types=(table.class_,)))
            continue
        if rel.target_record is not None:
            target_spec = mapping.records.get(rel.target_record)
            if target_spec is None:
                raise RecordError(
                    f"{record.record_type}: unknown target_record {rel.target_record!r}"
                )
            if len(target_spec.iri_keys) != 1:
                raise RecordError(
                    f"{record.record_type}: target_record {rel.target_record!r} "
                    "needs exactly one IRI key"
                )
            target = build_iri(
                irules, source=mapping.source,
                record_type=rel.target_record, key_values=[raw],
            )
            relations.append(Relation(iri, rel.predicate, target))
            continue
        raise RecordError(
            f"{record.record_type}: relation {rel.field!r} needs code_table "
            "or target_record"
        )

    return Entity(iri=iri, types=(class_iri,), properties=properties), relations, list(code_nodes.values())
