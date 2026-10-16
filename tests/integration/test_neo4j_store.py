from pathlib import Path

import pytest
from testcontainers.community.neo4j import Neo4jContainer

from feomcp.config import Settings
from feomcp.errors import QueryRejected, WriteRejected
from feomcp.neo4j_store import Neo4jStore
from feomcp.ports import Entity, Relation, WriteBatch

TBox = "https://example.org/onto#"


@pytest.fixture(scope="module")
def store():
    # Enterprise is required for RBAC (CREATE ROLE / GRANT). Neo4j licenses
    # Enterprise free of charge for development, testing and non-production use.
    container = Neo4jContainer(
        "neo4j:5.26-enterprise", username="neo4j", password="test-password-123"
    ).with_env("NEO4J_ACCEPT_LICENSE_AGREEMENT", "yes")
    with container:
        container.start()
        uri = container.get_connection_url()
        settings = Settings.from_env({
            "FEOMCP_NEO4J_URI": uri,
            "FEOMCP_NEO4J_USER": "neo4j",
            "FEOMCP_NEO4J_PASSWORD": "test-password-123",
            "FEOMCP_MAX_BATCH": "100",
        })
        s = Neo4jStore(settings)
        yield s
        s.close()


def test_read_query_returns_rows(store):
    rows = store.submit("MATCH (n) RETURN count(n) AS c", {})
    assert rows[0]["c"] == 0


def test_guard_runs_inside_submit(store):
    with pytest.raises(QueryRejected):
        store.submit("CREATE (n:X)", {})


def test_upsert_writes_entity_and_relation(store):
    batch = WriteBatch(
        entities=[
            Entity(iri="https://example.org/id/customerA", types=(TBox + "Customer",),
                   properties={"hasName": "A"}),
            Entity(iri="https://example.org/id/contract001", types=(TBox + "Contract",),
                   properties={"hasAmount": 1200000, "hasStatus": "draft", "archived": False}),
        ],
        relations=[
            Relation("https://example.org/id/contract001", TBox + "belongsToCustomer",
                     "https://example.org/id/customerA"),
        ],
    )
    receipt = store.upsert(batch)
    assert receipt.applied is True
    assert set(receipt.written_iris) == {
        "https://example.org/id/customerA", "https://example.org/id/contract001",
    }
    rows = store.submit(
        "MATCH (k:Contract {uri: $uri})-[:belongsToCustomer]->(c:Customer) "
        "RETURN k.uri AS contract, c.uri AS customer",
        {"uri": "https://example.org/id/contract001"},
    )
    assert rows == [{"contract": "https://example.org/id/contract001",
                     "customer": "https://example.org/id/customerA"}]


def test_upsert_duplicate_iri_in_one_batch_is_idempotent(store):
    batch = WriteBatch(entities=[
        Entity(iri="https://example.org/id/dup", types=(TBox + "Customer",), properties={"hasName": "x"}),
        Entity(iri="https://example.org/id/dup", types=(TBox + "Customer",), properties={"hasName": "x"}),
    ])
    store.upsert(batch)
    rows = store.submit("MATCH (n {uri: $uri}) RETURN count(n) AS c", {"uri": "https://example.org/id/dup"})
    assert rows[0]["c"] == 1


def test_oversized_batch_is_rejected_not_chunked(store):
    entities = [
        Entity(iri=f"https://example.org/id/x{i}", types=(TBox + "Customer",), properties={"hasName": "n"})
        for i in range(101)
    ]
    with pytest.raises(WriteRejected) as exc:
        store.upsert(WriteBatch(entities=entities))
    assert "split" in exc.value.hint.lower()
    rows = store.submit("MATCH (n {uri: $uri}) RETURN count(n) AS c",
                        {"uri": "https://example.org/id/x0"})
    assert rows[0]["c"] == 0


