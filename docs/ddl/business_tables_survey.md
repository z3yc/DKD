# 业务表勘查记录（索引 / 数据量 / 口径）· 2026-09-21

> 目的：补上排期任务 0-4 要求的“数据量与索引勘查归档”，并作为 **1-2（订单聚合）** 的 EXPLAIN 留档。
> 方法：直连本地 `dkd` 库（MySQL 8.0）读取 `information_schema`，用真实 SQL 跑 `EXPLAIN`；
> 规模类结论用**临时压力表**（20 万行，用完即删）验证，不污染业务表。
> 结论均附命令与原始输出，可复跑。

---

## 一、表清单：行数 / 索引（实测）

| 表 | 行数 | 索引 | 备注 |
| --- | --- | --- | --- |
| `tb_vending_machine` | 15 | PRIMARY(id)、`inner_code` 唯一、node_id、vm_type_id、policy_id | 运行中（`vm_status=1`）设备 15 台 |
| `tb_node` | 6 | PRIMARY、region_id、partner_id | 点位名 |
| `tb_region` | 4 | PRIMARY | 区域名 |
| `tb_channel` | 272 | PRIMARY、vm_id、`inner_code` | 货道档案（`channel_code`/`sku_id`/`max_capacity`） |
| `tb_inventory` | 3 | PRIMARY、vm_id、sku_id、`(vm_id,sku_id)` 唯一 | 库存（`current_stock`/`min_stock`/`is_alert`） |
| `tb_order` | 29 | PRIMARY、`order_no` 唯一 | **无 `inner_code` 索引、无 `create_time` 索引** |
| `tb_task` | 22 | PRIMARY、product_type_id、task_status、create_type、`task_code` 唯一 | **无 `inner_code`/`create_time` 索引** |
| `tb_task_details` | 13 | PRIMARY、task_id | 22 个工单只有 13 条明细（历史数据不完整） |
| `tb_emp` | 17 | PRIMARY、region_id、role_id、user_name 唯一、mobile 唯一 | 接单人区域匹配用 |

**数据特征（影响测试与演示脚本）**：订单数据的 `create_time` 落在 **2023-09-10 ~ 2023-09-15**，
工单落在 2025-11；因此任何“近 30 天”的演示在本库都会得到**空结果**——
live 抽样测试必须显式传历史窗口（`tests/test_read_tools_live.py` 用 2023-09 的固定窗口）。

---

## 二、EXPLAIN 留档（1-2 的核心验收）

### 2.1 真实 `tb_order` 现状（29 行）

```sql
explain select inner_code, channel_code, count(*), sum(amount) from tb_order
where inner_code in ('A1000001') and create_time >= '2023-09-01' and create_time < '2023-09-16'
  and status = 2 group by inner_code, channel_code limit 2000;
```
```
type: ALL   possible_keys: NULL   key: NULL   rows: 28   filtered: 3.57
Extra: Using where; Using temporary
```
> 结论：**全表扫描**。小表下无感，但本库**根本没有可用于该查询的索引**——`tb_order` 只有
> PRIMARY 与 `order_no` 唯一索引，而聚合的过滤条件是 `inner_code` + `create_time`。

### 2.2 规模验证（临时表 20 万行，用完已删）

构造 `tmp_order_scale`（20 万行，`inner_code` 500 个取值、`create_time` 跨 600 天、`status` 混合），
分别在三种索引状态下执行**同一组查询**：

| 场景 | 查询写法 | 用到的索引 | key_len | rows（预估扫描行数） |
| --- | --- | --- | --- | --- |
| A：无索引（= 真实 `tb_order` 现状） | 范围比较 | 无 | NULL | **199,430**（≈全表） |
| A：无索引 | `date_format` 包裹列 | 无 | NULL | **199,430** |
| B：`(inner_code, create_time)` 复合索引 | **范围比较** | `idx_inner_create` | **69** | **2** |
| B：`(inner_code, create_time)` 复合索引 | `date_format` 包裹列 | `idx_inner_create`（只用到前半段） | **63** | **800** |

原始输出（场景 B）：

```
--- 范围比较 ---                       --- 函数包裹（date_format）---
type: range                            type: range
key: idx_inner_create                  key: idx_inner_create
key_len: 69                            key_len: 63          ← 少了 create_time 的 6 字节
rows: 2                                rows: 800            ← 400 倍差距
Extra: Using index condition; Using where; Using temporary
```

