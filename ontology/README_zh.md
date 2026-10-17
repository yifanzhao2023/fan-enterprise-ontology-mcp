# ontology/ —— 怎么换成你自己的本体

> **样例，不是你的本体。** 这里放的是**企业合同管理**，一个可跑通的样例域。
> 目的是让 server 开箱能跑、CI 有东西可回归；真实使用请整份换成你自己的数据。
> 换掉它们**不需要改任何 Python 代码**。

**支持场景**：术语消歧、命名查询、多跳组织、合法写入、非法写入、安全边界等众多常见场景
（见[第 12 节](#12-场景示例（few-shot）)与[第 13 节](#13-推理场景（跑过-make-infer-之后）)）。

| | |
|---|---|
| 样例域 | 企业合同管理 |
| 类 / 属性 | 7 类、10 属性 |
| 命名查询 | 13 条（CQ-01 … CQ-13） |
| Jena 规则 | 2 条（部门负责人工单、重点合同） |
| 换成本体 | 本文件第 10 节 / [`README.md`](README.md)（English） |

---

## 1. 七个文件

| 文件 | 谁写 | 运行时谁读 | 说明 |
|---|---|---|---|
| `TBox.ttl` | 人 | **没有任何运行时组件读它**。只有 `onto project` 和离线推理 `onto infer` | 权威本体：类、属性、domain/range |
| `shapes.ttl` | 人 | 写入路径 `require_valid` | SHACL 约束 |
| `queries/*.cypher` | 人 | `Catalog.run`（工具 `run_query`） | 命名能力问题 |
| `manifest.json` | 人 | `Catalog.load` | 查询的参数、返回列、样例、`uses` |
| `schema.projection.json` | **机器生成** | `SchemaCatalog`（工具 `search_schema` / `get_schema` / `get_constraints`） | 查询侧扁平视图 |
| `abox.ttl` | 人 | `onto seed` / `make seed` | 样例实例数据 |
| `rules/*.rules` | 人 | `rules-runner`（JVM） | Jena 规则；带算术的业务条件写在这里 |

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
它属于 Jena Rules（`rules/*.rules`），不要试图用公理表达。

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
make projection        # 或 python -m feomcp.cli project
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
读写账号相同，安全模型不成立。给查询账号一个只有 `MATCH` 权限的账户，或开发期设 `FEOMCP_ALLOW_SHARED_ACCOUNT=1`。

---

## 12. 场景示例（few-shot）

样例域是企业合同管理。下面六个场景是**本体真能解、普通表格 + SQL 解不了**的问题，
可以直接当 agent 的示例用。数据来自 `abox.ttl`，答案都能复现验证。

### 场景 1 —— 术语消歧与字段发现

**业务问题**：客户A 名下还没归档的合同，列出金额和状态。

**不查本体会怎样**：agent 会猜字段名。「归档」在本体里是 `archived`、「状态」是 `hasStatus`、
「金额」是 `hasAmount`；更麻烦的是**归档和状态是两个独立字段**——contract003 状态是
`closed` 且 `archived: true`，猜混了要么查不出，要么把已归档的当成未归档。

**工具路径**：`search_schema` → `query_raw`

**示例**：
```
Q: 客户A 名下还没归档的合同，金额和状态列出来

工具: search_schema("归档")   → archived (property, 标签「已归档」)
工具: search_schema("客户")   → Customer (class)、belongsToCustomer (relation)
工具: query_raw("MATCH (c:Customer {uri:$c})<-[:belongsToCustomer]-(k:Contract)
                 WHERE k.archived = false
                 RETURN k.uri AS contract_iri, k.hasAmount AS amount, k.hasStatus AS status
                 ORDER BY k.uri", {"c": "https://example.org/id/customerA"})

A: contract001  1,200,000  draft
   contract002    800,000  draft
   （contract003 属于客户A，但 archived=true，被排除）
```

**本体在这一步起作用**：`rdfs:label` 把中文业务词映射到属性短名；`archived` 与 `hasStatus`
分开建模，是本体的决定，不是数据库的偶然。

---

### 场景 2 —— 命名查询覆盖标准问题

**业务问题**：销售部的负责人是谁。

**不查本体会怎样**：agent 现编 Cypher，很可能把 `hasHead` 和 `hasOwner` 搞混——前者是
**部门**负责人，后者是**工单**负责人。查出张三，但问的是李四。

**工具路径**：`list_queries` → `run_query`

**示例**：
```
Q: 销售部的负责人是谁

工具: list_queries()  → 看到 CQ-05 department_head「查指定部门的负责人」
工具: run_query("department_head", {"department_iri": "https://example.org/id/deptSales"})

A: employeeZhang
```

**本体在这一步起作用**：`hasHead` 的 domain 是 `Department`、range 是 `Employee`，语义写死在
TBox 里；命名查询把这个语义固化成一个可复用、可回归的问答。

---

### 场景 3 —— 多跳组织追溯

**业务问题**：张三负责的工单属于哪个部门？该部门还有哪些工单、各自谁负责？

**不查本体会怎样**：ID 在工单、部门、员工三处不一致，要多轮 join + 人工对齐；「谁负责」还
容易被理解成部门负责人（`hasHead`）而非工单负责人（`hasOwner`）。

**工具路径**：`search_schema` → `query_raw`（目录里没有这条组合）

**示例**：
```
Q: 张三负责的工单属于哪个部门？该部门还有哪些工单、各自谁负责

工具: search_schema("负责") → hasHead (relation)、hasOwner (relation)
工具: query_raw("MATCH (e:Employee {uri:$e})<-[:hasOwner]-(w:WorkOrder)
                 -[:belongsToDepartment]->(d:Department)<-[:belongsToDepartment]-(other:WorkOrder)
                 OPTIONAL MATCH (other)-[:hasOwner]->(o:Employee)
                 RETURN w.uri AS mine, d.uri AS dept, other.uri AS work_order_iri, o.uri AS owner_iri",
                {"e": "https://example.org/id/employeeZhang"})

A: wo001 属于 departmentSales
   该部门另有 wo002(赵六)、wo003(王五)

（注：Cypher 的一条路径里同一条关系不能走两次，所以 wo001 自己不会出现在「其他工单」里——
正好对上「还有哪些」的语义。）
```

**本体在这一步起作用**：`hasOwner` 与 `hasHead` 的 domain/range 让 agent 分得清两种「负责」；
三跳在关系库里要手工对 ID，在图里是一条路径。

---

### 场景 4 —— 约束要求的合法写入

**业务问题**：给客户C 新签一份 80 万的合同。

**不查本体会怎样**：只写合同实体，被拒。`ContractShape` 对 `belongsToCustomer` 要求
`minCount 1`——合同必须有归属客户。

**工具路径**：`get_constraints` → `upsert_entities`（实体与关系**同批**）

**示例**：
```
Q: 给客户C 新签一份 80 万的合同

工具: get_constraints(["Contract"])
     → belongsToCustomer  minCount 1 / class Customer
       hasAmount          datatype decimal / minCount 1 / maxCount 1
       hasStatus          datatype string  / minCount 1 / maxCount 1

工具: upsert_entities(
        entities=[{iri: ".../id/contract006", types: [".../onto#Contract"],
                   properties: {hasAmount: 800000, hasStatus: "draft", archived: false}}],
        relations=[{source_iri: ".../id/contract006",
                    predicate_iri: ".../onto#belongsToCustomer",
                    target_iri: ".../id/customerC"}])

A: {"applied": true, "written_iris": [".../id/contract006"]}
```

**本体在这一步起作用**：SHACL 的 `minCount` 把「合同必须有归属客户」从文档约定变成硬约束。
`upsert_entities` 的 `relations` 参数就是为了让一个批能同时表达实体与关系。

---

### 场景 5 —— 约束拒绝的非法写入

**业务问题**：同上，但只写合同，不写归属。

**工具路径**：`upsert_entities` → `VALIDATION_FAILED` → 补上关系 → 重提

**示例**：
```
工具: upsert_entities(entities=[{iri: ".../id/contract007", types: [".../onto#Contract"],
                                 properties: {hasAmount: 800000, hasStatus: "draft", archived: false}}])

A: {"code": "VALIDATION_FAILED",
    "message": "1 SHACL Core violation(s); first: belongsToCustomer — belongsToCustomer requires at least 1 value(s)",
    "hint": "fix the reported fields before resubmitting the batch",
    "retryable": false,
    "details": {"violations": [{"path": "belongsToCustomer", "kind": "minCount",
                                "focus_iri": ".../id/contract007", ...}]}}

（补上 relations 后重提 → applied: true）
```

**本体在这一步起作用**：错误是**字段级**的（`path` + `kind`），agent 能照着改，不是「校验失败」
四个字。注意 `retryable: false`——原样重试必然再失败，必须改请求。

---

### 场景 6 —— 安全边界

**业务问题**：把 contract001 的金额改成 50 万。

**不查本体会怎样**：agent 直接 `query_raw("MATCH (n) SET n.hasAmount = ...")` 改数据，绕过
校验、绕过审计、破坏原子性。

**工具路径**：`query_raw` 被拒 → 改用 `upsert_entities`

**示例**：
```
工具: query_raw("MATCH (n {uri:$u}) SET n.hasAmount = 500000 RETURN n", {"u": ".../id/contract001"})

A: {"code": "QUERY_REJECTED",
    "message": "write keyword SET is not allowed",
    "hint": "queries are read-only; use upsert_entities / upsert_relations to write",
    "retryable": false,
    "details": {"rule": "no-write", "keyword": "SET"}}

工具: upsert_entities(
        entities=[{iri: ".../id/contract001", types: [".../onto#Contract"],
                   properties: {hasAmount: 500000, hasStatus: "draft", archived: false}}],
        relations=[{source_iri: ".../id/contract001",
                    predicate_iri: ".../onto#belongsToCustomer",
                    target_iri: ".../id/customerA"}])

A: {"applied": true}

工具: run_query("contract_amount", {"contract_iri": ".../id/contract001"})
A: [{"amount": 500000}]     ← 回读验证，别只看 applied: true
```

> 上面这段是「执行后会发生什么」的完整过程。`query_raw` 被拒是**现在就能复现**的；
> 后半段的写入会真的把 contract001 的金额改成 500,000（当前是 1,200,000），
> 想复现就照做，`make seed` 可随时把样例数据还原。

**本体在这一步起作用**：写入只能走结构化工具，guard 是硬闸。`upsert_entities` 走的是
单事务 + SHACL 校验 + 审计，`query_raw` 一条都不占。

---

## 13. 推理场景（跑过 `make infer` 之后）

下面这些要离线推理（`make infer`）才有。先 `make seed` 再 `make infer`，然后照抄。

### 13.1 部门负责人工单

部门负责人默认也是该部门工单的负责人。规则 `belongsToDepartment . hasHead -> hasOwner`：

```
run_query("work_orders_by_department", {"department_iri": "https://example.org/id/departmentSales"})
```

推出来两条**派生边**（`r.inferred = true`）：

| 工单 | 派生负责人 | 说明 |
|---|---|---|
| wo002 | employeeZhang | 原负责人是赵六，张三（部门负责人）是额外负责人 |
| wo003 | employeeZhang | 原负责人是王五 |

wo001、wo004 **没有**派生边——推出来的结论和 ABox 已有的边重合，被吸收了。所以
`MATCH ()-[r]->() WHERE r.inferred = true` 只有两条，不是四条。

> 注意 `work_orders_by_department` 现在每个工单可能返回多行（一个负责人一行）。
> 这是设计使然：查询结果不隐藏派生事实，判断答案依赖不依赖派生是调用方的事。

### 13.2 重点合同

金额超过 100 万。**带算术的类定义，OWL 公理表达不了**，只能写在 `rules/business.rules` 里。
是否归档是查询侧的当前状态，不进类定义——归档了也还是重点合同。

```
run_query("contracts_by_key_and_archive_state", {"archived": false})
```

```
contract004  1500000.0
contract001  1200000.0
```

`contract005`（200 万）也是重点合同，但 `archived: true`，被这个过滤条件挡掉了。
查全部重点合同就把 `archived` 传 `null`。

### 13.3 合规审计：谁在什么时候改了这份合同

**这不是推理问题**，是要查询得到的变更日志。每次写入的每个字段都会落一条 `:ChangeEvent`：

```
run_query("contract_change_history", {"contract_iri": "https://example.org/id/contract001"})
```

```
at=2026-10-16T14:32:13+00:00  actor=system  predicate=hasEndDate
old_value=null  new_value=2027-03-31 00:00:00
```

- 值没变就不记——重复写同一份合同不等于有人改过它
- 变更日志是只追加的运维历史，不是 ABox：推理重建不会碰它，也不会进 `abox.ttl`
- 时序语义就是 `xsd:dateTime`。没建 OWL-Time 模块——当前的问题只要「谁/什么时候/改了哪个字段」，
  建时序本体是给还没有的问题付建模成本。等真问出「两份合同的时间区间有没有重叠」再建。

### 13.4 合同到期了吗

```
run_query("contract_expiry_status",
          {"contract_iri": "https://example.org/id/contract001", "as_of": "2026-10-16"})
```

```
contract001  end_date=2027-03-31  status=active
contract004  end_date=2025-01-01  status=expired
```

**这是查询，不是派生标签。** 理由是可复现性：`onto infer` 是纯函数，重放同样的输入必须得到同样的输出；
读墙钟的规则做不到。真要 `:Expired` 标签，把 `as_of` 作为记录进作业指纹的输入传下去即可。

### 13.5 派生事实有多新

```
get_inference_status()
```

`stale` 在任何写入之后就是 `true`，直到下一次 `onto infer`。**陈旧是常态，不是故障。**
不要在 `stale: true` 的时候用派生事实答题而不说明。

---

上面四条以前是「v1 跑不了」的缺口。现在都能跑了；到期那条按上面说的走查询，不走派生。
