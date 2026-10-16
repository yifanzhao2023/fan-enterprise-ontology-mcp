from pathlib import Path

import pytest

from beomcp.errors import ValidationFailed
from beomcp.ports import Entity
from beomcp.validate import require_valid, validate_core

TBox = "https://example.org/onto#"
SHAPES = Path("ontology/shapes.ttl")


def ent(iri, types, **props):
    return Entity(iri=iri, types=(TBox + types,), properties=props)


def test_valid_contract_passes():
    e = ent(
        "https://example.org/id/c1",
        "Contract",
        hasAmount=1200000,
        hasStatus="draft",
        archived=False,
        belongsToCustomer="https://example.org/id/customerA",
    )
    assert validate_core([e], SHAPES) == []


def test_missing_required_amount_is_field_level():
    e = ent("https://example.org/id/c1", "Contract", hasStatus="draft")
    violations = validate_core([e], SHAPES)
    paths = {v.path for v in violations}
    assert "hasAmount" in paths
    assert all(v.focus_iri == e.iri for v in violations)


def test_datatype_mismatch_is_field_level_failure():
    e = ent(
        "https://example.org/id/c1",
        "Contract",
        hasAmount="not-a-number",
        hasStatus="draft",
        belongsToCustomer="https://example.org/id/customerA",
    )
    violations = validate_core([e], SHAPES)
    amount = [v for v in violations if v.path == "hasAmount"]
    assert amount, violations
    assert amount[0].kind == "datatype"
    assert amount[0].message


def test_require_valid_raises_validation_failed():
    e = ent("https://example.org/id/c1", "Contract")
    with pytest.raises(ValidationFailed) as exc:
        require_valid([e], SHAPES)
    assert exc.value.details["violations"]
    assert exc.value.retryable is False


def test_untargeted_type_is_ignored():
    e = ent("https://example.org/id/d1", "Department")
    assert validate_core([e], SHAPES) == []


def test_sh_class_rejects_a_bare_string():
    """sh:class means "the object is an instance of this class" — a plain string
    is not an instance of anything."""
    e = ent(
        "https://example.org/id/c1",
        "Contract",
        hasAmount=1,
        hasStatus="draft",
        belongsToCustomer="banana",
    )
    violations = validate_core([e], SHAPES)
    assert [v for v in violations if v.kind == "class"], violations


def test_sh_class_accepts_an_iri():
    e = ent(
        "https://example.org/id/c1",
        "Contract",
        hasAmount=1,
        hasStatus="draft",
        belongsToCustomer="https://example.org/id/customerA",
    )
    assert validate_core([e], SHAPES) == []


def test_sh_class_checks_the_target_type_when_known():
    e = ent(
        "https://example.org/id/c1",
        "Contract",
        hasAmount=1,
        hasStatus="draft",
        belongsToCustomer="https://example.org/id/oops",
    )
    index = {"https://example.org/id/oops": ["Employee"]}
    violations = validate_core([e], SHAPES, type_index=index)
    assert [v for v in violations if v.kind == "class"], violations


def test_unknown_datatype_fails_closed():
    """An unrecognised datatype is one we cannot check, so we do not accept it."""
    import tempfile
    from pathlib import Path

    from rdflib import Graph, Literal, Namespace, RDF, XSD

    SH = Namespace("http://www.w3.org/ns/shacl#")
    TBox = "https://example.org/onto#"
    g = Graph()
    g.parse("ontology/shapes.ttl", format="turtle")
    g.add((Namespace(TBox)["OddShape"], RDF.type, SH.NodeShape))
    g.add((Namespace(TBox)["OddShape"], SH.targetClass, Namespace(TBox)["Contract"]))
    blank = Namespace(TBox)["oddProp"]
    g.add((Namespace(TBox)["OddShape"], SH.property, blank))
    g.add((blank, SH.path, Namespace(TBox)["hasStatus"]))
    g.add((blank, SH.datatype, XSD.dateTime))

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "shapes.ttl"
        g.serialize(destination=str(path), format="turtle")
        e = ent("https://example.org/id/c1", "Contract", hasStatus="draft")
        violations = validate_core([e], path)
        assert [v for v in violations if v.path == "hasStatus"], violations
