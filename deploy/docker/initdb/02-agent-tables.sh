#!/bin/bash
# ============================================================================
# 首次初始化 ②：智能体自有表（agent_*）
#
# 为什么要单独一轮：`agent_*` 五张表是**智能体自维护**的表（会话/留痕/计划/开关），
# 与业务事实表同库但授权不同（见 docs/ddl/create_agent_db_user.sql 的两级授权）。
# 顺序依赖：本脚本必须在 01 之后（无强依赖），且必须在 scripts/setup-agent-db.sh 之前
#   —— GRANT 一张不存在的表会报 ERROR 1146，账号会拿不到权限。
#
# 不运行 add_index_tb_order_and_tb_task.sql：它针对业务表且状态是「待评审，尚未执行」
#   （docs/ddl 文件头写明），属需评审的变更，不由部署脚本顺手执行。
# ============================================================================

NODE="[initdb 02]"
DB="${MYSQL_DATABASE:-dkd}"

echo "$NODE 开始：agent_* 自有表 → 库 $DB"

for f in /ddl/agent_tables.sql /ddl/agent_restock_pause.sql /ddl/agent_restock_plan_status_ordering.sql; do
  if [ ! -f "$f" ]; then
    echo "$NODE 跳过（文件不存在）：$f"
    continue
  fi
  echo "$NODE 执行 $f"
  if mysql --protocol=socket -uroot -p"$MYSQL_ROOT_PASSWORD" "$DB" < "$f"; then
    echo "$NODE OK   $f"
  else
    echo "$NODE !!FAIL $f（后续步骤可能连带失败，请检查）"
  fi
done

echo "$NODE 完成（agent_* 共 5 张表：conversation/message/decision_log/restock_plan/restock_pause）。"
echo "$NODE 下一步：在宿主机执行 deploy/docker/scripts/setup-agent-db.sh 建最小权限账号。"
