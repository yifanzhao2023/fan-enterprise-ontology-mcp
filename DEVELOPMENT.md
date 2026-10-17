# Development & Testing

> Maintainer's guide: repository layout, module boundaries, test layers, CI gates and
> the release/tag convention. Kept out of the README so the front page stays focused
> on what the product does.

## Development

### Repository layout

```
src/feomcp/           the MCP server, the ingest bridge and the offline chain
  ports.py            QueryEngine + FactStore protocols, IngestJobMeta
  neo4j_store.py      Neo4j adapter: driver, representation writer, ingest + :IngestJob
  schema.py           reads schema.projection.json
  catalog.py          named queries (the CQ catalog)
  validate.py         SHACL Core at write time, full SHACL offline, TBox profile gate
  guard.py            the Cypher trust boundary
  server.py           tool surface — the only exit
  adapter.py          SourceAdapter / Record / SourceSnapshot boundary (user code)
  mapping.py          mapping + irules loading, lint, coercion, IRI, apply_record
  ingest.py           run_ingest: adapter records -> one WriteBatch, failure split
  cli.py              onto project / seed / infer / import / lint-mapping
jvm/                  rules-runner: Turtle in, delta out. No DB, no network
ontology/             sample domain (replace it — see ontology/README.md)
  rules/*.rules       Jena rules; arithmetic business conditions live here
  mapping/*.yaml      terminology mapping + irules.yaml (the import data contract)
  mapping/glossary.md the same alignment for business reviewers
examples/             reference Odoo adapter + snapshot fixture (no live system needed)
tests/                unit / integration / e2e
```

### Local development workflow

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

make rules-runner     # build jvm/target/rules-runner.jar (needs JDK 17+ and Maven)
make unit             # no dependencies
make integration      # testcontainers, needs a running Docker daemon
make demo             # down -v + up + seed + infer + e2e against docker-compose
make lint-mapping     # every mapping against schema.projection.json
```

`make integration` and `make demo` need Docker. `make unit` does not — it is the
fast loop. On a machine without a Docker daemon, run the integration suite against a
reachable Neo4j host (copy `src tests ontology examples Makefile pyproject.toml` over
and use that host's venv).

### Module boundaries (design summary)

Only two Python ports earn their interface — storage and query — because both have a
planned second implementation. SHACL validation, projection reading, the query
catalog, and the representation writer are concrete: a second implementation is not
planned, and wrapping them would be tax paid to a hypothetical.

`SourceAdapter` is a protocol as well, but it is not a backend port: it is the
**user-facing extension point**. Every external system gets its own adapter; the core
never learns which ERP / MES / WMS it is talking to. Adapters fetch and flatten only —
types, predicates, coercion, IRI construction and validation belong to the bridge.

**Inference is a process boundary, not a Python interface.** Its contract is file
exchange (`Turtle in → inferred.ttl out`). Swapping Jena for ELK means replacing
`rules-runner`; the Python orchestration layer does not change.

### The ingest path

```
adapter.iter_records() -> Record(record_type, flat fields)
  -> mapping.apply_record(): class, properties, relations, code tables, coercion
  -> build_iri(): deterministic, {source} segment
  -> WriteBatch -> FactStore.ingest()
       -> same representation writer + write-time SHACL Core as the MCP tools
       -> :ChangeEvent (actor = "ingest:<job_id>")
       -> :IngestJob(ok) in the same transaction
