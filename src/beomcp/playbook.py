"""The knowledge an agent needs to use this MCP server correctly.

It lives here rather than in a Claude Code skill so every MCP client gets it —
Codex does not read Claude Code skills. A skill may point at these resources;
it must not duplicate them.
"""

QUERY_PLAYBOOK = """# How to query this ontology

## Order of operations

1. `search_schema(keywords)` — confirm the class and property names actually exist.
   Do not guess field names; the vocabulary is the ontology's, not the English one
   you would expect.
2. `list_queries()` — check whether a named competency question already covers the
   question. Most business questions are one of these.
3. `run_query(name, params)` — use it when one fits. Named queries are curated,
   parameterised and regression-tested against expected results.
4. `query_raw(cypher, params)` — only when nothing in the catalog fits. Ad-hoc
   combinations only.

## Writing raw Cypher

- **Read-only.** The guard rejects `CREATE`, `MERGE`, `DELETE`, `DETACH`, `SET`,
  `REMOVE`, `DROP` and `LOAD CSV`.
- **APOC and `dbms.*` are disabled entirely.** Do not propose them.
- **`CALL` is default-deny.** Only a handful of built-in introspection procedures are
  permitted, and a `CALL` the guard cannot resolve is rejected outright.
- Always include `LIMIT`.
- Pass values as `$parameters`. Never build a query by string concatenation — the
  driver parameterises them, and that is the whole injection defence.
- An instance node carries `uri` as its identity. Labels are the short names of its
  types; datatype properties are node properties; object properties are relationships.

## Reading errors

| code | meaning | what to do |
|---|---|---|
| `SCHEMA_NOT_FOUND` | the type or query name does not exist | run `search_schema` or `list_queries`; do not retry |
| `QUERY_SYNTAX` | the Cypher did not parse | fix it; check labels and relation names via `get_schema()` |
| `QUERY_REJECTED` | a safety rule fired | do not retry the same query — rewrite it |
| `QUERY_LIMIT` | too many rows, or too slow | narrow the filter, or add `LIMIT` |
| `VALIDATION_FAILED` | SHACL Core violation | fix the fields named in `details.violations` |
| `WRITE_REJECTED` | batch too large, or a bad identifier | split the batch, or fix the name |
| `BACKEND_UNAVAILABLE` | the database or the server failed | see `retryable` below |

`retryable: true` means the identical request may succeed unmodified — retry it.
`retryable: false` means you must change the request first.

## Derived facts are stale by design

`:INFERRED` is written by an offline batch job, not by your write. Between two jobs it
is stale, and query results do not hide that. If the answer depends on a derived type
or relation, say so in the answer.
"""

WRITE_PLAYBOOK = """# How to write to this ontology

## Read the constraints first

`get_constraints(types)` returns the Core requirements for the types you are about to
write. Its `scope` is always `"core"`: passing write-time validation guarantees Core
conformance only. Other constraints surface later, at offline full validation.

## One batch carries both halves

`upsert_entities` takes an optional `relations`; `upsert_relations` takes optional
`entities`. Send them together.

A `Contract` has a `minCount 1` on `belongsToCustomer` — writing the contract without
its customer relation is rejected. The batch is validated as a unit, so supply the
instance and its relation in the same call.

## Batches are atomic

All-or-nothing. Nothing partial is ever committed, so a rejected batch leaves the graph
exactly as it was.

A batch larger than `BEOMCP_MAX_BATCH` is refused with a hint to split it. Split it —
chunked writes are deliberately not offered, because they would break atomicity.

## Identifiers are agent input

`types` and property keys arrive from you and land in Cypher identifier positions.
Letters, digits, underscore and hyphen only; anything else is refused with
`rule: identifier-whitelist`. A bare string in `types` is refused too — it must be a
list of names.

## When validation fails

`VALIDATION_FAILED` carries `details.violations[]`, each with `focus_iri`, `shape_iri`,
`path`, `message` and `kind`. Fix the named fields and resubmit the whole batch.

Write-time validation runs the SHACL Core subset only: `minCount`, `maxCount`,
`datatype`, `class`.

## After writing

Writing does **not** update `:INFERRED`. Derived facts stay stale until the next
offline batch job.

Verify a write by reading it back with a named query. `applied: true` says the
transaction committed, not that the graph looks the way you meant it to.
"""
