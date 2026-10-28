# DKD 业务规范（清晰版）· V1.0

> **这份文件是什么**：DKD 平台**唯一口径**的业务标准（to-be）。它不描述"代码现在长什么样"，而是规定"业务应该怎么跑"。
> **与另外两份文档的关系**：
> - `docs/DKD业务全景梳理.md` = **as-is 体检**（现状、断点、数据证据）；
> - 本文 = **to-be 标准 + 本轮落地状态 + 迁移步骤**；
> - `AGENTS.md` = 开发纪律（安全红线、命名、测试门槛）。
> **基线**：2026-10-28，`VERSION 0.3.0`。任何业务口径变更，先改本文，再改代码（AGENTS §4.3）。

---

## 0. 五条总原则（所有后续规则都由它们推出）

| # | 原则 | 落地判定 |
| --- | --- | --- |
| 1 | **一个事实只有一个所有者** | 每个概念能回答"权威表/字段是什么、谁只能读"（§1） |
| 2 | **状态变更只经状态机** | 状态字段没有第二个写入口；通用编辑接口不得改状态（§3） |
| 3 | **写操作 = 事务 + 校验 + 留痕** | 跨表写同事务；拒绝路径有明确文案；关键变更写流水（§4） |
| 4 | **口径可对账** | 同一指标只有一种算法（状态过滤 + 时间窗 + 粒度），能写出对账 SQL（§4.5） |
| 5 | **失败必须可见** | 不吞异常、不静默跳过、定时任务失败落 `sys_job_log`（§4.7） |

---

## 1. 概念与唯一事实源

| 概念 | 权威来源（唯一） | 唯一写入口 | 允许的冗余/缓存 | 只读方 |
| --- | --- | --- | --- | --- |
| 区域 / 点位 / 合作商 | `tb_region` / `tb_node` / `tb_partner` | 各自管理接口 | `tb_emp.region_name`（区域改名同步） | 报表、Agent |
| 售卖机器 | `tb_vending_machine`（`inner_code` 唯一） | 设备管理；**状态只由工单完成驱动** | `tb_task.addr/region_id`（建单快照） | 全部 |
| **货道卖什么 / 容量** | `tb_channel.sku_id`（0=空货道）、`max_capacity` | 货道配置接口（全量提交） | `tb_inventory.sku_id/max_stock`（同步写） | 补货建议、Agent |
| **库存数量（台账）** | **`tb_inventory.current_stock`** | **库存变更服务（唯一入口）** | `tb_channel.current_capacity` = 设备侧缓存，同事务同步写 | 预警、报表、Agent |
| 预警阈值 | `tb_inventory.min_stock` | 库存初始化/编辑 | —— | 预警、建议 |
| **库存流水（审计）** | `tb_inventory_log` | 库存变更服务 | —— | 审计、异常检测 |
| **工单** | `tb_task` + `tb_task_details` | 建单服务 / 状态机（app 接单完成、后台取消） | 明细内 `sku_name/sku_image` 快照 | 运维端、Agent、报表 |
| 订单 | `tb_order`（**仓外写入**，全快照） | 仅"取消/退款"在本仓 | —— | 报表、Agent |
| 报表 | `tb_report` | 报表任务（唯一算法） | —— | 管理端、Agent |
| 智能体会话/计划/留痕 | `agent_conversation/message/restock_plan/decision_log/run/step/tool_call` | Python 侧（`agent_*` 可写，无 DELETE/DDL） | —— | 管理端只读 |

**明确不允许的写法**（本轮已按此收口）：
- 任何地方直接 `update tb_inventory set current_stock=...` 而不写流水；
- 补货完成只写 `tb_channel` 不写台账；
- 通过 `PUT /manage/task` 或 `PUT /manage/vm` 直接改状态；
- 用 `tb_inventory_log` 的真实列名以外的字段写日志（历史 Mapper 写错列，已修）。

### 1.1 库存数量与货道缓存的关系（必须理解）

