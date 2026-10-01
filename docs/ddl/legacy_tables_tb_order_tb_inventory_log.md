# 存量业务表结构归档：`tb_order` / `tb_inventory_log`（2026-10-01 实测导出）

> 目的：AGENTS §1 约束 5 记录「存量业务表 DDL 未入库（`dkd-parent/sql/` 只有 RuoYi 系统表、quartz、`tb_report.sql`），
> 涉及表结构工作时需先导出归档至 `docs/ddl/`」。本文件补上 **2-1（异常检测）** 与 **FIX-4 第 4 条索引**所需的
> 两张表结构。
>
> 方法：用 dkd-agent 的**只读账号**查 `information_schema.columns`（命令见文末）；**只读导出，未改任何结构**。

---

## 1. `tb_order`（订单，25 列）

| 列 | 类型 | 注释 |
| --- | --- | --- |
| id | bigint | 主键 |
| order_no | varchar(50) | 订单编号 |
| third_no | varchar(34) | 第三方平台单号 |
| inner_code | varchar(15) | 机器编号 |
| channel_code | varchar(10) | 货道编号 |
| sku_id | bigint | skuId |
| sku_name | varchar(20) | 商品名称 |
| class_id | int | 商品类别Id |
| **status** | int | **订单状态:0-待支付;1-支付完成;2-出货成功;3-出货失败;4-已取消** |
| amount | int | 支付金额 |
| price | int | 商品金额 |
| pay_type | varchar(10) | 支付类型，1支付宝 2微信 |
| **pay_status** | int | **支付状态，0-未支付;1-支付完成;2-退款中;3-退款完成** |
| bill | int | 合作商账单金额 |
| addr | varchar(200) | 点位地址 |
| region_id | bigint | 所属区域Id |
| region_name | varchar(50) | 区域名称 |
| business_type | int | 所属商圈 |
| partner_id | int | 合作商Id |
| open_id | varchar(200) | 跨站身份验证 |
| node_id | bigint | 点位Id |
| node_name | varchar(50) | 点位名称 |
| cancel_desc | varchar(200) | 取消原因 |
| create_time | timestamp | 创建时间 |

**要点（写 SQL 前必须知道）**：

- **没有 `del_flag`**：这是存量表，不属 RuoYi 审计四件套那批；任何“顺手加 `del_flag='0'`”都会 `ERROR 1054`。
- **没有 `update_time`**（只有 `create_time`）→ 无法用 `update_time` 做增量抽取。
- 本机数据量：**29 行**，`create_time` 落在 **2023-09-10 ~ 2023-09-15**；
  `status` 分布 = `{0:3, 1:10, 2:7, 3:2, 4:7}`（实测）。
- `channel_code` 可为空（本机 7 张 `status=2` 里 2 张为空，占 29%）→ 见 FIX-21。
- 索引（FIX-4 已执行）：`idx_order_inner_code_create_time (inner_code, create_time)`、
  `idx_order_status_create_time (status, create_time)`。

---

## 2. `tb_inventory_log`（库存变动日志，14 列）

| 列 | 类型 | 注释 |
| --- | --- | --- |
| id | bigint | 主键ID |
| vm_id | bigint | 售货机ID |
| sku_id | bigint | 商品SKU ID |
| channel_id | bigint | 货道ID |
| change_type | tinyint | 变动类型（1销售 2补货 3损耗 4调整 5盘点） |
| change_amount | int | 变动数量（正数为增加，负数为减少） |
| before_stock | int | 变动前库存 |
| after_stock | int | 变动后库存 |
| order_id | bigint | 关联订单ID（销售类型时） |
| business_type | varchar(50) | 业务类型 |
| operator_id | bigint | 操作人ID |
| operator_name | varchar(100) | 操作人姓名 |
| create_time | datetime | 创建时间 |
| remark | varchar(500) | 备注 |

**要点**：

- **本机 0 行**（空表）→ 任何基于它的检测/聚合**都无法用真库验证识别正确性**，只能用构造样本。
  这与 AGENTS §9.3「不隐瞒」直接相关：不能因为“脚本跑过没报错”就说库存异常检测已验收。
- **没有 `inner_code`**（只有 `vm_id`）→ 按设备编号过滤必须先 `join tb_vending_machine v on v.id = l.vm_id`。
  这一点直接决定了 2-1 查询的写法（`read_tools.list_inventory_anomalies`）。
- **没有 `order_no`**（只有 `order_id`）→ “有单/无单”只能按主键关联 `tb_order.id`；
  填了不存在的 `order_id` 与 `order_id is null` 从日志侧无法区分，因此 2-1 用 `left join` 把两者归为同一类
  （见 `InventoryAnomaly` docstring）。
- **FIX-4 第 4 条索引的列名已核实**：`(vm_id, create_time)` 可行（两列都存在，且与 2-1 的查询形状一致）。
  索引 DDL 见 `docs/ddl/add_index_tb_order_and_tb_task.sql` 末尾（原注释为“未执行，需先核列名”）。

---

## 3. 复跑导出命令

```bash
cd dkd-agent
set -a; source ../.env; set +a
.venv/Scripts/python.exe - <<'PY'
import asyncio, sys
sys.path.insert(0, ".")
from sqlalchemy import text
from app.db import get_sessionmaker, dispose_engine

async def main():
    async with get_sessionmaker()() as s:
        for t in ("tb_order", "tb_inventory_log"):
            r = await s.execute(text(
                "select column_name, column_type, column_comment from information_schema.columns "
                "where table_schema = database() and table_name = :t order by ordinal_position"
            ), {"t": t})
            print("==", t)
            for row in r.mappings().all():
                print("  ", row["column_name"], "|", row["column_type"], "|", row["column_comment"])
    await dispose_engine()

asyncio.run(main())
PY
```

> ⚠️ 注意：`information_schema` 只返回**当前账号有权限**的表；dkd-agent 用的是只读白名单账号，
> 因此能查到的就是它**允许读**的表——这本身就是白名单的一次交叉验证。
