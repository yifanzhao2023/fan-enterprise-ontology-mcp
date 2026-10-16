import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import SchemaNotFound, ValidationFailed
from .ports import QueryEngine


@dataclass(frozen=True)
class ParamDef:
    name: str
    type: str
    required: bool
    default: Any = None


@dataclass(frozen=True)
class ReturnDef:
    name: str
    type: str


@dataclass(frozen=True)
class QueryDef:
    name: str
    cqid: str
    question_zh: str
    params: tuple[ParamDef, ...]
    returns: tuple[ReturnDef, ...]
    sample_params: Mapping[str, Any]
    sample_result: Sequence[Mapping[str, Any]]
    uses: tuple[str, ...]
    tags: tuple[str, ...]
    cypher: str


class Catalog:
    def __init__(self, queries: Mapping[str, QueryDef], namespace: str = "") -> None:
        self._queries = dict(queries)
        self.namespace = namespace

    @classmethod
    def load(cls, ontology_dir: Path) -> "Catalog":
        manifest = json.loads((ontology_dir / "manifest.json").read_text(encoding="utf-8"))
        known = _known_terms(ontology_dir)
        by_name: dict[str, QueryDef] = {}
        for raw in manifest["queries"]:
            unknown = [u for u in raw.get("uses", []) if u not in known]
            if unknown:
                raise ValidationFailed(
                    f"manifest entry {raw['name']!r} references unknown terms: {unknown}",
                    hint="regenerate the projection with `onto project`, or fix the `uses` list",
                    details={"name": raw["name"], "unknown": unknown},
                )
            cypher = (ontology_dir / "queries" / f"{raw['name']}.cypher").read_text(encoding="utf-8")
            by_name[raw["name"]] = QueryDef(
                name=raw["name"],
                cqid=raw["cqid"],
                question_zh=raw["question_zh"],
                params=tuple(
                    ParamDef(p["name"], p["type"], p.get("required", False), p.get("default"))
                    for p in raw.get("params", [])
                ),
                returns=tuple(ReturnDef(r["name"], r["type"]) for r in raw.get("returns", [])),
                sample_params=raw.get("sample_params", {}),
                sample_result=raw.get("sample_result", []),
                uses=tuple(raw.get("uses", [])),
                tags=tuple(raw.get("tags", [])),
                cypher=cypher,
            )
        return cls(by_name, manifest.get("namespace", ""))

    def list(self, domain: str | None = None) -> Sequence[QueryDef]:
        defs = list(self._queries.values())
        if domain is None:
            return defs
        return [d for d in defs if domain in d.tags]

    def run(
        self, engine: QueryEngine, name: str, params: Mapping[str, Any]
    ) -> Sequence[Mapping[str, Any]]:
        if name not in self._queries:
            raise SchemaNotFound(
                f"unknown query {name!r}",
                hint="call list_queries() to see available named queries",
                details={"known": sorted(self._queries)},
            )
        q = self._queries[name]
        bound = _bind_params(q, params)
        return engine.submit(q.cypher, bound, lang="cypher")


def _bind_params(q: QueryDef, params: Mapping[str, Any]) -> dict[str, Any]:
    bound: dict[str, Any] = {}
    for p in q.params:
        if p.name in params:
            bound[p.name] = params[p.name]
        elif p.default is not None:
            bound[p.name] = p.default
        elif p.required:
            raise ValidationFailed(
                f"missing required parameter {p.name!r} for {q.name!r}",
                hint=f"pass {p.name} ({p.type})",
                details={"query": q.name, "parameter": p.name},
            )
    return bound


def _known_terms(ontology_dir: Path) -> set[str]:
    projection = ontology_dir / "schema.projection.json"
    if not projection.exists():
        return set()
    data = json.loads(projection.read_text(encoding="utf-8"))
    terms = {c["name"] for c in data.get("classes", [])}
    terms |= {p["name"] for p in data.get("properties", [])}
    return terms