```
        写：补货/初始化/调整（唯一入口，同一事务）
        ┌───────────────────────────────┐
        ▼                               ▼
tb_inventory.current_stock      tb_channel.current_capacity
（台账 = 数量事实）              （设备侧缓存 = 现场读数）
        ▲                               ▲
        │                               │
   预警/报表/Agent 读它          仓外交易（销售出货）只写它
```

- **补货**：两边同时加（同一事务），台账为准；
- **销售**：仓外系统只写 `channel`（本仓无法控制），因此**台账与缓存会出现差额**；
- **对账**：每日定时任务按 `channel.current_capacity` 校准台账，差额记一笔 `change_type=4 调整`（`business_type=RECONCILE`）并留痕——**这是 Phase B 待建项，未建之前以台账为准、差额可见**。

---

## 2. 标准业务流程（清楚到"谁在哪个事务里做什么"）

### 2.1 投放上架（一条链，不允许半配置设备）

```
建区域 → 建点位 → 建设备（按型号 行×列 自动生成货道）
  → ① 配置货道：绑商品 + 设容量（PUT /manage/channel/config）
        · 同一设备同一 SKU 只允许占用一个货道（业务约束，避免库存歧义）
        · 配置成功即同步库存台账（新建 tb_inventory：current_stock=0/min_stock 默认/max_stock=容量）
  → ② 库存初始化（可并入①，或单独 POST /manage/inventory/init，必须写流水 change_type=4）
  → ③ 建投放工单（type=1，设备必须非运行中）
  → 运维端接单 → 完成 → 设备 vm_status=1（唯一合法投产入口）
```

验收标准：新建一台设备后，`tb_channel`、`tb_inventory`、`tb_task` 三者能按设备串起来；没有"有货道无库存行"的设备。

### 2.2 补货（本仓最核心的写闭环）

```
① 预警：tb_inventory.current_stock <= min_stock（缺货=0，低库存=<=min）
② 建议：统计基线（Agent）或规则兜底 → 人工确认（不自动建单）
③ 建单：POST /manage/task（type=2）
    校验：设备存在且运行中 / 无同类型在途工单 / 员工存在+启用+角色匹配 /
          明细非空 / channelCode 属于该设备 / 0 < 数量 <= 货道容量
    落库：tb_task(status=1) + tb_task_details（同事务）
④ 运维端：接单(1→2) → 到场 → 完成(2→4)
    **完成是一个事务，做四件事**：
      a. 工单状态 → 4
      b. 货道缓存：current_capacity = min(current_capacity + 实补量, max_capacity)
      c. 库存台账：current_stock = min(current_stock + 实补量, max_stock)，重算 is_alert
      d. 流水：change_type=2（补货），记录 before/after、操作人、business_type=RESTOCK
    任一步失败 → 整体回滚（工单不完成），错误文案回给运维
⑤ 结果：预警自动消除（因为②读的就是③写的同一张台账）
```

**"实补量"规则**：明细填的数量是"计划补货量"；落库存时按 `min(计划量, 货道容量-当前数)` 夹取，流水记录**实际数量**，不允许超过容量（历史缺陷：无条件累加可超容量）。

### 2.3 维修

```
设备异常/报障 → 诊断（Agent 取证，不产病因）→ 派单提案（同区域维修员）
→ 人工确认 → 回调 Java 建维修单（type=3，允许"停机"设备）
→ 运维端接单 → 到场维修 → 完成（只改工单状态）
```

**已知缺口（Phase B 待决）**：维修单没有"现象/原因/处置/配件/照片"字段，运维端无上传 → 无法沉淀知识，Agent 只能给证据。见 §8-3。

### 2.4 撤机

建撤机工单（type=4，仅运行中设备）→ 运维完成 → `vm_status=3`。设备状态不允许由编辑接口直接改。

### 2.5 报表

```
每日 08:00 日报 / 周一 08:00 周报（sys_job status=0 正常）
  聚合 tb_order(status=2) → 营收/订单/设备/故障/低库存 → LLM 解读（失败降级为纯数据）
  → upsert tb_report（report_type+report_date 唯一）
```

### 2.6 智能体（旁路）

