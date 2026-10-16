import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from neo4j import GraphDatabase
from rdflib import Graph

from .config import Settings
from .errors import BackendUnavailable, BeomcpError, QueryLimitExceeded, QuerySyntaxError, WriteRejected
from .guard import check_cypher
from .ports import Entity, Relation, WriteBatch, WriteReceipt
from .validate import require_valid

# Identifiers that may be interpolated into Cypher. Everything else is refused.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_URI_LIKE = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_SECRET_LIKE = re.compile(r"(?i)\b(password|passwd|pwd|secret|token|auth)\b\s*[=:]\s*\S+")


class Neo4jStore:
    """Both ports. One representation writer shared by ingest and derived apply,
    so the two write paths cannot drift."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._read = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        self._write = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_write_user, settings.neo4j_write_password),
        )

    def close(self) -> None:
        self._read.close()
        self._write.close()

    # ---- QueryEngine ----

    def submit(
        self,
        query: str,
        params: Mapping[str, Any],
        *,
        lang: Literal["cypher", "sparql"] = "cypher",
    ) -> Sequence[Mapping[str, Any]]:
        if lang != "cypher":
            raise QuerySyntaxError(
                f"query language {lang!r} is not supported by this backend",
                hint="the Neo4j backend accepts 'cypher' only",
                details={"lang": lang},
            )
        check_cypher(query, max_length=self._settings.max_query_length)
        try:
            rows = self._run_with_timeout(query, dict(params))
        except FuturesTimeout:
            raise QueryLimitExceeded(
                f"query exceeded {self._settings.query_timeout_ms}ms",
                hint="narrow the pattern or add an index-friendly filter",
                details={"rule": "timeout", "timeout_ms": self._settings.query_timeout_ms},
            )
        except BeomcpError:
            raise
        except Exception as exc:  # noqa: BLE001 - map driver errors to our codes
            text = _scrub(str(exc))
            if "SyntaxError" in text or "InvalidSyntax" in text:
                raise QuerySyntaxError(
                    "Cypher syntax error",
                    hint=text.split("\n")[0][:200],
                    details={"driver": text.split("\n")[0][:200]},
                ) from exc
            raise BackendUnavailable(
                "database unavailable",
                hint="retry later; no retry storm",
                details={"driver": text.split("\n")[0][:200]},
            ) from exc
        return rows[: self._settings.max_rows]

    def _run_with_timeout(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        def work() -> list[dict[str, Any]]:
            with self._read.session() as session:
                return [dict(r) for r in session.run(query, params)]

        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=self._settings.query_timeout_ms / 1000.0)

    # ---- FactStore ----

    def upsert(self, batch: WriteBatch) -> WriteReceipt:
        if batch.size() > self._settings.max_batch:
            raise WriteRejected(
                f"batch of {batch.size()} exceeds limit {self._settings.max_batch}",
                hint="split into smaller batches",
                details={"size": batch.size(), "limit": self._settings.max_batch},
            )
        _check_identifiers(batch)
        require_valid(_validation_view(batch), self._settings.ontology_dir / "shapes.ttl")

        written: list[str] = []
        with self._write.session() as session:

            def work(tx):
                for entity in batch.entities:
                    _write_entity(tx, entity, inferred=False)
                    written.append(entity.iri)
                for rel in batch.relations:
                    _write_relation(tx, rel, inferred=False)

            session.execute_write(work)
        return WriteReceipt(
            written_iris=tuple(written),
            skipped=(),
            validation={"violations": []},
            applied=True,
        )

    def materialize_rdf(self, dest: Path) -> None:
        with self._read.session() as session:
            rows = session.run("CALL n10s.rdf.export.all({format: 'Turtle'})")
            turtle = rows.single()[0]
        Path(dest).write_text(turtle, encoding="utf-8")

    def replace_derived(self, inferred: Path, violations: Path) -> WriteReceipt:
        """Clear and rewrite :INFERRED / :ConstraintViolation in one transaction.

        The whole job's result lands together or not at all, so the derived layer
        is always exactly one complete job — never a mixture of two. A singleton
        InferenceJob node records what that job was, which is how the agent
        answers "how fresh are the derived facts".
        """
        g = Graph()
        if inferred.exists() and inferred.stat().st_size:
            g.parse(inferred, format="turtle")
        v = Graph()
        if violations.exists() and violations.stat().st_size:
            v.parse(violations, format="turtle")

        job_id = _job_id(inferred, violations)
        finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        written: list[str] = []
        with self._write.session() as session:

            def work(tx):
                tx.run("MATCH (n:INFERRED) DETACH DELETE n")
                tx.run("MATCH (n:ConstraintViolation) DETACH DELETE n")
                tx.run("MATCH (j:InferenceJob) DETACH DELETE j")
                for subj, pred, obj in g:
                    if str(pred).endswith("#type"):
                        _write_type(tx, str(subj), str(obj), inferred=True)
                        written.append(str(subj))
                    elif hasattr(obj, "toPython"):
                        _write_literal(tx, str(subj), pred_name(pred), obj.toPython(), inferred=True)
                    else:
                        _write_relation(
                            tx, Relation(str(subj), str(pred), str(obj)), inferred=True
                        )
                for subj, _pred, obj in v:
                    tx.run(
                        "MERGE (n:ConstraintViolation {uri: $uri}) SET n.message = $msg",
                        {"uri": str(subj), "msg": str(obj)},
                    )
                tx.run(
                    "MERGE (j:InferenceJob {key: 'latest'}) "
                    "SET j.job_id = $job_id, j.finished_at = $finished_at, "
                    "    j.derived_count = $derived, j.violation_count = $violations",
                    {
                        "job_id": job_id,
                        "finished_at": finished_at,
                        "derived": len(set(written)),
                        "violations": len(v),
                    },
                )

            session.execute_write(work)
        return WriteReceipt(
            written_iris=tuple(sorted(set(written))),
            skipped=(),
            validation={"violations": []},
            applied=True,
        )


def _check_identifiers(batch: WriteBatch) -> None:
    """Refuse malformed identifiers before anything else looks at the data.

    This is a security rejection, not a data-quality one. An agent-supplied key
    that could break out of a Cypher identifier slot must be reported as such
    even when the record would also have failed SHACL validation — otherwise the
    attacker is handed a data-quality error and the injection goes unnamed.
    """
    for entity in batch.entities:
        for t in entity.types:
            pred_name(t)
        for key in entity.properties:
            pred_name(key)
    for rel in batch.relations:
        pred_name(rel.predicate_iri)


def _validation_view(batch: WriteBatch) -> list[Entity]:
    """Fold the batch's relations into each entity's property map before validation.

    sh:path on an object property means "the values of this predicate", and a
    relation in the same batch is exactly such a value. Validating properties in
    isolation would reject a well-formed write whose object property is supplied
    as a relation — which is the normal shape of an upsert.
    """
    outgoing: dict[str, dict[str, Any]] = {}
    for rel in batch.relations:
        outgoing.setdefault(rel.source_iri, {})[pred_name(rel.predicate_iri)] = rel.target_iri
    return [
        Entity(iri=e.iri, types=e.types, properties={**e.properties, **outgoing.get(e.iri, {})} )
        for e in batch.entities
    ]


def _job_id(*paths: Path) -> str:
    """Content-derived job identity. Replay the same inputs, get the same id."""
    h = hashlib.sha256()
    for p in paths:
        h.update(p.read_bytes() if p.exists() else b"")
    return h.hexdigest()[:12]


def _scrub(text: str) -> str:
    """Keep driver detail out of the agent's view. No URIs, no credentials."""
    return _SECRET_LIKE.sub("[redacted]", _URI_LIKE.sub("[uri]", text))


