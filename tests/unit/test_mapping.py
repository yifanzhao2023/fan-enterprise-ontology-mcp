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


# --- Task 2: coercion, IRI generation, record application --------------------

from datetime import date, datetime

from feomcp.adapter import Record
from feomcp.mapping import (
    Irules,
    RecordError,
    apply_record,
    build_iri,
    coerce_value,
)

XSD = "http://www.w3.org/2001/XMLSchema#"
IRULES = Irules(
    base="https://example.org/id",
    template="{base}/{source}/{record_type}/{key}",
    key_join="-",
    digest="x",
)


def test_coerce_value_matrix():
    assert coerce_value("12", XSD + "integer") == 12
    assert coerce_value("1000.50", XSD + "decimal") == 1000.5
    assert coerce_value("true", XSD + "boolean") is True
    assert coerce_value("false", XSD + "boolean") is False
    assert coerce_value("plain", None) == "plain"
    dt = coerce_value("2026-10-17 03:12:00", XSD + "dateTime", "%Y-%m-%d %H:%M:%S")
    assert dt == datetime(2026, 10, 17, 3, 12, 0)
    assert coerce_value("2026-10-17", XSD + "date") == date(2026, 10, 17)


@pytest.mark.parametrize("value,datatype", [
    ("not-a-number", XSD + "integer"),
    ("not-a-number", XSD + "decimal"),
    ("maybe", XSD + "boolean"),
    ("17/10/2026", XSD + "date"),
    ("nope", "http://www.w3.org/2001/XMLSchema#dateTime"),
])
def test_coerce_value_rejects_bad_input(value, datatype):
    with pytest.raises(RecordError):
        coerce_value(value, datatype)


def test_build_iri_is_deterministic_and_rejects_empty_key():
    assert (
        build_iri(IRULES, source="erp", record_type="customer", key_values=["42"])
        == "https://example.org/id/erp/customer/42"
    )
    assert (
        build_iri(IRULES, source="erp", record_type="customer", key_values=["a b"])
        == "https://example.org/id/erp/customer/a%20b"
    )
    assert (
        build_iri(IRULES, source="erp", record_type="order", key_values=["1", "2"])
        == "https://example.org/id/erp/order/1-2"
    )
    with pytest.raises(RecordError):
        build_iri(IRULES, source="erp", record_type="customer", key_values=[""])
    with pytest.raises(RecordError):
        build_iri(IRULES, source="erp", record_type="customer", key_values=[])


DOC_MAPPING = """
source: erp
version: 1
records:
  customer:
    class: "https://example.org/onto/Customer"
    iri: {keys: [customer_id]}
    properties:
      - {field: customer_name, predicate: "https://example.org/onto/hasName", datatype: "xsd:string"}
      - {field: credit_limit, predicate: "https://example.org/onto/hasCreditLimit", datatype: "xsd:decimal"}
    relations:
      - {field: region_code, predicate: "https://example.org/onto/belongsToRegion", code_table: region}
  business_document:
    iri: {keys: [doc_id]}
    class_by_field: doc_kind
    class_values:
      contract: "https://example.org/onto/Contract"
      key_contract: "https://example.org/onto/KeyContract"
code_tables:
  region:
    class: "https://example.org/onto/Region"
    values:
      "01": "https://example.org/onto/region/North"
      "02": "https://example.org/onto/region/South"
"""


def _doc_mapping(tmp_path):
    p = tmp_path / "erp.yaml"
    p.write_text(DOC_MAPPING, encoding="utf-8")
    return load_mapping(p)


def test_apply_record_properties_relations_and_code_table_nodes(tmp_path):
    m = _doc_mapping(tmp_path)
    entity, relations, code_entities = apply_record(
        m, IRULES, Record("customer", {
            "customer_id": "42",
            "customer_name": "ACME",
            "credit_limit": "1000.50",
            "region_code": "01",
        })
    )
    assert entity.iri == "https://example.org/id/erp/customer/42"
    assert entity.types == ("https://example.org/onto/Customer",)
    assert entity.properties == {
        "hasName": "ACME",
        "hasCreditLimit": 1000.5,
    }
    assert len(relations) == 1
    assert relations[0].predicate_iri == "https://example.org/onto/belongsToRegion"
    assert relations[0].target_iri == "https://example.org/onto/region/North"
    assert [(e.iri, e.types) for e in code_entities] == [
        ("https://example.org/onto/region/North", ("https://example.org/onto/Region",))
    ]


def test_apply_record_conditional_class(tmp_path):
    m = _doc_mapping(tmp_path)
    entity, _, _ = apply_record(
        m, IRULES, Record("business_document", {"doc_id": "7", "doc_kind": "key_contract"})
    )
    assert entity.types == ("https://example.org/onto/KeyContract",)


def test_apply_record_unknown_class_value(tmp_path):
    m = _doc_mapping(tmp_path)
    with pytest.raises(RecordError):
        apply_record(m, IRULES, Record("business_document", {"doc_id": "7", "doc_kind": "memo"}))


def test_apply_record_unknown_record_type(tmp_path):
    m = _doc_mapping(tmp_path)
    with pytest.raises(RecordError):
        apply_record(m, IRULES, Record("widget", {"id": "1"}))


