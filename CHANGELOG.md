# Changelog

## 0.1.0 — 2026-10-16

First release. Plan 1 of 2: the ontology foundation and the MCP server.

### Added

- Ontology data under `ontology/` — a worked sample domain (enterprise contract
  management) with TBox, SHACL shapes, a query catalog and a sample ABox. Replace
  it with your own; no Python changes needed. See [`ontology/README.md`](ontology/README.md).
- MCP server (`feomcp`) — 10 tools covering schema supply, competency-question
  lookup, raw Cypher and controlled writes; 5 resources and 2 prompts carrying the
  usage playbook; a Claude Code skill wrapping them.
- Write-time SHACL Core validation, atomic batches, a default-deny Cypher guard,
  an audit log and read-time field redaction.
- `onto project` (schema projection) and `onto seed` (load an ABox) CLIs; `make demo`
  runs the whole path end to end.

### Not in this release

Offline inference (`onto-infer`, the JVM `rules-runner`), the `get_inference_status`
and `list_violations` tools, and the compliance-audit scenarios — those are Plan 2.
