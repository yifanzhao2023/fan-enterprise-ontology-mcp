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
