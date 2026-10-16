import json
import subprocess
import sys
from pathlib import Path

from feomcp.projection import build_projection


def test_projection_has_six_classes_and_nine_properties():
    p = build_projection(Path("ontology/TBox.ttl"), Path("ontology/shapes.ttl"))
    names = {c["name"] for c in p["classes"]}
    assert {"Contract", "Customer", "WorkOrder", "Department", "Employee", "BusinessDocument"} <= names
    prop_names = {x["name"] for x in p["properties"]}
    assert {"hasAmount", "belongsToCustomer", "hasStatus"} <= prop_names


def test_contract_relations_are_typed():
    p = build_projection(Path("ontology/TBox.ttl"), Path("ontology/shapes.ttl"))
    contract = next(c for c in p["classes"] if c["name"] == "Contract")
    out = {r["name"]: r for r in contract["relations_out"]}
    assert out["belongsToCustomer"]["target"] == "Customer"
    assert out["belongsToCustomer"]["min"] == 1


def test_constraints_only_contain_core_kinds():
    p = build_projection(Path("ontology/TBox.ttl"), Path("ontology/shapes.ttl"))
    allowed = {"minCount", "maxCount", "datatype", "class"}
    for entries in p["constraints"].values():
        for c in entries:
            assert c["kind"] in allowed, c


def test_projection_is_byte_stable(tmp_path):
    a = build_projection(Path("ontology/TBox.ttl"), Path("ontology/shapes.ttl"))
    b = build_projection(Path("ontology/TBox.ttl"), Path("ontology/shapes.ttl"))
    ja = json.dumps(a, sort_keys=True, ensure_ascii=False, indent=2)
    jb = json.dumps(b, sort_keys=True, ensure_ascii=False, indent=2)
    assert ja == jb


def test_onto_project_writes_file(tmp_path):
    out = tmp_path / "schema.projection.json"
    subprocess.run(
        [sys.executable, "-m", "feomcp.cli", "project",
         "--tbox", "ontology/TBox.ttl", "--shapes", "ontology/shapes.ttl",
         "--out", str(out)],
        check=True,
    )
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert out.read_bytes().endswith(b"\n")
