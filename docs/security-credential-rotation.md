# DKD 凭据轮换 runbook（排期任务 0-14b）

> 背景：密钥曾以明文进入过 Git 历史（已通过 14a 重写提交 + 14c 清理 dangling 对象消除**可达**痕迹），
> 但**凡是进入过历史的凭据都必须视为已泄露并轮换**——重写历史不等于换锁。
> 本文件是 14b 的可执行脚本化说明：每个凭据的**位置 → 轮换步骤 → 影响面 → 验证 → 回滚**。
>
> 现状（滚动更新）：14a ✅（配置外置已提交，可达历史无明文）、14c ✅（对象库密钥命中 0、未可达对象 0）、
> `dkd_agent` 库账号 ✅（2026-09-21 已轮换并实测旧口令失效）、其余见下表。

---

## 一、凭据清单与状态

| # | 凭据 | 环境变量 | 注入位置 | 影响面 | 状态 |
| --- | --- | --- | --- | --- | --- |
| 1 | MySQL `root`（或 dkd 库管理员）口令 | `DKD_DB_PASSWORD` | `dkd-parent/dkd-admin/src/main/resources/application-druid.yml`；`dkd-app/src/main/resources/application-dev.yml` | **大**：改 MySQL 服务端口令会牵动同机其他项目（my/gogs/itest/…）的配置 | ⏳ 待人工（需 DBA/值班窗口） |
| 2 | Redis 口令 | `DKD_REDIS_PASSWORD` | `dkd-parent/.../application.yml`、`dkd-app` 配置 | 大：需同时改 Redis 服务端配置与**所有**客户端（含本机其他项目） | ⏳ 待人工 |
| 3 | JWT 签发密钥 | `DKD_JWT_SECRET` | `dkd-parent/.../application.yml` | **中**：轮换后**所有在线用户立即掉线**（需重登），无数据影响 | ⏳ 待人工（建议放值班窗口/低峰） |
| 4 | OSS AccessKey/SecretKey | `DKD_OSS_ACCESS_KEY` / `DKD_OSS_SECRET_KEY` | `dkd-parent/.../application.yml` | 小：仅文件上传；需在阿里云控制台新建 AK → 改配置 → 灰度观察 → 禁用旧 AK | ⏳ 待人工（需云控制台） |
| 5 | DeepSeek API Key | `DKD_DEEPSEEK_API_KEY` | `dkd-parent/.../application.yml`、`dkd-agent`（复用同一变量） | 小：仅 LLM 调用；控制台重新签发即可 | ✅ 旧 Key 实测已失效（HTTP 401，2026-09-21 核）；当前 Key 有效（live 用例通过） |
| 6 | Druid 监控台口令 | `DKD_DRUID_PASSWORD` | `application-druid.yml`（login-username: ruoyi） | 极小：仅 `/druid` 页面 | ⏳ 待人工（本地低优先） |
| 7 | 智能体只读库账号 `dkd_agent` | `DKD_AGENT_DB_PASSWORD` | `dkd-parent` **不引用**；仅 `dkd-agent`（Python）使用 | 小：只影响智能体服务（业务表只读白名单 + `agent_*` 可写无 DELETE） | ✅ 2026-09-21 已轮换（旧口令 1045 被拒、权限矩阵复测 3 项拒绝全中、Python 侧留痕写入复测通过） |

> 关键判断：**1/2/3 属"改了会影响别人"的凭据，必须由项目负责人协调窗口**；
> 4/5/6/7 可在低峰自行完成。本次执行的是 #7（唯一不改服务端配置的一类）。

---

## 二、通用轮换流程（四步，缺一不可）

1. **新值生成**：`python -c "import secrets;print(secrets.token_urlsafe(24))"`；
   长度/字符集要求：JWT secret ≥ 32 字符随机串；数据库口令 ≥ 20 字符（避免 `$` 等 shell 特殊字符，或改写脚本用单引号）。
2. **写入 .env（不入仓）**：只改 `.env`（已 gitignore），**绝不**把真实值写回 `application-*.yml`（占位符 `${DKD_*}` 必须保持）。
3. **重启加载**：Spring Boot / Python **都不会自动读 `.env`** ⇒ 需 `setx`（新开终端生效）或 IDE 运行配置注入，然后重启进程。
4. **验证与清理**：
   - 旧口令**必须失败**（不留"新旧都可用"的过渡态，否则等于没轮换）；
   - 新口令按功能验证（见各凭据的验证命令）；
   - 记录轮换时间与执行人（本文件 §五 变更记录）。

---

## 三、逐凭据操作要点

### #3 JWT secret（最需要注意影响面）

```bash
# 1) 生成新值并注入（Windows 永久注入，需重开终端/IDE）
setx DKD_JWT_SECRET "<新随机串>"
# 2) 重启 dkd-parent
# 3) 验证：旧 token 必须失效（信封 401），重新登录后新 token 可用
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer <旧token>" http://127.0.0.1:8080/manage/inventory/list
# 期望：HTTP 200 + 信封 code=401（RuoYi 约定，见方案 §3.3.1 D）
```
> 影响：在线用户全部掉线。**必须**在低峰或值班窗口执行，并提前告知使用方。

### #4 OSS AK/SK（灰度而非硬切）

1. 控制台**新建**一对 AK（不要先禁用旧的）；
2. 改 `.env` 的 `DKD_OSS_ACCESS_KEY/SECRET_KEY` → 重启 → 上传一张图验证；
3. 观察 1~2 天无异常后，**禁用/删除旧 AK**；
4. 回滚：把 `.env` 改回旧 AK（旧 AK 未禁用前可回滚）。

