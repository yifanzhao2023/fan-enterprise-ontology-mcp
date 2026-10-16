---
name: beomcp
description: Use the enterprise ontology MCP server (beomcp) to answer business questions over contracts, customers, work orders and org structure, and to write instance data into it. Triggers when the user asks about 合同/客户/工单/部门/员工 business facts, wants to query or write enterprise ontology data, asks "which contracts…", "who reports to…", or asks to load/validate ontology files. Also triggers on /beomcp.
---

# Using the beomcp ontology MCP

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

## Known limits (do not promise these)

Derived facts — "the department head owns that department's work orders", "contracts
over 1M that are not archived are key contracts" — come from an offline inference
job that is not in v1. If the answer needs one, say so rather than inventing it.

Full change history ("who edited this contract and when, over time") is likewise
not available; only the last write is stamped.
