# ontology/ —— 怎么换成你自己的本体

这个目录放的是**一个可跑通的样例**（企业合同管理），不是你的本体。
目的是让 server 开箱能跑、CI 有东西可回归。下面说明每个文件是什么、怎么写、怎么验证。

换掉它们**不需要改任何 Python 代码**。

---

## 1. 六个文件

| 文件 | 谁写 | 运行时谁读 | 说明 |
|---|---|---|---|
| `TBox.ttl` | 人 | **没有任何运行时组件读它**。只有 `onto project` 和（Plan 2 的）推理机 | 权威本体：类、属性、domain/range |
| `shapes.ttl` | 人 | 写入路径 `require_valid` | SHACL 约束 |
| `queries/*.cypher` | 人 | `Catalog.run`（工具 `run_query`） | 命名能力问题 |
| `manifest.json` | 人 | `Catalog.load` | 查询的参数、返回列、样例、`uses` |
| `schema.projection.json` | **机器生成** | `SchemaCatalog`（工具 `search_schema` / `get_schema` / `get_constraints`） | 查询侧扁平视图 |
| `abox.ttl` | 人 | `onto seed` / `make seed` | 样例实例数据 |

**关键**：Agent 读到的 schema 全部来自 `schema.projection.json`，不是 `TBox.ttl`。
投影是查询侧的扁平视图，**不含 OWL 公理，也永不喂给推理机**。

---

## 2. TBox.ttl

写类和属性。两件事必须遵守：

**（1）锁死 OWL 2 RL 子集。** 下列构造器禁止出现，CI 的 profile 检查会拦：

```
owl:unionOf   owl:complementOf   owl:oneOf   owl:hasValue   owl:disjointWith
```

**（2）算术条件不进 TBox。** 「金额 > 100 万」这类比较写不进 OWL——描述逻辑没有比较运算。
它属于 Jena Rules（Plan 2），不要试图用公理表达。

最小形状：

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

命名空间 `https://example.org/onto#` 是样例用的，换成你的业务命名空间。
它会出现在每个查询结果的 `@context.vocab` 里（由 `manifest.json` 的 `namespace` 字段提供）。

---

## 3. shapes.ttl

写约束。**写入时只执行这四类**，其余不进写时路径：

| 约束 | 含义 | 写入时执行 |
|---|---|---|
| `sh:minCount` | 至少几个值 | ✅ |
| `sh:maxCount` | 至多几个值 | ✅ |
| `sh:datatype` | 字面量类型 | ✅ |
| `sh:class` | 对象必须是某类的实例 | ✅ |
| 其它（`sh:minInclusive`、SHACL-AF、SPARQL 约束…） | | ❌ 留给离线全量 |

所以：**不要写你没打算强制执行的约束**。写进去却不校验，比不写更糟——`get_constraints` 也不会展示它。

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

`sh:targetClass` 决定这个 shape 应用到哪些实例。写入时按被写类型的短名匹配。

---

## 4. queries/*.cypher

每条命名查询一个文件。约定：

- **文件名 = `manifest.json` 里的 `name`**（`contracts_by_customer.cypher` ↔ `"name": "contracts_by_customer"`）
- 参数用 `$param`，由工具调用时传入，**绝不拼字符串**
- 必须带 `LIMIT`（默认行上限 50）
- 只读。guard 会拒 `CREATE`/`MERGE`/`DELETE`/`SET`/`REMOVE`/`DROP`/`LOAD CSV`，以及任何 `apoc.*` / `dbms.*` / 白名单外的 `CALL`

```cypher
MATCH (c:Customer {uri: $customer_iri})<-[:belongsToCustomer]-(k:Contract)
WHERE toFloat(k.hasAmount) > toFloat($min_amount)
RETURN k.uri AS contract_iri, toFloat(k.hasAmount) AS amount
ORDER BY amount DESC
LIMIT 50
```

---

## 5. manifest.json

不是可选的元数据——**没有它目录加载直接失败**。

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

**三个字段是 CI 契约，不是装饰**：

