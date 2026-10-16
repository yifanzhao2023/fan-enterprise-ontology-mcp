import time
from typing import Any, Callable, Mapping, Sequence

from mcp.server.mcpserver import MCPServer

from .audit import AuditLogger
from .catalog import Catalog
from .config import Settings
from .errors import BackendUnavailable, FeomcpError, WriteRejected
from .neo4j_store import Neo4jStore
from .playbook import QUERY_PLAYBOOK, WRITE_PLAYBOOK
from .ports import Entity, Relation, WriteBatch
from .redact import redact_rows
from .schema import SchemaCatalog

TOOL_NAMES = (
    "search_schema",
    "get_schema",
    "get_constraints",
    "list_queries",
    "run_query",
    "query_raw",
    "upsert_entities",
    "upsert_relations",
)

def _context(vocab: str) -> dict[str, Any]:
    """The @context names the ontology's namespace so the agent knows what the
    IRIs in the rows mean. It is not a JSON-LD context and is not used to
    serialise RDF — that happens over .ttl files only."""
    return {
        "kind": "feomcp-result",
        "note": "business contract layer, not strict standard JSON-LD",
        "vocab": vocab,
    }


_VOCAB = "https://example.org/onto#"


def _envelope(rows: Sequence[Mapping[str, Any]], *, truncated: bool) -> dict[str, Any]:
    return {
        "@context": _context(_VOCAB),
        "rows": list(rows),
        "row_count": len(rows),
        "truncated": truncated,
    }


def _call(
    *,
    tool: str,
    params: Mapping[str, Any],
    principal: str,
    audit: AuditLogger,
    fields: Sequence[str],
    fn: Callable[[], Any],
) -> Any:
    """One wrapper for every tool: audit it, redact what it returns, and never
    let a raw traceback reach the agent."""
    started = time.perf_counter()

    def finish(row_count: int, ok: bool) -> None:
        audit.log(tool=tool, params=params, principal=principal,
                  duration_ms=(time.perf_counter() - started) * 1000,
                  row_count=row_count, ok=ok)

    try:
        result = fn()
    except FeomcpError as exc:
        finish(0, False)
        return exc.to_payload()
    except Exception:  # noqa: BLE001 - a raw traceback must never reach the agent
        finish(0, False)
        return BackendUnavailable(
            "internal error",
            hint="retry later; report the timestamp if it persists",
            retryable=True,
        ).to_payload()

    if isinstance(result, dict) and "rows" in result:
        result = dict(result)
        result["rows"] = list(redact_rows(result["rows"], fields))
        finish(result["row_count"], True)
        return result

    count = 0
    if isinstance(result, dict):
        count = int(result.get("row_count", result.get("count", 0)) or 0)
    finish(count, True)
    return result


