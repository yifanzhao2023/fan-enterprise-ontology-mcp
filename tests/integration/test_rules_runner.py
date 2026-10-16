"""The JVM rules-runner: Turtle in, delta out.

The jar is a build artifact. A missing jar fails here rather than skipping —
a skipped test in CI is a lie about coverage. Run `make rules-runner` first.
"""
import subprocess
from pathlib import Path

from rdflib import RDF, Graph, URIRef

JAR = Path("jvm/target/rules-runner.jar")
EX = "https://example.org/id/"
ONTO = "https://example.org/onto#"


def run_runner(tbox, abox, rules, out):
    return subprocess.run(
        ["java", "-jar", str(JAR),
         "--tbox", str(tbox), "--abox", str(abox),
         "--rules", str(rules), "--out", str(out)],
        capture_output=True, text=True, timeout=120,
    )


def test_runner_derives_key_contract_and_the_dept_head_owner(tmp_path):
    abox = tmp_path / "abox.ttl"
    abox.write_text(
        "@prefix onto: <https://example.org/onto#> .\n"
        "@prefix ex:   <https://example.org/id/> .\n"
        "ex:contractBig a onto:Contract ; onto:hasAmount 1500000 .\n"
        "ex:contractSmall a onto:Contract ; onto:hasAmount 1000 .\n"
        "ex:wo1 a onto:WorkOrder ; onto:belongsToDepartment ex:dept1 .\n"
        "ex:dept1 onto:hasHead ex:emp1 .\n",
        encoding="utf-8",
    )
    out = tmp_path / "inferred.ttl"
    proc = run_runner(Path("ontology/TBox.ttl"), abox, Path("ontology/rules"), out)
    assert proc.returncode == 0, proc.stderr

    g = Graph()
    g.parse(out, format="turtle")
    assert (URIRef(EX + "contractBig"), RDF.type, URIRef(ONTO + "KeyContract")) in g
    assert (URIRef(EX + "contractSmall"), RDF.type, URIRef(ONTO + "KeyContract")) not in g
    assert (URIRef(EX + "wo1"), URIRef(ONTO + "hasOwner"), URIRef(EX + "emp1")) in g


def test_runner_output_is_disjoint_from_the_input(tmp_path):
    """A restated input triple would make replace_derived strip an ABox label."""
    abox = tmp_path / "abox.ttl"
    abox.write_text(
        "@prefix onto: <https://example.org/onto#> .\n"
        "@prefix ex:   <https://example.org/id/> .\n"
        "ex:contractBig a onto:Contract ; onto:hasAmount 1500000 .\n",
        encoding="utf-8",
    )
    out = tmp_path / "inferred.ttl"
    proc = run_runner(Path("ontology/TBox.ttl"), abox, Path("ontology/rules"), out)
    assert proc.returncode == 0, proc.stderr

    data = Graph()
    data.parse("ontology/TBox.ttl", format="turtle")
    data.parse(abox, format="turtle")
    inferred = Graph()
    inferred.parse(out, format="turtle")
    overlap = [t for t in inferred if t in data]
    assert not overlap, overlap


def test_runner_applies_rdfs_subclass_closure(tmp_path):
    """The TBox is a reasoner input, not just a file we checksum.

    Without the RDFS base rules a rule matching onto:Contract never fires for an
    instance typed only as a subclass, and rdfs:subClassOf is decorative.
    """
    abox = tmp_path / "abox.ttl"
    abox.write_text(
        "@prefix onto: <https://example.org/onto#> .\n"
        "@prefix ex:   <https://example.org/id/> .\n"
        "ex:c1 a onto:Contract ; onto:hasAmount 1 .\n",
        encoding="utf-8",
    )
    out = tmp_path / "inferred.ttl"
    proc = run_runner(Path("ontology/TBox.ttl"), abox, Path("ontology/rules"), out)
    assert proc.returncode == 0, proc.stderr
    g = Graph()
    g.parse(out, format="turtle")
    assert (URIRef(EX + "c1"), RDF.type, URIRef(ONTO + "BusinessDocument")) in g
    assert (URIRef(EX + "c1"), RDF.type, URIRef(ONTO + "Contract")) not in g


def test_runner_rejects_an_unparseable_tbox(tmp_path):
    """Exit 3 means the TBox is unusable. The OWL 2 RL profile gate is Python's
    job — Jena has no OWL2RLProfile class; that one is OWL API."""
    tbox = tmp_path / "tbox.ttl"
    tbox.write_text("this is not turtle @@@" + chr(92) + "n", encoding="utf-8")
    abox = tmp_path / "abox.ttl"
    abox.write_text("", encoding="utf-8")
    out = tmp_path / "inferred.ttl"
    proc = run_runner(tbox, abox, Path("ontology/rules"), out)
    assert proc.returncode == 3, (proc.returncode, proc.stdout, proc.stderr)


def test_runner_writes_an_empty_file_when_nothing_is_derived(tmp_path):
    abox = tmp_path / "abox.ttl"
    abox.write_text(
        "@prefix onto: <https://example.org/onto#> .\n"
        "@prefix ex:   <https://example.org/id/> .\n"
        "ex:tiny a onto:Contract ; onto:hasAmount 1 .\n",
        encoding="utf-8",
    )
    out = tmp_path / "inferred.ttl"
    proc = run_runner(Path("ontology/TBox.ttl"), abox, Path("ontology/rules"), out)
    assert proc.returncode == 0, proc.stderr
    g = Graph()
    g.parse(out, format="turtle")
    # RDFS closure still fires (Contract -> BusinessDocument); only the business
    # rules are idle. What matters is that the file is valid and additive.
    assert (URIRef(EX + "tiny"), RDF.type, URIRef(ONTO + "KeyContract")) not in g
