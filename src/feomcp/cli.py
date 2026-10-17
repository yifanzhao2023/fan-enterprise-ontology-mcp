import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from .projection import build_projection

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onto")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("project", help="build schema.projection.json from TBox + shapes")
    p.add_argument("--tbox", default="ontology/TBox.ttl")
    p.add_argument("--shapes", default="ontology/shapes.ttl")
    p.add_argument("--out", default="ontology/schema.projection.json")
    s = sub.add_parser("seed", help="load an ABox turtle file into Neo4j")
    s.add_argument("--abox", default="ontology/abox.ttl")
    i = sub.add_parser("infer", help="run the offline inference chain")
    i.add_argument("--rules", default="ontology/rules")
    i.add_argument("--shapes", default="ontology/shapes.ttl")
    i.add_argument("--tbox", default="ontology/TBox.ttl")
    i.add_argument("--allow-dirty", action="store_true")
    i.add_argument("--keep", default=None, help="keep intermediates in this directory")
    args = parser.parse_args(argv)
    if args.command == "seed":
        from .config import Settings
        from .neo4j_store import Neo4jStore
        from .seed import load_abox
        store = Neo4jStore(Settings.from_env())
        try:
            receipt = store.upsert(load_abox(Path(args.abox)))
        finally:
            store.close()
        print(f"seeded {len(receipt.written_iris)} entities from {args.abox}")
        return 0
    if args.command == "project":
        data = build_projection(Path(args.tbox), Path(args.shapes))
        text = json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
        return 0
    if args.command == "infer":
        from .config import Settings
        return run_infer(
            Settings.from_env(),
            rules_dir=Path(args.rules),
            shapes=Path(args.shapes),
            tbox=Path(args.tbox),
            allow_dirty=args.allow_dirty,
            keep=Path(args.keep) if args.keep else None,
        )
    return 2

# --- chain C: export -> infer -> validate -> replace --------------------------
# Any failure writes nothing. The previous derived layer is always better than a
# half-built one, and the ordering is not negotiable: validation happens after
# inference so derived facts are checked too, and the write happens last so no
# intermediate state is ever visible to an agent.

class RunnerFailed(Exception):
    """Carries the rules-runner's own exit code out to the CLI."""
    def __init__(self, code: int) -> None:
        super().__init__(f"rules-runner exited {code}")
        self.code = code

def _git_dirty() -> bool:
    out = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, check=False,
    )
    return bool(out.stdout.strip())

def _git_sha() -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    )
    return out.stdout.strip() if out.returncode == 0 else ""

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    if path.exists():
        h.update(path.read_bytes())
    return h.hexdigest()[:16]

def _graph_hash(path: Path) -> str:
    """Canonical hash of a Turtle graph.
    File bytes are not stable: Jena serialises a hash model in whatever order it
    feels like, so the same logical output would get a different job id every run.
    Hashing the sorted N-Triples is what makes "replay the same inputs, get the
    same job id" actually true.
    """
    from rdflib import Graph
    if not path.exists() or not path.stat().st_size:
        return _sha256(path)
    g = Graph()
    g.parse(path, format="turtle")
    lines = sorted(f"{s} {p} {o} ." for s, p, o in g)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]

def _rules_hash(rules_dir: Path) -> str:
    h = hashlib.sha256()
    if rules_dir.exists():
        for f in sorted(rules_dir.glob("*.rules")):
            h.update(f.read_bytes())
    return h.hexdigest()[:16]

def _default_runner(*, tbox: Path, abox: Path, rules_dir: Path, out: Path, **_kw) -> int:
    jar = Path("jvm/target/rules-runner.jar")
    proc = subprocess.run(
        ["java", "-jar", str(jar),
         "--tbox", str(tbox), "--abox", str(abox),
         "--rules", str(rules_dir), "--out", str(out)],
        capture_output=True, text=True, check=False,
    )
    return proc.returncode

