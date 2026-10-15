from pathlib import Path

import pytest

from beomcp.errors import SchemaNotFound
from beomcp.schema import SchemaCatalog


def test_search_finds_class_by_keyword():
    s = SchemaCatalog.load(Path("ontology"))
    hits = s.search("contract", limit=10)
    names = {h["name"] for h in hits}
    assert "Contract" in names


def test_search_respects_limit():
    s = SchemaCatalog.load(Path("ontology"))
    assert len(s.search("", limit=2)) <= 2


def test_get_returns_full_class():
    s = SchemaCatalog.load(Path("ontology"))
    (contract,) = s.get(["Contract"])
    assert contract["name"] == "Contract"
    out = {r["name"] for r in contract["relations_out"]}
    assert "belongsToCustomer" in out


def test_get_unknown_type_raises_with_candidates():
    s = SchemaCatalog.load(Path("ontology"))
    with pytest.raises(SchemaNotFound) as exc:
        s.get(["Contractt"])
    assert "Contract" in str(exc.value)


def test_constraints_declare_core_scope():
    s = SchemaCatalog.load(Path("ontology"))
    result = s.constraints(["Contract"])
    assert result["scope"] == "core"
    kinds = {c["kind"] for entries in result["by_type"].values() for c in entries}
    assert kinds <= {"minCount", "maxCount", "datatype", "class"}


def test_corrupt_projection_fails_at_load_not_at_first_query(tmp_path):
    (tmp_path / "schema.projection.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        SchemaCatalog.load(tmp_path)


def test_missing_projection_fails_at_load(tmp_path):
    with pytest.raises(ValueError, match="onto project"):
        SchemaCatalog.load(tmp_path)


def test_projection_without_classes_is_rejected(tmp_path):
    (tmp_path / "schema.projection.json").write_text('{"version": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="schema projection"):
        SchemaCatalog.load(tmp_path)
