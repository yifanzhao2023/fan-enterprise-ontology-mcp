"""Write-time SHACL Core subset validation.

Only sh:minCount / sh:maxCount / sh:datatype / sh:class run here, on the affected
subgraph, synchronously. SHACL-AF and SPARQL constraints never run on the write
path — they are too slow, and write time answers one question only: can this
record enter?
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from rdflib import RDF, Graph
from rdflib import Namespace

from .errors import ValidationFailed
from .ports import Entity

SH = Namespace("http://www.w3.org/ns/shacl#")


@dataclass(frozen=True)
class Violation:
    focus_iri: str
    shape_iri: str
    path: str
    message: str
    kind: str

    def to_dict(self) -> dict[str, str]:
        return {
            "focus_iri": self.focus_iri,
            "shape_iri": self.shape_iri,
            "path": self.path,
            "message": self.message,
            "kind": self.kind,
        }


def validate_core(
    entities: Sequence[Entity],
    shapes_path: Path,
    type_index: Mapping[str, Sequence[str]] | None = None,
) -> list[Violation]:
    """type_index maps an IRI to the types the caller knows it carries — from the
    same batch, or from the graph. It lets sh:class check more than "is a string"."""
    shapes = _shapes(shapes_path)
    out: list[Violation] = []
    for entity in entities:
        for shape in _shapes_for(shapes, entity.types):
            out.extend(_check(shapes, shape, entity, type_index))
    return out


def require_valid(
    entities: Sequence[Entity],
    shapes_path: Path,
    type_index: Mapping[str, Sequence[str]] | None = None,
) -> None:
    violations = validate_core(entities, shapes_path, type_index)
    if violations:
        first = violations[0]
        raise ValidationFailed(
            f"{len(violations)} SHACL Core violation(s); first: {first.path} — {first.message}",
            hint="fix the reported fields before resubmitting the batch",
            details={"violations": [v.to_dict() for v in violations]},
        )


def _shapes(path: Path) -> Graph:
    g = Graph()
    g.parse(path, format="turtle")
    return g


def _local(uri: Any) -> str:
    return str(uri).rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def _shapes_for(shapes: Graph, types: Sequence[str]) -> list[Any]:
    wanted = {_local(t) for t in types}
    found = []
    for shape in shapes.subjects(RDF.type, SH.NodeShape):
        target = shapes.value(shape, SH.targetClass)
        if target is not None and _local(target) in wanted:
            found.append(shape)
    return found


def _check(
    shapes: Graph, shape: Any, entity: Entity,
    type_index: Mapping[str, Sequence[str]] | None = None,
) -> list[Violation]:
    out: list[Violation] = []
    for prop_shape in shapes.objects(shape, SH.property):
        path = shapes.value(prop_shape, SH.path)
        if path is None:
            continue
        name = _local(path)
        values = entity.properties.get(name)
        if values is None:
            present: list[Any] = []
        elif isinstance(values, (list, tuple)):
            present = list(values)
        else:
            present = [values]

        min_count = shapes.value(prop_shape, SH.minCount)
        if min_count is not None and len(present) < int(min_count):
            out.append(Violation(entity.iri, str(shape), name,
                                 f"{name} requires at least {int(min_count)} value(s)", "minCount"))
            continue

        max_count = shapes.value(prop_shape, SH.maxCount)
        if max_count is not None and len(present) > int(max_count):
            out.append(Violation(entity.iri, str(shape), name,
                                 f"{name} allows at most {int(max_count)} value(s)", "maxCount"))
            continue

        datatype = shapes.value(prop_shape, SH.datatype)
        if datatype is not None:
            for v in present:
                if not _matches_datatype(v, datatype):
                    out.append(Violation(
                        entity.iri, str(shape), name,
                        f"{name} must be {_local(datatype)}, got {type(v).__name__}", "datatype",
                    ))

        klass = shapes.value(prop_shape, SH["class"])
        if klass is not None:
            for v in present:
                if not _is_iri(v):
                    out.append(Violation(entity.iri, str(shape), name,
                                         f"{name} must reference a {_local(klass)} IRI", "class"))
                elif type_index is not None and v in type_index:
                    wanted = _local(klass)
                    if wanted not in type_index[v]:
                        out.append(Violation(
                            entity.iri, str(shape), name,
                            f"{name} must reference a {wanted}, got {sorted(type_index[v])}",
                            "class",
                        ))
    return out


def _is_iri(value: Any) -> bool:
    """sh:class means "the object is an instance of this class", so at minimum the
    value has to name something. A bare string does not."""
    return isinstance(value, str) and (
        "://" in value or value.startswith(("urn:", "#", "/")) or ":" in value.split("/")[-1]
    )


def _matches_datatype(value: Any, datatype: Any) -> bool:
    name = _local(datatype)
    if name == "decimal":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "string":
        return isinstance(value, str)
    # Fail closed: a datatype we cannot check is a datatype we do not accept.
    return False