def run_infer(
    settings,
    *,
    rules_dir: Path,
    shapes: Path,
    tbox: Path,
    allow_dirty: bool = False,
    keep: Path | None = None,
    runner=None,
    store=None,
) -> int:
    """Chain C: export -> infer -> validate -> replace. Any failure writes nothing.
    Returns the rules-runner's own exit code when the runner fails (2 bad args,
    3 unusable TBox/ABox, 4 inference blew up) so CI can tell a config error from
    an ontology problem. A banned TBox is 3, a dirty worktree is 2, anything else
    is 4.
    """
    from .neo4j_store import Neo4jStore
    from .validate import (
        banned_owl_constructs,
        unmodelled_characteristics,
        validate_full,
        write_violations,
    )
    runner = runner or _default_runner
    if _git_dirty() and not allow_dirty:
        print("worktree is dirty; commit first, or pass --allow-dirty", file=sys.stderr)
        return 2
    banned = banned_owl_constructs(tbox)
    if banned:
        print(
            "TBox uses OWL constructors the modelling contract forbids: "
            + ", ".join(banned)
            + ". Move the condition into ontology/rules/.",
            file=sys.stderr,
        )
        return 3
    for term in unmodelled_characteristics(tbox):
        print(
            f"warning: {term} is declared in the TBox but is not applied by the "
            f"rules engine; write it in {rules_dir}/",
            file=sys.stderr,
        )
    fingerprint = {
        "git_sha": _git_sha(),
        "allow_dirty": allow_dirty,
        "hash_tbox": _sha256(tbox),
        "hash_rules": _rules_hash(rules_dir),
        "hash_shapes": _sha256(shapes),
    }
    own_store = store is None
    store = store or Neo4jStore(settings)
    try:
        work = Path(keep) if keep else Path(tempfile.mkdtemp(prefix="feomcp-infer-"))
        work.mkdir(parents=True, exist_ok=True)
        abox = work / "abox.ttl"
        inferred = work / "inferred.ttl"
        violations = work / "violations.ttl"
        jid = "unknown"
        try:
            # 1 export. The write counter is read *before* the export so a write
            # landing mid-job cannot be claimed as seen.
            fingerprint["abox_writes"] = store.abox_writes()
            store.materialize_rdf(abox)
            fingerprint["hash_abox"] = _sha256(abox)
            jid = fingerprint["hash_abox"][:12]
            # Same inputs as the last successful job -> nothing can have changed.
            # Four keys on purpose: dropping shapes would skip a run that only
            # changed the SHACL rules, which changes the violations.
            if store.derived_layer_matches(**fingerprint):
                print("inputs unchanged since the last job; skipping")
                return 0
            # 2 infer (JVM; pure function over files)
            code = runner(tbox=tbox, abox=abox, rules_dir=rules_dir, out=inferred)
            if code != 0:
                raise RunnerFailed(code)
            # 3 full validation over TBox + ABox + inferred
            from rdflib import Graph
            merged = work / "merged.ttl"
            g = Graph()
            for f in (tbox, abox, inferred):
                if f.exists() and f.stat().st_size:
                    g.parse(f, format="turtle")
            g.serialize(destination=str(merged), format="turtle")
            # The TBox rides along so sh:class can see subclass relations. Shapes
            # target instances, so no TBox axiom is itself validated.
            found = validate_full([merged], shapes)
            # Content-derived identity: replay the same inputs, get the same id.
            jid = (_graph_hash(inferred) + fingerprint["hash_abox"])[:12]
            write_violations(found, violations, job_id=jid)
            fingerprint["hash_inferred"] = _graph_hash(inferred)
            fingerprint["hash_violations"] = _graph_hash(violations)
            fingerprint["abox_triples"] = len(g)
            # 4 replace, last — so no half-built derived state is ever visible
            store.replace_derived(
                inferred, violations, fingerprint=fingerprint, job_id=jid
            )
            print(f"inference job {jid}: {len(found)} violations")
            return 0
        except RunnerFailed as exc:
            store.record_inference_failure(jid, str(exc), **fingerprint)
            print(f"inference failed (exit {exc.code}); derived layer untouched",
                  file=sys.stderr)
            return exc.code
        except Exception as exc:  # noqa: BLE001 - any failure must not write
            store.record_inference_failure(jid, str(exc)[:300], **fingerprint)
            print(f"inference failed; derived layer untouched: {exc}", file=sys.stderr)
            return 4
        finally:
            if keep is None:
                shutil.rmtree(work, ignore_errors=True)
    finally:
        if own_store:
            store.close()

if __name__ == "__main__":
    raise SystemExit(main())
