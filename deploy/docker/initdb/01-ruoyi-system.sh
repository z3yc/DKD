#!/bin/bash
# ============================================================================
# 首次初始化 ①：RuoYi 系统表 + quartz + AI 报表表
#
# 执行者：mysql 官方镜像的入口脚本（仅在**数据卷为空**时执行 /docker-entrypoint-initdb.d/*）。
# 为什么不用 set -e / exit：
#   Windows 检出的脚本可能没有可执行位，入口脚本会改用 `source` 执行本文件，
#   此时 set -e 会污染入口 shell、exit 会直接终止初始化流程。失败要“显式报错但不带崩”。
# ============================================================================

NODE="[initdb 01]"
DB="${MYSQL_DATABASE:-dkd}"

echo "$NODE 开始：系统表/quartz/报表表 → 库 $DB"

for f in /sql/ry_20231130.sql /sql/quartz.sql /sql/tb_report.sql; do
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

echo "$NODE 完成。"
echo "$NODE ⚠️ 业务表（tb_inventory/tb_order/tb_task/tb_channel/tb_emp ...）DDL 不在仓库里，"
echo "$NODE    必须人工导入现网 dump，否则管理后台与智能体的读工具都会报“表不存在”。"
echo "$NODE    步骤见 deploy/docker/README.md §4。"