def test_replace_derived_clears_and_rewrites(store, tmp_path):
    inf = tmp_path / "inferred.ttl"
    inf.write_text(
        "@prefix onto: <https://example.org/onto#> .\n"
        "<https://example.org/id/contract001> a onto:KeyContract .\n",
        encoding="utf-8",
    )
    vis = tmp_path / "violations.ttl"
    vis.write_text("", encoding="utf-8")
    store.replace_derived(inf, vis)
    rows = store.submit(
        "MATCH (n:INFERRED {uri: $uri}) RETURN count(n) AS c",
        {"uri": "https://example.org/id/contract001"},
    )
    assert rows[0]["c"] == 1


def test_replace_derived_writes_singleton_job_metadata(store, tmp_path):
    inf = tmp_path / "inferred.ttl"
    inf.write_text(
        "@prefix onto: <https://example.org/onto#> .\n"
        "<https://example.org/id/c9> a onto:KeyContract .\n",
        encoding="utf-8",
    )
    vis = tmp_path / "violations.ttl"
    vis.write_text("", encoding="utf-8")
    store.replace_derived(inf, vis)
    rows = store.submit(
        "MATCH (j:InferenceJob {key: 'latest'}) RETURN j",
        {},
    )
    assert rows[0]["j"]["derived_count"] == 1
    assert rows[0]["j"]["violation_count"] == 0
    assert rows[0]["j"]["job_id"]


def test_agent_supplied_property_key_rejects_identifier_injection(store):
    """Property keys arrive from the agent and land in a Cypher identifier slot."""
    from feomcp.errors import WriteRejected

    batch = WriteBatch(entities=[
        Entity(
            iri="https://example.org/id/evil",
            types=(TBox + "Customer",),
            properties={"x` = 1, y": "boom"},
        ),
    ])
    with pytest.raises(WriteRejected) as exc:
        store.upsert(batch)
    assert exc.value.details["rule"] == "identifier-whitelist"


def test_agent_supplied_type_name_rejects_identifier_injection(store):
    from feomcp.errors import WriteRejected

    batch = WriteBatch(entities=[
        Entity(iri="https://example.org/id/evil2", types=(TBox + "Customer` DETACH DELETE n",), properties={}),
    ])
    with pytest.raises(WriteRejected):
        store.upsert(batch)


def test_property_value_with_quotes_and_chinese_survives(store):
    value = 'a"b\\c\n中文值 🎯'
    batch = WriteBatch(entities=[
        Entity(iri="https://example.org/id/quoted", types=(TBox + "Customer",),
               properties={"hasName": value}),
    ])
    store.upsert(batch)
    rows = store.submit("MATCH (n {uri: $u}) RETURN n.hasName AS v", {"u": "https://example.org/id/quoted"})
    assert rows[0]["v"] == value


def test_parameters_are_never_interpolated_into_the_query(store):
    payload = "1' OR '1'='1"
    rows = store.submit(
        "MATCH (n {uri: $u}) RETURN count(n) AS c",
        {"u": payload},
    )
    assert rows[0]["c"] == 0


def test_empty_batch_is_a_no_op(store):
    before = store.submit("MATCH (n) RETURN count(n) AS c", {})[0]["c"]
    receipt = store.upsert(WriteBatch())
    after = store.submit("MATCH (n) RETURN count(n) AS c", {})[0]["c"]
    assert receipt.applied is True
    assert after == before


def test_same_iri_across_two_batches_stays_one_node(store):
    for name in ["一次", "二次"]:
        store.upsert(WriteBatch(entities=[
            Entity(iri="https://example.org/id/across", types=(TBox + "Customer",),
                   properties={"hasName": name}),
        ]))
    rows = store.submit(
        "MATCH (n {uri: $u}) RETURN count(n) AS c, n.hasName AS v",
        {"u": "https://example.org/id/across"},
    )
    assert rows[0]["c"] == 1
    assert rows[0]["v"] == "二次"