- 读：只读账号 + 白名单（`tb_*` 仅 SELECT）；
- 写：只回调 `POST /agent/callback/task`，复用建单校验链，必须人工确认；
- 留痕：`agent_decision_log`（决策）+ `agent_run/step/tool_call`（过程）；
- 降级：`agent.enabled=false` 时主业务零影响；LLM 失败退纯统计基线。

---

## 3. 状态机（唯一合法迁移）

### 3.1 设备 `vm_status`

```
0 未投放 ──投放工单完成──► 1 运行中 ──撤机工单完成──► 3 已撤机
                             │
                             └──故障上报（仓外/人工）──► 2 停机 ──维修完成/人工──► 1
```

- 只有 app 的工单完成会改状态；后台编辑接口**不得**改（本轮已禁止直接改，改走工单）。
- 建单校验依赖此状态：投放要求非运行中；补货/撤机要求运行中；维修要求 1 或 2。

### 3.2 工单 `task_status`

```
1 待接单 ──接单──► 2 进行中 ──完成──► 4 完成（终态）
   │                  │
   └──取消────────────┴──► 3 取消（终态；拒绝与取消共用，desc 说明原因）
```

- 无"拒单"独立状态；无回退、无重派（需要时新建工单）。
- 后台只能取消（1/2→3）；完成只能由执行人本人在运维端操作。
- 通用编辑接口改状态一律拒绝（文案：「请通过接单/完成/取消操作变更工单状态」）。

### 3.3 补货计划 `agent_restock_plan.status`

```
1 建议 ─┬─► 2 已调整 ─┬─► 4 已建单 ──► 5 已复盘（终态）
        │              │
        └─► 3 已跳过 ◄─┘        4 ─► 6 待指派
3 ──恢复建议（仅当天、原因必填）──► 1
1/2/6 ──CAS 抢占──► 7 建单中 ──► 4
```

### 3.4 订单 `status`（仓外写，本仓只读+两动作）

`0 待支付 → 1 支付完成 → 2 出货成功 / 3 出货失败`；`0/1 → 4 已取消`；退款只改 `pay_status=2`。
统计销量**只认 `status=2`**。

### 3.5 报表 `status`

`0 生成中 → 1 成功 / 2 失败`；成功后同一天重跑用 upsert 覆盖，不新增行、不因唯一键报错。

---

## 4. 业务规则（可直接写测试的版本）

### 4.1 建单（唯一入口 `TaskServiceImpl.insertTaskDtoInternal`）

| # | 规则 | 失败文案（中文，原样透传） |
| --- | --- | --- |
| 1 | 设备按 `inner_code` 存在 | 售货机不存在 |
| 2 | 类型白名单 ∈ {1,2,3,4} | 工单类型不合法 |
| 3 | 状态×类型：投放非运行中；补货/撤机=运行中；维修∈{运行中,停机} | 设备正在运行中，请勿重复投放 / 设备不在运行中，请勿重复补货 / 设备未投放或已撤机，无法创建维修工单 / 设备不在运行中，请勿重复撤机 |
| 4 | 同设备+同类型无活跃工单（状态 1/2） | 设备有未完成工单，请勿重复创建工单 |
| 5 | 员工存在且启用 | 员工不存在 / 员工已停用 |
| 6 | 员工角色匹配（**补货 → 1002 运营**；**维修/投放/撤机 → 1003 维修**；1001 管理可兜底任意类型） | 该员工角色不能执行此类工单 |
| 7 | 员工区域 == 设备区域 | 员工区域与设备区域不一致，请勿创建工单 |
| 8 | 类型 2 必须有明细；明细 `channelCode` 属于该设备；`0 < expectCapacity <= max_capacity` | 补货工单必须包含明细 / 货道不属于该设备 / 补货数量超出货道容量 |
| 9 | 唯一键冲突转业务文案（保留 cause） | 设备有未完成工单或工单号冲突，请刷新重试 |

- 创建类型：后台=1 手动；Agent 回调=0 自动（便于审计区分）。
- 事务边界：`tb_task` + `tb_task_details` 同事务；建单**不预占库存**。

### 4.2 完成（app）