> **这是 `date_format()` 禁令的量化证据**（`AGENTS §10` / 排期 1-2）：
> 同一份数据、同一个索引，仅仅因为把列包进函数，索引就“用不上后半段”，
> 预估扫描行数从 **2** 涨到 **800**。函数包裹列的危害不是“可能慢”，而是**索引直接失效**。

### 2.3 建议的索引（待评审，DDL 见 `docs/ddl/add_index_tb_order_and_tb_task.sql`）

| 表 | 建议索引 | 支撑的查询 |
| --- | --- | --- |
| `tb_order` | `(inner_code, create_time)` | 近 N 天按设备/货道聚合（1-2）、异常订单检测（2-1） |
| `tb_order` | `(status, create_time)` | 全局按状态的时间窗统计（报表/看板） |
| `tb_task` | `(inner_code, task_status, product_type_id)` | 在途补货工单查询（1-2 去重）、防重复建单 |
| `tb_inventory_log`（Phase 2 用） | `(vm_id, create_time)` | 库存异常变动检测（2-1） |

> ⚠️ 本表**尚未在生产/开发库执行**：AGENTS §2.4 要求索引变更附 DDL 并经评审；
> 2.2 的规模验证是**临时表**上做的，验证完已 `drop table`，业务表结构未变。

---

## 三、发现的既有问题（不是本次改动引入，需产品/Java 侧决策）

| # | 问题 | 证据 | 影响 | 建议 |
| --- | --- | --- | --- | --- |
| 1 | **Java 报表 SQL 丢了参数绑定** | `ReportMapper.xml:122-130`：`sumRevenueByStatusAndDateRange` / `countOrdersByStatusAndDateRange` 只写 `where status >= 1`，未绑定 `#{status}`、未用 `beginTime/endTime`（而接口 `ReportMapper.java:27-33` 声明了这三个 `@Param`） | “AI 报表分析”页营收/订单数/榜单实为**全量 `status>=1`**，与所选时间窗无关；Agent 若以它为对账基准会一直对不上 | 交 Java 侧修（属业务口径变更，不由 Agent 侧顺手改）；Agent 侧对账改用 `ReportServiceImpl.java:28` 的口径定义 + 独立 SQL |
| 2 | **`tb_order` 缺聚合所需索引** | 见 §2.1/§2.2 | 数据量上来后 30 天聚合会全表扫描，与主库业务争抢（本机无只读从库） | 采纳 §2.3 建议索引（附回滚） |
| 3 | **`tb_inventory` 与 `tb_channel` 档案不一致（实测 3/3）** | `sku_id` 与容量两侧全不一致（例：vm=80 channel=4732 → inventory.sku_id=1 / channel.sku_id=6，`inventory.max_stock=6` / `channel.max_capacity=10`） | “货道容量”取哪个直接决定建议量；若静默选边，补货建议会系统性偏大或偏小 | ① 读工具**如实返回两侧取值**并暴露 `data_consistent=false`（已实现）；② 需业务确认权威源（建议：`tb_channel` 为货道档案权威，`tb_inventory` 只管库存数）；③ 补数据修复任务 |
| 4 | **订单状态口径须锁死** | `VmSystemConstant.java:83-102`（0 创建/1 支付/2 出货成功/3 出货失败/4 失效）；`ReportServiceImpl.java:28` 报表用 `2` | 口径混用会让“销量”差好几倍 | Agent 侧固定 `status=2`（配置项 `DKD_AGENT_SALES_ORDER_STATUS`），并在输出里带上口径说明 |
| 5 | **`tb_task_details` 明细缺失** | 22 个工单仅 13 条明细（`left join` 后部分工单 channels 为空） | 在途工单去重若只盯明细，会漏判 | 读工具返回工单级在途列表（`channels` 可能为空，已实现） |

---

## 四、复跑方式

```bash
set -a; source .env; set +a
# 结构/行数勘查
mysql -uroot -p"$DKD_DB_PASSWORD" -e "select table_name, table_rows, index_name, group_concat(column_name) \
  from information_schema.tables t join information_schema.statistics s using (table_name) \
  where t.table_schema='dkd' group by table_name, index_name;"
# 工具层真实库抽样 + 对账（1-1 / 1-2 验收）
cd dkd-agent && uv run pytest -m live tests/test_read_tools_live.py -v -s
```