def test_read_only_identity_cannot_write_at_database_level(store):
    """Prove the database enforces it, not just the code. A misconfigured account
    would otherwise leave the guard as the only line of defence."""
    from neo4j import GraphDatabase

    with store._write.session() as s:
        # Neo4j 5.x rejects IF NOT EXISTS on CREATE ROLE / CREATE USER.
        s.run("CREATE ROLE feomcp_reader").consume()
        s.run("GRANT ACCESS ON DATABASE * TO feomcp_reader").consume()
        s.run("GRANT MATCH {*} ON GRAPH * TO feomcp_reader").consume()
        s.run(
            "CREATE USER feomcp_reader SET PASSWORD 'read-only-pw' CHANGE NOT REQUIRED"
        ).consume()
        s.run("GRANT ROLE feomcp_reader TO feomcp_reader").consume()

    driver = GraphDatabase.driver(
        store._settings.neo4j_uri, auth=("feomcp_reader", "read-only-pw")
    )
    try:
        with driver.session() as s:
            with pytest.raises(Exception):
                s.run("CREATE (n:ShouldNeverLand {uri: 'x'})").consume()
        with driver.session() as s:
            assert s.run("MATCH (n:ShouldNeverLand) RETURN count(n) AS c").single()["c"] == 0
    finally:
        driver.close()


def test_driver_errors_are_mapped_without_leaking_internals(store):
    from feomcp.errors import QuerySyntaxError

    with pytest.raises(QuerySyntaxError) as exc:
        store.submit("MATCH (n RETURN n", {})
    assert "bolt://" not in exc.value.hint
    assert "password" not in exc.value.hint.lower()


def test_backend_failure_maps_to_opaque_backend_unavailable(store):
    """A real non-syntax backend failure must map to BACKEND_UNAVAILABLE and leak
    no credentials. (Neo4j reports unknown functions as SyntaxError, so a query
    cannot be used to force this path — a rejected connection can.)"""
    from neo4j import GraphDatabase

    from feomcp.errors import BackendUnavailable

    saved = store._read
    bad = GraphDatabase.driver(
        store._settings.neo4j_uri, auth=("not-the-user", "not-the-password")
    )
    store._read = bad
    try:
        with pytest.raises(BackendUnavailable) as exc:
            store.submit("MATCH (n) RETURN count(n) AS c", {})
        assert "not-the-password" not in exc.value.hint
        assert "not-the-password" not in str(exc.value.details)
    finally:
        store._read = saved
        bad.close()


def test_replace_derived_twice_preserves_the_abox_node(store, tmp_path):
    """Derived types are annotations on an instance. Rebuilding the derived layer
    must never take the instance — or its edges — with it."""
    store.upsert(WriteBatch(
        entities=[Entity("https://example.org/id/keep", (TBox + "Customer",), {"hasName": "keep"})],
    ))
    inf = tmp_path / "inferred.ttl"
    inf.write_text(
        "@prefix onto: <https://example.org/onto#> .\n"
        "<https://example.org/id/keep> a onto:KeyCustomer .\n",
        encoding="utf-8",
    )
    vis = tmp_path / "violations.ttl"
    vis.write_text("", encoding="utf-8")

    store.replace_derived(inf, vis)
    store.replace_derived(inf, vis)  # second job is the one that used to destroy it

    rows = store.submit(
        "MATCH (n {uri: $u}) RETURN count(n) AS c, n.hasName AS v",
        {"u": "https://example.org/id/keep"},
    )
    assert rows[0]["c"] == 1
    assert rows[0]["v"] == "keep"


def test_derived_label_is_stripped_on_rebuild(store, tmp_path):
    inf = tmp_path / "inferred.ttl"
    inf.write_text(
        "@prefix onto: <https://example.org/onto#> .\n"
        "<https://example.org/id/keep> a onto:KeyCustomer .\n",
        encoding="utf-8",
    )
    vis = tmp_path / "violations.ttl"
    vis.write_text("", encoding="utf-8")
    store.replace_derived(inf, vis)
    assert store.submit(
        "MATCH (n:KeyCustomer {uri: $u}) RETURN count(n) AS c",
        {"u": "https://example.org/id/keep"},
    )[0]["c"] == 1

    inf.write_text("", encoding="utf-8")
    store.replace_derived(inf, vis)
    assert store.submit(
        "MATCH (n:KeyCustomer {uri: $u}) RETURN count(n) AS c",
        {"u": "https://example.org/id/keep"},
    )[0]["c"] == 0
    assert store.submit(
        "MATCH (n {uri: $u}) RETURN count(n) AS c",
        {"u": "https://example.org/id/keep"},
    )[0]["c"] == 1