- 前置：状态=2 且操作人=执行人；
- 副作用按类型：投放→设备 1；撤机→设备 3；补货→§2.2 四件事；维修→无；
- 全部在**同一事务**；失败整体回滚并返回可读文案；
- 不存在"失败返回 false 但已提交"的路径（历史缺陷已修）。

### 4.3 库存变更（唯一入口）

入参：`vmId, channelId, skuId, changeAmount(带符号), changeType, operator`。

| 校验 | 规则 |
| --- | --- |
| 数量范围 | `after >= 0`；`after <= max_stock`（max 为空不校验；补货按剩余容量夹取） |
| 变更类型 | 1 销售 / 2 补货 / 3 损耗 / 4 调整(含初始化/对账) / 5 盘点 |
| 流水必写 | 真实列：`change_amount/before_stock/after_stock/order_id/business_type/operator_id/operator_name/create_time/remark` |
| 警告标记 | `is_alert = (current_stock <= min_stock)` 每次变更后重算 |
| 时间 | 补货同时更新 `last_supply_time`（台账与货道） |

### 4.4 预警与补货量（规则版兜底口径）

- 缺货：`current_stock = 0`；低库存：`current_stock <= min_stock`（SQL 与 Service 必须一致）；
- 建议量：目标 `max_stock*0.85`，下限 `min_stock*2`，上限 `max_stock`；现库存 ≥ `max_stock*0.9` 不建议；
- 智能体版：7/14/30 天加权分位 + LLM 校准系数（不改数量）+ 夹取 `[0, 容量-现库存]`；
- **容量以 `tb_channel.max_capacity` 为准**；`tb_inventory.max_stock` 是同步冗余。

### 4.5 报表口径

| 指标 | 算法 |
| --- | --- |
| 营收 / 订单数 | `tb_order`，`status=2`，`create_time >= begin AND < end`（半开区间） |
| 设备总数 | `count(*) from tb_vending_machine` |
| 在线/运行设备 | `vm_status=1` |
| 故障设备 | **仅 `vm_status=2` 停机**（未投放/撤机不算故障） |
| 库存不足设备 | `count(distinct vm_id)` where `current_stock <= min_stock` |
| 热销商品/高收入点位 | 按成功订单聚合，Top N |
| 生成 | 同 `(report_type, report_date)` 覆盖更新；失败写 `status=2` 并抛异常给调度器 |

### 4.6 权限

- 所有 `/manage/**` 接口必须有 `@PreAuthorize`；AI 诊断/运营建议用 `manage:vm:query`（设备上下文能力）。
- 智能体网关按**业务能力**判权：读 `manage:task:list`、写 `manage:task:add`、诊断 `manage:vm:query`；白名单外 404。
- 菜单权限与网关权限必须同名（不再出现 `manage:restock:*` 与 `manage:task:*` 两套）；`docs/ddl/agent_restock_menu.sql` 已按新口径改为 `manage:task:*`，**已建库的旧环境**需执行 Phase B 第 1.6 步对齐。
- 数据范围：当前无行级权限；**跨区域可见是已知风险**，Phase C 决策是否引入（见 §8-4）。

### 4.7 定时任务

- 默认状态 `status=0`（正常）；`1` 是暂停，安装即暂停=永不执行（历史坑）。
- 失败必须抛异常（进 `sys_job_log`）；4xx 不重试、5xx/传输失败重试；被暂停/开关关闭记 WARN 不算失败。
- 智能补货 102：每日 06:00，`concurrent=1`，暂停开关 `agent_restock_pause` 有消费方。

### 4.8 数据一致性检查（每日/发版前）

```sql
-- 1) 重复活跃工单（应为 0）
select inner_code, product_type_id, count(*) c from tb_task
 where task_status in (1,2) group by inner_code, product_type_id having c>1;
-- 2) 库存台账与货道缓存的差异（应可解释：差异=仓外销售未对账）
select i.vm_id, i.channel_id, i.current_stock, c.current_capacity,
       i.current_stock - c.current_capacity as diff
  from tb_inventory i join tb_channel c on c.id = i.channel_id
 where i.current_stock <> c.current_capacity;
-- 3) 台账/货道 SKU 与容量不一致（应为 0）
select i.id from tb_inventory i join tb_channel c on c.id=i.channel_id
 where i.sku_id <> c.sku_id or i.max_stock <> c.max_capacity;
-- 4) 库存在流水上的可追溯性（每笔变更都能对上 before/after 链）
-- 5) 报表：同类型同日只有一行（唯一键保证）
```

