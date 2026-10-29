# 合作商分成核算 · 规格草案（P2-25）

> 来源：`docs/DKD系统性梳理-2026-10-28.md` §五 P2-25「`PolicyServiceImpl` 纯 CRUD，抽成/分成计算完全缺失」
> 状态：**草案（未实现）** ｜ 编制日期：2026-10-29 ｜ 决策人：项目负责人 / 业务方
> 为什么先出草案而不直接写代码：这是**钱**的计算，且仓库内没有可依据的结算口径文档。按 AGENTS §9.3「只读不猜」与 §7「安全红线 > 数据一致性」，猜测口径实现的风险远大于晚一天上线。

---

## 1. 结论先说

1. **数据字段在、计算链路不在**：`tb_partner.profit_ratio`、`tb_order.partner_id`、`tb_vending_machine.partner_id/policy_id`、`tb_policy.discount` 都存在，但全仓没有任何按合作商聚合营收 / 计算分成的代码（`ReportServiceImpl` 只按设备/点位/商品聚合，不区分 `partner_id`）。
2. **不能只按「订单金额 × 分成比例」实现**：至少还有 4 个口径未定义（比例单位、计入订单状态、退款冲回、账期边界），任一理解错都会算错钱。
3. **当前 P2 动作 = 登记缺口 + 给规格 + 等拍板**；拍板后建议分两步做（先只读报表接口，再落库结算单），见 §5。

---

## 2. 数据通路盘点（证据到列）

| 对象 | 表 / 列 | 证据 | 现状 |
| --- | --- | --- | --- |
| 合作商分成比例 | `tb_partner.profit_ratio` → `Partner.profitRatio`（`Long`） | `PartnerMapper.xml:12/23/60/90` | 字段存在；**单位未定义**（30 = 30%？3000 = 30.00%？）；无校验（可为空/越界） |
| 订单归属合作商 | `tb_order.partner_id` → `Order.partnerId` | `OrderMapper.xml:34/44/86/143` | 字段存在；**由谁写入未在仓库闭环**（`OrderController.add()` 的下单链路本身缺库存/金额校验，见梳理报告 P0-1） |
| 设备归属合作商 | `tb_vending_machine.partner_id` | `VendingMachineMapper.xml:16/29/39/62/104` | 设备建点时从点位冗余带出（`VendingMachineServiceImpl.insertVendingMachine` 里 `BeanUtil.copyProperties(node, ...)`） |
| 设备价格策略 | `tb_vending_machine.policy_id` → `tb_policy.discount`（`Long`） | `VendingMachineMapper.xml:23/43/69/111`、`PolicyMapper.xml:10/16` | 字段存在；`discount` 语义同样未定义（折扣 80 = 8 折？） |
| 营收金额 | `tb_order.amount`（`Long`，单位未知：分/元） | `OrderMapper.xml:44` | 存在；`ReportMapper.sumRevenueByStatusAndDateRange` 以 `status=2` 聚合 |
| 退款状态 | `tb_order.pay_status`（2=退款中、3=退款完成） | `docs/CONTRACTS/status-codes.md` §订单/支付状态 | 存在；但「退款是否冲减营收/分成」无定义 |
| 合作商账号 | `tb_partner.account/password`，前端 `loginType=2` | `docs/DKD业务全景梳理.md:251/357` | 合作商可登录后台，但目前没有分成页面/接口 |

**结论**：字段足够支撑一版「按合作商聚合营收 × 比例」的报表，但**字段单位与业务口径是空白**，必须先拍板。

---

## 3. 待拍板问题（按影响面排序）

| # | 问题 | 候选口径 | 默认建议 | 影响 |
| --- | --- | --- | --- | --- |
| Q1 | `profit_ratio` 单位 | ①百分数（30=30%）②万分比（3000=30%）③千分比 | **百分数**（与前端表单"分成比例(%)"一致；需查现网数据分布验证：若存在 >100 的值则可能是万分比） | 算错 100 倍 |
| Q2 | 计入分成的订单状态 | ①`status=2` 出货成功（与报表营收口径一致）②`pay_status=1` 已支付 ③两者都要 | **`status=2`**（既有报表口径，且出货成功才产生真实商品交付） | 口径与营收报表需一致 |
| Q3 | 退款/取消如何冲回 | ①退款完成（`pay_status=3`）全额冲减 ②不冲减（分成按出货计）③按退款发生账期冲减 | **按退款完成所在账期冲减**（权责发生制；需 `tb_order.update_time` 或退款流水时间） | 跨期账对不平 |
| Q4 | 结算周期与边界 | ①自然月 ②自然周 ③自定义账期 | **自然月**（与现有日报/周报体系兼容，`ReportServiceImpl` 已有时间窗查询范式） | 对账口径 |
| Q5 | 设备未投放/撤机期间 | ①不特殊处理 ②排除非运行期订单 | 不特殊处理（无订单即无分成） | 低 |
| Q6 | 比例变更历史 | ①只按当前比例（一改历史全变）②按下单时快照 | **按下单时快照**（需新增 `tb_order.profit_ratio_snapshot` 列或结算单冻结），否则改比例会重写历史对账结果 | 审计风险 |
| Q7 | 金额单位 | `amount` 是分还是元 | 需查现网数据（`amount` 值域与 SKU 售价对比） | 100 倍 |