def test_inferred_relations_are_cleared_on_rebuild(store, tmp_path):
    # both endpoints must exist first: a derived relation joins two ABox nodes
    store.upsert(WriteBatch(entities=[
        Entity("https://example.org/id/keep", (TBox + "Customer",), {"hasName": "keep"}),
        Entity("https://example.org/id/roled", (TBox + "Employee",), {}),
    ]))
    inf = tmp_path / "inferred.ttl"
    inf.write_text(
        "@prefix onto: <https://example.org/onto#> .\n"
        "<https://example.org/id/keep> onto:knows <https://example.org/id/roled> .\n",
        encoding="utf-8",
    )
    vis = tmp_path / "violations.ttl"
    vis.write_text("", encoding="utf-8")
    store.replace_derived(inf, vis)
    assert store.submit(
        "MATCH ()-[r]->() WHERE r.inferred = true RETURN count(r) AS c", {}
    )[0]["c"] == 1

    inf.write_text("", encoding="utf-8")
    store.replace_derived(inf, vis)
    assert store.submit(
        "MATCH ()-[r]->() WHERE r.inferred = true RETURN count(r) AS c", {}
    )[0]["c"] == 0


def test_same_iri_with_different_type_sets_collapses_to_one_node(store):
    """MERGE on (n:Label {uri}) matches the whole pattern; two type sets would
    create two nodes and later fan relation writes out."""
    store.upsert(WriteBatch(entities=[Entity("https://example.org/id/roled", (TBox + "Employee",), {})]))
    store.upsert(WriteBatch(entities=[
        Entity("https://example.org/id/roled", (TBox + "Employee", TBox + "Department"), {}),
    ]))
    rows = store.submit(
        "MATCH (n {uri: $u}) RETURN count(n) AS c", {"u": "https://example.org/id/roled"}
    )
    assert rows[0]["c"] == 1


def test_type_names_that_are_cypher_keywords_or_have_hyphens_write_cleanly(store):
    for name in ("Set", "Pre-Paid", "Match"):
        store.upsert(WriteBatch(entities=[
            Entity(f"https://example.org/id/kw-{name}", (TBox + name,), {"hasName": name}),
        ]))
    rows = store.submit(
        "MATCH (n) WHERE n.hasName IN $names RETURN count(n) AS c",
        {"names": ["Set", "Pre-Paid", "Match"]},
    )
    assert rows[0]["c"] == 3


def test_materialize_rdf_round_trips_the_seeded_abox(store, tmp_path):
    """Every ABox fact the representation writer stored must come back out as RDF.

    Without this, onto-infer exports an empty abox.ttl, derives nothing, and the
    agent is told there are no derived facts while the rules should have fired.
    """
    from rdflib import RDF, Graph, URIRef
    from feomcp.seed import load_abox

    batch = load_abox(Path("ontology/abox.ttl"))
    store.upsert(batch)

    out = tmp_path / "abox.ttl"
    store.materialize_rdf(out)
    g = Graph()
    g.parse(out, format="turtle")

    ex = URIRef("https://example.org/id/")
    onto = "https://example.org/onto#"
    assert (ex + "contract001", RDF.type, URIRef(onto + "Contract")) in g
    assert (ex + "customerA", RDF.type, URIRef(onto + "Customer")) in g
    # datatype property with a Chinese literal survives
    names = [str(o) for s, p, o in g if str(p).endswith("#hasName")]
    assert "客户A" in names
    # object property survives
    assert (
        ex + "contract001",
        URIRef(onto + "belongsToCustomer"),
        ex + "customerA",
    ) in g
    # every entity in the batch is present in the export
    exported = {str(s) for s in g.subjects()}
    missing = [e.iri for e in batch.entities if e.iri not in exported]
    assert not missing, missing


