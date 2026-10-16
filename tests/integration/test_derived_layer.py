"""The derived layer must never take an ABox fact with it.

Derived-ness is a per-annotation fact, not a per-name one. A flat list of derived
type *names* cannot tell "KeyContract because a rule said so" from "KeyContract
because someone wrote it", and a rebuild that strips by name eats the second one.
"""
from pathlib import Path

from feomcp.ports import Entity, Relation, WriteBatch

TBox = "https://example.org/onto#"
EX = "https://example.org/id/"


def _empty(path: Path) -> Path:
    path.write_text("", encoding="utf-8")
    return path


def _ttl(path: Path, body: str) -> Path:
    path.write_text("@prefix onto: <https://example.org/onto#> .\n" + body, encoding="utf-8")
    return path


def test_an_asserted_type_survives_a_rebuild_that_strips_that_name(store, tmp_path):
    """Regression: a node typed KeyContract in the ABox lost that type as soon as
    any rule derived KeyContract on some other node."""
    store.upsert(WriteBatch(entities=[
        Entity(EX + "bigDeal", (TBox + "KeyContract",), {"hasAmount": 2000000}),
        Entity(EX + "c1", (TBox + "Customer",), {"hasName": "c1"}),
    ]))

    inf = _ttl(tmp_path / "inferred.ttl",
               "<https://example.org/id/bigDeal> a onto:BusinessDocument , onto:Contract .\n"
               "<https://example.org/id/c1> a onto:KeyContract .\n")
    vis = _empty(tmp_path / "violations.ttl")

    store.replace_derived(inf, vis)
    store.replace_derived(_empty(inf), vis)

    rows = store.submit("MATCH (n {uri: $u}) RETURN labels(n) AS ls", {"u": EX + "bigDeal"})
    assert "KeyContract" in set(rows[0]["ls"]), rows


def test_an_asserted_type_is_not_dropped_from_the_export(store, tmp_path):
    """The export must not apply a global derived-name denylist either: if the
    type is missing from abox.ttl the rules cannot match it on the next run."""
    store.upsert(WriteBatch(entities=[
        Entity(EX + "bigDeal2", (TBox + "KeyContract",), {"hasAmount": 2000000}),
        Entity(EX + "c2", (TBox + "Customer",), {"hasName": "c2"}),
    ]))
    inf = _ttl(tmp_path / "inferred.ttl",
               "<https://example.org/id/c2> a onto:KeyContract .\n")
    store.replace_derived(inf, _empty(tmp_path / "violations.ttl"))

    out = tmp_path / "abox.ttl"
    store.materialize_rdf(out)
    text = out.read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if "bigDeal2" in l)
    assert "KeyContract" in line, line


def test_a_derived_type_is_still_stripped_on_rebuild(store, tmp_path):
    """The other half: a label the derived layer really did add goes away."""
    store.upsert(WriteBatch(entities=[
        Entity(EX + "plain", (TBox + "Customer",), {"hasName": "plain"}),
    ]))
    inf = _ttl(tmp_path / "inferred.ttl",
               "<https://example.org/id/plain> a onto:KeyContract .\n")
    vis = _empty(tmp_path / "violations.ttl")
    store.replace_derived(inf, vis)
    assert store.submit(
        "MATCH (n:KeyContract {uri: $u}) RETURN count(n) AS c", {"u": EX + "plain"},
    )[0]["c"] == 1

    store.replace_derived(_empty(inf), vis)
    rows = store.submit("MATCH (n {uri: $u}) RETURN labels(n) AS ls", {"u": EX + "plain"})
    assert "KeyContract" not in set(rows[0]["ls"]), rows
    assert "Customer" in set(rows[0]["ls"]), rows


