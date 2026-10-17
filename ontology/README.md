# ontology/ — how to swap in your own ontology

> **This is a sample, not your ontology.** What lives here is **enterprise contract
> management**, a runnable sample domain. Its purpose is to make the server work out
> of the box and to give CI something to regress against. Replace all of it with your
> own data before using this for real. Doing so **requires no Python changes**.

**Supported scenarios:** term disambiguation, named queries, multi-hop org traversal,
a legal write, an illegal write, a security boundary, offline-derived facts, and a
batch import from an external ERP — see [section 12](#12-scenario-examples-few-shot),
[section 13](#13-inference-scenarios-after-make-infer) and
[section 14](#14-importing-from-a-business-system-onto-import).

| | |
|---|---|
| Sample domain | Enterprise contract management |
| Classes / properties | 7 classes, 10 properties |
| Named queries | 13 (CQ-01 … CQ-13) |
| Jena rules | 2 (department-head work orders, key contracts) |
| Import mapping | `mapping/odoo.yaml` + `mapping/irules.yaml` (reference source: Odoo) |
| Swapping it out | section 10 below / [`README_zh.md`](README_zh.md)（中文） |

---

## 1. The files in this directory

| File | Written by | Read at runtime by | What it is |
|---|---|---|---|
| `TBox.ttl` | human | **nothing at runtime**. Only `onto project` and the reasoner | the authoritative ontology: classes, properties, domain/range |
| `shapes.ttl` | human | the write path (`require_valid`) | SHACL constraints |
| `queries/*.cypher` | human | `Catalog.run` (the `run_query` tool) | named competency questions |
| `manifest.json` | human | `Catalog.load` | each query's parameters, return columns, sample, `uses` |
| `schema.projection.json` | **machine-generated** | `SchemaCatalog` (`search_schema` / `get_schema` / `get_constraints`) | flat query-side view |
| `abox.ttl` | human | `onto seed` / `make seed` | sample instances |
| `rules/*.rules` | human | `rules-runner` (JVM) | Jena rules; arithmetic business conditions |
| `mapping/<source>.yaml` | human | `onto import`, `onto lint-mapping` | terminology mapping: source columns/values to classes, properties, relations, code tables |
| `mapping/irules.yaml` | human | `onto import` | entity IRI template — deterministic, with a `{source}` segment |
| `mapping/glossary.md` | human (business review) | humans, agent playbook | the same alignment in plain language |

**The key point:** every schema the agent sees comes from `schema.projection.json`,
not `TBox.ttl`. The projection is a flat query-side view — **no OWL axioms, and it is
never fed to the reasoner**.

---

## 2. TBox.ttl

Declare classes and properties. Two rules are mandatory:

**(1) Lock the OWL 2 RL subset.** These constructors must not appear; the CI profile
check refuses them:

```
owl:unionOf   owl:complementOf   owl:oneOf   owl:hasValue   owl:disjointWith
```

**(2) Arithmetic does not go in the TBox.** "Amount > 1 million" cannot be expressed
in OWL — description logics have no comparison operator. It belongs in Jena rules
(`rules/*.rules`). Do not try to axiomatise it.

Minimal shape:

```turtle
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix onto: <https://example.org/onto#> .

onto:Contract a owl:Class ;
    rdfs:label "合同"@zh , "Contract"@en .

onto:hasAmount a owl:DatatypeProperty ;
    rdfs:domain onto:Contract ;
    rdfs:range  xsd:decimal .

onto:belongsToCustomer a owl:ObjectProperty ;
    rdfs:domain onto:Contract ;
    rdfs:range  onto:Customer .
```

The namespace `https://example.org/onto#` belongs to the sample. Use your own business
namespace. It appears as `@context.vocab` on every query result (from the `namespace`
field in `manifest.json`).

**Give every term an `rdfs:label`.** `search_schema` matches names *and* labels, which
is how a Chinese business word reaches `archived` or `hasAmount`. Without labels the
agent is reduced to guessing identifiers.

---

## 3. shapes.ttl

Constraints. **Only these four kinds run at write time**; nothing else enters the write
path:

| Constraint | Meaning | Runs at write time |
|---|---|---|
| `sh:minCount` | at least N values | yes |
| `sh:maxCount` | at most N values | yes |
| `sh:datatype` | literal datatype | yes |
| `sh:class` | the object must be an instance of a class | yes |
| anything else (`sh:minInclusive`, SHACL-AF, SPARQL constraints …) | | no — offline full run only |

So: **do not write a constraint you do not intend to enforce.** A constraint that is
present but never checked is worse than none — `get_constraints` will not show it
either.

```turtle
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix onto: <https://example.org/onto#> .

onto:ContractShape a sh:NodeShape ;
    sh:targetClass onto:Contract ;
    sh:property [
        sh:path onto:hasAmount ;
        sh:datatype xsd:decimal ;
        sh:minCount 1 ;
        sh:maxCount 1
    ] ;
    sh:property [
        sh:path onto:belongsToCustomer ;
        sh:class onto:Customer ;
        sh:minCount 1
    ] .
```

`sh:targetClass` decides which instances the shape applies to. At write time the match
is on the short name of the type being written.

---

## 4. queries/*.cypher

One file per named query. Conventions:

- **file name = the `name` in `manifest.json`** (`contracts_by_customer.cypher` ↔ `"name": "contracts_by_customer"`)
- parameters use `$param`, supplied by the tool call — **never string-concatenated**
- must carry a `LIMIT` (row cap is 50 by default)
- read-only. The guard rejects `CREATE`/`MERGE`/`DELETE`/`SET`/`REMOVE`/`DROP`/`LOAD CSV`, any `apoc.*` / `dbms.*`, and any `CALL` outside the whitelist

```cypher
MATCH (c:Customer {uri: $customer_iri})<-[:belongsToCustomer]-(k:Contract)
WHERE toFloat(k.hasAmount) > toFloat($min_amount)
RETURN k.uri AS contract_iri, toFloat(k.hasAmount) AS amount
ORDER BY amount DESC
LIMIT 50
```

**Queries see derived facts.** After `make infer`, a query that walks `hasOwner` will
return both the asserted owner and the derived one. Results do not hide staleness —
deciding whether an answer depends on derived facts is the caller's job.

---

## 5. manifest.json

Not optional metadata — **without it the catalog refuses to load**.

```json
{
  "version": 1,
  "namespace": "https://example.org/onto#",
  "queries": [
    {
      "name": "contracts_over_amount_by_customer",
      "cqid": "CQ-03",
      "question_zh": "找出指定客户名下金额超过阈值的合同",
      "params": [
        {"name": "customer_iri", "type": "iri", "required": true},
        {"name": "min_amount", "type": "decimal", "required": true, "default": 1000000}
      ],
      "returns": [
        {"name": "contract_iri", "type": "iri"},
        {"name": "amount", "type": "decimal"}
      ],
      "sample_params": {"customer_iri": "https://example.org/id/customerA", "min_amount": 1000000},
      "sample_result": [{"contract_iri": "https://example.org/id/contract001", "amount": 1200000.0}],
      "uses": ["Contract", "Customer", "hasAmount", "belongsToCustomer"],
      "tags": ["合同", "金额"]
    }
  ]
}
```

**Three fields are CI contract, not decoration:**

| Field | What it does |
|---|---|
| `uses` | checked against `schema.projection.json` at load time. A class/property name missing from the projection → load fails |
| `sample_params` | the regression input |
| `sample_result` | the expected output. Change the data and you must update this, or CI goes red |

`namespace` becomes the `@context.vocab` value on every query result.

`sample_result: null` means "demonstrated in the docs, not regression-tested". Use it
for a query whose output cannot be pinned down — `contract_change_history` returns a
run timestamp, so no fixed sample can ever match.

---

## 6. schema.projection.json

**Machine-generated. Do not hand-edit.**

```bash
make projection        # or: python -m feomcp.cli project
```

CI runs `make lint-projection`: regenerate, then `git diff --exit-code`. Mismatch is
failure. Change `TBox.ttl` or `shapes.ttl` and forget to regenerate, and the PR goes red.

It carries only what the query side needs: class names, property names, kinds,
cardinality, Core constraints, and `rdfs:label`s. No OWL axioms.

---

## 7. abox.ttl

Sample instances. `make seed` loads them into Neo4j and `tests/e2e` reads the same file,
so the demo and the tests cannot drift apart.

```turtle
@prefix onto: <https://example.org/onto#> .
@prefix ex:   <https://example.org/id/> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

ex:contract001 a onto:Contract ;
    onto:hasAmount 1200000.0 ;
    onto:hasStatus "draft" ;
    onto:archived false ;
    onto:hasEndDate "2027-03-31T00:00:00"^^xsd:dateTime ;
    onto:belongsToCustomer ex:customerA .
```

`rdf:type` → entity type, literal → datatype property, URI object → relation. Exactly
the same representation writer the MCP write tools use.

Write money as `1200000.0`, not `1200000`. Turtle makes the bare integer `xsd:integer`
while the TBox declares `xsd:decimal`, and the offline SHACL checks the literal's
datatype. The exporter stamps the TBox's declared range back on, so the round trip
stays honest either way — but keeping the file consistent saves the next reader a
puzzle.

---

## 8. What to run after a change

In order:

```bash
make projection        # 1. regenerate the projection
make lint-projection   # 2. confirm it matches the TBox (what CI runs)
make lint-mapping      # 3. confirm every mapping still points at live terms
make unit              # 4. unit tests (RL constructor blacklist, SHACL decisions)
make seed              # 5. load the ABox (Neo4j must be up)
make infer             # 6. build the derived layer
make e2e               # 7. every CQ against its sample_result
```

One shot:

```bash
make demo              # = up + seed + infer + e2e
```

---

## 9. CI gates

| Gate | What it catches | How |
|---|---|---|
| `make lint-projection` | projection drifted from TBox/shapes | `git diff --exit-code` |
| OWL 2 RL constructor check | axioms beyond RL crept in | `tests/unit/test_ontology_model.py` |
| SHACL on the sample ABox | dirty sample data | `tests/unit/test_validate.py` |
| `uses` ⊆ projection | a query references a deleted class/property | `Catalog.load` raises at load time |
| every CQ vs `sample_result` | an ontology change broke a query | `tests/e2e/test_demo.py` |
| `onto infer` runs | broken rules | `make demo` |
| `make lint-mapping` | a mapping references a deleted class/property, an unknown code table or target record | `validate_mapping` |

---

## 10. Replacing the sample with yours: a full walkthrough

1. Rewrite `TBox.ttl`: your classes and properties, your namespace
2. Rewrite `shapes.ttl`: only the four constraint kinds you actually enforce
3. Rewrite `manifest.json`: change `namespace`, rewrite `queries` entry by entry
4. Rewrite `queries/*.cypher`: file names must match `name`
5. Rewrite `abox.ttl`: your sample instances (or leave it empty and use the write tools)
6. Rewrite `rules/*.rules`: your business conditions, or delete the directory
7. Write `mapping/<source>.yaml` for each external system and run `make lint-mapping`
8. `make projection && make lint-projection`
9. `make seed && make infer && make e2e`
10. Run one import: `onto import --source <source> --adapter <module>`
11. Commit `schema.projection.json` once CI is green

No Python changes at any step.

---

## 11. FAQ

**`Catalog.load` raises `references unknown terms`**
`manifest.json`'s `uses` names a class/property the projection does not have. Run
`make projection`, or fix `uses`.

**`FileNotFoundError: queries/xxx.cypher`**
The manifest `name` and the file name disagree.

**A CQ regression went red but the query is right**
`sample_result` is a hard-coded expectation. Change the data or the query and you must
update it too.

**`make lint-projection` is red after a TBox change**
Correct — that is exactly what it is for. Run `make projection` and commit
`schema.projection.json` alongside.

**`build_server` says "query and write accounts are the same account"**
Read and write share an account, which breaks the security model. Give the query
account a `MATCH`-only role, or set `FEOMCP_ALLOW_SHARED_ACCOUNT=1` during development
only.

**`make infer` warns about a TBox term**
`TransitiveProperty`, `SymmetricProperty`, `inverseOf`, `propertyChainAxiom` and friends
are legal OWL 2 RL but the rules engine does not apply them. Write the rule yourself in
`rules/*.rules`. The warning is so the silence is not a surprise.

---

## 12. Scenario examples (few-shot)

The sample domain is enterprise contract management. These six are questions an
ontology actually answers and a flat table plus SQL does not. They work as agent
examples; the data comes from `abox.ttl` and every answer reproduces.

### Scenario 1 — term disambiguation and field discovery

**Business question:** contracts for customer A that are not archived, with amount and status.

**Without the ontology:** the agent guesses field names. "归档" is `archived`, "状态" is
`hasStatus`, "金额" is `hasAmount` — and the harder part is that **archived and status are
independent fields**. `contract003` is `closed` *and* `archived: true`; confuse the two and
you either find nothing or report an archived contract as open.

**Tool path:** `search_schema` → `query_raw`

```
Q: 客户A 名下还没归档的合同，金额和状态列出来

tool: search_schema("归档")   -> archived (property, label 「已归档」)
tool: search_schema("客户")   -> Customer (class), belongsToCustomer (relation)
tool: query_raw("MATCH (c:Customer {uri:$c})<-[:belongsToCustomer]-(k:Contract)
                 WHERE k.archived = false
                 RETURN k.uri AS contract_iri, k.hasAmount AS amount, k.hasStatus AS status
                 ORDER BY k.uri", {"c": "https://example.org/id/customerA"})

A: contract001  1,200,000  draft
   contract002    800,000  draft
   (contract003 belongs to customer A but archived=true, so it is excluded)
```

**Where the ontology earns its keep:** `rdfs:label` maps the Chinese business word to
the property short name. Keeping `archived` and `hasStatus` separate is a modelling
decision, not a database accident.

---

### Scenario 2 — named queries cover standard questions

**Business question:** who heads the sales department?

**Without the ontology:** the agent improvises Cypher and very likely confuses `hasHead`
with `hasOwner` — the first is the **department** head, the second is the **work-order**
owner. It answers with Zhang, you asked for Li.

**Tool path:** `list_queries` → `run_query`

```
Q: 销售部的负责人是谁

tool: list_queries()  -> CQ-05 department_head 「查指定部门的负责人」
tool: run_query("department_head", {"department_iri": "https://example.org/id/departmentSales"})

A: employeeZhang
```

**Where the ontology earns its keep:** `hasHead` has `rdfs:domain Department` and
`rdfs:range Employee` — the semantics are written down in the TBox. The named query
freezes that into a reusable, regression-tested question.

---

### Scenario 3 — multi-hop org traversal

**Business question:** which department does Zhang's work order belong to? What other
work orders does that department have, and who owns each?

**Without the ontology:** identifiers disagree across work orders, departments and
employees; you need several joins and manual alignment. "Who is responsible" also
blurs into department head (`hasHead`) rather than work-order owner (`hasOwner`).

**Tool path:** `search_schema` → `query_raw` (the catalog has no combined query for this)

```
Q: 张三负责的工单属于哪个部门？该部门还有哪些工单、各自谁负责

tool: search_schema("负责") -> hasHead (relation), hasOwner (relation)
tool: query_raw("MATCH (e:Employee {uri:$e})<-[:hasOwner]-(w:WorkOrder)
                 -[:belongsToDepartment]->(d:Department)<-[:belongsToDepartment]-(other:WorkOrder)
                 OPTIONAL MATCH (other)-[:hasOwner]->(o:Employee)
                 RETURN w.uri AS mine, d.uri AS dept, other.uri AS work_order_iri, o.uri AS owner_iri",
                {"e": "https://example.org/id/employeeZhang"})

A: wo001 is in departmentSales
   that department also has wo002 (Zhao) and wo003 (Wang)

(Note: Cypher will not traverse the same relationship twice in one path, so wo001
 does not appear in "other work orders" — which is exactly what "还有" means.)
```

**Where the ontology earns its keep:** `hasOwner` and `hasHead` have distinct
domain/range, so the agent can tell the two kinds of "responsible" apart. Three hops
are manual ID alignment in a relational schema; here they are one path.

---

### Scenario 4 — a legal write, as the constraints demand

**Business question:** sign a new 800,000 contract for customer C.

**Without the ontology:** write only the contract entity and get rejected.
`ContractShape` requires `belongsToCustomer` with `minCount 1` — a contract must have a
customer.

**Tool path:** `get_constraints` → `upsert_entities` (entities and relations **in one batch**)

```
Q: 给客户C 新签一份 80 万的合同

tool: get_constraints(["Contract"])
     -> belongsToCustomer  minCount 1 / class Customer
        hasAmount          datatype decimal / minCount 1 / maxCount 1
        hasStatus          datatype string  / minCount 1 / maxCount 1

tool: upsert_entities(
        entities=[{iri: ".../id/contract006", types: [".../onto#Contract"],
                   properties: {hasAmount: 800000, hasStatus: "draft", archived: false}}],
        relations=[{source_iri: ".../id/contract006",
                    predicate_iri: ".../onto#belongsToCustomer",
                    target_iri: ".../id/customerC"}])

A: {"applied": true, "written_iris": [".../id/contract006"]}
```

**Where the ontology earns its keep:** the SHACL `minCount` turns "a contract must have a
customer" from documentation into a hard constraint. The `relations` parameter on
`upsert_entities` exists so one batch can express both halves.

---

### Scenario 5 — an illegal write, rejected

**Business question:** same as above, but write only the contract.

**Tool path:** `upsert_entities` → `VALIDATION_FAILED` → add the relation → resubmit

```
tool: upsert_entities(entities=[{iri: ".../id/contract007", types: [".../onto#Contract"],
                                 properties: {hasAmount: 800000, hasStatus: "draft", archived: false}}])

A: {"code": "VALIDATION_FAILED",
    "message": "1 SHACL Core violation(s); first: belongsToCustomer — belongsToCustomer requires at least 1 value(s)",
    "hint": "fix the reported fields before resubmitting the batch",
    "retryable": false,
    "details": {"violations": [{"path": "belongsToCustomer", "kind": "minCount",
                                "focus_iri": ".../id/contract007", ...}]}}

(add the relation and resubmit -> applied: true)
```

**Where the ontology earns its keep:** the error is **field-level** (`path` + `kind`), so
the agent can act on it instead of reading "validation failed". Note
`retryable: false` — retrying the identical request is guaranteed to fail again; the
request has to change.

---

### Scenario 6 — the security boundary

**Business question:** change contract001's amount to 500,000.

**Without the ontology:** the agent runs
`query_raw("MATCH (n) SET n.hasAmount = ...")`, bypassing validation, audit and
atomicity.

**Tool path:** `query_raw` refused → use `upsert_entities`

```
tool: query_raw("MATCH (n {uri:$u}) SET n.hasAmount = 500000 RETURN n", {"u": ".../id/contract001"})

A: {"code": "QUERY_REJECTED",
    "message": "write keyword SET is not allowed",
    "hint": "queries are read-only; use upsert_entities / upsert_relations to write",
    "retryable": false,
    "details": {"rule": "no-write", "keyword": "SET"}}

tool: upsert_entities(
        entities=[{iri: ".../id/contract001", types: [".../onto#Contract"],
                   properties: {hasAmount: 500000, hasStatus: "draft", archived: false}}],
        relations=[{source_iri: ".../id/contract001",
                    predicate_iri: ".../onto#belongsToCustomer",
                    target_iri: ".../id/customerA"}])

A: {"applied": true}

tool: run_query("contract_amount", {"contract_iri": ".../id/contract001"})
A: [{"amount": 500000}]     <- read back to verify; `applied: true` is not proof
```

> The first half (`query_raw` refused) reproduces right now. The second half really does
> change contract001's amount to 500,000 (it is 1,200,000 in the sample), so run it only
> if you want to see it. `make seed` restores the sample at any time.

**Where the ontology earns its keep:** writes only go through the structured tools, and
the guard is a hard gate. `upsert_entities` gets a single transaction, SHACL validation
and an audit line; `query_raw` gets none of the three.

---

## 13. Inference scenarios (after `make infer`)

These need the offline chain. Run `make seed`, then `make infer`, then copy.

### 13.1 Department-head work orders

A department head is also the owner of that department's work orders. Rule
`belongsToDepartment . hasHead -> hasOwner`:

```
run_query("work_orders_by_department", {"department_iri": "https://example.org/id/departmentSales"})
```

Two **derived edges** come out (`r.inferred = true`):

| Work order | Derived owner | |
|---|---|---|
| wo002 | employeeZhang | the asserted owner is Zhao; Zhang (the head) is an additional one |
| wo003 | employeeZhang | the asserted owner is Wang |

wo001 and wo004 get **no** derived edge — the conclusion coincides with an edge the ABox
already has and is absorbed. So `MATCH ()-[r]->() WHERE r.inferred = true` returns two
rows, not four.

> `work_orders_by_department` can now return more than one row per work order, one per
> responsible person. That is deliberate: query results do not hide derived facts.

### 13.2 Key contracts

Amount over 1,000,000. **A class definition with arithmetic — OWL cannot express it**, so
it lives in `rules/business.rules`. Whether a contract is archived is current state and
belongs to the query, not the class: an archived contract is still a key contract.

```
run_query("contracts_by_key_and_archive_state", {"archived": false})
```

```
contract004  1500000.0
contract001  1200000.0
```

`contract005` (2,000,000) is also a key contract but `archived: true`, so this filter
drops it. Pass `archived: null` for all of them.

### 13.3 Compliance audit: who changed this contract, and when

**This is not an inference question** — it is a queryable change log. Every field of every
write produces a `:ChangeEvent` row:

```
run_query("contract_change_history", {"contract_iri": "https://example.org/id/contract001"})
```

```
at=2026-10-16T14:32:13+00:00  actor=system  predicate=hasEndDate
old_value=null  new_value=2027-03-31 00:00:00
```

- An unchanged value records nothing — re-asserting a contract is not an edit
- The log is append-only operational history, not ABox: a derived rebuild never touches
  it, and it never reaches `abox.ttl`
- Temporal semantics are `xsd:dateTime`. There is deliberately no OWL-Time module — the
  current question is only "who, when, which field". A time ontology is modelling cost
  paid for a question nobody has asked yet.

### 13.4 Has this contract expired?

```
run_query("contract_expiry_status",
          {"contract_iri": "https://example.org/id/contract001", "as_of": "2026-10-16"})
```

```
contract001  end_date=2027-03-31  status=active
contract004  end_date=2025-01-01  status=expired
```

**This is a query, not a derived label.** The reason is reproducibility: `onto infer` is a
pure function and replaying the same inputs must give the same outputs, which a rule that
reads the wall clock cannot do. If you ever need an `:Expired` label, thread `as_of`
through as a recorded input in the job fingerprint.

### 13.5 How fresh are the derived facts?

```
get_inference_status()
```

`stale` is `true` after any write, until the next `onto infer`. **Stale is the normal
state, not a fault.** Do not answer from a derived fact while `stale` is true without
saying so.

---

These four used to be the "v1 cannot do this" gaps. All of them run now — expiry as a
query rather than a derivation, for the reason above.

---

## 14. Importing from a business system (`onto import`)

Plan 3 adds a second way in: `onto import` loads a batch from an external system (ERP /
MES / WMS) through a mapping, instead of one `upsert` at a time or a whole Turtle file.
The batch goes through the **same representation writer and write-time SHACL** as the
MCP tools, so imported data and tool-written data are indistinguishable.

### 14.1 The adapter boundary

An adapter is user code outside `ontology/`. It has two methods and does no ontology
work — fetch and flatten only:

```python
class SourceAdapter(Protocol):
    def snapshot(self) -> SourceSnapshot: ...        # source_id, snapshot_time, version
    def iter_records(self) -> Iterator[Record]: ...  # record_type, flat {field: value}
```

The repo ships a reference adapter over an Odoo snapshot:
`examples/odoo_adapter.py` + `examples/odoo_records.json` (three customers, two orders).

### 14.2 `mapping/<source>.yaml`

```yaml
source: odoo
version: 1
records:
  customer:
    class: "https://example.org/onto#Customer"
    iri: {keys: [partner_id]}
    properties:
      - {field: name, predicate: "https://example.org/onto#hasName", datatype: xsd:string}
  contract:
    class: "https://example.org/onto#Contract"
    iri: {keys: [order_id]}
    properties:
      - {field: amount_total, predicate: "https://example.org/onto#hasAmount",
         datatype: xsd:decimal}
      - {field: commitment_date, predicate: "https://example.org/onto#hasEndDate",
         datatype: xsd:dateTime, format: "%Y-%m-%d %H:%M:%S"}
    relations:
      - {field: partner_id, predicate: "https://example.org/onto#belongsToCustomer",
         target_record: customer}
code_tables: {}
```

Rules:

1. `class` **or** `class_by_field` + `class_values` — mutually exclusive.
2. A relation names either `code_table:` (an enum individual) or `target_record:` (a
   foreign key to another record's IRI) — never both, exactly one.
3. Anything not listed in the mapping is dropped. The bridge never invents a property.
4. `onto lint-mapping` checks every referenced class / property against
   `schema.projection.json`, plus code-table and target-record names. Run it after any
   TBox change; it is also a CI gate.

### 14.3 `mapping/irules.yaml`

```yaml
base: "https://example.org/id"
template: "{base}/{source}/{record_type}/{key}"
key_join: "-"
```

The `{source}` segment is what keeps identifiers globally unique today and mergeable
when a second source arrives. `--base-iri` overrides the base; otherwise
`FEOMCP_BASE_IRI` applies when set.

### 14.4 Coercion

`xsd:string`, `xsd:integer`, `xsd:decimal`, `xsd:boolean`, `xsd:dateTime`, `xsd:date`
(the last two accept an optional `format:`). A value that cannot be converted skips that
record. **Coercion happens only on the import path** — agent upserts must send
already-typed values.

### 14.5 Run it

```bash
make lint-mapping
.venv/bin/python -m feomcp.cli import \
  --source odoo --adapter examples.odoo_adapter --actor alice
# import ingest-1a2b3c4d5e6f: 5 written, 0 skipped of 5
```

### 14.6 What the audit row tells you

One `:IngestJob` per attempt, append-only:

```cypher
MATCH (j:IngestJob)
RETURN j.job_id, j.source_id, j.snapshot_time, j.status,
       j.total, j.succeeded, j.failed, j.error_details
ORDER BY j.imported_at DESC
```

Field-level changes from the batch carry `actor = "ingest:<job_id>"`, so
`list_changes(iri)` answers "which import changed this contract".

### 14.7 Failure behaviour

| Situation | Result |
|---|---|
| A record misses a key, has an unknown record type, hits an unmapped code, or fails coercion | record skipped, counted in `failed`, reason in `error_details`; the batch continues |
| SHACL rejects the batch, the batch exceeds `FEOMCP_MAX_BATCH`, or the adapter/driver throws | nothing written; `:IngestJob(failed)` records the cause |

A successful import writes the ABox and `:IngestJob(ok)` in the same transaction.
