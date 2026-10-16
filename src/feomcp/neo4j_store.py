import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from neo4j import GraphDatabase
from rdflib import Graph, Literal as RdfLiteral, Namespace, RDF

from .config import Settings
from .errors import BackendUnavailable, FeomcpError, QueryLimitExceeded, QuerySyntaxError, WriteRejected
from .guard import check_cypher
from .ports import Entity, Relation, WriteBatch, WriteReceipt
from .validate import require_valid

# Identifiers that may be interpolated into Cypher. Everything else is refused.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_URI_LIKE = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_SECRET_LIKE = re.compile(r"(?i)\b(password|passwd|pwd|secret|token|auth)\b\s*[=:]\s*\S+")

# Derived literals are namespaced so a rebuild can tell them apart from ABox
# values and remove them without touching the instance's own properties.
_DERIVED_PREFIX = "inf_"


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
            return self._run_with_timeout(query, dict(params))
        except FuturesTimeout:
            raise QueryLimitExceeded(
                f"query exceeded {self._settings.query_timeout_ms}ms",
                hint="narrow the pattern or add an index-friendly filter",
                retryable=True,
                details={"rule": "timeout", "timeout_ms": self._settings.query_timeout_ms},
            )
        except FeomcpError:
            raise
        except Exception as exc:  # noqa: BLE001 - map driver errors to our codes
            raise _map_driver_error(exc)

    def _run_with_timeout(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(self._read_rows, query, params)
            return future.result(timeout=self._settings.query_timeout_ms / 1000.0)
        finally:
            # Never join: on a timeout the worker must be abandoned, not awaited,
            # or the caller blocks for the full query duration anyway.
            pool.shutdown(wait=False, cancel_futures=True)

    def _read_rows(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        cap = self._settings.max_rows
        with self._read.session() as session:
            out: list[dict[str, Any]] = []
            for record in session.run(query, params):
                out.append(dict(record))
                if len(out) >= cap:
                    break
            return out

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
        try:
            with self._write.session() as session:

                def work(tx):
                    for entity in batch.entities:
                        _write_entity(tx, entity, inferred=False)
                    for rel in batch.relations:
                        _write_relation(tx, rel, inferred=False)
                    # A monotonic counter, not a timestamp: get_inference_status
                    # compares it to the value the last job saw, and two writes in
                    # the same second still have to register as stale.
                    tx.run(
                        "MERGE (m:StoreMeta {key: 'abox'}) SET "
                        "m.writes = coalesce(m.writes, 0) + 1, "
                        "m.last_write_at = $now",
                        {"now": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                    )

                session.execute_write(work)
        except FeomcpError:
            raise
        except Exception as exc:  # noqa: BLE001 - same mapping as submit
            raise _map_driver_error(exc)

        written.extend(e.iri for e in batch.entities)
        return WriteReceipt(
            written_iris=tuple(dict.fromkeys(written)),
            skipped=(),
            validation={"violations": []},
            applied=True,
        )

    def materialize_rdf(self, dest: Path) -> None:
        """ABox -> Turtle for the offline reasoner. Business instances only.

        Derived facts stay out on purpose. Exporting them would make the next
        run's delta empty, `replace_derived` would then strip the layer, and the
        two would oscillate. So do the internal rows — change log, job metadata,
        store counters and violations each carry a `uri`, and none of them is
        business data.

        The writer stores only local names (they land in Cypher identifier
        slots), so the ontology IRIs come back from the TBox.
        """
        from rdflib import Graph, Literal, URIRef
        from rdflib.namespace import RDF

        job_rows = self._read_rows(
            "MATCH (j:InferenceJob {key: 'latest'}) "
            "RETURN j.derived_labels AS labels, j.derived_properties AS props",
            {},
        )
        derived_labels = set(job_rows[0]["labels"] or []) if job_rows else set()
        derived_props = set(job_rows[0]["props"] or []) if job_rows else set()

        with self._read.session() as session:
            nodes = [
                dict(r)
                for r in session.run(
                    "MATCH (n) WHERE n.uri IS NOT NULL "
                    "RETURN n.uri AS u, labels(n) AS ls, properties(n) AS p"
                )
            ]
            edges = [
                dict(r)
                for r in session.run(
                    "MATCH (a)-[r]->(b) "
                    "WHERE a.uri IS NOT NULL AND b.uri IS NOT NULL "
                    "  AND (r.inferred IS NULL OR r.inferred = false) "
                    "RETURN a.uri AS s, type(r) AS t, b.uri AS o"
                )
            ]

        terms = _term_index(self._settings.ontology_dir / "TBox.ttl")
        index = {k: v[0] for k, v in terms.items()}
        ranges = {k: v[1] for k, v in terms.items()}

        def term(name: str) -> URIRef:
            return URIRef(index.get(name, f"urn:feomcp:term:{name}"))

        def value_of(name: str, raw: Any) -> Any:
            """The TBox declares the range; Neo4j only holds a Python value.

            Without this the round trip loses xsd:decimal (a float comes back
            as xsd:double) and the offline SHACL flags every contract.
            """
            dt = ranges.get(name)
            return Literal(raw, datatype=URIRef(dt)) if dt else Literal(raw)

        g = Graph()
        for row in nodes:
            subject = URIRef(row["u"])
            if str(subject).startswith("urn:feomcp:"):
                continue
            labels = [
                lab for lab in (row["ls"] or [])
                if lab not in ("Resource", "INFERRED") and lab not in derived_labels
            ]
            for lab in sorted(labels):
                g.add((subject, RDF.type, term(lab)))
            for key, value in (row["p"] or {}).items():
                if key == "uri" or key.startswith("inf_") or key in derived_props:
                    continue
                g.add((subject, term(key), value_of(key, value)))
        for row in edges:
            s, o = URIRef(row["s"]), URIRef(row["o"])
            if str(s).startswith("urn:feomcp:") or str(o).startswith("urn:feomcp:"):
                continue
            g.add((s, term(row["t"]), o))
        g.serialize(destination=str(dest), format="turtle")

    def replace_derived(
        self,
        inferred: Path,
        violations: Path,
        *,
        fingerprint: Mapping[str, Any] | None = None,
    ) -> WriteReceipt:
        """Replace the derived layer in one transaction.

        Derived facts are annotations on existing ABox nodes and edges, never
        replacements for them: a derived type is a label on the instance, a
        derived relation is an edge marked `inferred`, a derived literal is
        namespaced under `inf_`. A rebuild therefore strips those annotations
        and writes the new set. It never deletes an instance node.
        """
        g = Graph()
        if inferred.exists() and inferred.stat().st_size:
            g.parse(inferred, format="turtle")
        v = Graph()
        if violations.exists() and violations.stat().st_size:
            v.parse(violations, format="turtle")

        job_id = _job_id(inferred, violations)
        finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        derived_labels = sorted({
            pred_name(obj) for _s, p, obj in g if str(p).endswith("#type")
        })
        derived_props = sorted({
            pred_name(p) for _s, p, obj in g
            if not str(p).endswith("#type") and isinstance(obj, RdfLiteral)
        })

        written: list[str] = []
        with self._write.session() as session:

            def work(tx):
                old = _previous_job(tx)
                _strip_derived(tx, old.get("derived_labels", []), old.get("derived_properties", []))
                for subj, pred, obj in g:
                    if str(pred).endswith("#type"):
                        _write_type(tx, str(subj), str(obj), inferred=True)
                        written.append(str(subj))
                    elif isinstance(obj, RdfLiteral):
                        _write_literal(tx, str(subj), pred_name(pred), obj.toPython(), inferred=True)
                    else:
                        _write_relation(
                            tx, Relation(str(subj), str(pred), str(obj)), inferred=True
                        )
                for row in _violation_nodes(v, job_id):
                    tx.run(
                        "MERGE (n:ConstraintViolation {uri: $uri}) SET "
                        "n.focus = $focus, n.shape = $shape, n.path = $path, "
                        "n.message = $message, n.job_id = $job",
                        row,
                    )
                _write_job_row(
                    tx,
                    job_id=job_id,
                    status="ok",
                    derived_count=len(g),
                    violation_count=len(_violation_nodes(v, job_id)),
                    derived_labels=derived_labels,
                    derived_properties=derived_props,
                    finished_at=finished_at,
                    abox_writes=_current_abox_writes(tx),
                    neo4j_version=_neo4j_version(tx),
                    **(fingerprint or {}),
                )

            session.execute_write(work)
        return WriteReceipt(
            written_iris=tuple(dict.fromkeys(written)),
            skipped=(),
            validation={"violations": []},
            applied=True,
        )


    def list_violations(
        self, focus_iri: str | None = None, job_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Offline SHACL results. Defaults to the latest successful job.

        Old runs must not pile up and confuse a question about the data as it
        stands now. The spec says "align with the metadata job_id", which is the
        latest *attempt* — but a failed attempt wrote no violations, so the rows
        on the graph are from `last_ok_job_id`. Filtering on that is what keeps
        the default honest.
        """
        conds: list[str] = []
        params: dict[str, Any] = {}
        if focus_iri is not None:
            conds.append("n.focus = $focus")
            params["focus"] = focus_iri
        if job_id is not None:
            conds.append("n.job_id = $job")
            params["job"] = job_id
        else:
            rows = self._read_rows(
                "MATCH (j:InferenceJob {key: 'latest'}) "
                "RETURN j.last_ok_job_id AS id",
                {},
            )
            ok = rows[0]["id"] if rows else None
            if ok is None:
                return []
            conds.append("n.job_id = $job")
            params["job"] = ok
        rows = self._read_rows(
            "MATCH (n:ConstraintViolation) WHERE " + " AND ".join(conds)
            + " RETURN n.uri AS uri, n.focus AS focus, n.shape AS shape,"
            " n.path AS path, n.message AS message, n.job_id AS job_id"
            " ORDER BY n.uri",
            params,
        )
        return [dict(r) for r in rows]

    def inference_status(self) -> dict[str, Any]:
        """Freshness of the derived layer. Never raises — stale is normal.

        `status: "ok"` with `derived_count: 0` already means "ran clean, nothing
        derived"; a run that never got off the ground is `status: "failed"`.
        """
        with self._read.session() as session:
            job = session.run("MATCH (j:InferenceJob {key: 'latest'}) RETURN j").single()
            meta = session.run("MATCH (m:StoreMeta {key: 'abox'}) RETURN m").single()
        j = dict(job["j"]) if job else {}
        m = dict(meta["m"]) if meta else {}
        writes = int(m.get("writes", 0) or 0)
        job_writes = int(j.get("abox_writes", 0) or 0)
        last_ok = j.get("last_ok_job_id")
        return {
            "job_id": j.get("job_id"),
            "status": j.get("status", "none"),
            "finished_at": j.get("finished_at"),
            "last_ok_job_id": last_ok,
            "last_ok_finished_at": j.get("last_ok_finished_at"),
            "derived_count": j.get("derived_count", 0),
            "violation_count": j.get("violation_count", 0),
            "abox_writes": writes,
            "job_abox_writes": job_writes,
            "stale": last_ok is None or writes != job_writes,
        }

    def derived_layer_matches(self, **fingerprint: Any) -> bool:
        """True when the last successful job saw identical inputs.

        Four keys on purpose. Dropping `shapes` would silently skip a run that
        only changed the SHACL rules — the derived facts would be the same but
        the violations would not.
        """
        rows = self._read_rows(
            "MATCH (j:InferenceJob {key: 'latest'}) WHERE j.status = 'ok' "
            "RETURN j.hash_tbox AS tbox, j.hash_rules AS rules, "
            "j.hash_abox AS abox, j.hash_shapes AS shapes",
            {},
        )
        if not rows:
            return False
        row = rows[0]
        return bool(row["tbox"]) and (
            row["tbox"] == fingerprint.get("hash_tbox")
            and row["rules"] == fingerprint.get("hash_rules")
            and row["abox"] == fingerprint.get("hash_abox")
            and row["shapes"] == fingerprint.get("hash_shapes")
        )

    def record_inference_failure(
        self, job_id: str, error: str, **fingerprint: Any
    ) -> None:
        """Record a failed attempt without touching the derived layer.

        The previous successful job's annotations stay exactly where they are;
        only the singleton job row moves forward, and its derived_* fields are
        left alone so the row still describes what is actually in the graph.
        """
        with self._write.session() as session:

            def work(tx):
                _write_job_row(
                    tx,
                    job_id=job_id,
                    status="failed",
                    error=str(error)[:300],
                    **fingerprint,
                )

            session.execute_write(work)




def _current_abox_writes(tx) -> int:
    row = tx.run("MATCH (m:StoreMeta {key: 'abox'}) RETURN m").single()
    return int(dict(row["m"]).get("writes", 0) or 0) if row else 0


def _neo4j_version(tx) -> str:
    row = tx.run(
        "CALL dbms.components() YIELD name, versions "
        "WHERE name CONTAINS 'Neo4j' RETURN versions[0] AS v"
    ).single()
    return str(row["v"]) if row and row["v"] is not None else ""


def _write_job_row(
    tx,
    *,
    job_id: str,
    status: str,
    error: str = "",
    derived_count: int | None = None,
    violation_count: int | None = None,
    derived_labels: Sequence[str] | None = None,
    derived_properties: Sequence[str] | None = None,
    finished_at: str | None = None,
    **fingerprint: Any,
) -> None:
    """One singleton row.

    Success overwrites the derived-layer fields; failure leaves them alone, so
    the row always describes both the latest attempt and the derived layer that
    is actually in the graph.
    """
    from datetime import datetime, timezone

    finished_at = finished_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    if status == "ok":
        tx.run(
            "MERGE (j:InferenceJob {key: 'latest'}) SET "
            "j.job_id = $job_id, j.status = $status, j.error = $error, "
            "j.finished_at = $finished_at, j.last_ok_job_id = $job_id, "
            "j.last_ok_finished_at = $finished_at, "
            "j.derived_count = $derived, j.violation_count = $violations, "
            "j.derived_labels = $labels, j.derived_properties = $props, "
            "j.git_sha = $git_sha, j.allow_dirty = $allow_dirty, "
            "j.hash_abox = $hash_abox, j.hash_tbox = $hash_tbox, "
            "j.hash_rules = $hash_rules, j.hash_shapes = $hash_shapes, "
            "j.hash_inferred = $hash_inferred, j.hash_violations = $hash_violations, "
            "j.neo4j_version = $neo4j_version, j.n10s_version = $n10s_version, "
            "j.abox_writes = $abox_writes, j.abox_triples = $abox_triples",
            {
                "job_id": job_id,
                "status": status,
                "error": error,
                "finished_at": finished_at,
                "derived": derived_count,
                "violations": violation_count,
                "labels": list(derived_labels or []),
                "props": list(derived_properties or []),
                **_fingerprint_params(fingerprint),
            },
        )
    else:
        # Derived-layer fields are deliberately untouched.
        tx.run(
            "MERGE (j:InferenceJob {key: 'latest'}) SET "
            "j.job_id = $job_id, j.status = $status, j.error = $error, "
            "j.finished_at = $finished_at, j.git_sha = $git_sha, "
            "j.allow_dirty = $allow_dirty",
            {
                "job_id": job_id,
                "status": status,
                "error": error,
                "finished_at": finished_at,
                "git_sha": fingerprint.get("git_sha", ""),
                "allow_dirty": bool(fingerprint.get("allow_dirty", False)),
            },
        )


def _fingerprint_params(fp: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "hash_abox", "hash_tbox", "hash_rules",
        "hash_shapes", "hash_inferred", "hash_violations",
    )
    out: dict[str, Any] = {k: str(fp.get(k, "") or "") for k in keys}
    out["git_sha"] = str(fp.get("git_sha", "") or "")
    out["neo4j_version"] = str(fp.get("neo4j_version", "") or "")
    out["n10s_version"] = str(fp.get("n10s_version", "") or "")
    out["abox_writes"] = int(fp.get("abox_writes", 0) or 0)
    out["abox_triples"] = int(fp.get("abox_triples", 0) or 0)
    out["allow_dirty"] = bool(fp.get("allow_dirty", False))
    return out


_SH = Namespace("http://www.w3.org/ns/shacl#")


def _violation_nodes(g: Graph, job_id: str) -> list[dict[str, str]]:
    """Read the pinned violations.ttl shape (one sh:ValidationResult per row)."""
    out = []
    for node in g.subjects(RDF.type, _SH["ValidationResult"]):
        def val(pred):
            v = g.value(node, pred)
            return str(v) if v is not None else ""

        out.append({
            "uri": str(node),
            "focus": val(_SH["focusNode"]),
            "shape": val(_SH["resultShape"]),
            "path": val(_SH["resultPath"]),
            "message": val(_SH["resultMessage"]),
            "job": job_id,
        })
    return out


def _term_index(tbox: Path) -> dict[str, tuple[str, str]]:
    """Local name -> (ontology IRI, declared rdfs:range or "").

    Anything the TBox does not declare is not an ontology term; it is exported
    under a visibly synthetic IRI rather than a guessed namespace, so no rule can
    match it by accident. The range comes along because Neo4j cannot round-trip
    XSD datatypes on its own.
    """
    from rdflib import Graph, URIRef

    g = Graph()
    g.parse(tbox, format="turtle")
    index: dict[str, tuple[str, str]] = {}
    for s, p, o in g:
        for node in (s, p, o):
            if isinstance(node, URIRef):
                index[pred_name(node)] = (str(node), "")
    for s, p, o in g:
        if str(p).endswith("#range") and isinstance(o, URIRef):
            name = pred_name(s)
            iri = index.get(name, (str(s), ""))[0]
            index[name] = (iri, str(o))
    return index



def _previous_job(tx) -> dict[str, Any]:
    row = tx.run("MATCH (j:InferenceJob {key: 'latest'}) RETURN j").single()
    return dict(row["j"]) if row else {}


def _strip_derived(tx, labels: Sequence[str], props: Sequence[str]) -> None:
    """Remove the previous job's annotations. Never delete a node."""
    tx.run("MATCH ()-[r]->() WHERE r.inferred = true DELETE r")
    for label in labels:
        tx.run(f"MATCH (n:INFERRED) REMOVE n:`{pred_name(label)}`")
    for prop in props:
        tx.run(f"MATCH (n:INFERRED) REMOVE n.`{pred_name(prop)}`")
    tx.run("MATCH (n:INFERRED) REMOVE n:INFERRED")
    tx.run("MATCH (n:ConstraintViolation) DETACH DELETE n")


def _map_driver_error(exc: Exception) -> FeomcpError:
    text = _scrub(str(exc))
    head = text.split("\n")[0][:200]
    if "SyntaxError" in text or "InvalidSyntax" in text:
        return QuerySyntaxError(
            "Cypher syntax error", hint=head, details={"driver": head}
        )
    return BackendUnavailable(
        "database unavailable",
        hint="retry later; no retry storm",
        retryable=True,
        details={"driver": head},
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
    outgoing: dict[str, dict[str, list[Any]]] = {}
    for rel in batch.relations:
        outgoing.setdefault(rel.source_iri, {}).setdefault(
            pred_name(rel.predicate_iri), []
        ).append(rel.target_iri)
    views = []
    for e in batch.entities:
        merged = dict(e.properties)
        for key, values in outgoing.get(e.iri, {}).items():
            merged[key] = values
        views.append(Entity(iri=e.iri, types=e.types, properties=merged))
    return views


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

    Identity is the uri alone; labels are applied afterwards so two entities
    sharing an IRI but differing in type collapse onto one node instead of
    splitting into several (which would also fan relation writes out).
    """
    labels = [pred_name(t) for t in entity.types] or ["Resource"]
    props = {pred_name(k): v for k, v in entity.properties.items()}
    sets = [f"n:`{label}`" for label in labels] + ["n += $props"]
    tx.run(
        f"MERGE (n {{uri: $uri}}) SET {', '.join(sets)}",
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
    if inferred:
        safe = _DERIVED_PREFIX + safe
    tx.run(f"MERGE (n {{uri: $uri}}) SET n.`{safe}` = $v", {"uri": iri, "v": value})
    if inferred:
        tx.run("MATCH (n {uri: $uri}) SET n:INFERRED", {"uri": iri})


def _write_relation(tx, rel: Relation, *, inferred: bool) -> None:
    name = pred_name(rel.predicate_iri)
    # Relationships cannot carry labels in Neo4j — use a property marker.
    # Only a brand-new edge may be marked derived. Stamping an existing ABox edge
    # would make the next rebuild's `WHERE r.inferred = true DELETE r` destroy a
    # fact someone wrote on purpose.
    mark = " ON CREATE SET r.inferred = true" if inferred else ""
    tx.run(
        f"MATCH (a {{uri: $s}}), (b {{uri: $t}}) "
        f"MERGE (a)-[r:`{name}`]->(b){mark}",
        {"s": rel.source_iri, "t": rel.target_iri},
    )
