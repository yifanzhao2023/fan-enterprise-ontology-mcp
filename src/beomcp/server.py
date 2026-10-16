from typing import Any, Mapping, Sequence

from mcp.server.mcpserver import MCPServer

from .catalog import Catalog
from .config import Settings
from .errors import BeomcpError
from .neo4j_store import Neo4jStore
from .ports import Entity, Relation, WriteBatch
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


def build_server(settings: Settings | None = None) -> MCPServer:
    s = settings or Settings.from_env()
    store = Neo4jStore(s)
    schema = SchemaCatalog.load(s.ontology_dir)
    catalog = Catalog.load(s.ontology_dir)
    mcp = MCPServer("beomcp")

    @mcp.tool()
    def search_schema(keywords: str, limit: int = 20) -> dict[str, Any]:
        """Keyword search over ontology classes, properties and relations."""
        results = schema.search(keywords, limit)
        return {"results": list(results), "count": len(results)}

    @mcp.tool()
    def get_schema(entity_types: list[str]) -> dict[str, Any]:
        """Full schema for the given entity types: properties, cardinality, relations."""
        types = schema.get(entity_types)
        return {"types": [dict(t) for t in types], "count": len(types)}

    @mcp.tool()
    def get_constraints(entity_types: list[str]) -> dict[str, Any]:
        """SHACL Core subset constraints, for pre-write judgement. scope is always "core"."""
        return dict(schema.constraints(entity_types))

    @mcp.tool()
    def list_queries(domain: str | None = None) -> dict[str, Any]:
        """List named competency questions with their parameters."""
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

    @mcp.tool()
    def run_query(name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Run a named competency question."""
        rows = catalog.run(store, name, params)
        return _envelope(rows, truncated=len(rows) >= s.max_rows)

    @mcp.tool()
    def query_raw(cypher: str, params: dict[str, Any]) -> dict[str, Any]:
        """Run read-only Cypher. Guarded: no writes, no APOC, row and length caps."""
        rows = store.submit(cypher, params, lang="cypher")
        return _envelope(rows, truncated=len(rows) >= s.max_rows)

    @mcp.tool()
    def upsert_entities(entities: list[dict[str, Any]]) -> dict[str, Any]:
        """Write instances. Structured parameters, not Cypher. Atomic batch."""
        batch = WriteBatch(entities=[
            Entity(iri=e["iri"], types=tuple(e["types"]), properties=e.get("properties", {}))
            for e in entities
        ])
        return _receipt(store.upsert(batch))

    @mcp.tool()
    def upsert_relations(relations: list[dict[str, Any]]) -> dict[str, Any]:
        """Write relations between existing instances. Atomic batch."""
        batch = WriteBatch(relations=[
            Relation(r["source_iri"], r["predicate_iri"], r["target_iri"]) for r in relations
        ])
        return _receipt(store.upsert(batch))

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
