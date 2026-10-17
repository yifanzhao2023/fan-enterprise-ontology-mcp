import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import SchemaNotFound


class SchemaCatalog:
    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = data
        self._classes = {c["name"]: c for c in data.get("classes", [])}
        self._properties = {p["name"]: p for p in data.get("properties", [])}

    @classmethod
    def load(cls, ontology_dir: Path) -> "SchemaCatalog":
        """Refuse to start on a missing or corrupt projection (spec §8 failure
        modes). A half-parsed projection would only surface as a confusing
        runtime error on the agent's first query."""
        path = ontology_dir / "schema.projection.json"
        if not path.exists():
            raise ValueError(
                f"{path} is missing — run `onto project` before starting the server"
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict) or "classes" not in data:
            raise ValueError(f"{path} is not a schema projection (no 'classes' key)")
        return cls(data)

    def known_terms(self) -> set[str]:
        """Every IRI the projection knows, for mapping lint."""
        return {c["iri"] for c in self._classes.values()} | {
            p["iri"] for p in self._properties.values()
        }

    def search(self, keywords: str, limit: int = 20) -> Sequence[Mapping[str, Any]]:
        needle = keywords.strip().lower()
        hits: list[dict[str, Any]] = []
        for c in self._classes.values():
            labels = [str(x) for x in c.get("labels", [])]
            matched = "name" if needle in c["name"].lower() else (
                "label" if any(needle in x.lower() for x in labels) else None)
            if not needle or matched:
                hits.append({"kind": "class", "name": c["name"], "iri": c["iri"],
                             "match": matched or "name", "labels": labels})
        for p in self._properties.values():
            labels = [str(x) for x in p.get("labels", [])]
            matched = "name" if needle in p["name"].lower() else (
                "label" if any(needle in x.lower() for x in labels) else None)
            if not needle or matched:
                kind = "property" if p["kind"] == "datatype" else "relation"
                hits.append({"kind": kind, "name": p["name"], "iri": p["iri"],
                             "match": matched or "name", "labels": labels})
        return hits[:limit]

    def get(self, entity_types: Sequence[str]) -> Sequence[Mapping[str, Any]]:
        missing = [t for t in entity_types if t not in self._classes]
        if missing:
            raise SchemaNotFound(
                f"unknown entity type(s): {missing}",
                hint="run search_schema() to find the right names",
                details={"missing": missing, "known": sorted(self._classes)},
            )
        return [self._classes[t] for t in entity_types]

    def constraints(self, entity_types: Sequence[str]) -> Mapping[str, Any]:
        self.get(entity_types)  # raises on unknown
        by_type = {
            t: self._data.get("constraints", {}).get(t, []) for t in entity_types
        }
        return {"scope": "core", "by_type": by_type}