### #7 `dkd_agent` 库账号（本次已执行，步骤可复用）

```bash
set -a; source .env; set +a
# 1) 改口令（两个 host 变体都要改：Python 走 127.0.0.1，MySQL CLI 走 localhost）
mysql -uroot -p"$DKD_DB_PASSWORD" -e "ALTER USER 'dkd_agent'@'localhost' IDENTIFIED BY '<新口令>'; \
  ALTER USER 'dkd_agent'@'127.0.0.1' IDENTIFIED BY '<新口令>'; FLUSH PRIVILEGES;"
# 2) 旧口令必须被拒（预期 Access denied 1045）
mysql -udkd_agent -p"<旧口令>" -N -e "select 1"
# 3) 新口令按功能验证：允许的能读、该拒的仍拒
mysql -udkd_agent -p"<新口令>" -N dkd -e "select count(*) from tb_inventory"          # 允许（白名单只读）
mysql -udkd_agent -p"<新口令>" -N dkd -e "update tb_inventory set current_capacity=1" # 期望拒绝 1142
mysql -udkd_agent -p"<新口令>" -N dkd -e "select 1 from tb_sys_user limit 1"          # 期望拒绝 1142（非白名单）
# 4) 更新 .env 的 DKD_AGENT_DB_PASSWORD → 重启 dkd-agent → /health + 一次对话落库验证
curl -s http://127.0.0.1:8090/health
```
> 为什么这一步能安全自行执行：该账号**只服务于智能体**，不涉及其他项目与 MySQL 服务端口令，
> 回滚只需把 `.env` 改回旧值（且旧口令在 ALTER 后已不可用——因此**轮换前先确认 Python 侧配置改法**再动手）。

> ⚠️ **轮换必踩的坑（2026-09-21 实测）**：MySQL 8 默认 `caching_sha2_password` 在**口令刚变更/服务刚重启**时
> 需要走一次全量握手（缓存失效），此时非 TLS 连接必须用 RSA 加密 ⇒ 若 Python 侧未装 **`cryptography`**，
> 连库会直接报 `RuntimeError: 'cryptography' package is required for sha256_password or caching_sha2_password auth methods`。
> **本项目已将该依赖声明进 `dkd-agent/pyproject.toml`**（`uv add cryptography`）——否则生产上任何一次 MySQL 重启或口令轮换都会把智能体打挂。

### #1 / #2 MySQL、Redis 服务端口令

- 必须**先盘点同机依赖**：`grep -rn "DKD_DB_PASSWORD\|password" --include=*.yml /f /d 2>/dev/null`（或按项目清单核对）；
- 顺序：改服务端口令 → 同步所有客户端配置 → 重启 → 验证各项目可用；
- 回滚：MySQL 用 `ALTER USER ... IDENTIFIED BY '<旧口令>'`；Redis 改回 `requirepass` 并 `CONFIG REWRITE`；
- **不建议**在无人值守时执行（失败会让多个项目同时不可用）。

---

## 四、验证与验收（14b 的验收标准）

| 验收项 | 命令/证据 | 期望 |
| --- | --- | --- |
| 仓库可达历史无明文 | `git grep -InE "(LTAI[0-9A-Za-z]{10,}|sk-[0-9a-zA-Z]{20,})" -- .` | 0 命中（✅ 已验） |
| 本地对象库无残留密钥 | `git cat-file --batch-all-objects --batch --buffer \| grep -acE "(LTAI…\|sk-…)"` | 0（✅ 已验，14c） |
| 未可达对象清空 | `git fsck --full --unreachable --no-reflogs \| wc -l` | 0（✅ 已验） |
| `.env` / SQLite 不入仓 | `git check-ignore -v .env dkd-agent/var/checkpoints.db` | 命中 gitignore（✅ 已验） |
| 旧 DeepSeek Key 失效 | 用旧 Key 调 DeepSeek | HTTP 401（✅ 已实测） |
| `dkd_agent` 旧口令失效 | 见 §三 #7 步骤 2 | `ERROR 1045`（✅ 已实测） |
| 各服务轮换后功能正常 | 登录/上传/对话/智能体链路 | 全通过 |

> **线上环境**额外要求：生产环境变量由部署平台注入（K8s Secret / systemd EnvironmentFile / 云托管环境变量），
> 不入镜像、不入 CI 日志、不在启动脚本里 `echo`。

---

## 五、变更记录

| 日期 | 凭据 | 执行人 | 验证 | 备注 |
| --- | --- | --- | --- | --- |
| 2026-09-21 | 源码明文 → `${DKD_*}` 占位符（14a） | zyc | 可达历史 grep 0 命中 | 初始提交重写为 `3e1f4a8` |
| 2026-09-21 | 清理 dangling 旧提交对象（14c） | zyc | 对象库密钥命中 2→0、未可达对象 0 | `git reflog expire --expire=now --all && git gc --prune=now` |
| 2026-09-21 | DeepSeek 旧 Key 失效核对 | zyc | 旧 Key HTTP 401 | 供应商侧已失效；当前 Key live 用例通过 |
| 2026-09-21 | `dkd_agent` 库账号口令轮换（#7） | zyc | 旧口令 1045；新口令：白名单 SELECT 成功、业务表 UPDATE 1142、非白名单 SELECT 1142、DELETE agent_* 1142；Python 侧 9 帧流式正常且留痕写入成功（`agent_decision_log.cost_tokens=26`） | 连带发现并修复依赖缺口：新增 `cryptography>=50.0.1`（caching_sha2_password 全量握手必需） |
| — | MySQL root / Redis / JWT secret / OSS AK / Druid（#1~#4、#6） | 待指派 | — | **需值班窗口与云控制台权限** |
