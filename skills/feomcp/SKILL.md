---
name: feomcp
description: Use the enterprise ontology MCP server (feomcp) to answer business questions over contracts, customers, work orders and org structure, and to write instance data into it. Triggers when the user asks about 合同/客户/工单/部门/员工 business facts, wants to query or write enterprise ontology data, asks "which contracts…", "who reports to…", asks to load/validate ontology files, or asks to bulk-import data from an ERP/CSV. Also triggers on /feomcp.
---

# Using the feomcp ontology MCP

This MCP exposes an enterprise ontology as an agent semantic memory layer. It is
not a document store and not a SQL console — the value is the **schema and the
constraints**, so use them.

## Before anything else

Read `ontology://playbook` from the MCP resources. It is the authoritative
decision tree, error table and write rules. This skill is only a wrapper: **do not
restate the playbook here, and do not assume its content** — if the playbook and
this file ever disagree, the playbook wins.

The two prompts `query-playbook` and `write-playbook` carry the same text through
the prompt channel.

## Order of operations — querying

1. `search_schema(keywords)` — confirm the names exist. **Never guess field names.**
   The vocabulary is the ontology's, not the English you would expect: "归档" is
   `archived`, "归属客户" is `belongsToCustomer`.
2. `list_queries()` — a named competency question probably already answers this.
3. `run_query(name, params)` — prefer this. Curated, parameterised, regression-tested.
4. `query_raw(cypher, params)` — only for combinations the catalog does not cover.

## Order of operations — writing

1. `get_constraints(types)` — see what must be present before you write.
2. `upsert_entities` / `upsert_relations` — **send the instance and its relations in
   the same call.** A `Contract` requires `belongsToCustomer`; writing the contract
   alone is rejected by design.
3. Read it back with a named query. `applied: true` says the transaction committed,
   not that the graph looks the way you meant it to.

## Claude Code specifics

- Do not reach for the Neo4j driver, `cypher-shell`, or a bash pipe to the database.
  Everything goes through the MCP tools, so the guard and the audit log apply.
- Do not write raw Cypher when a named query exists. If you are about to write a
  `MATCH` you have not shown the user, check `list_queries()` first.
- Long schema answers: ask `search_schema` with a small `limit` first, then
  `get_schema` on the two or three types that matter. The full projection is huge
  and is never meant for a prompt.
- If a tool returns `retryable: false`, **change the request** — do not resend it.
  `QUERY_REJECTED` is a safety rejection, not a transient failure.

## Bulk import is CLI-only

`onto import` is **not an MCP tool**. Do not emulate it with a flood of `upsert_*`
calls — the batch would lose lineage and could trip the batch cap.

When the user wants to load data from an ERP / MES / WMS:

1. `python -m feomcp.cli lint-mapping` must pass first.
2. They run:
   `python -m feomcp.cli import --source <id> --adapter <module> --actor <who>`
3. Afterwards, check the lineage with `query_raw`:

   ```cypher
   MATCH (j:IngestJob)
   RETURN j.job_id, j.source_id, j.status, j.total, j.succeeded, j.failed,
          j.error_details, j.imported_at
   ORDER BY j.imported_at DESC LIMIT 5
   ```

   Field-level changes from that batch carry `actor = "ingest:<job_id>"`.

Do not call an import successful because the CLI printed a job id. Check the
`:IngestJob` row's `status` and counts; `failed > 0` means rows were skipped, and
`error_details` says why.

## Known limits (do not promise these)

- **Derived facts are batch, not live.** They come from the offline `onto infer`
  job. Call `get_inference_status()` first: `stale: true` means the ABox changed
  since the last run, so say the derived answer may be out of date.
- **Change history is per field, and it exists.** Use the named query
  `contract_change_history` (or `:ChangeEvent` via `query_raw`) for "who changed
  this, when". It is append-only and inference never rebuilds it.
- **No text extraction.** Pulling entities out of documents or contracts is not
  built; only structured sources through `onto import`.
- **No incremental sync.** `onto import` reads a snapshot; scheduled deltas are
  planned (P1).
- **No multi-source entity resolution.** One source per entity type is the v1
  assumption.
- **The agent cannot trigger inference or import.** Both are CLI/batch operations,
  deliberately not exposed as tools.