| 字段 | 作用 |
|---|---|
| `uses` | 加载时拿去 `schema.projection.json` 里核对。引用了投影里不存在的类/属性 → 拒启动 |
| `sample_params` | 回归测试的输入 |
| `sample_result` | 回归测试的期望输出。数据变了要同步改，否则 CI 红 |

`namespace` 会成为每个查询结果 `@context.vocab` 的值。

---

## 6. schema.projection.json

**机器生成，不要手改。**

```bash
make projection        # 或 python -m beomcp.cli project
```

CI 跑 `make lint-projection`：重新生成后 `git diff --exit-code`，不一致即失败。
改了 `TBox.ttl` 或 `shapes.ttl` 忘重新生成，PR 会红。

它只包含查询侧需要的东西：类名、属性名、类型、基数、Core 约束。不含 OWL 公理。

---

## 7. abox.ttl

样例实例数据。`make seed` 加载进 Neo4j，`tests/e2e` 也读同一份——所以 demo 和测试不可能漂移。

```turtle
@prefix onto: <https://example.org/onto#> .
@prefix ex:   <https://example.org/id/> .

ex:contract001 a onto:Contract ;
    onto:hasAmount 1200000 ;
    onto:hasStatus "draft" ;
    onto:archived false ;
    onto:belongsToCustomer ex:customerA .
```

`rdf:type` → 实体类型，字面量 → 数据属性，URI 对象 → 关系。和 MCP 写入工具走同一套表示写入器。

---

## 8. 改完之后跑什么

按顺序：

```bash
make projection        # 1. 重新生成投影
make lint-projection   # 2. 确认投影与 TBox 一致（CI 同款）
make unit              # 3. 单元测试（含 OWL 2 RL 构造器黑名单、SHACL 判定）
make seed              # 4. 载入 ABox（需要 Neo4j 起着）
make e2e               # 5. 全部 CQ 比对 sample_result
```

一键：

```bash
make demo              # = up + seed + e2e
```

---

## 9. CI 门禁

| 门禁 | 拦什么 | 怎么跑 |
|---|---|---|
| `make lint-projection` | 投影与 TBox/shapes 脱节 | `git diff --exit-code` |
| OWL 2 RL 构造器检查 | 引入超出 RL 的公理 | `tests/unit/test_ontology_model.py` |
| SHACL 校验样例 ABox | 样例数据脏 | `tests/unit/test_validate.py` |
| `uses` ⊆ 投影 | 查询引用已删的类/属性 | `Catalog.load` 加载期抛错 |
| 全部 CQ 比对 `sample_result` | 本体变更打断查询 | `tests/e2e/test_demo.py` |

---

## 10. 把样例换成你的：完整走查

1. 改 `TBox.ttl`：换成你的类与属性，命名空间改成你的
2. 改 `shapes.ttl`：只写你真要强制的约束（那四类）
3. 改 `manifest.json`：`namespace` 改掉，`queries` 逐条重写
4. 改/增 `queries/*.cypher`：文件名对上 `name`
5. 改 `abox.ttl`：换成你的样例实例（或留空，只用 MCP 写入工具造数据）
6. `make projection && make lint-projection`
7. `make seed && make e2e`
8. CI 绿了再提交 `schema.projection.json`

---

## 11. 常见问题

**`Catalog.load` 抛 `references unknown terms`**
`manifest.json` 的 `uses` 里有投影里没有的类/属性名。跑 `make projection`，或改 `uses`。

**`FileNotFoundError: queries/xxx.cypher`**
manifest 的 `name` 和文件名对不上。

**CQ 回归红了，但查询本身没错**
`sample_result` 是写死的期望值。你改了数据或查询，就要同步改它。

**改了 TBox 但 `make lint-projection` 红**
正常——它就是拦这个的。跑 `make projection`，把 `schema.projection.json` 一起提交。

**`build_server` 报 "query and write accounts are the same account"**
读写账号相同，安全模型不成立。给查询账号一个只有 `MATCH` 权限的账户，或开发期设 `BEOMCP_ALLOW_SHARED_ACCOUNT=1`。
