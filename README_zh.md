# fan-enterprise-ontology-mcp

**让企业本体更简单** · Making enterprise ontology simpler.

**把企业本体当作 Agent 语义记忆层的 MCP server。**

Agent 读取本体 Schema、查询命名能力问题或裸 Cypher、写入经过校验的实例数据、消费离线推理出的派生事实——全部以 MCP 工具形式暴露。

**语言：** [English](README.md) · [简体中文](README_zh.md)

> **状态：v1 + Plan 3 已完成。** 本体存储与查询、Agent 集成层、离线推理、SHACL 校验，以及映射驱动的批量导入与导入血缘审计，均已实现并通过测试。知识抽取仍列入规划，尚未实现。

- **协议：** MCP（stdio）
- **语言：** Python（MCP SDK）+ JVM 推理 CLI
- **存储：** Neo4j
- **交换格式：** Turtle（RDF）；工具返回使用 JSON-LD 对齐的 `@context` 作为业务契约层，不作为 RDF 交换格式
- **推理：** Jena rules（OWL 2 RL + 业务规则），离线执行
- **校验：** pySHACL（写时 SHACL Core，离线全量 SHACL）
- **导入：** mapping 工件进 Git + `onto import`（CLI，批原子），每次尝试追加一条 `:IngestJob` 血缘
- **许可证：** [Apache License 2.0](LICENSE)

---

## 索引