def build_server(settings: Settings | None = None, *, principal: str = "agent") -> MCPServer:
    s = settings or Settings.from_env()
    if s.neo4j_user == s.neo4j_write_user and not s.allow_shared_account:
        raise ValueError(
            "query and write accounts are the same account; query tools must use a "
            "read-only account. Grant MATCH-only to a separate reader, or set "
            "FEOMCP_ALLOW_SHARED_ACCOUNT=1 to accept this (development only)."
        )
    store = Neo4jStore(s)
    schema = SchemaCatalog.load(s.ontology_dir)
    catalog = Catalog.load(s.ontology_dir)
    global _VOCAB
    _VOCAB = catalog.namespace
    audit = AuditLogger()
    fields = tuple(s.redact_fields)
    mcp = MCPServer("feomcp")

    def call(tool: str, params: Mapping[str, Any], fn: Callable[[], Any]) -> Any:
        return _call(tool=tool, params=params, principal=principal,
                     audit=audit, fields=fields, fn=fn)

    @mcp.tool()
    def search_schema(keywords: str, limit: int = 20) -> dict[str, Any]:
        """Keyword search over ontology classes, properties and relations."""

        def work():
            results = schema.search(keywords, limit)
            return {"results": list(results), "count": len(results)}

        return call("search_schema", {"keywords": keywords, "limit": limit}, work)

    @mcp.tool()
    def get_schema(entity_types: list[str]) -> dict[str, Any]:
        """Full schema for the given entity types: properties, cardinality, relations."""

        def work():
            types = schema.get(entity_types)
            return {"types": [dict(t) for t in types], "count": len(types)}

        return call("get_schema", {"entity_types": list(entity_types)}, work)

    @mcp.tool()
    def get_constraints(entity_types: list[str]) -> dict[str, Any]:
        """SHACL Core subset constraints, for pre-write judgement. scope is always "core"."""

        def work():
            return dict(schema.constraints(entity_types))

        return call("get_constraints", {"entity_types": list(entity_types)}, work)

    @mcp.tool()
    def list_queries(domain: str | None = None) -> dict[str, Any]:
        """List named competency questions with their parameters."""

        def work():
            defs = catalog.list(domain)
            return {
                "queries": [
                    {
                        "name": d.name,
                        "cqid": d.cqid,
                        "question_zh": d.question_zh,
                        "params": [
                            {"name": p.name, "type": p.type, "required": p.required}
                            for p in d.params
                        ],
                        "returns": [{"name": r.name, "type": r.type} for r in d.returns],
                        "tags": list(d.tags),
                    }
                    for d in defs
                ],
                "count": len(defs),
            }

        return call("list_queries", {"domain": domain}, work)

    @mcp.tool()
    def run_query(name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Run a named competency question."""

        def work():
            rows = catalog.run(store, name, params)
            return _envelope(rows, truncated=len(rows) >= s.max_rows)

        return call("run_query", {"name": name, "params": params}, work)

    @mcp.tool()
    def query_raw(cypher: str, params: dict[str, Any]) -> dict[str, Any]:
        """Run read-only Cypher. Guarded: no writes, no APOC, row and length caps."""

        def work():
            rows = store.submit(cypher, params, lang="cypher")
            return _envelope(rows, truncated=len(rows) >= s.max_rows)

        return call("query_raw", {"cypher": cypher, "params": params}, work)

    @mcp.tool()
    def upsert_entities(
        entities: list[dict[str, Any]],
        relations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Write instances. Structured parameters, not Cypher. Atomic batch.

        Pass `relations` alongside `entities` when the shape constrains an object
        property (a Contract needs its belongsToCustomer): the batch is validated
        and written as one unit, so both halves land together or not at all.
        """

        def work():
            batch = WriteBatch(
                entities=_coerce_entities(entities),
                relations=_coerce_relations(relations or []),
            )
            return _receipt(store.upsert(batch))

        return call(
            "upsert_entities",
            {"entities": entities, "relations": relations or []},
            work,
        )

    @mcp.tool()
    def upsert_relations(
        relations: list[dict[str, Any]],
        entities: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Write relations between instances. Atomic batch.

        Pass `entities` alongside `relations` to create the endpoints and the
        edges in one transaction, validated together.
        """

        def work():
            batch = WriteBatch(
                entities=_coerce_entities(entities or []),
                relations=_coerce_relations(relations),
            )
            return _receipt(store.upsert(batch))

        return call(
            "upsert_relations",
            {"relations": relations, "entities": entities or []},
            work,
        )


    # ---- resources: reference material an agent can pull on demand ----

    def _read_ontology_file(name: str) -> str:
        path = s.ontology_dir / name
        return path.read_text(encoding="utf-8") if path.exists() else f"(missing: {name})"

    @mcp.resource(
        "ontology://playbook",
        name="playbook",
        description="How to query and write against this ontology — decision tree, error handling, write rules",
        mime_type="text/markdown",
    )
    def _res_playbook() -> str:
        return QUERY_PLAYBOOK + "\n---\n\n" + WRITE_PLAYBOOK

    @mcp.resource(
        "ontology://catalog",
        name="catalog",
        description="Named competency questions: what each one answers and what parameters it takes",
        mime_type="text/markdown",
    )
    def _res_catalog() -> str:
        lines = []
        for d in catalog.list():
            lines.append(f"## {d.cqid} — {d.name}")
            lines.append(d.question_zh)
            lines.append("params: " + (", ".join(f"{q.name}: {q.type}" for q in d.params) or "(none)"))
        return "\n\n".join(lines) or "(empty catalog)"

    @mcp.resource("ontology://tbox", name="tbox",
                  description="TBox.ttl source — human reference, not used at runtime",
                  mime_type="text/turtle")
    def _res_tbox() -> str:
        return _read_ontology_file("TBox.ttl")

    @mcp.resource("ontology://shapes", name="shapes",
                  description="shapes.ttl source — SHACL constraints",
                  mime_type="text/turtle")
    def _res_shapes() -> str:
        return _read_ontology_file("shapes.ttl")

    @mcp.resource("ontology://schema", name="schema",
                  description="Full schema projection — large; for human reference, never into a prompt",
                  mime_type="application/json")
    def _res_schema() -> str:
        return _read_ontology_file("schema.projection.json")

    # ---- prompts: the playbooks, invocable by name ----

    @mcp.prompt(name="query-playbook",
                description="Read the schema, pick a named query, fall back to raw Cypher, handle errors")
    def _prompt_query() -> str:
        return QUERY_PLAYBOOK

    @mcp.prompt(name="write-playbook",
                description="Write structure, how to fix validation failures, when derived facts update")
    def _prompt_write() -> str:
        return WRITE_PLAYBOOK

    return mcp


def _coerce_entities(raw: Sequence[Any]) -> list[Entity]:
    """Trust boundary: the agent's JSON must match the shape or be refused with
    a field-level error. A bare string in `types` would otherwise be split into
    one label per character and written without complaint."""
    out: list[Entity] = []
    for i, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise WriteRejected(
                f"entities[{i}] must be an object",
                hint='pass {"iri": ..., "types": [...], "properties": {...}}',
                details={"index": i},
            )
        iri = item.get("iri")
        if not isinstance(iri, str) or not iri:
            raise WriteRejected(
                f"entities[{i}].iri must be a non-empty string",
                hint="every instance needs an IRI",
                details={"index": i, "field": "iri"},
            )
        types = item.get("types")
        if not isinstance(types, list) or not all(isinstance(t, str) and t for t in types):
            raise WriteRejected(
                f"entities[{i}].types must be a list of strings",
                hint='pass types as ["https://example.org/onto#Contract"]',
                details={"index": i, "field": "types"},
            )
        props = item.get("properties", {})
        if not isinstance(props, Mapping):
            raise WriteRejected(
                f"entities[{i}].properties must be an object",
                hint="pass datatype properties as key/value pairs",
                details={"index": i, "field": "properties"},
            )
        out.append(Entity(iri=iri, types=tuple(types), properties=dict(props)))
    return out


def _coerce_relations(raw: Sequence[Any]) -> list[Relation]:
    out: list[Relation] = []
    for i, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise WriteRejected(
                f"relations[{i}] must be an object",
                hint='pass {"source_iri": ..., "predicate_iri": ..., "target_iri": ...}',
                details={"index": i},
            )
        values = []
        for field in ("source_iri", "predicate_iri", "target_iri"):
            value = item.get(field)
            if not isinstance(value, str) or not value:
                raise WriteRejected(
                    f"relations[{i}].{field} must be a non-empty string",
                    hint="every relation needs a source, a predicate and a target",
                    details={"index": i, "field": field},
                )
            values.append(value)
        out.append(Relation(*values))
    return out


def _receipt(r) -> dict[str, Any]:
    return {
        "written_iris": list(r.written_iris),
        "skipped": list(r.skipped),
        "validation": dict(r.validation),
        "applied": r.applied,
    }


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