def test_materialize_rdf_excludes_internal_metadata_nodes(store, tmp_path):
    """:ChangeEvent / :InferenceJob / :StoreMeta / :ConstraintViolation all carry a
    `uri`. If n10s exports them, the rules engine sees operational metadata as
    business instances and may derive from it."""
    from rdflib import Graph

    # Employee has no shape, so the write-time SHACL does not stand in the way
    store.upsert(WriteBatch(
        entities=[Entity("https://example.org/id/c9", (TBox + "Employee",), {})],
    ))
    inf = tmp_path / "inferred.ttl"
    inf.write_text("", encoding="utf-8")
    vis = tmp_path / "violations.ttl"
    vis.write_text(
        "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
        "<urn:feomcp:violation:zz:0> a sh:ValidationResult ;\n"
        '    sh:focusNode <https://example.org/id/c9> ;\n'
        '    sh:resultMessage "m" .\n',
        encoding="utf-8",
    )
    store.replace_derived(inf, vis)

    out = tmp_path / "abox.ttl"
    store.materialize_rdf(out)
    g = Graph()
    g.parse(out, format="turtle")
    subjects = {str(s) for s in g.subjects()}
    assert not any(s.startswith("urn:feomcp:") for s in subjects), subjects
    assert "https://example.org/id/c9" in subjects


def test_derived_relation_never_claims_an_abox_edge(store, tmp_path):
    """An edge someone wrote on purpose must not become a derived edge.

    If it does, the next rebuild's `WHERE r.inferred = true DELETE r` destroys it.
    """
    store.upsert(WriteBatch(
        entities=[
            Entity("https://example.org/id/woA", (TBox + "WorkOrder",), {}),
            Entity("https://example.org/id/empA", (TBox + "Employee",), {}),
            Entity("https://example.org/id/deptA", (TBox + "Department",), {}),
        ],
        relations=[
            # WorkOrderShape requires belongsToDepartment
            Relation(
                "https://example.org/id/woA",
                TBox + "belongsToDepartment",
                "https://example.org/id/deptA",
            ),
            Relation(
                "https://example.org/id/woA", TBox + "hasOwner", "https://example.org/id/empA",
            ),
        ],
    ))
    inf = tmp_path / "inferred.ttl"
    inf.write_text(
        "@prefix onto: <https://example.org/onto#> .\n"
        "<https://example.org/id/woA> onto:hasOwner <https://example.org/id/empA> .\n",
        encoding="utf-8",
    )
    vis = tmp_path / "violations.ttl"
    vis.write_text("", encoding="utf-8")

    store.replace_derived(inf, vis)
    store.replace_derived(inf, vis)

    rows = store.submit(
        "MATCH (:WorkOrder {uri: $s})-[r:hasOwner]->(:Employee {uri: $t}) "
        "RETURN count(r) AS c, r.inferred AS marked",
        {"s": "https://example.org/id/woA", "t": "https://example.org/id/empA"},
    )
    assert rows[0]["c"] == 1
    assert rows[0]["marked"] in (None, False)

    # And the consequence: once the rule stops deriving that edge, the ABox edge
    # must still be there. If it was claimed as derived, the rebuild ate it.
    inf.write_text("", encoding="utf-8")
    store.replace_derived(inf, vis)
    rows = store.submit(
        "MATCH (:WorkOrder {uri: $s})-[r:hasOwner]->(:Employee {uri: $t}) RETURN count(r) AS c",
        {"s": "https://example.org/id/woA", "t": "https://example.org/id/empA"},
    )
    assert rows[0]["c"] == 1


