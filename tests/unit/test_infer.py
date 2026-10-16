"""Chain C orchestration: export -> infer -> validate -> replace.
The store is faked so the ordering and the failure behaviour can be tested
without a JVM or a database. The rule under test is simple: any failure means
nothing is written, and the runner's own exit code survives to the CLI.
"""
from pathlib import Path
import pytest
from feomcp.config import Settings

class FakeStore:
    def __init__(self):
        self.materialized = None
        self.replaced = None
        self.failed = None
        self._matches = False
    def materialize_rdf(self, dest: Path) -> None:
        self.materialized = dest
        dest.write_text(
            "@prefix onto: <https://example.org/onto#> ." + chr(10) +
            "@prefix ex:   <https://example.org/id/> ." + chr(10) +
            "ex:contract001 a onto:Contract ." + chr(10),
            encoding="utf-8",
        )
    def replace_derived(self, inferred: Path, violations: Path, **kw):
        self.job_id = kw.get("job_id")
        self.replaced = (inferred, violations)
        self.replaced_existed = inferred.exists() and violations.exists()
        class R:
            written_iris = ()
            skipped = ()
            validation = {"violations": []}
            applied = True
        return R()
    def record_inference_failure(self, job_id, error, **kw):
        self.failed = (job_id, error, kw)
    def abox_writes(self):
        return 0
    def derived_layer_matches(self, **kw):
        return self._matches
    def close(self):
        pass

def make_settings(tmp_path):
    return Settings(
        store="neo4j",
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password="test-password-123",
        neo4j_write_user="neo4j",
        neo4j_write_password="test-password-123",
        ontology_dir=tmp_path,
        query_timeout_ms=1000,
        max_rows=10,
        max_query_length=256,
        max_batch=10,
        redact_fields=("password",),
        allow_shared_account=True,
    )

def run(tmp_path, *, runner, store, allow_dirty=False, monkeypatch=None):
    from feomcp.cli import run_infer
    if monkeypatch is not None:
        monkeypatch.setattr("feomcp.cli._git_dirty", lambda: False)
    return run_infer(
        make_settings(tmp_path),
        rules_dir=Path("ontology/rules"),
        shapes=Path("ontology/shapes.ttl"),
        tbox=Path("ontology/TBox.ttl"),
        runner=runner,
        allow_dirty=allow_dirty,
        store=store,
    )

def test_run_infer_orders_steps_and_writes_on_success(tmp_path, monkeypatch):
    store = FakeStore()
    calls = []
    def fake_runner(*, tbox, abox, rules_dir, out, **kw):
        calls.append("runner")
        out.write_text(
            "@prefix onto: <https://example.org/onto#> ." + chr(10) +
            "<https://example.org/id/contract001> a onto:KeyContract ." + chr(10),
            encoding="utf-8",
        )
        return 0
    code = run(tmp_path, runner=fake_runner, store=store, monkeypatch=monkeypatch)
    assert code == 0
    assert store.materialized is not None
    assert store.replaced is not None
    inferred, violations = store.replaced
    # Files must exist at write time. They do not survive afterwards — the
    # work directory is cleaned up unless --keep says otherwise.
    assert store.replaced_existed
    assert store.failed is None
    assert calls == ["runner"]

def test_run_infer_does_not_write_when_the_runner_fails(tmp_path, monkeypatch):
    store = FakeStore()
    def fake_runner(**kw):
        return 4
    code = run(tmp_path, runner=fake_runner, store=store, monkeypatch=monkeypatch)
    assert code == 4
    assert store.replaced is None
    assert store.failed is not None

def test_run_infer_passes_the_runner_exit_code_through(tmp_path, monkeypatch):
    """A TBox that cannot be parsed must not look like a generic inference crash —
    CI has to tell a config error from an ontology problem."""
    store = FakeStore()
    for runner_code, expected in ((2, 2), (3, 3), (4, 4)):
        store.replaced = None
        store.failed = None
        def failing_runner(_c=runner_code, **_kw):
            return _c
        code = run(tmp_path, runner=failing_runner, store=store, monkeypatch=monkeypatch)
        assert code == expected, (runner_code, code)
        assert store.replaced is None
        assert store.failed is not None

def test_run_infer_refuses_a_dirty_worktree(tmp_path, monkeypatch):
    from feomcp.cli import run_infer
    store = FakeStore()
    monkeypatch.setattr("feomcp.cli._git_dirty", lambda: True)
    code = run_infer(
        make_settings(tmp_path),
        rules_dir=Path("ontology/rules"),
        shapes=Path("ontology/shapes.ttl"),
        tbox=Path("ontology/TBox.ttl"),
        runner=lambda **kw: 0,
        store=store,
    )
    assert code == 2
    assert store.materialized is None

def test_run_infer_allow_dirty_records_the_flag(tmp_path, monkeypatch):
    from feomcp.cli import run_infer
    store = FakeStore()
    monkeypatch.setattr("feomcp.cli._git_dirty", lambda: True)
    code = run_infer(
        make_settings(tmp_path),
        rules_dir=Path("ontology/rules"),
        shapes=Path("ontology/shapes.ttl"),
        tbox=Path("ontology/TBox.ttl"),
        runner=lambda **kw: 0,
        allow_dirty=True,
        store=store,
    )
    assert code == 0

def test_run_infer_skips_when_the_inputs_are_unchanged(tmp_path, monkeypatch):
    store = FakeStore()
    store._matches = True
    def must_not_run(**_kw):
        raise AssertionError("runner must not be invoked when inputs are unchanged")
    code = run(tmp_path, runner=must_not_run, store=store, monkeypatch=monkeypatch)
    assert code == 0
    assert store.replaced is None

def test_unmodelled_characteristics_are_reported_for_the_sample_tbox():
    from feomcp.validate import unmodelled_characteristics
    # The sample ontology must stay clean, or every run prints noise.
    assert unmodelled_characteristics(Path("ontology/TBox.ttl")) == []

def test_banned_owl_constructs_are_detected(tmp_path):
    """The modelling contract forbids these; a TBox that declares one must not
    reach the rules engine."""
    from feomcp.validate import banned_owl_constructs
    assert banned_owl_constructs(Path("ontology/TBox.ttl")) == []
    bad = tmp_path / "tbox.ttl"
    bad.write_text(
        "@prefix owl: <http://www.w3.org/2002/07/owl#> ." + chr(10) +
        "@prefix ex:  <https://example.org/onto#> ." + chr(10) +
        "ex:A a owl:Class ; owl:equivalentClass [ a owl:Class ; "
        "owl:unionOf (ex:B ex:C) ] ." + chr(10),
        encoding="utf-8",
    )
    assert banned_owl_constructs(bad) == ["unionOf"]

def test_unmodelled_characteristics_warn_but_do_not_block(tmp_path):
    """Legal OWL 2 RL that the rules engine silently ignores. The run still
    happens — it just cannot be a surprise."""
    from feomcp.validate import unmodelled_characteristics
    tbox = tmp_path / "tbox.ttl"
    tbox.write_text(
        "@prefix owl: <http://www.w3.org/2002/07/owl#> ." + chr(10) +
        "@prefix ex:  <https://example.org/onto#> ." + chr(10) +
        "ex:partOf a owl:ObjectProperty , owl:TransitiveProperty ." + chr(10),
        encoding="utf-8",
    )
    assert unmodelled_characteristics(tbox) == ["TransitiveProperty"]
