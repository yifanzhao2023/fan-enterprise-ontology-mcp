import time
from typing import Any, Callable, Mapping, Sequence

from mcp.server.mcpserver import MCPServer

from .audit import AuditLogger
from .catalog import Catalog
from .config import Settings
from .errors import BackendUnavailable, BeomcpError
from .neo4j_store import Neo4jStore
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

_CONTEXT = {
    "kind": "beomcp-result",
    "note": "business contract layer, not strict standard JSON-LD",
    "vocab": "https://example.org/onto#",
}


def _envelope(rows: Sequence[Mapping[str, Any]], *, truncated: bool) -> dict[str, Any]:
    return {
        "@context": _CONTEXT,
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
    except BeomcpError as exc:
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
    store = Neo4jStore(s)
    schema = SchemaCatalog.load(s.ontology_dir)
    catalog = Catalog.load(s.ontology_dir)
    audit = AuditLogger()
    fields = tuple(s.redact_fields)
    mcp = MCPServer("beomcp")

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

        return call("run_query", {"name": name}, work)

    @mcp.tool()
    def query_raw(cypher: str, params: dict[str, Any]) -> dict[str, Any]:
        """Run read-only Cypher. Guarded: no writes, no APOC, row and length caps."""

        def work():
            rows = store.submit(cypher, params, lang="cypher")
            return _envelope(rows, truncated=len(rows) >= s.max_rows)

        return call("query_raw", {"cypher": cypher}, work)

    @mcp.tool()
    def upsert_entities(entities: list[dict[str, Any]]) -> dict[str, Any]:
        """Write instances. Structured parameters, not Cypher. Atomic batch."""

        def work():
            batch = WriteBatch(entities=[
                Entity(iri=e["iri"], types=tuple(e["types"]),
                       properties=e.get("properties", {}))
                for e in entities
            ])
            return _receipt(store.upsert(batch))

        return call("upsert_entities", {"entities": entities}, work)

    @mcp.tool()
    def upsert_relations(relations: list[dict[str, Any]]) -> dict[str, Any]:
        """Write relations between existing instances. Atomic batch."""

        def work():
            batch = WriteBatch(relations=[
                Relation(r["source_iri"], r["predicate_iri"], r["target_iri"])
                for r in relations
            ])
            return _receipt(store.upsert(batch))

        return call("upsert_relations", {"relations": relations}, work)

    return mcp


def _receipt(r) -> dict[str, Any]:
    return {
        "written_iris": list(r.written_iris),
        "skipped": list(r.skipped),
        "validation": dict(r.validation),
        "applied": r.applied,
    }


def main() -> None:
    build_server().run()
