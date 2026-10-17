# Changelog

## 1.0.2 — 2026-10-17

Plan 3: land real data — terminology mapping, batch ingest and lineage audit.

### Added

- **Terminology mapping** — `ontology/mapping/<source>.yaml` maps source columns to
  properties, source values to classes, relations to code tables or foreign-key
  records, plus `mapping/irules.yaml` for deterministic entity IRIs and a
  `glossary.md` for business review. `onto lint-mapping` checks every reference
  against the schema projection.
- **Batch ingest bridge** — `onto import --source <id> --adapter <module>` turns
  adapter records into one `WriteBatch` through the mapping, IRI rule and datatype
  coercion (string / integer / decimal / boolean / dateTime / date). It reuses the
  MCP write path's representation writer and write-time SHACL Core, so imported and
  tool-written data cannot drift. Record-level failures are skipped and counted;
  batch-level failures write nothing.
- **Ingest lineage audit** — one append-only `:IngestJob` per attempt with
  `job_id` (attempt identity), `input_hash` (content identity), source, snapshot
  time, actor, counts and `error_details`. `:ChangeEvent.actor = "ingest:<job_id>"`
  links field-level changes back to the job.
- Reference Odoo adapter and snapshot fixture (`examples/odoo_adapter.py`,
  `examples/odoo_records.json`), plus an integration test suite for the store.
- `FEOMCP_BASE_IRI` and `PyYAML` as a runtime dependency.

### Changed

- The write-time SHACL datatype check accepts `xsd:dateTime` / `xsd:date`.
- README, `ontology/` guide and diagrams cover the ingest path (Chain D).

## 1.0.1 — 2026-10-17

Plan 2: offline inference and derived facts.

### Added

- `onto infer`, the four-step offline chain: rdflib export → JVM `rules-runner`
  (Turtle in, delta out) → full pySHACL validation → single-transaction rebuild.
- Jena business rules for key contracts and department-head work orders.
- `get_inference_status` (write-counter staleness) and `list_violations` tools,
  bringing the MCP surface to 10 tools.
- Append-only `:ChangeEvent` log for the compliance-audit question.
- Inference CI gate and the derived-fact scenario examples.

### Fixed

- The derived layer strips by annotation, not by name, so an asserted type is never
  removed because a rule derived the same name elsewhere.
- `materialize_rdf` round-trips the ABox; a pre-existing ABox edge is never marked
  derived.

## 1.0.0 — 2026-10-16

Plan 1: the ontology foundation and the MCP server.

### Added

- Ontology data under `ontology/` — a worked sample domain (enterprise contract
  management) with TBox, SHACL shapes, a query catalog and a sample ABox. Replace
  it with your own; no Python changes needed. See [`ontology/README.md`](ontology/README.md).
- MCP server (`feomcp`) — 8 tools covering schema supply, competency-question
  lookup, raw Cypher and controlled writes; 5 resources and 2 prompts carrying the
  usage playbook; a Claude Code skill wrapping them.
- Write-time SHACL Core validation, atomic batches, a default-deny Cypher guard,
  an audit log and read-time field redaction.
- `onto project` (schema projection) and `onto seed` (load an ABox) CLIs; `make demo`
  runs the whole path end to end.

### Not in 1.0.0

Offline inference (`onto infer`, the JVM `rules-runner`), the `get_inference_status`
and `list_violations` tools, and the compliance-audit scenarios — those shipped in
1.0.1.
