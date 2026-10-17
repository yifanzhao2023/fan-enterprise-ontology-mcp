from pathlib import Path

import pytest

from feomcp.catalog import Catalog
from feomcp.errors import SchemaNotFound, ValidationFailed


class FakeEngine:
    def __init__(self):
        self.seen = []

    def submit(self, query, params, *, lang="cypher"):
        self.seen.append((query, tuple(sorted(params.items())), lang))
        return [{"contract_iri": "https://example.org/id/c1", "amount": 1200000}]


def test_catalog_loads_all_queries():
    cat = Catalog.load(Path("ontology"))
    assert len(cat.list()) == 13


def test_manifest_using_unknown_class_fails_at_load():
    import json
    import tempfile

    src = Path("ontology")
    with tempfile.TemporaryDirectory() as td:
        dst = Path(td)
        (dst / "queries").mkdir()
        for f in (src / "queries").iterdir():
            (dst / "queries" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
        manifest["queries"][0]["uses"] = ["NoSuchClass"]
        (dst / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (dst / "schema.projection.json").write_text(
            (src / "schema.projection.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        with pytest.raises(Exception):
            Catalog.load(dst)


def test_run_returns_rows_and_records_query():
    cat = Catalog.load(Path("ontology"))
    engine = FakeEngine()
    rows = cat.run(
        engine,
        "contracts_over_amount_by_customer",
        {"customer_iri": "https://example.org/id/customerA", "min_amount": 1000000},
    )
    assert rows[0]["amount"] == 1200000
    assert engine.seen[0][2] == "cypher"


def test_run_unknown_name_raises_schema_not_found():
    cat = Catalog.load(Path("ontology"))
    with pytest.raises(SchemaNotFound):
        cat.run(FakeEngine(), "nope", {})


def test_run_missing_required_param_raises():
    cat = Catalog.load(Path("ontology"))
    with pytest.raises(ValidationFailed):
        cat.run(FakeEngine(), "contracts_over_amount_by_customer", {})


def test_default_params_are_filled():
    cat = Catalog.load(Path("ontology"))
    engine = FakeEngine()
    cat.run(engine, "contracts_over_amount_by_customer",
            {"customer_iri": "https://example.org/id/customerA"})
    assert ("min_amount", 1000000) in engine.seen[0][1]
