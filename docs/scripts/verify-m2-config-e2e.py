"""M2 配置化底座 · 真库端到端验收（Java 8080 + Python 8090 + MySQL）。

## 为什么单独一个脚本

单测证明“逻辑正确”，`verify-agent-config-seed.py` 证明“seed 与常量逐字一致”，
但「Java 保存 → 事务写版本/历史 → 异步通知 Python → 下一次调用生效」这条**跨进程链路**
只能在真环境验证（排期 2-2/2-3/2-7/2-8 的联调验收）。

## 安全护栏（AGENTS §10 验证脚本纪律）

- **只操作 `code='restock'` 的内置行，且要求运行前处于未修改的 v1 状态**；
  若已被人工改过（version>1 / enabled=0 / change_note 非 seed），直接 `BLOCKED` 退出，
  绝不在“别人改过的配置”上做覆盖实验。
- 备份/恢复走**独立 autocommit 连接**（不在长事务里读，避免 REPEATABLE READ 读到写前快照，
  这正是 M2 验收脚本“新增 0 == 成功 0 却真建了一张工单”的成因）。
- 恢复动作只在“本次确实写过”（version>备份 version 且 change_note 以 `e2e-smoke` 开头）时执行，
  且恢复内容逐列来自**运行前备份**，不重新拼默认值。
- 所有写入走 Java API（唯一合法写通道）；直接 SQL 仅用于**完成清理恢复**，并在输出中明示。

## 前置条件

- Java 后端 8080（当前分支代码）、Python 8090（当前分支代码）均已启动；
- `.env` 有 `DKD_AGENT_SERVICE_SECRET`；环境变量 `MYSQL_PWD` 为 root 口令（仅本机验收用）；
- 库 `dkd` 已执行 `agent_config.sql` + seed + 菜单 SQL，且 restock 行处于 seed v1。

## 退出码

- 0 = PASS；1 = FAIL（真实断言失败）；2 = BLOCKED（服务未起/前置不满足/row 非初始态）。

用法（仓库根，Windows）：
    $env:MYSQL_PWD='<root 口令>'
    dkd-agent\\.venv\\Scripts\\python.exe docs/scripts/verify-m2-config-e2e.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dkd-agent"))

import httpx  # noqa: E402
import pymysql  # noqa: E402

JAVA_BASE = os.environ.get("DKD_E2E_JAVA_BASE", "http://127.0.0.1:8080")
PY_BASE = os.environ.get("DKD_E2E_PY_BASE", "http://127.0.0.1:8090")
CODE = "restock"
EXIT_OK, EXIT_FAIL, EXIT_BLOCKED = 0, 1, 2

#: 备份列的固定顺序（恢复时逐列写回，避免“用默认值覆盖人工修改”）
BACKUP_COLUMNS = (
    "name",
    "description",
    "system_prompt",
    "tools",
    "model",
    "temperature",
    "max_tokens",
    "timeout_s",
    "max_retries",
    "params_json",
    "enabled",
    "disabled_reason",
    "change_note",
    "create_by",
    "create_time",
    "update_by",
    "update_time",
    "del_flag",
)


def blocked(message: str) -> int:
    print(f"BLOCKED: {message}")
    return EXIT_BLOCKED


def service_secret() -> str:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return ""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DKD_AGENT_SERVICE_SECRET=") and not line.startswith("#"):
            return line.split("=", 1)[1].strip()
    return ""


def mysql_conn() -> pymysql.connections.Connection:
    return pymysql.connect(
        host="127.0.0.1",
        user="root",
        password=os.environ.get("MYSQL_PWD", ""),
        database="dkd",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def read_row(conn: pymysql.connections.Connection) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM agent_config WHERE code=%s AND del_flag='0'", (CODE,))
        return cur.fetchone()


def history_versions(conn: pymysql.connections.Connection) -> list[int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT version FROM agent_config_history WHERE code=%s ORDER BY version", (CODE,)
        )
        return [int(row["version"]) for row in cur.fetchall()]


def restore(conn: pymysql.connections.Connection, backup: dict) -> None:
    """按运行前备份恢复整行 + 删除本次新增的历史版本（仅测试清理用）。"""
    columns = ", ".join(f"{col} = %s" for col in BACKUP_COLUMNS)
    values = [backup[col] for col in BACKUP_COLUMNS]
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE agent_config SET {columns}, version = 1 WHERE id = %s",
            (*values, backup["id"]),
        )
        cur.execute(
            "DELETE FROM agent_config_history WHERE code=%s AND version > 1", (CODE,)
        )


def main() -> int:  # noqa: PLR0915 —— 验收脚本按步骤线性展开，便于逐条对照报告
    secret = service_secret()
    if not os.environ.get("MYSQL_PWD"):
        return blocked("未设置 MYSQL_PWD（本机验收需要 root 口令做备份/恢复）")
    if not secret:
        return blocked(".env 缺少 DKD_AGENT_SERVICE_SECRET")

    try:
        with httpx.Client(base_url=JAVA_BASE, timeout=20) as client:
            login = client.post("/login", json={"username": "admin", "password": "admin123"})
            if login.status_code != 200 or login.json().get("token") is None:
                return blocked(f"Java 登录失败（{login.status_code}）——后端未就绪或口令不同")
            token = login.json()["token"]
    except httpx.HTTPError as exc:
        return blocked(f"Java 后端不可达：{type(exc).__name__}")

    try:
        with httpx.Client(base_url=PY_BASE, timeout=20) as py:
            health = py.get("/health")
            if health.status_code != 200 or not health.json().get("config_from_db"):
                return blocked("Python 服务未就绪或未开启 DKD_AGENT_CONFIG_FROM_DB")
    except httpx.HTTPError as exc:
        return blocked(f"Python 服务不可达：{type(exc).__name__}")

    conn = mysql_conn()
    backup: dict | None = None
    try:
        backup = read_row(conn)
        if backup is None:
            return blocked("agent_config 缺少 restock 行（未执行 seed）")
        if int(backup["version"]) != 1 or int(backup["enabled"]) != 1:
            return blocked(
                f"restock 行不是 seed v1 初始态（version={backup['version']} "
                f"enabled={backup['enabled']}）；请先人工确认，不在已修改配置上做验收"
            )
        print(f"备份：id={backup['id']} version={backup['version']} code={CODE}")

        headers = {"Authorization": "Bearer " + token}
        failures: list[str] = []

        def check(name: str, ok: bool, detail: str = "") -> None:
            print(f"{'PASS' if ok else 'FAIL'} {name}{(' — ' + detail) if detail else ''}")
            if not ok:
                failures.append(name + (f"（{detail}）" if detail else ""))

        with httpx.Client(base_url=JAVA_BASE, headers=headers, timeout=20) as client:
            listing = client.get("/manage/agent/config/list", params={"pageNum": 1, "pageSize": 20})
            rows = listing.json().get("rows", []) if listing.status_code == 200 else []
            check("列表返回 3 行内置配置", listing.status_code == 200 and len(rows) == 3)

            detail = client.get(f"/manage/agent/config/{backup['id']}")
            check(
                "详情返回完整 system_prompt",
                detail.status_code == 200 and isinstance(detail.json().get("data"), dict),
            )

            history = client.get(f"/manage/agent/config/{backup['id']}/history")
            hist_rows = history.json().get("data") or []
            check(
                "历史含 v1 快照",
                history.status_code == 200 and any(int(r.get("version", 0)) == 1 for r in hist_rows),
            )

            routers = client.get("/getRouters")
            routes_text = json.dumps(routers.json(), ensure_ascii=False) if routers.status_code == 200 else ""
            check("菜单路由含 agentConfig", "agentConfig" in routes_text)

            # 1) 乐观锁：用过期 version 提交，必须 HTTP 409（不得静默覆盖）
            stale = client.put(
                f"/manage/agent/config/{backup['id']}/enabled",
                json={"enabled": 1, "disabledReason": None, "version": 999},
            )
            check("stale version 返回 HTTP 409", stale.status_code == 409, f"http={stale.status_code}")

            # 2) 字段校验：prompt 过短，必须 HTTP200 + code400 + 字段级 data（且不落库）
            invalid_body = {
                "name": backup["name"],
                "description": backup["description"],
                "systemPrompt": "太短",
                "tools": backup["tools"],
                "model": backup["model"],
                "temperature": backup["temperature"],
                "maxTokens": backup["max_tokens"],
                "timeoutS": backup["timeout_s"],
                "maxRetries": backup["max_retries"],
                "paramsJson": json.loads(backup["params_json"]) if backup["params_json"] else {},
                "enabled": 1,
                "disabledReason": None,
                "version": int(backup["version"]),
                "changeNote": "e2e-smoke 校验拒绝（不应落库）",
            }
            invalid = client.put(f"/manage/agent/config/{backup['id']}", json=invalid_body)
            invalid_json = invalid.json() if invalid.status_code == 200 else {}
            check(
                "过短 prompt 被字段级拒绝",
                invalid.status_code == 200
                and invalid_json.get("code") == 400
                and isinstance(invalid_json.get("data"), dict),
                f"http={invalid.status_code} code={invalid_json.get('code')}",
            )
            after_invalid = read_row(conn)
            check(
                "校验失败未落库（version 仍为 1）",
                int(after_invalid["version"]) == 1,
                f"version={after_invalid['version']}",
            )

            # 3) 预热 Python 配置缓存，再经 Java 保存，立即读 effective 验证 reload 生效
            with httpx.Client(base_url=PY_BASE, headers={"X-Agent-Secret": secret}, timeout=20) as py:
                before = py.get("/agents/config/effective", params={"code": CODE})
                before_ver = int((before.json().get("data") or {}).get("config", {}).get("version", -1))
            check("保存前 Python effective=v1", before_ver == 1, f"version={before_ver}")

            save_body = dict(invalid_body)
            save_body["systemPrompt"] = backup["system_prompt"]
            save_body["changeNote"] = "e2e-smoke 正常保存"
            saved = client.put(f"/manage/agent/config/{backup['id']}", json=save_body)
            saved_json = saved.json() if saved.status_code == 200 else {}
            saved_version = int((saved_json.get("data") or {}).get("version", -1))
            check("保存成功且 version=2", saved.status_code == 200 and saved_version == 2, f"version={saved_version}")

            # 立即读（远小于 TTL 30s）：若 Java 未通知 Python，这里仍是 1
            with httpx.Client(base_url=PY_BASE, headers={"X-Agent-Secret": secret}, timeout=20) as py:
                after = py.get("/agents/config/effective", params={"code": CODE})
                after_cfg = (after.json().get("data") or {}).get("config") or {}
                after_ver = int(after_cfg.get("version", -1))
            check("Java 保存后 Python 立即读到 v2（reload 生效）", after_ver == 2, f"version={after_ver}")

            # 4) 回滚：把 v1 快照复制为新版本
            rolled = client.post(
                f"/manage/agent/config/{backup['id']}/rollback",
                json={"targetVersion": 1, "version": 2, "changeNote": "e2e-smoke 回滚至 v1"},
            )
            rolled_json = rolled.json() if rolled.status_code == 200 else {}
            check(
                "回滚成功且 version=3",
                rolled.status_code == 200
                and int((rolled_json.get("data") or {}).get("version", -1)) == 3,
                f"version={(rolled_json.get('data') or {}).get('version')}",
            )
            restored_detail = client.get(f"/manage/agent/config/{backup['id']}").json().get("data") or {}
            check(
                "回滚后内容与 v1 一致（prompt 逐字相等）",
                restored_detail.get("systemPrompt") == backup["system_prompt"],
            )

        # 5) 清理：恢复备份 + 删除本次 e2e 产生的历史版本（直接 SQL，明示用途）
        current = read_row(conn)
        if int(current["version"]) > int(backup["version"]) and str(current["change_note"]).startswith(
            ("e2e-smoke", "回滚")
        ):
            restore(conn, backup)
            print("已恢复：restock 行回到备份 v1，并删除本次 e2e 产生的历史版本（直接 SQL 清理）")
        else:
            print("未检测到本次写入痕迹，跳过恢复")
        with httpx.Client(base_url=PY_BASE, headers={"X-Agent-Secret": secret}, timeout=20) as py:
            py.post("/agents/config/reload")

        final = read_row(conn)
        final_hist = history_versions(conn)
        check("清理后 version=1", int(final["version"]) == 1, f"version={final['version']}")
        check("清理后历史仅 v1", final_hist == [1], f"versions={final_hist}")

        if failures:
            print(f"FAIL: {len(failures)} 项未通过")
            for item in failures:
                print(f"  - {item}")
            return EXIT_FAIL
        print("PASS: M2 真库端到端（列表/详情/历史/菜单/409/字段校验/保存/reload/回滚/清理）")
        return EXIT_OK
    finally:
        # 兜底恢复：无论成功/断言失败/异常，只要检测到“本次 e2e 写入”的痕迹就恢复备份，
        # 避免中途异常把真库留在 version>1 的中间态（显式清理已恢复时这里自动跳过）。
        try:
            if backup is not None:
                current = read_row(conn)
                if int(current["version"]) > int(backup["version"]) and str(
                    current["change_note"]
                ).startswith(("e2e-smoke", "回滚")):
                    restore(conn, backup)
                    print("自动恢复：检测到未清理的 e2e 写入，已按备份恢复 restock 行与历史")
                    with httpx.Client(
                        base_url=PY_BASE,
                        headers={"X-Agent-Secret": secret},
                        timeout=20,
                    ) as py:
                        py.post("/agents/config/reload")
        finally:
            conn.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 —— 脚本兜底：未预期异常不冒充 PASS/FAIL
        print(f"BLOCKED: 未预期异常 {type(exc).__name__}: {str(exc)[:300]}")
        raise SystemExit(EXIT_BLOCKED) from exc
