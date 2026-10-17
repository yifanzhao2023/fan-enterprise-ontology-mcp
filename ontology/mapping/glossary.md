# 业务术语对照表（Odoo 参考源）

映射文件给程序读；这张表给人评审，也给 Agent playbook 用。

| 自然语言术语 | 本体概念 | Odoo 来源 | 说明 |
|---|---|---|---|
| 客户 | `onto:Customer` | `res.partner` → `partner_id`, `name` | 仅取客户主数据的最小集合 |
| 合同 | `onto:Contract` | `sale.order` → `order_id`, `amount_total`, `state`, `commitment_date` | 订单金额与状态直接映射；承诺交付日作为到期时间 |
| 合同归属客户 | `onto:belongsToCustomer` | `sale.order.partner_id` → Customer IRI | 外键按 `target_record` 规则生成目标 IRI |

未映射的源字段（如 `product.product`）一律丢弃：本体没有对应概念时不造属性。