def test_replace_derived_stores_violation_fields(store, tmp_path):
    """The offline SHACL rows have to be queryable by focus and by job, so each
    one lands as its own node with the four fields the spec names."""
    inf = tmp_path / "inferred.ttl"
    inf.write_text("", encoding="utf-8")
    vis = tmp_path / "violations.ttl"
    vis.write_text(
        "@prefix sh: <http://www.w3.org/ns/shacl#> .\n"
        "<urn:feomcp:violation:aa:0> a sh:ValidationResult ;\n"
        "    sh:focusNode <https://example.org/id/contract001> ;\n"
        "    sh:resultShape <https://example.org/onto#ContractShape> ;\n"
        "    sh:resultPath <https://example.org/onto#hasAmount> ;\n"
        '    sh:resultMessage "less than 1" .\n',
        encoding="utf-8",
    )
    store.replace_derived(inf, vis)
    rows = store.submit(
        "MATCH (n:ConstraintViolation {uri: $u}) RETURN n", {"u": "urn:feomcp:violation:aa:0"},
    )
    assert rows[0]["n"]["focus"] == "https://example.org/id/contract001"
    assert rows[0]["n"]["shape"] == "https://example.org/onto#ContractShape"
    assert rows[0]["n"]["path"] == "https://example.org/onto#hasAmount"
    assert rows[0]["n"]["message"] == "less than 1"
    assert rows[0]["n"]["job_id"]

    job = store.submit("MATCH (j:InferenceJob {key: 'latest'}) RETURN j", {})[0]["j"]
    assert job["violation_count"] == 1


def test_inference_status_tracks_the_derived_layer_lifecycle(store, tmp_path):
    """Fresh -> stale. After a job -> not stale. After a write -> stale again.

    Derived facts are stale the moment anything is written. The signal is a write
    counter, not a clock: two writes in the same second still register, and no
    clock skew can hide one.

    The store fixture is module-scoped, so the precondition is established
    explicitly rather than assumed from test order.
    """
    with store._write.session() as session:
        session.run("MATCH (j:InferenceJob) DETACH DELETE j")
        session.run("MATCH (m:StoreMeta) DETACH DELETE m")

    status = store.inference_status()
    assert status["stale"] is True
    assert status["status"] == "none"

    inf = tmp_path / "inferred.ttl"
    inf.write_text("", encoding="utf-8")
    vis = tmp_path / "violations.ttl"
    vis.write_text("", encoding="utf-8")
    store.replace_derived(inf, vis)
    assert store.inference_status()["stale"] is False

    store.upsert(WriteBatch(entities=[
        Entity("https://example.org/id/new1", (TBox + "Customer",), {"hasName": "新"}),
    ]))
    assert store.inference_status()["stale"] is True


def _write_two_violation_jobs(store, tmp_path):
    inf = tmp_path / "inferred.ttl"
    inf.write_text("", encoding="utf-8")

    def vis_file(name, focus):
        path = tmp_path / name
        path.write_text(
            "@prefix sh: <http://www.w3.org/ns/shacl#> ." + chr(10) +
            f"<urn:feomcp:violation:{name}:0> a sh:ValidationResult ;" + chr(10) +
            f"    sh:focusNode <{focus}> ;" + chr(10) +
            '    sh:resultMessage "m" .' + chr(10),
            encoding="utf-8",
        )
        return path

    store.replace_derived(inf, vis_file("a.ttl", "https://example.org/id/contract001"))
    first = store.inference_status()["job_id"]
    store.replace_derived(inf, vis_file("b.ttl", "https://example.org/id/contract002"))
    second = store.inference_status()["job_id"]
    return first, second


def test_list_violations_defaults_to_the_latest_job(store, tmp_path):
    """History must not pile up and confuse a question about the data as it
    stands now. `job_id` is for audit and job-to-job comparison."""
    first, second = _write_two_violation_jobs(store, tmp_path)
    rows = store.list_violations()
    assert [r["focus"] for r in rows] == ["https://example.org/id/contract002"]
    assert all(r["job_id"] == second for r in rows)


def test_list_violations_filters_by_focus_and_job(store, tmp_path):
    """`job_id` narrows to one run. It cannot reach a superseded one: v1 rebuilds
    the derived layer wholesale, so only the latest job's rows exist. Cross-job
    comparison needs history retention, which is deferred on purpose."""
    first, second = _write_two_violation_jobs(store, tmp_path)

    assert store.list_violations(focus_iri="https://example.org/id/contract001") == []
    assert [r["focus"] for r in store.list_violations(focus_iri="https://example.org/id/contract002")]         == ["https://example.org/id/contract002"]

    assert [r["focus"] for r in store.list_violations(job_id=second)]         == ["https://example.org/id/contract002"]
    assert store.list_violations(job_id=first) == []
