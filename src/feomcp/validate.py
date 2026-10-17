"""Write-time SHACL Core subset validation.

Only sh:minCount / sh:maxCount / sh:datatype / sh:class run here, on the affected
subgraph, synchronously. SHACL-AF and SPARQL constraints never run on the write
path — they are too slow, and write time answers one question only: can this
record enter?
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from rdflib import RDF, Graph, Literal, URIRef
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


def write_violations(
    violations: Sequence[Violation], dest: Path, job_id: str
) -> None:
    """Serialize violations in the pinned shape.

    One named `sh:ValidationResult` per violation. A blank node would be useless
    as a `:ConstraintViolation.uri` in Neo4j, and a triple soup would make
    `list_violations(focus_iri=...)` impossible to filter.
    """
    g = Graph()
    for i, v in enumerate(violations):
        node = URIRef(f"urn:feomcp:violation:{job_id}:{i}")
        g.add((node, RDF.type, SH.ValidationResult))
        g.add((node, SH.focusNode, URIRef(v.focus_iri)))
        if v.shape_iri:
            g.add((node, SH.resultShape, URIRef(v.shape_iri)))
        if v.path:
            g.add((node, SH.resultPath, URIRef(v.path)))
        g.add((node, SH.resultMessage, Literal(v.message)))
    g.serialize(destination=str(dest), format="turtle")


def validate_full(data_paths: Sequence[Path], shapes_path: Path) -> list[Violation]:
    """Offline full validation over the merged data graph.

    Unlike validate_core this runs everything shapes.ttl holds, including
    SHACL-AF and SPARQL constraints (advanced=True). It is the only place those
    run: the write path deliberately refuses them as too slow.

    The TBox rides along in the data graph so `sh:class` can see subclass
    relations. Shapes target instances, so no TBox axiom is itself validated.
    """
    import pyshacl

    data = Graph()
    for path in data_paths:
        if path.exists() and path.stat().st_size:
            data.parse(path, format="turtle")

    conforms, results_graph, _text = pyshacl.validate(
        data_graph=data,
        shacl_graph=str(shapes_path),
        advanced=True,
        abort_on_first=False,
        meta_shacl=False,
        inplace=False,
    )
    if conforms:
        return []

    out: list[Violation] = []
    for node in results_graph.subjects(RDF.type, SH["ValidationResult"]):
        def val(pred: str) -> str:
            v = results_graph.value(node, SH[pred])
            return str(v) if v is not None else ""

        out.append(Violation(
            focus_iri=val("focusNode"),
            shape_iri=val("resultShape"),
            path=val("resultPath"),
            message=val("resultMessage"),
            kind=val("resultSeverity") or "violation",
        ))
    return out


# --- TBox profile gate -------------------------------------------------------
# The modelling contract locks the TBox to the Jena OWL 2 RL subset. Jena has no
# OWL2RLProfile class (that is OWL API), so this is the spec's documented
# fallback: a constructor blacklist. It is not complete, and it is checked here
# — once, in Python — before the JVM is ever launched.


def _tbox_local_names(tbox: Path) -> tuple[set[str], set[str]]:
    """Return (names used as predicates, names used as types)."""
    g = Graph()
    g.parse(tbox, format="turtle")
    predicates = {_local(p) for _s, p, _o in g}
    types: set[str] = set()
    for _s, p, o in g:
        if str(p).endswith("#type"):
            types.add(_local(o))
    return predicates, types


def banned_owl_constructs(tbox: Path) -> list[str]:
    """Constructs the modelling contract forbids outright.

    These break the reasoning strategy, not just the vocabulary: a rules engine
    cannot honour them and silently derives nothing.
    """
    predicates, types = _tbox_local_names(tbox)
    banned = {"unionOf", "complementOf", "oneOf", "hasValue", "disjointWith"}
    return sorted((predicates | types) & banned)


def unmodelled_characteristics(tbox: Path) -> list[str]:
    """Legal OWL 2 RL that the rules engine silently ignores.

    Declaring these is not an error — write the rule yourself in
    ontology/rules/ — but it must not be a surprise.
    """
    predicates, types = _tbox_local_names(tbox)
    unmodelled = {
        "TransitiveProperty", "SymmetricProperty", "ReflexiveProperty",
        "IrreflexiveProperty", "AsymmetricProperty", "inverseOf",
        "propertyChainAxiom",
    }
    return sorted((predicates | types) & unmodelled)