def test_apply_record_missing_field(tmp_path):
    m = _doc_mapping(tmp_path)
    with pytest.raises(RecordError):
        apply_record(m, IRULES, Record("customer", {}))
    with pytest.raises(RecordError):
        apply_record(
            m, IRULES,
            Record("customer", {"customer_id": "42", "customer_name": "ACME"}),
        )  # credit_limit and region_code declared but absent


def test_apply_record_code_table_miss(tmp_path):
    m = _doc_mapping(tmp_path)
    with pytest.raises(RecordError):
        apply_record(
            m, IRULES,
            Record("customer", {
                "customer_id": "42", "customer_name": "ACME",
                "credit_limit": "1", "region_code": "99",
            }),
        )


def test_apply_record_no_code_nodes_when_no_relations(tmp_path):
    m = _doc_mapping(tmp_path)
    _, relations, code_entities = apply_record(
        m, IRULES, Record("business_document", {"doc_id": "7", "doc_kind": "contract"})
    )
    assert relations == [] and code_entities == []


# --- Task 7: foreign keys to another record's IRI ----------------------------

def test_apply_record_foreign_key_relation(tmp_path):
    m = _doc_mapping(tmp_path)
    m.records  # mapping has customer + business_document
    # a document that belongs to a customer by id
    text = DOC_MAPPING.replace(
        "    class_by_field: doc_kind\n",
        "    class_by_field: doc_kind\n"
        "    relations:\n"
        "      - {field: customer_id, predicate: \"https://example.org/onto/belongsToCustomer\", target_record: customer}\n",
    )
    p = tmp_path / "fk.yaml"
    p.write_text(text, encoding="utf-8")
    fk = load_mapping(p)
    entity, relations, code_entities = apply_record(
        fk, IRULES, Record("business_document", {
            "doc_id": "7", "doc_kind": "contract", "customer_id": "42",
        })
    )
    assert entity.iri == "https://example.org/id/erp/business_document/7"
    assert len(relations) == 1
    assert relations[0].target_iri == "https://example.org/id/erp/customer/42"
    assert code_entities == []


def test_apply_record_relation_needs_code_table_or_target_record(tmp_path):
    text = DOC_MAPPING.replace(
        "    class_by_field: doc_kind\n",
        "    class_by_field: doc_kind\n"
        "    relations:\n"
        "      - {field: customer_id, predicate: \"https://example.org/onto/belongsToCustomer\"}\n",
    )
    p = tmp_path / "bad.yaml"
    p.write_text(text, encoding="utf-8")
    bad = load_mapping(p)
    with pytest.raises(RecordError):
        apply_record(bad, IRULES, Record("business_document", {
            "doc_id": "7", "doc_kind": "contract", "customer_id": "42",
        }))


# --- Final review fixes ------------------------------------------------------

def test_irules_with_base_recomputes_digest():
    ir = Irules(base="https://a/id", template="{base}/{key}", key_join="-", digest="filehash")
    changed = ir.with_base("https://b/id")
    assert changed.base == "https://b/id"
    assert changed.digest != ir.digest


def test_validate_mapping_unknown_code_table(tmp_path):
    text = DOC_MAPPING.replace("code_table: region", "code_table: nope")
    p = tmp_path / "bad.yaml"
    p.write_text(text, encoding="utf-8")
    problems = validate_mapping(load_mapping(p), SchemaCatalog({
        "classes": [{"name": "Customer", "iri": "https://example.org/onto/Customer"}],
        "properties": [
            {"name": "hasName", "iri": "https://example.org/onto/hasName", "kind": "datatype"},
            {"name": "hasCreditLimit", "iri": "https://example.org/onto/hasCreditLimit", "kind": "datatype"},
            {"name": "belongsToRegion", "iri": "https://example.org/onto/belongsToRegion", "kind": "object"},
        ],
    }))
    assert any("nope" in problem for problem in problems)


def test_validate_mapping_unknown_target_record(tmp_path):
    text = DOC_MAPPING.replace(
        "    class_by_field: doc_kind\n",
        "    class_by_field: doc_kind\n"
        "    relations:\n"
        "      - {field: customer_id, predicate: \"https://example.org/onto/belongsToCustomer\", target_record: ghost}\n",
    )
    p = tmp_path / "bad.yaml"
    p.write_text(text, encoding="utf-8")
    problems = validate_mapping(load_mapping(p), SchemaCatalog({
        "classes": [{"name": "Customer", "iri": "https://example.org/onto/Customer"}],
        "properties": [],
    }))
    assert any("ghost" in problem for problem in problems)


def test_validate_mapping_rejects_class_and_class_by_field(tmp_path):
    text = DOC_MAPPING.replace(
        "  business_document:\n    iri: {keys: [doc_id]}",
        "  business_document:\n    class: \"https://example.org/onto/Contract\"\n    iri: {keys: [doc_id]}",
    )
    p = tmp_path / "bad.yaml"
    p.write_text(text, encoding="utf-8")
    problems = validate_mapping(load_mapping(p), SchemaCatalog({
        "classes": [{"name": "Customer", "iri": "https://example.org/onto/Customer"},
                    {"name": "Contract", "iri": "https://example.org/onto/Contract"}],
        "properties": [],
    }))
    assert any("business_document" in problem for problem in problems)


def test_load_mapping_missing_required_key_raises_value_error(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("records: {}\n", encoding="utf-8")  # no source/version
    with pytest.raises(ValueError):
        load_mapping(p)