---

## 5. 清理清单（"混乱"具体清在哪）

| 类别 | 处理 | 状态 |
| --- | --- | --- |
| 库存双写（channel vs inventory） | 台账为准、货道为缓存、补货同事务双写 | ✅ 本轮代码 |
| 库存流水列名错误 | 按真实表列重写 Mapper/实体 | ✅ 本轮代码 |
| `updateInventoryQuantity` 死代码 | 收口为唯一库存变更入口 | ✅ 本轮代码 |
| 建单类型/员工/明细/数量校验缺失 | 补齐（§4.1） | ✅ 本轮代码 |
| 后台编辑可改工单/设备状态 | 禁止，状态只经状态机 | ✅ 本轮代码 |
| 报表任务默认暂停 + 吞异常 + 口径偏 | 启用、抛异常、distinct/停机定义、upsert | ✅ 本轮代码（SQL 待执行） |
| `/manage/ai/**` 无权限 | 加 `manage:vm:query` | ✅ 本轮代码 |
| 前端订单退款/取消必崩等 3 断点 | 修复 | ✅ 本轮代码 |
| `tb_inventory` 唯一键 `(vm_id,sku_id)` 与"按货道"冲突 | 改 `uk_vm_channel`（DDL，待执行） | ⏳ Phase B |
| 重复活跃工单 + 防重唯一索引 | 清数据 + 加生成列唯一索引（DDL，待执行） | ⏳ Phase B |
| 日报/周报任务启用 | `sys_job.status='0'`（SQL，待执行） | ⏳ Phase B |
| 补货菜单权限点漂移 | 菜单 perms 改 `manage:task:*`（SQL，待执行） | ⏳ Phase B |
| 规则版补货入口 | 保留为兜底，观察期后下线 | ⏳ Phase C |
| 空转字段：`policy.discount` / `job.alert_value` / `sku.is_discount` | 标注为"外部结算/预留"，菜单不展示或注释说明 | ⏳ Phase C |
| 前端硬编码路由（库存/报表） | 待 `sys_menu` 补齐后从 constantRoutes 移除 | ⏳ Phase C |
| 维修单结构化回执 | 需业务拍板（§8-3） | ⏳ 待决 |
| 区域级数据权限 | 需业务拍板（§8-4） | ⏳ 待决 |

---

## 6. 迁移与验收

### Phase A：代码（✅ 2026-10-28 已完成并验证）

```powershell
# 后端编译（manage + 依赖）
cd dkd-parent; mvn -pl dkd-manage -am compile -q
# 后端单测（含本次新增的拒绝路径/报表口径用例）
cd dkd-parent; mvn -pl dkd-admin -am test          # 128 passed / 0 failed
# 运维端（独立 Maven 工程，不能用 -pl）
mvn -f dkd-app/pom.xml compile -q                  # exit 0
# 前端
cd dkd-vue; npm run build:prod                     # exit 0
# 智能体（本次改了派单角色过滤：维修取 1003）
cd dkd-agent; uv run ruff check app tests          # All checks passed
cd dkd-agent; uv run pytest                        # 469 passed / 14 deselected
```

> 说明：以上为**编译/单测/构建**级验证；**未做真机联调**（后端/前端/Python 未启动，MySQL 只读账号无法造数），
> 端到端验收按下面 Phase C 在具备环境后执行。

### Phase B：数据与 DDL（执行前必须备份 + 预检）

脚本：`docs/ddl/normalize_business_v2.sql`（含预检、修数、DDL、回滚说明）。**当前未执行**。
执行者：DBA/负责人用 root（本机 `dkd_agent` 只有白名单只读权限）。