def test_an_asserted_edge_clears_the_inferred_marker(store, tmp_path):
    """The mirror of 'a derived edge must not claim an ABox edge': an ABox write
    that restates a derived edge has to promote it, or the next rebuild deletes
    the operator's assertion along with the derivation."""
    store.upsert(WriteBatch(
        entities=[
            Entity(EX + "woP", (TBox + "WorkOrder",), {}),
            Entity(EX + "empP", (TBox + "Employee",), {}),
            Entity(EX + "deptP", (TBox + "Department",), {}),
        ],
        relations=[Relation(EX + "woP", TBox + "belongsToDepartment", EX + "deptP")],
    ))
    inf = _ttl(tmp_path / "inferred.ttl",
               "<https://example.org/id/woP> onto:hasOwner <https://example.org/id/empP> .\n")
    vis = _empty(tmp_path / "violations.ttl")
    store.replace_derived(inf, vis)
    assert store.submit(
        "MATCH ()-[r]->() WHERE r.inferred = true RETURN count(r) AS c", {},
    )[0]["c"] >= 1

    # the operator confirms it as ground truth
    store.upsert(WriteBatch(relations=[
        Relation(EX + "woP", TBox + "hasOwner", EX + "empP"),
    ]))

    store.replace_derived(_empty(inf), vis)
    rows = store.submit(
        "MATCH (:WorkOrder {uri: $s})-[r:hasOwner]->(:Employee {uri: $t}) RETURN count(r) AS c",
        {"s": EX + "woP", "t": EX + "empP"},
    )
    assert rows[0]["c"] == 1, "the asserted edge was destroyed by the rebuild"


def test_rebuild_removes_derived_literals(store, tmp_path):
    """Derived literals are stored as `inf_<name>`. The strip step has to use the
    same name, or a rule that stops firing leaves its literal behind forever."""
    store.upsert(WriteBatch(entities=[
        Entity(EX + "lit1", (TBox + "Employee",), {}),
    ]))
    inf = _ttl(tmp_path / "inferred.ttl",
               '<https://example.org/id/lit1> onto:hasStatus "hot" .\n')
    vis = _empty(tmp_path / "violations.ttl")
    store.replace_derived(inf, vis)
    assert store.submit(
        "MATCH (n {uri: $u}) RETURN n.inf_hasStatus AS v", {"u": EX + "lit1"},
    )[0]["v"] == "hot"

    store.replace_derived(_empty(inf), vis)
    rows = store.submit(
        "MATCH (n {uri: $u}) RETURN n.inf_hasStatus AS v", {"u": EX + "lit1"},
    )
    assert rows[0]["v"] is None, rows


def test_replace_derived_records_the_job_id_it_was_given(store, tmp_path):
    """onto infer stamps its job id into the violation URIs; the metadata row must
    hold the same one or the operator's audit trail dead-ends."""
    inf = _empty(tmp_path / "inferred.ttl")
    vis = _empty(tmp_path / "violations.ttl")
    store.replace_derived(inf, vis, job_id="deadbeefcafe")
    assert store.inference_status()["job_id"] == "deadbeefcafe"


def test_a_relation_change_records_the_previous_target(store):
    """"Who changed this contract" is half an audit record without the old value.
    Property changes already carry one."""
    store.upsert(WriteBatch(
        entities=[
            Entity(EX + "woQ", (TBox + "WorkOrder",), {}),
            Entity(EX + "emp1", (TBox + "Employee",), {}),
            Entity(EX + "emp2", (TBox + "Employee",), {}),
            Entity(EX + "deptQ", (TBox + "Department",), {}),
        ],
        relations=[
            Relation(EX + "woQ", TBox + "belongsToDepartment", EX + "deptQ"),
            Relation(EX + "woQ", TBox + "hasOwner", EX + "emp1"),
        ],
    ))
    store.upsert(WriteBatch(relations=[
        Relation(EX + "woQ", TBox + "hasOwner", EX + "emp2"),
    ]), actor="alice")

    rows = store.list_changes(EX + "woQ")
    owner = next(r for r in rows if r["predicate"] == "hasOwner" and r["actor"] == "alice")
    assert owner["new_value"] == EX + "emp2"
    assert owner["old_value"] is not None and "emp1" in str(owner["old_value"]), owner


def test_change_event_uris_are_unique_across_batches(store):
    """CREATE with a colliding uri mints two nodes sharing one identity, which
    makes the compliance log ambiguous about who did what."""
    for i in range(3):
        store.upsert(WriteBatch(entities=[
            Entity(EX + "uniq", (TBox + "Employee",), {"hasName": f"n{i}"}),
        ]), actor=f"a{i}")
    rows = store.list_changes(EX + "uniq")
    uris = [r for r in store.submit(
        "MATCH (e:ChangeEvent {subject: $s}) RETURN e.uri AS u", {"s": EX + "uniq"})]
    assert len({r["u"] for r in uris}) == len(uris), uris
