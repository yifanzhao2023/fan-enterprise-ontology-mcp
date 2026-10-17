"""Mapping artifact loading and projection lint.

The mapping is a data contract: it must parse from YAML, expose the exact
terms it references, and refuse to run when it points at classes or
properties the TBox no longer has.
"""
from pathlib import Path

import pytest

from feomcp.mapping import load_mapping, load_irules, validate_mapping
from feomcp.schema import SchemaCatalog

SAMPLE = """
source: erp
version: 1
records:
  customer:
    class: "https://example.org/onto/Customer"
    iri: {keys: [customer_id]}
    properties:
      - {field: customer_name, predicate: "https://example.org/onto/hasName", datatype: "xsd:string"}
    relations:
      - {field: region_code, predicate: "https://example.org/onto/belongsToRegion", code_table: region}
code_tables:
  region:
    class: "https://example.org/onto/Region"
    values: {"01": "https://example.org/onto/region/North"}
"""

DANGLING = """
source: erp
version: 1
records:
  customer:
    class: "https://example.org/onto/Gone"
    iri: {keys: [customer_id]}
code_tables: {}
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "erp.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_mapping(tmp_path):
    m = load_mapping(_write(tmp_path, SAMPLE))
    assert m.source == "erp"
    assert m.version == 1
    assert m.ref == "erp.yaml@1"
    assert m.records["customer"].class_ == "https://example.org/onto/Customer"
    assert m.records["customer"].iri_keys == ("customer_id",)
    assert m.records["customer"].properties[0].predicate == "https://example.org/onto/hasName"
    assert m.records["customer"].relations[0].code_table == "region"
    assert m.code_tables["region"].class_ == "https://example.org/onto/Region"
    assert m.code_tables["region"].values["01"] == "https://example.org/onto/region/North"
    assert len(m.digest) == 16


def test_validate_mapping_dangling_class(tmp_path):
    m = load_mapping(_write(tmp_path, DANGLING))
    schema = SchemaCatalog({"classes": [], "properties": []})
    assert "https://example.org/onto/Gone" in validate_mapping(m, schema)


def test_validate_mapping_clean(tmp_path):
    m = load_mapping(_write(tmp_path, SAMPLE))
    schema = SchemaCatalog({
        "classes": [
            {"name": "Customer", "iri": "https://example.org/onto/Customer"},
            {"name": "Region", "iri": "https://example.org/onto/Region"},
        ],
        "properties": [
            {"name": "hasName", "iri": "https://example.org/onto/hasName", "kind": "datatype"},
            {"name": "belongsToRegion", "iri": "https://example.org/onto/belongsToRegion", "kind": "object"},
        ],
    })
    assert validate_mapping(m, schema) == []


def test_load_irules(tmp_path):
    p = tmp_path / "irules.yaml"
    p.write_text(
        'base: "https://example.org/id"\n'
        'template: "{base}/{source}/{record_type}/{key}"\n'
        "key_join: '-'\n",
        encoding="utf-8",
    )
    ir = load_irules(p)
    assert ir.base == "https://example.org/id"
    assert ir.key_join == "-"
    assert ir.template.endswith("{key}")
    assert len(ir.digest) == 16
