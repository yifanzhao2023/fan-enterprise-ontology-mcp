from pathlib import Path

import pytest

from feomcp.errors import ValidationFailed
from feomcp.ports import Entity
from feomcp.validate import require_valid, validate_core

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


def test_write_violations_emits_pinned_validation_results(tmp_path):
    """One named sh:ValidationResult per violation.

    A blank node would be useless as a `:ConstraintViolation.uri` in Neo4j, and a
    triple soup would make `list_violations(focus_iri=...)` impossible to filter.
    """
    from rdflib import RDF, Graph, URIRef

    from feomcp.validate import SH as NSH, Violation, write_violations

    v = Violation(
        focus_iri="https://example.org/id/contract001",
        shape_iri="https://example.org/onto#ContractShape",
        path="https://example.org/onto#hasAmount",
        message="less than 1",
        kind="minCount",
    )
    out = tmp_path / "violations.ttl"
    write_violations([v], out, job_id="0a1b2c3d4e5f")

    g = Graph()
    g.parse(out, format="turtle")
    results = list(g.subjects(RDF.type, NSH.ValidationResult))
    assert len(results) == 1
    r = results[0]
    assert str(r) == "urn:feomcp:violation:0a1b2c3d4e5f:0"
    assert g.value(r, NSH.focusNode) == URIRef("https://example.org/id/contract001")
    assert g.value(r, NSH.resultShape) == URIRef("https://example.org/onto#ContractShape")
    assert g.value(r, NSH.resultPath) == URIRef("https://example.org/onto#hasAmount")
    assert str(g.value(r, NSH.resultMessage)) == "less than 1"


def test_validate_full_reports_violations_on_the_merged_graph(tmp_path):
    from feomcp.validate import validate_full

    abox = tmp_path / "abox.ttl"
    abox.write_text(
        "@prefix onto: <https://example.org/onto#> .\n"
        "@prefix ex:   <https://example.org/id/> .\n"
        'ex:contract001 a onto:Contract ; onto:hasStatus "draft" .\n',
        encoding="utf-8",
    )
    inferred = tmp_path / "inferred.ttl"
    inferred.write_text("", encoding="utf-8")

    violations = validate_full([abox, inferred], Path("ontology/shapes.ttl"))
    paths = {v.path for v in violations}
    assert any(p.endswith("#hasAmount") for p in paths), violations
    assert any(p.endswith("#belongsToCustomer") for p in paths), violations


def test_validate_full_passes_when_the_sample_abox_is_complete(tmp_path):
    """The seeded ABox is written to satisfy shapes.ttl; the merged graph with an
    empty inferred layer must be clean."""
    from feomcp.validate import validate_full

    inferred = tmp_path / "inferred.ttl"
    inferred.write_text("", encoding="utf-8")
    violations = validate_full([Path("ontology/abox.ttl"), inferred], Path("ontology/shapes.ttl"))
    assert violations == [], violations


def test_validate_full_merges_the_inferred_layer_into_the_data_graph(tmp_path):
    from feomcp.validate import validate_full

    abox = tmp_path / "abox.ttl"
    abox.write_text(
        "@prefix onto: <https://example.org/onto#> .\n"
        "@prefix ex:   <https://example.org/id/> .\n"
        'ex:contract001 a onto:Contract ; onto:hasStatus "draft" ; onto:hasAmount 1 .\n',
        encoding="utf-8",
    )
    inferred = tmp_path / "inferred.ttl"
    inferred.write_text(
        "@prefix onto: <https://example.org/onto#> .\n"
        "@prefix ex:   <https://example.org/id/> .\n"
        "ex:customerA a onto:Customer ." + chr(10) + 
        "ex:contract001 onto:belongsToCustomer ex:customerA .\n",
        encoding="utf-8",
    )
    violations = validate_full([abox, inferred], Path("ontology/shapes.ttl"))
    paths = {v.path for v in violations}
    assert not any(p.endswith("#belongsToCustomer") for p in paths), violations