```

- **Record-level failures** (missing key, unknown record type, unmapped code, bad
  coercion) are skipped, counted in `failed`, and recorded in `error_details`.
- **Batch-level failures** (SHACL rejection, batch-size limit, adapter/driver error)
  write nothing; `record_ingest_failure` appends an `:IngestJob(failed)` row.
- `job_id` identifies the attempt (append-only, never reused); `input_hash` identifies
  the inputs (source + snapshot time + mapping + IRI-rule digests).
- A relation names exactly one of `code_table:` (enum individual) or `target_record:`
  (foreign key to another record's IRI).
- `apply_record` skips a `None` property with no declared datatype: writing it through
  `SET n += {k: null}` would delete an existing value instead of leaving it alone.

---

## Testing & Acceptance

### Test layers

| Layer | Scope | Depends on |
|---|---|---|
| Unit | `guard` (table-driven injection samples), `schema` / `catalog` parsing, `validate` Core judgments, `cli` apply logic against a fake JVM, mapping load/lint/coercion/IRI, `run_ingest` against a fake store, the reference Odoo adapter | nothing |
| Integration | `neo4j_store`: upsert atomicity, representation writer, `materialize_rdf` round-trip, `replace_derived` rebuild; `ingest`: ABox + `:IngestJob(ok)` in one transaction, failed-job-only on rejection, duplicate-IRI idempotence, datetime round-trip; rules-runner; server tool surface | testcontainers (Neo4j) |
| End-to-end | `make demo`: load sample → `onto infer` → all CQs → assert `sample_result` | docker-compose |
| CQ regression | Every manifest entry runs `sample_params` against `sample_result` | same |

### CI gates (every PR)

| Gate | Catches |
|---|---|
| `onto project` then `git diff --exit-code` | Projection drifting from Turtle |
| TBox profile check | OWL constructs beyond RL |
| SHACL validation of sample ABox | Dirty sample data |
| `onto infer` runs | Broken rules |
| All CQs vs `sample_result` | Ontology changes breaking queries |
| manifest `uses` ⊆ projection | Queries referencing deleted classes / properties |
| `make lint-mapping` | A mapping referencing a deleted term, an unknown code table or target record |
| Unit + integration tests | Code regressions |
| `make demo` full chain | Integration breaks |

`make ci-infer-gate` = `lint-projection lint-mapping unit demo`.

**TBox profile check**: `feomcp.validate.banned_owl_constructs` runs before the JVM is
launched and rejects the five constructors the modeling contract forbids
(`owl:unionOf` / `owl:complementOf` / `owl:oneOf` / `owl:hasValue` /
`owl:disjointWith`). It is a blacklist lint and is named as incomplete in the code.
The spec's preferred authoritative check was an OWL API `OWL2RLProfile`; Jena has no
such class, so the blacklist is the implementation.

### Error paths that must be tested

- Every guard rule has at least one sample (including escape hatches like `apoc.cypher.run`)
- `VALIDATION_FAILED` is field-level (shape / path / message / focus)
- **JVM failure does not apply** — previous `:INFERRED` intact
- Batch atomicity: half a batch invalid → nothing lands
- Ingest record-level failure → that record skipped and counted, the batch continues
- Ingest batch-level failure → ABox untouched, `:IngestJob(failed)` appended, never a
  second failure row
- Duplicate IRI across imports → one node, both attempts audited
- Timeout / row cap triggers
- Projection missing or invalid → refuse to start
- Dirty git tree → `onto infer` refuses

### Verified behaviours

The spikes that were open at design time are resolved; each is now pinned by a test.

| Behaviour | What holds |
|---|---|
| ABox round-trip | `materialize_rdf` re-emits every entity, type, property and relation that was written (zero diff) |
| URI unique key | the `uri` property is the entity unique key; repeated writes are idempotent; no duplicate entities |
| Version compatibility | Neo4j 5.x stable for the read/write sessions and the derived-layer rebuild |
| Ingest atomicity | ABox write and `:IngestJob(ok)` commit in one transaction; a rejected batch leaves the ABox untouched and only a failed job row |
| Ingest replay identity | same source + snapshot + mapping + irules produce the same `input_hash` and the same generated IRIs |
| Temporal values | naive `datetime` written by the bridge is exported by `materialize_rdf` as `xsd:dateTime` |

The one open risk is large-instance export memory (see the README's "Known risk" note);
the sample is small, so it is untested at production scale.

### Metrics (POC thresholds, fixed)

| Metric | Threshold |
|---|---|
| Named-query path success rate | **≥ 99%** |
| Raw Cypher path syntax pass rate | ≥ 85% (**after one retry**) |
| Raw Cypher execution success rate | ≥ 95% |
| Answer correctness | ≥ 90% |
| CQ coverage | 100% on sample set, ≥ 90% on real ontology |
| SHACL violation rate | < 1% |
| Query P95 | < 500 ms |
| Write validation P95 | < 200 ms |
| Schema tokens injected per call | < 2000 |

The two query paths are measured separately so the value of the query catalog is
visible. If the named-query path drops below 95%, the fix is more CQs — not tuning
raw Cypher.

---

## Releases

The version lives in `pyproject.toml`. Each plan ships as one minor version, and the
tag is created on `main` after the plan's PR is merged (annotated tags):

| Version | Plan | Main commit tagged |
|---|---|---|
| 1.0.0 | Plan 1 — ontology foundation + MCP server | PR #3 merge (`d469059`) |
| 1.0.1 | Plan 2 — offline inference + derived facts | PR #5 merge (`3ba2374`) |
| 1.0.2 | Plan 3 — terminology mapping + ingest + lineage | the merge that lands this plan |

`CHANGELOG.md` records each release. Historical plan documents under `docs/` are
deliberately not committed; this file is, so external contributors can see how to
build and test.