def pred_name(uri: Any) -> str:
    """Extract the short name and refuse anything that could not be an ontology term.

    Property keys and type names arriving in upsert_entities are agent input and
    land in Cypher identifier positions. Fail closed here — one guard, every write
    path (ingest and derived apply both call this).
    """
    name = str(uri).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    if not _IDENT.match(name):
        raise WriteRejected(
            f"identifier {name!r} is not a valid ontology term name",
            hint="use names from get_schema(); letters, digits, underscore and hyphen only",
            details={"rule": "identifier-whitelist", "identifier": name},
        )
    return name


def _write_entity(tx, entity: Entity, *, inferred: bool) -> None:
    """Write one instance node.

    `properties` carries datatype properties only; object properties belong in
    `WriteBatch.relations`. Every key and every type passes through pred_name
    before it can reach a Cypher identifier position.
    """
    labels = ":".join(pred_name(t) for t in entity.types) or "Resource"
    props = {pred_name(k): v for k, v in entity.properties.items()}
    tx.run(
        f"MERGE (n:{labels} {{uri: $uri}}) SET n += $props",
        {"uri": entity.iri, "props": props},
    )
    if inferred:
        tx.run("MATCH (n {uri: $uri}) SET n:INFERRED", {"uri": entity.iri})


def _write_type(tx, iri: str, type_iri: str, *, inferred: bool) -> None:
    label = pred_name(type_iri)
    tx.run(f"MERGE (n {{uri: $uri}}) SET n:`{label}`", {"uri": iri})
    if inferred:
        tx.run("MATCH (n {uri: $uri}) SET n:INFERRED", {"uri": iri})


def _write_literal(tx, iri: str, key: str, value: Any, *, inferred: bool) -> None:
    safe = pred_name(key)
    tx.run(f"MERGE (n {{uri: $uri}}) SET n.`{safe}` = $v", {"uri": iri, "v": value})
    if inferred:
        tx.run("MATCH (n {uri: $uri}) SET n:INFERRED", {"uri": iri})


def _write_relation(tx, rel: Relation, *, inferred: bool) -> None:
    name = pred_name(rel.predicate_iri)
    suffix = " SET r:INFERRED" if inferred else ""
    tx.run(
        f"MATCH (a {{uri: $s}}), (b {{uri: $t}}) "
        f"MERGE (a)-[r:`{name}`]->(b){suffix}",
        {"s": rel.source_iri, "t": rel.target_iri},
    )