- [1. 概述](#1-概述)
- [2. 适用用户与场景](#2-适用用户与场景)
- [3. 主要架构](#3-主要架构)
- [4. 主要流程](#4-主要流程)
  - [链路 A —— 写入 ABox](#链路-a--agent-写入-abox)
  - [链路 B —— Agent 查询](#链路-b--agent-查询)
  - [链路 C —— 离线推理](#链路-c--离线推理（onto-infer）)
  - [链路 D —— 批量导入](#链路-d--批量导入onto-import)
- [5. 主要功能](#5-主要功能)
- [6. 工具](#6-工具)
- [7. Resources 与 Prompts](#7-resources-与-prompts)
- [8. 安装](#8-安装)
- [9. 配置](#9-配置)
- [10. Quick Start](#10-quick-start)
- [11. 安全模型](#11-安全模型)
- [12. 限制](#12-限制)
- [13. 路线图](#13-路线图)
- [License](#license)

---

## 1. 概述

这里的企业本体是结构化的业务语义模型——类、关系、属性、约束、公理——外加符合该模型的实例事实。它相对普通属性图或文本索引的独特价值在**公理与约束**：模型能声明并推出「部门负责人必然是该部门工单的负责人」这样的业务必然性。

本项目**不做建模环境**。它做的是：让人工编写（进 Git）的本体在运行时可被 Agent 使用，并通过校验与离线推理保持实例库与本体一致。

**TBox 在 Git，ABox 在 Neo4j。** 权威本体是带版本的 Turtle/JSON-LD。Neo4j 存实例外加两类派生工件。Agent 读到的 schema 是 CI 从 TBox 生成的扁平投影。

---

## 2. 适用用户与场景

> 特别适用于国央企、事业单位、高校等对可审计性、标准语义、变更可追溯性要求高的环境。

**适用用户**

- **知识工程师 / 本体工程师**：能读写 Turtle、SHACL shapes、Jena rules。TBox 在 Git，由人负责。
- **Agent 应用开发者**：在 Claude Code、Codex 或自研 MCP 客户端上，需要结构化业务事实而非文本检索。
- **数据治理 / 主数据团队**：需要可审计、带约束校验的业务事实，且违规要能回报。
- **平台 / 基础架构工程师**：已有 Neo4j 运维能力，能跑一个 JVM 侧批处理作业。

**适用场景**

- Agent 要回答基于实体与关系的业务问题——合同、客户、工单、组织架构——而不是文档文本上的问题。
- 业务名词有歧义，需要本体钉死（外部客户 vs. 内部客户）。
- 查询带真实业务谓词：金额 > 100 万、未归档、由某人负责、属于某部门。
- 事实必须可校验、可审计，约束违规要能暴露而不是静默存进去。
- 想要本体作为 Agent 的语义记忆层，而不是搜索索引。

**不适用场景**

- 非结构化文档问答——用向量库 / RAG。
- 只要实体关系存储、不要公理与约束——属性图加一层轻 schema 更便宜。
- 需要从文本做知识抽取或多源实体消歧——已规划。结构化源系统的术语对齐与批量导入已在 Plan 3 交付。
- 需要请求时在线 OWL DL 推理——本方案推理是离线批处理。
- 没有 JVM 且无法安装 Java——推理链要跑 JVM。
- 需要图形化本体建模——用 Protégé。本项目刻意不做建模。

---

## 3. 主要架构

![架构 —— TBox 在 Git，ABox 在 Neo4j，推理离线](misc/diagrams/architecture.gif)

**命名约定**：`onto infer` 是用户跑的 Python CLI（导出 → 调 JVM → 校验 → 回写四步编排）；`rules-runner` 是 JVM 纯函数（`Turtle in → Turtle out`）。换推理引擎只换 `rules-runner`，`onto infer` 不动。

**交付物**


| #   | 工件                                             | 职责                                                                 |
| --- | ---------------------------------------------- | ------------------------------------------------------------------ |
| 1   | MCP server（Python）                             | schema 供给、查询目录、裸 Cypher、受控写入、写时 SHACL Core                         |
| 2   | `onto infer`（Python CLI） + `rules-runner`（JVM） | 编排导出 → 调 JVM 推理 → 校验 → 回写。**JVM 侧不持 Neo4j 驱动、不持 DB 凭据、无写权限**，只读写文件 |
| 3   | `ontology/`（Git）                               | TBox、shapes、rules、查询目录、schema 投影（生成物，CI 门禁）                        |
| 4   | Skills + MCP prompts                           | Claude Code skill 包调用习惯；Codex 读 MCP prompts/resources              |
| 5   | `onto import`（Python CLI）                      | adapter → mapping / IRI / 强转 → 与工具相同的表示写入器和写时 SHACL；每次尝试追加一条 `:IngestJob` |


> **`ontology/` 里是可跑通的样例，不是你的本体。** 这些文件建的是一个样例域——企业合同管理——
> 目的是让 server 开箱能跑、CI 有东西可回归。请换成你自己的 `TBox.ttl`、`shapes.ttl`、
> `queries/*.cypher` 与 `manifest.json`，Python 代码一行都不用改。改完 TBox 记得跑
> `onto project` 重新生成 `schema.projection.json`（否则 CI 拦），新查询必须登记进
> `manifest.json`——`Catalog.load` 会拿每条的 `uses` 去投影里核对，对不上直接拒启动。

### 数据分层


| 位置                                       | 内容                                                       | 谁写                                            |
| ---------------------------------------- | -------------------------------------------------------- | --------------------------------------------- |
| Git `TBox.ttl` / `shapes.ttl` / `rules/` | 权威本体与规则                                                  | 人（Protégé / LLM + 人审）                         |
| Git `schema.projection.json`             | **查询侧扁平投影**——类、属性、关系、基数、Core 约束。**不含全部 OWL 公理，且永不进推理链路** | CI 跑 `onto project`；`git diff --exit-code` 门禁 |
| Git `queries/*.cypher` + `manifest.json` | 查询目录 = 能力问题（CQ）集合                                        | 人 + CI 门禁                                     |
| Git `mapping/*.yaml` + `irules.yaml` + `glossary.md` | 术语映射、IRI 规则与业务术语表——导入的数据契约                     | 人，随 TBox 一起评审                                |
| Neo4j ABox                               | 实例                                                       | MCP 受控 ingest（Agent 可写）                       |
| Neo4j `:INFERRED` / `{inferred:true}`    | 派生类型与派生关系                                                | `onto infer` 批作业                              |
| Neo4j `:ConstraintViolation`             | 离线 SHACL 违规，关联到违规实例                                      | 同一作业，清空重建                                     |
| Neo4j `:IngestJob`                       | 只追加的导入血缘：源系统、快照时间、触发人、计数、错误明细                    | `onto import`，每次尝试一条节点                        |


**Neo4j 里有三类语义节点**，另有只追加的审计行（`:ChangeEvent`、`:InferenceJob`、`:IngestJob`）。没有 TBox 节点，没有 schema 节点。

### 建模契约

> v1 TBox **锁定 Jena OWL 2 RL 子集**，禁止引入超出 RL 表达能力的 OWL DL 构造；**算术业务条件一律写 Jena Rules，不得写进 TBox 公理。**

该契约在 CI 中有机器检查（见 [CI 门禁](ontology/README_zh.md#9-ci-门禁)）。它同时永久关掉了推理器选型问题：RL 范围内 Jena rules 是完备的——v1 不引 ELK、不引 HermiT。

---

## 4. 主要流程

### 链路 A —— Agent 写入 ABox

![链路 A —— Agent 写入 ABox](misc/diagrams/chain-a.gif)

1. Agent 调 `upsert_entities` / `upsert_relations`，**结构化参数，不是 Cypher**。
2. `validate_core` 只跑 SHACL Core 子集：`sh:minCount`、`sh:maxCount`、`sh:datatype`、`sh:class`。筛选 `sh:targetClass` 命中本次被写类型的 node shape，只在受影响子图上跑，同步执行。
3. 表示写入器分配/复用 IRI（`uri` 属性是实体唯一键）、把属性映射到 datatype 属性 vs. 对象属性、标签对齐 `rdf:type`。
4. 返回 `WriteReceipt`：写入 IRI、跳过项、校验摘要。

两条硬约束：

- **批原子性由 Neo4j 单事务保证**。整批提交或整批回滚——数据库层面，不靠应用层回滚。API 侧限制单批上限，超限直接拒并提示分批调用；分块写入会破坏批原子，故不提供。
- **写入不更新 `:INFERRED`**。派生事实在下次 `onto infer` 前保持陈旧（见[陈旧性语义](#陈旧性语义)）。

SHACL-AF / SPARQL 约束**永不进写时路径**——太慢，且写时只回答「这条记录能不能进」。

### 链路 B —— Agent 查询

![链路 B —— Agent 查询](misc/diagrams/chain-b.gif)

返回包固定带 `@context`、`truncated`、`row_count`。

**`@context` 是业务契约层，不是严格标准 JSON-LD。** 它的存在意义是让 Agent 理解实体语义，不作为 RDF 交换格式。真 JSON-LD / Turtle 只走文件链路（`abox.ttl`、`inferred.ttl`、`violations.ttl`）。

错误结构化、可操作：


| code                  | `hint` 给什么                                |
| --------------------- | ----------------------------------------- |
| `SCHEMA_NOT_FOUND`    | 跑 `search_schema`；给相近候选                   |
| `QUERY_SYNTAX`        | 回贴 Neo4j 原文 + 「用 `get_schema()` 核对标签与关系名」 |
| `QUERY_REJECTED`      | 说明拦的哪条 guard 规则                           |
| `QUERY_LIMIT`         | 加 `LIMIT`、收窄过滤、用索引可查条件                    |
| `VALIDATION_FAILED`   | **字段级**违规：shape / path / message / focus  |
| `WRITE_REJECTED`      | 分批调用                                      |
| `BACKEND_UNAVAILABLE` | 稍后重试，不要重试风暴                               |


### 链路 C —— 离线推理（`onto infer`）

四步。**JVM 只做第二步。**

![链路 C —— 离线推理](misc/diagrams/chain-c.gif)

顺序不可协商：校验必须在推理之后（否则推出来的事实从没被校验）；回写必须最后（否则 Agent 读到半成品）。

### 文件契约


| 文件               | 内容                                                   | 产出方     |
| ---------------- | ---------------------------------------------------- | ------- |
| `abox.ttl`       | ABox 全量 RDF                                          | `materialize_rdf`（rdflib） |
| `inferred.ttl`   | **只含派生三元组，不含原始 ABox**                                | JVM     |
| `violations.ttl` | SHACL 违规：focus node IRI / shape IRI / path / message | pySHACL |


`inferred.ttl` 只含增量是强制的。没有它 `replace_derived` 无法区分派生与原始，重建时会把真事实一起删掉。JVM 的输出语义是「规则推出、且输入图中不存在的三元组」。

### JVM 不直写 Neo4j

> `rules-runner` 的 JVM 进程**不持 Neo4j 驱动、不持 DB 凭据、无写权限**。它只读写文件。Python 编排层（`onto infer`）独占所有写入。

实际收益：权限最小化（监管环境的安全评审更好过）、JVM 侧可脱离数据库单测、推理可复现（同输入必同输出）、换推理引擎只换第二步。

### 可复现性

每次推理作业的输入都做指纹并记在单例元数据节点上：`TBox.ttl` / `rules/` / `shapes.ttl` 的 git commit SHA，`abox.ttl` / `inferred.ttl` / `violations.ttl` / `shapes.ttl` 的内容哈希，Neo4j 版本，以及各计数。

- `onto infer` 从 git 检出读 TBox / rules / shapes
- 工作树不干净则拒绝运行；`--allow-dirty` 可放行但会在元数据标注
- 可复现性定义：**重放同一组三个输入文件，必得同一输出**

用新 TBox 推旧 ABox 是本体演化的常态，不是 bug。真正的风险是不留痕；指纹化才是让每次运行可审计的手段。

### 陈旧性语义

写入后到下次 `onto infer` 之间，`:INFERRED` 是陈旧的。三处显式处理：

- 查询结果**不隐藏**陈旧。Agent 自己判断答案是否依赖派生事实。
- `replace_derived` 顺手写**单例元数据节点**（`job_id`、`finished_at`、输入计数、派生计数、违规计数），Agent 可查派生层有多新。一个节点——不是第四类语义节点。
- 触发频率是运维决策。文档给建议值（每小时，或写入后 N 分钟 debounce），不写死进代码。

### 链路 D —— 批量导入（`onto import`）

真实企业的数据在 ERP / MES / WMS 系统里，不在 Turtle 里。`onto import` 让一整批数据
走与 Agent 工具**相同的表示写入器和写时 SHACL**，因此导入的事实与工具写出的事实无法漂移。

```
外部系统
   │  SourceAdapter（用户代码：只拉取 + 拍平）
   ▼
Record(record_type, {field: value}) + SourceSnapshot(source_id, snapshot_time)
   │
   ▼  onto import --source <id> --adapter <module>
mapping/<source>.yaml   → 类、属性、关系、代码表
mapping/irules.yaml     → 实体 IRI，确定性，带 {source} 段
datatype 强转           → string / integer / decimal / boolean / dateTime / date
   │
   ▼
WriteBatch → FactStore.ingest
   ├─ 表示写入器 + 写时 SHACL Core（与工具同一条路径）
   ├─ :ChangeEvent 字段级变更，actor = "ingest:<job_id>"
   └─ :IngestJob(ok) 同事务写入
```

**adapter 边界刻意做得很笨。** adapter 只负责拉取和拍平；类型、谓词、强转、IRI 生成、
校验全在桥里——正因如此 mapping 才能进 Git、才能回放。

**两类失败：**

- **记录级** —— 缺主键、未知 record_type、代码表 miss、强转失败。该记录跳过，计入
  `failed`，原因写进 `error_details`。一行脏数据不会卡死整个 ERP 抽取。
- **批级** —— SHACL 拒绝、超批量上限、adapter 或驱动错误。什么都不写，只追加一条
  `:IngestJob(failed)` 说明原因。

不变量：成功的导入一定同时留下 ABox 写入与 `:IngestJob(ok)`（同一事务）；失败的导入
ABox 不动，只留失败作业行。

**血缘。** `:IngestJob` 只追加，每次尝试一条：

| 字段 | 含义 |
|---|---|
| `job_id` | 尝试身份（唯一，永不复用） |
| `input_hash` | 内容身份：源 + 快照时间 + mapping + IRI 规则的摘要 |
| `source_id` / `source_version` / `snapshot_time` | 数据从哪来 |
| `actor` / `imported_at` | 谁在什么时候触发 |
| `status` / `total` / `succeeded` / `failed` / `error_details` | 结果如何 |
| `mapping_ref` | 由哪个 mapping 版本驱动 |

同一批写入产生的字段级变更带 `actor = "ingest:<job_id>"`，「这次导入改了什么」一条查询即可。
批原子性与链路 A 相同：一次导入 = 一个事务；超限的抽取直接拒绝，不切块。

### 已知风险

`materialize_rdf` 在大实例集导出时的内存占用。导出是把整张图读进一个 rdflib 模型；真实实例集一旦显示吃紧，退路是按标签分区导出多个文件喂 JVM。契约目前假定单文件 `abox.ttl`。

---

## 5. 主要功能

### Schema 供给（控制 token 成本）

- `search_schema` / `get_schema` / `get_constraints` 读扁平投影文件——零数据库往返
- 结果形状紧凑：类名、关系名、基数、Core 约束
- 完整投影只作为 resource 供人查阅，永不进 prompt

### 查询目录 = 能力问题（CQ）

命名查询以参数化 Cypher 存在 Git，带 manifest。同一工件两用：

- **CI 回归** —— 每个 PR 跑目录、比对预期结果
- **Agent 运行时** —— `list_queries()` / `run_query(name, params)` 覆盖高频意图

裸 Cypher（`query_raw`）留给 ad-hoc 组合查询兜底。

### 受控写入

Agent 通过结构化参数写实例。没有 Cypher 写入路径，写侧注入面从根上不存在。写时 SHACL Core 校验原子地拒绝不合规批次。

### 离线推理 + 派生事实隔离

Jena rules 产出派生类型与派生关系，物化进 Neo4j 并打显式标记，每次作业整体重建，绝不混进 ABox。

### 术语对齐（Plan 3）

mapping 是数据契约，不是代码：`ontology/mapping/<source>.yaml` 把源列映射到属性、
源值映射到类、代码表映射到枚举个体。它进 Git、像代码一样评审，`onto lint-mapping`
会拒绝引用投影里已不存在的术语。配套的 `glossary.md` 用自然语言给业务评审同一份对齐。

### 批量导入桥（Plan 3）

`onto import` 把 adapter 记录经 mapping、确定性 IRI 规则和 datatype 强转变成
`WriteBatch`。它复用 MCP 写入路径的表示写入器与写时 SHACL，导入数据与工具写出的数据
不可能漂移。源系统是 adapter——只拉取、只拍平的用户代码——接 ERP / MES / WMS 不需要改核心。

### 导入血缘审计（Plan 3）

每次导入尝试追加一条 `:IngestJob`，记录源系统、快照时间、触发人、计数与错误明细。
成功和失败都记，且节点永不覆盖，重试因此留有历史。字段级 `:ChangeEvent` 通过 `actor`
回链到作业。

### 违规回报

离线 SHACL 违规落成 `:ConstraintViolation` 节点并关联到违规实例，Agent 能答「这条记录哪里不对」，而不只是「有没有问题」。

---

## 6. 工具

十个工具，五组。


| #   | 工具                                     | 作用                         | 来源                     |
| --- | -------------------------------------- | -------------------------- | ---------------------- |
| 1   | `search_schema(keywords, limit=20)`    | 关键词检索类 / 属性 / 关系           | 投影文件                   |
| 2   | `get_schema(entity_types[])`           | 指定类型完整 schema：属性、基数、出入边关系  | 投影文件                   |
| 3   | `get_constraints(entity_types[])`      | SHACL **Core 子集**约束，供写入前预判 | 投影文件                   |
| 4   | `list_queries(domain?)`                | 列命名查询（= CQ），带自然语言描述与参数     | manifest               |
| 5   | `run_query(name, params)`              | 跑命名查询                      | QueryEngine            |
| 6   | `query_raw(cypher, params)`            | 裸 Cypher 兜底（只读 + guard）    | QueryEngine            |
| 7   | `upsert_entities(entities[])`          | 写实例（IRI 复用、属性、类型）          | validate → FactStore   |
| 8   | `upsert_relations(relations[])`        | 写关系                        | validate → FactStore   |
| 9   | `get_inference_status()`               | 派生事实新鲜度                    | 单例元数据节点                |
| 10  | `list_violations(focus_iri?, job_id?)` | 查 SHACL 违规                 | `:ConstraintViolation` |


**没有 `load_ontology` 工具。** TBox 走 Git 加载，不走 MCP。这是「TBox 权威源在 Git」的直接后果——少一整组工具，也少一处攻击面。


**也没有导入工具。** 批量导入是 CLI 上的 `onto import`，Agent 无法误触一次大写入。
### 工具契约

- 工具名、参数名、返回形状**不随后端变**
- 新增只加字段不改字段；废弃标 `deprecated: true` 保留一个版本
- Neo4j 换 GraphDB，**Agent 侧零改动**

### 必须写明的边界

**`get_constraints` 只返回 Core 子集**（`minCount` / `maxCount` / `datatype` / `class`），供写入前预判。返回里带 `scope: "core"`。**通过写时校验只保证 Core 合规**——SHACL-AF / SPARQL 约束的违规要到下次离线全量校验才暴露，不在写入时。

**`list_violations` 默认返回最近一次*成功*作业的违规**（元数据节点的 `last_ok_job_id`），而不是最近一次尝试——失败的尝试没写违规，图里的行来自上次成功作业。v1 派生层是整体重建，图上只有最近一批；跨作业对比需要历史保留，v1 没做。

---

## 7. Resources 与 Prompts

### Resources

```
ontology://catalog    查询目录总览（轻）
ontology://playbook   「怎么查这个本体」——决策树 + 错误重试策略
ontology://shapes     shapes.ttl 原文（人工查阅）
ontology://tbox       TBox.ttl 原文（人工查阅）
ontology://schema     完整投影（大，仅人工查阅，永不进 prompt）
```

### Prompts

- `query-playbook` —— 读 schema → 选命名查询 → 裸 Cypher 兜底 → 错误重试
- `write-playbook` —— 写入结构、校验失败怎么改、派生事实何时更新

### Claude Code 与 Codex 分工


| 载体                 | Claude Code | Codex  | 内容              |
| ------------------ | ----------- | ------ | --------------- |
| MCP tools          | 用           | 用      | 能力本身            |
| MCP resources      | 用           | 看实现    | playbook、schema |
| MCP prompts        | 用           | 看实现    | 两个 playbook     |
| Claude Code skills | 用           | **不用** | 调用习惯包装          |


**知识落在 MCP resource 与 prompt**（至少 `ontology://playbook`）。skill 只是薄壳——**不要在 skill 里复述 playbook**，否则两边必然漂移。否则 Codex 拿到一堆裸工具，没人告诉它「先 `search_schema`，再 `list_queries`，最后才 `query_raw`」。

skill（`skills/feomcp/SKILL.md`）承载触发条件、操作顺序，以及 Claude Code 特有、不可跨客户端的那些习惯。安装：

```bash
mkdir -p .claude/skills && cp -r skills/feomcp .claude/skills/   # 本项目
cp -r skills/feomcp ~/.claude/skills/                            # 所有项目
```

---

## 8. 安装

### 前置条件

- Python 3.12+
- Docker + Docker Compose（Neo4j 与测试套件）
- Neo4j 5.x —— 开发用 Community 足够；**要做读写账号分离必须 Enterprise**，因为
  RBAC（`CREATE ROLE` / `GRANT`）是 Enterprise 功能

### 安装

```bash
git clone https://github.com/yifanzhao2023/fan-enterprise-ontology-mcp.git
cd fan-enterprise-ontology-mcp
python -m venv .venv
.venv/bin/pip install -e ".[dev]"          # Windows: .venv\Scripts\pip install -e ".[dev]"
```

### 启动 Neo4j

```bash
docker compose up -d --wait
```

起 Neo4j 5.26 Community，`bolt://localhost:7687`（`neo4j` / `password`）。
**刻意不装 APOC**：guard 本来就拒一切 `apoc.*`，插件不装等于库层也跑不了，多一道防线。

### 生成 schema 投影

```bash
make projection
```

`schema.projection.json` 由 `TBox.ttl` + `shapes.ttl` 编译而来。**要一起提交**——过期 CI 会红。

> **`ontology/` 里放的是样例，不是你的本体。**
> 当前是一个可跑通的样例域——**企业合同管理**——目的是让 server 开箱能跑、CI 有东西可回归。
> 它刻意模拟了 7 个常见场景：**术语消歧、命名查询、多跳组织、合法写入、非法写入、安全边界、外部系统批量导入**。
> 真实使用时请整份替换成你自己的数据。怎么换见 [`ontology/README_zh.md`](ontology/README_zh.md)。

### 载入样例数据（可选）

```bash
make seed
```

读 `ontology/abox.ttl`，走 MCP 写入工具同一套表示写入器写进 Neo4j。要把样例本体换成你自己的，
看 [`ontology/README_zh.md`](ontology/README_zh.md)。

### 构建派生层

派生事实（`:INFERRED`）由离线批作业产生，**任何工具调用都不会触发**：

```bash
make infer
```

它导出 ABox，在一个没有数据库访问权限的 JVM 里跑 `ontology/rules/*.rules`，
用 SHACL 校验合并图，然后单事务整体替换派生层。用派生事实答题之前先看
`get_inference_status()`——`stale: true` 表示上次跑完之后又有写入。
需要它的场景见 [`ontology/README_zh.md`](ontology/README_zh.md) 第 13 节。

### 从外部系统导入（可选）

源系统是你在包外自己写的 adapter；仓库自带一个读 Odoo 快照的参考 adapter：

```bash
make lint-mapping                      # 全部 mapping 对照 schema 投影
.venv/bin/python -m feomcp.cli import \
  --source odoo --adapter examples.odoo_adapter --actor alice
```

整批必须放得进 `FEOMCP_MAX_BATCH`；超限直接拒绝，不切块。
`examples/odoo_records.json` 是快照 fixture（3 个客户、2 张订单），演示不需要真 Odoo。
生产部署把读 JSON 换成 Odoo 的 XML-RPC `execute_kw` 或 JSON-2 API，adapter 的两个方法不变。

### 注册进 coding agent

server 走 MCP stdio。命令是 venv 里的 console script——**用绝对路径**，agent 的环境不会激活你的 venv。

**Claude Code** —— 项目根目录 `.mcp.json`：

```json
{
  "mcpServers": {
    "feomcp": {
      "command": "/绝对路径/.venv/bin/feomcp",
      "args": [],
      "env": {
        "FEOMCP_ONTOLOGY_DIR": "/绝对路径/ontology",
        "FEOMCP_NEO4J_URI": "bolt://localhost:7687",
        "FEOMCP_NEO4J_USER": "reader",
        "FEOMCP_NEO4J_PASSWORD": "…",
        "FEOMCP_NEO4J_WRITE_USER": "writer",
        "FEOMCP_NEO4J_WRITE_PASSWORD": "…"
      }
    }
  }
}
```

或 `claude mcp add feomcp -- /绝对路径/.venv/bin/feomcp`。

**Codex** —— `~/.codex/config.toml`：

```toml
[mcp_servers.feomcp]
command = "/绝对路径/.venv/bin/feomcp"

[mcp_servers.feomcp.env]
FEOMCP_ONTOLOGY_DIR = "/绝对路径/ontology"
FEOMCP_NEO4J_URI = "bolt://localhost:7687"
FEOMCP_NEO4J_USER = "reader"
FEOMCP_NEO4J_PASSWORD = "…"
```

**其它 MCP 客户端** —— stdio 传输，命令 `feomcp`（等价 `python -m feomcp.server`）。

> 查询账号与写入账号相同时 server 拒绝启动。给查询账号只授 `MATCH` 权限，或开发期设
> `FEOMCP_ALLOW_SHARED_ACCOUNT=1`。

### 验证

```bash
make demo
```

起 Neo4j、重生成投影、载入样例 ABox、跑全部能力问题比对预期结果。
## 9. 配置

环境变量。不引配置框架。

| 变量 | 默认 | 作用 |
|---|---|---|
| `FEOMCP_STORE` | `neo4j` | 存储适配器 |
| `FEOMCP_NEO4J_URI` | `bolt://localhost:7687` | Neo4j bolt URI |
| `FEOMCP_NEO4J_USER` | `neo4j` | 只读账号（查询工具） |
| `FEOMCP_NEO4J_PASSWORD` | *(空)* | |
| `FEOMCP_NEO4J_WRITE_USER` | *同 `FEOMCP_NEO4J_USER`* | 读写账号（写入工具） |
| `FEOMCP_NEO4J_WRITE_PASSWORD` | *同 `FEOMCP_NEO4J_PASSWORD`* | |
| `FEOMCP_ONTOLOGY_DIR` | `ontology/` | TBox、shapes、queries、manifest、投影、ABox |
| `FEOMCP_QUERY_TIMEOUT_MS` | `10000` | 单查询超时 |
| `FEOMCP_MAX_ROWS` | `50` | 结果行数上限（同时提前停止读取） |
| `FEOMCP_MAX_QUERY_LENGTH` | `8192` | 查询字符串长度上限 |
| `FEOMCP_MAX_BATCH` | `500` | 单批写入条数上限 |
| `FEOMCP_REDACT_FIELDS` | *(空)* | 读出时打码的字段名，逗号分隔 |
| `FEOMCP_ALLOW_SHARED_ACCOUNT` | `0` | `1` 允许查询与写入共用一个账号（仅开发） |
| `FEOMCP_BASE_IRI` | `https://example.org/id` | `onto import` 生成实体 IRI 的基址，覆盖 `irules.yaml` |

**权限分离靠数据库原生账号**，不靠代码分支。server 维两个 driver session，按工具组路由：
查询工具（1–6、9、10）走只读账号，写入工具（7、8）走读写账号。

两者指向同一账号时 `build_server` 拒绝启动，除非设了 `FEOMCP_ALLOW_SHARED_ACCOUNT=1`。

默认值对应 `docker compose` 那套栈。生产必须显式设置账号变量。

---

## 10. Quick Start

```bash
# 一键：重置卷、起 Neo4j、生成投影、灌样例 ABox、跑离线推理、执行全部 CQ
make demo
```

这一步端到端走完全程。想分步手动驱动：

```bash
docker compose up -d --wait   # 起 Neo4j
make seed                     # 投影 + 灌样例 ABox
make infer                    # 构建派生层
make e2e                      # 全部 CQ 比对 sample_result
```

再通过映射桥导入参考 Odoo 快照：

```bash
make lint-mapping
.venv/bin/python -m feomcp.cli import --source odoo --adapter examples.odoo_adapter
```

然后按[安装](#8-安装)把 agent 接上，问一个业务问题。推荐第一个：

> 名下金额超过 100 万的合同有哪些？

agent 应该先 `search_schema` / `list_queries`，再 `run_query("contracts_over_amount_by_customer", {...})`——而不是直接写裸 Cypher。

不想连 agent、只想看工具面：

```bash
.venv/bin/python -m feomcp.server      # 起 MCP stdio server
```

**这是样例域。** `ontology/` 建的是企业合同管理，纯粹为了让栈开箱能跑——换成你自己的本体，
见 [`ontology/README_zh.md`](ontology/README_zh.md)。

---

## 11. 安全模型

1. **账号分离** —— 查询工具只读，写入工具读写，JVM 零 DB 凭据。
2. **Cypher 注入** —— `query_raw` 参数一律参数化，禁止字符串拼接进查询。guard 静态拒：`CREATE` / `MERGE` / `DELETE` / `DETACH` / `SET` / `REMOVE` / `DROP` / `LOAD CSV` / `dbms.*` / 白名单外任何 `CALL`。**APOC 整体禁用**——`apoc.cypher.run` 之类执行的是任意 Cypher 字符串，是代码执行逃逸口，不是工具函数。
3. **白名单默认全拒**，且清单接近为空：只放内建只读函数。schema 内省走 `get_schema()`，没有动机把白名单放开。日后要加任何过程，进配置里的显式清单，默认关闭。
4. **资源上限** —— 查询超时、行数上限、查询长度上限、写入批次上限。
5. **审计日志** —— `tool` / 参数摘要 / `principal`（调用方标识：Agent 会话 ID 或服务账号）/ `timestamp` / `duration` / `row_count` / `ok`。**不记完整结果**。
6. **读出时动态脱敏** —— `FEOMCP_REDACT_FIELDS` 在查询返回时打码。存储层保留完整数据，写入不脱敏。脱敏覆盖违规详情（`list_violations` 的 message 常嵌字段值）。v1 是全局配置，不做按 principal 差异化。
   ```
    # ponytail: 只做字段脱敏 + 账号分离 + 审计；三元组级 RBAC/ABAC
    # 待合规需求明确后再做
   ```

    三元组级 RBAC 是大工程，其形态（行级 / 字段级 / 基于属性）必须由业务驱动。v1 把能落地的做实并留好接缝，不做「看起来像 RBAC」的假实现。
7. **SHACL 不是授权。** SHACL 管数据质量，不管访问控制。别指望它管权限。
8. **Agent 不能触发昂贵操作。** 没有 `rebuild_inferred` 工具。推理只由 CI 或 cron 触发。

### 失败处理


| 故障                  | 处理                                       |
| ------------------- | ---------------------------------------- |
| Neo4j 不可用           | `BACKEND_UNAVAILABLE`；不做自动重试风暴           |
| 投影缺失或不合法            | **拒启动**（启动时校验）                           |
| pySHACL 抛异常         | `VALIDATION_FAILED`，附截断的原始异常摘要           |
| JVM 不可用 / 超时 / 推理失败 | **不回写**；旧 `:INFERRED` 保留；作业元数据记失败        |
| `abox.ttl` 导出失败     | 同上，不回写                                   |
| 回写事务失败              | 单事务回滚，旧派生数据完好                            |
| git 工作树脏            | `onto infer` 拒跑；`--allow-dirty` 放行并标注元数据 |
| 导入记录 mapping / 强转失败 | 跳过该记录，计入 `failed`，原因进 `:IngestJob.error_details`；整批继续 |
| 导入批级失败（SHACL / 超限 / 驱动） | 什么都不写；`:IngestJob(failed)` 记录原因 |


**「失败则不回写」是核心不变量。** 旧派生数据既好过没有派生数据，也好过半成品。任何一步失败，图保持在上次成功作业的状态。

### 三个不变量

1. 写入要么整批成功，要么整批消失（Neo4j 单事务）。
2. `:INFERRED` / `:ConstraintViolation` 任意时刻都恰好是一个完整作业的结果——从不混杂。
3. JVM 进程从不持有数据库写权限。

---

## 12. 限制

- 无建模 UI、无业务域建模 DSL。用 Protégé。
- 术语对齐与批量导入覆盖结构化源系统。文本知识抽取与多源实体消歧已规划。
- 导入是快照式批处理；增量同步已规划（P1）。
- 推理离线批处理；派生事实在两次作业之间是陈旧的，这是设计而非缺陷。
- TBox 限定 OWL 2 RL 子集。
- 查询返回是 JSON-LD 对齐的 `@context`，不是严格 JSON-LD。RDF 交换只走文件链路。
- 字段脱敏是全局配置，不做按 principal 差异化。三元组级 RBAC 不在 v1。
- `onto infer` 需要 JVM。

---

## 13. 路线图

**Plan 3 —— 让真实数据落地 —— 已完成。** 术语对齐、数据导入桥、只追加的导入血缘审计
一起交付并通过测试。

下一步是 **P1 —— 从快照到活系统**：

| 项             | 说明                                                            |
| -------------- | --------------------------------------------------------------- |
| 增量同步       | 定时拉取增量，复用同一条导入桥；需要源系统提供更新时间 / 版本标识 |
| 本体版本演进   | TBox 变更的影响报告，随后执行 ABox 迁移                          |
| 知识抽取       | LLM 从文本出候选，本体 + SHACL 校验收口，人工复核                 |

其余需求——质量看板、三元组级权限、多源实体消歧、写时同步推理、违规历史、可观测性、
存储后端替换、OBDA、OWL 2 EL + ELK——记录在内部需求文档里，不随仓库发布。

## License

采用 [Apache License, Version 2.0](LICENSE) 授权。