执行顺序：
1. **备份**：`mysqldump dkd tb_inventory tb_inventory_log tb_task tb_task_details tb_channel sys_job sys_menu > backup.sql`
2. **预检**：脚本第 0 步（重复工单 / 库存与货道差异 / 无法关联货道 / 同货道重复行 / 报表任务状态）
3. **修数**：库存台账对齐货道（空货道按 0 处理并保留 MIGRATION 流水）、重复活跃工单取消多余（默认保留 task_id 最大）、报表任务启用
   （注意：本机预检已确认 3 行库存与货道不一致、其中 1 行是"空货道却有库存"，需业务确认是删行还是清零）
4. **加约束**：`uk_vm_channel`、活跃工单唯一索引（生成列方案）
5. **验证**：脚本第 3 步全部符合预期；再跑 §4.8 的 5 条查询
6. **回滚**：脚本第 4 步（DDL 反向 + 以备份/快照表回滚数据）

> ⚠️ 未执行前的兼容行为（已在代码里显式处理）：
> - 库存台账仍受旧唯一键 `uk_vm_sku(vm_id,sku_id)` 约束 → 同一 SKU 占多货道时，第 2 个货道补货会经 `(vm,sku)` 回退命中第 1 个货道的台账行（货道缓存更新正确、台账归属可能偏差），Phase B 后消除；
> - 活跃工单唯一索引未建 → 防重仍是"应用层先查后插"，并发窗口存在（已有 4 张历史重复工单待 Phase B 清理）；
> - 日报/周报任务在库里仍是暂停 → Phase B 第 1.5 步后才会执行；
> - 补货菜单权限点仍是 `manage:restock:*` → Phase B 第 1.6 步后与网关对齐。

### Phase C：业务验收（每个流程一条）

| 流程 | 验收方式 |
| --- | --- |
| 投放 | 新建设备→配货道→初始化→投放工单完成→`vm_status=1`，且库存行存在 |
| 补货 | 造一台低库存设备→建单→app 完成→`tb_inventory` 增加、`tb_inventory_log` 有补货流水、预警列表消失 |
| 维修 | 停机设备建维修单（派 1003）→完成→工单完成且设备状态不变（回执字段待决） |
| 报表 | 手动生成日报→`tb_report` upsert→重跑不报错→数字与 §4.5 口径 SQL 一致 |
| 权限 | 普通角色调 `/manage/ai/diagnose` 应 403；有 `manage:vm:query` 才 200 |
| 智能体 | 补货确认建单→回调复用校验链→重复确认幂等；故障派单选 1003 后可建单；重复工单=0 |

---

## 7. 文档与口径的"单一来源"

- 本文是业务口径唯一来源；`docs/DKD业务全景梳理.md` 保留 as-is 证据，不再作为执行依据；
- 报表/库存/工单口径变更 → 先改本文 §4，再改代码与测试；
- 每次 Phase B 脚本执行后，在本文 §5 把对应行状态改为 ✅ 并附执行时间与验证结果（AGENTS §4.3）。

---

## 8. 待决事项（需要业务/负责人拍板，代码不擅自决定）

| # | 事项 | 现状 | 建议默认值 | 影响 |
| --- | --- | --- | --- | --- |
| 1 | 库存唯一粒度 | 现状 `(vm_id,sku_id)`；同一 SKU 在 80 号机有 3 个货道 | 改 `(vm_id,channel_id)`（按货道） | DDL + 3 行数据迁移 |
| 2 | 销售扣减归属 | 仓外在写 `channel`，台账不动 | 维持；建每日对账任务（Phase B） | 台账准确性 |
| 3 | 维修回执字段 | 无现象/原因/处置/配件/照片 | 增 4 个字段 + 运维端表单 | 诊断 Agent 知识库前提 |
| 4 | 区域数据权限 | 业务域 0 处 `@DataScope` | 引入 `region_id` 行级过滤（读侧） | 权限模型改造 |
| 5 | 规则版补货入口 | 与 Agent 工作台并存 | 观察期后下线，保留 API | 运营口径 |
| 6 | 真实数据导出 | 开发库样本极少（订单 29/2023-09） | 从生产脱敏导出 | M2/回测验收 |

---

*版本：V1.0（2026-10-28）。本文件与代码同步演进：Phase B 执行、§8 决策落地后必须回写本文件并更新版本号。*
