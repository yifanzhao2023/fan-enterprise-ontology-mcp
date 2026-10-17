"""The reference adapter proves the boundary is implementable outside the package."""
import importlib.util
import json
from pathlib import Path

import pytest

from feomcp.mapping import RecordError, apply_record, load_irules, load_mapping, validate_mapping
from feomcp.schema import SchemaCatalog

ROOT = Path(__file__).resolve().parents[2]


def _module():
    path = ROOT / "examples" / "odoo_adapter.py"
    spec = importlib.util.spec_from_file_location("odoo_adapter", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_snapshot_and_flat_records():
    adapter = _module().build_adapter()
    snap = adapter.snapshot()
    assert snap.source_id == "odoo"
    assert snap.snapshot_time == "2026-10-17T00:00:00Z"
    assert snap.version == "19.0"
    records = list(adapter.iter_records())
    assert len(records) == 5
    for record in records:
        assert isinstance(record.record_type, str)
        for value in record.fields.values():
            assert not isinstance(value, (dict, list))


def test_fixture_maps_cleanly_through_the_mapping():
    adapter = _module().build_adapter()
    mapping = load_mapping(ROOT / "ontology" / "mapping" / "odoo.yaml")
    irules = load_irules(ROOT / "ontology" / "mapping" / "irules.yaml")
    entities = 0
    for record in adapter.iter_records():
        try:
            entity, _, _ = apply_record(mapping, irules, record)
        except RecordError as exc:  # pragma: no cover - failure message is the point
            pytest.fail(f"{record.record_type} did not map: {exc}")
        assert entity.iri.startswith("https://example.org/id/odoo/")
        entities += 1
    assert entities == 5


def test_odoo_mapping_matches_the_projection():
    mapping = load_mapping(ROOT / "ontology" / "mapping" / "odoo.yaml")
    schema = SchemaCatalog.load(ROOT / "ontology")
    assert validate_mapping(mapping, schema) == []