> Q1/Q7 都可以**用现网数据自证**（`SELECT MIN/MAX(profit_ratio) FROM tb_partner;`、`SELECT amount,price FROM tb_order LIMIT 20`）。本机无业务库凭据，未执行；建议拍板前先跑这两条只读查询。

---

## 4. 建议实现方案（拍板后按此排期）

### 4.1 第一步：只读分成报表（低风险，1~2 人日）

- 位置：`dkd-manage` 新增 `PartnerSettlementService`（或挂在 `ReportServiceImpl` 下的独立方法），**不进 Controller 写业务逻辑**；
- 接口：`GET /manage/partner/settlement?partnerId=&beginTime=&endTime=`，返回 `{partnerId, partnerName, profitRatio, orderCount, grossAmount, shareAmount}`；
- SQL（必须限定时间窗口 + 走索引，禁止全表）：

```sql
SELECT COUNT(1) AS order_count, COALESCE(SUM(amount), 0) AS gross_amount
FROM tb_order
WHERE partner_id = #{partnerId}
  AND status = 2                    -- 出货成功（Q2 待拍板）
  AND create_time >= #{beginTime}
  AND create_time < DATE_ADD(#{endTime}, INTERVAL 1 DAY)
```

- 分成额 = `gross_amount × profit_ratio / 100`，用 `BigDecimal`（AGENTS §2.1 金额禁 float/double），`HALF_UP` 保留 2 位；
- 超管看全部、合作商账号只能看自己（`loginType=2` + `partnerId` 过滤，鉴权在 Controller 入口校验，不得只靠前端）。

### 4.2 第二步：结算单落库（1~2 人日，需 DDL 评审）

- 新表 `tb_partner_settlement`（`settlement_no/partner_id/period_begin/period_end/order_count/gross_amount/profit_ratio/share_amount/status/create_time…`），**按下单时比例快照（Q6）**；
- 生成方式：定时任务按月生成 + 人工确认（沿用 `tb_report` 的生成-确认范式：`ReportServiceImpl` 已有 `REPORT_STATUS_FAILED` 等状态机）；
- 幂等：`(partner_id, period_begin, period_end)` 唯一键；重复生成走更新而非插入。

### 4.3 明确不做

- 不在本次引入「自动打款/对私账户」；
- 不把分成计算放进 Python 智能体（业务事实写操作仍归 Java，AGENTS §1 约束 1）。

---

## 5. 验收标准（实现时用）

1. **对拍**：同一窗口内，分成报表的 `order_count/gross_amount` 与 `ReportMapper.sumRevenueByStatusAndDateRange`（全合作商合计）逐项一致；
2. **拒绝路径**：合作商账号查他人 `partnerId` → 403/空结果（必须有测试，不得只靠前端隐藏）；无 `profit_ratio` 的合作商 → 明确报错或记 0 并在报表列标注（不得静默算成 0 混入合计）；
3. **边界**：账期首日 00:00:00 / 末日 23:59:59 的订单归属；退款跨期冲减；比例为 0/100/空；
4. **金额精度**：`BigDecimal` + `HALF_UP`，与财务口径对拍分位；
5. **性能**：查询必须命中 `tb_order(partner_id, create_time)` 复合索引（新增索引走 `docs/ddl/` + 回滚）；单次查询限定 ≤ 1 个自然月。

---

## 6. 风险与未验证项

- **未验证**：现网 `profit_ratio`/`amount` 的真实值域（本机无业务库凭据，无法自证 Q1/Q7）；
- **未验证**：`tb_order.partner_id` 的实际填充率（若下单链路未写该列，需先补数据，否则报表为 0）；
- **依赖**：P0-1（下单链路断链）若继续存在，订单数据本身的可信度有限，分成报表只能作为「对账参考」不能直接用于结算；
- 本草案不改变任何代码；拍板后建议单独开 `feature/partner-settlement` 分支，并同步 `docs/ddl/`。
