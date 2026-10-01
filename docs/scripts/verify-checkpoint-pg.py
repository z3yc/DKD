"""PG checkpointer 端到端验收脚本（真 PostgreSQL，排期 0-10 / PG 评审方案 A，评审稿 §9）。

## 这个脚本要证明什么

评审稿 §9「若评审通过：验收方式」的四条 + §8-4 登记的一条未验证假设：

- [A] `DKD_AGENT_CHECKPOINT_BACKEND=postgres` 下配置与工厂正常工作，且**日志/描述里不含口令**；
- [B] `setup()` 幂等（重复启动不报错）、生命周期合同成立（open/close/is_open）；
- [C] **跨进程**「写入 → 新进程读回 → 继续追加」——这是评审稿 §9-2 的验收点
      （复用 verify-1-6 的双进程思路，用 `--phase write` / `--phase read` 两个进程跑）；
- [D] 负向权限：越权建库/建角色/改系统参数/在别的库建表**全部被拒**（对位 MySQL 侧 8 项拒绝路径）；
- [E] **评审稿 §8-4 的未验证假设**：账号只有 DML 权限（无 CREATE）时 `setup()` 是否还能幂等通过？
      —— 这条决定了授权脚本该选「方案 1 独立库 owner」还是「方案 2 DBA 预建表 + 只授 DML」。

## 用法

```bash
cd dkd-agent
# DSN 里是应用角色（见 docs/ddl/create_agent_pg_role.sql）
export DKD_AGENT_PG_DSN="postgresql://dkd_agent_pg:<口令>@127.0.0.1:5432/dkd_agent_state"
# 超级用户口令仅 [D]/[E] 需要（从环境变量读，不落盘、不进命令行历史）
export DKD_PG_SUPERUSER_PASSWORD="..."

.venv/Scripts/python.exe ../docs/scripts/verify-checkpoint-pg.py --phase all
.venv/Scripts/python.exe ../docs/scripts/verify-checkpoint-pg.py --phase write   # 进程 A
.venv/Scripts/python.exe ../docs/scripts/verify-checkpoint-pg.py --phase read    # 进程 B（全新进程）
.venv/Scripts/python.exe ../docs/scripts/verify-checkpoint-pg.py --phase cleanup
```

⚠️ 本脚本**必须**在 Windows 上先解决事件循环策略（`app.checkpoint.ensure_windows_event_loop_policy`
已在 `app.main` 模块级处理；本脚本直接调用同一函数，避免“脚本能跑、服务跑不了”的假通过）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import string
import sys
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parents[2] / "dkd-agent"
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402

THREAD_ID = "verify-pg-checkpoint"
PROBE_ROLE = "dkd_agent_pg_dml_probe"

_passed = 0
_failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    """打印一条可核对的结论（与其他 verify-*.py 同风格：只输出事实与判定）。"""
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  [PASS] {name}" + (f" —— {detail}" if detail else ""))
    else:
        _failed += 1
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


def dsn() -> str:
    value = os.environ.get("DKD_AGENT_PG_DSN", "").strip()
    if not value:
        raise SystemExit(
            "缺少 DKD_AGENT_PG_DSN；例如 export DKD_AGENT_PG_DSN="
            "'postgresql://dkd_agent_pg:<口令>@127.0.0.1:5432/dkd_agent_state'"
        )
    return value


def _settings_with_pg():
    from app.config import Settings

    return Settings(DKD_AGENT_CHECKPOINT_BACKEND="postgres", DKD_AGENT_PG_DSN=dsn())


# ---------------------------------------------------------------------------
# [A] 配置与工厂
# ---------------------------------------------------------------------------


def phase_a() -> None:
    print("\n[A] 配置 / 工厂 / 脱敏")
    from app.checkpoint import (
        BACKEND_POSTGRES,
        PostgresCheckpointStore,
        build_checkpoint_store,
        mask_dsn,
    )

    s = _settings_with_pg()
    check("后端解析为 postgres", s.checkpoint_backend == BACKEND_POSTGRES, s.checkpoint_backend)

    store = build_checkpoint_store(s)
    check("工厂给出 PostgresCheckpointStore", isinstance(store, PostgresCheckpointStore))
    check("未 open 时 is_open=False", store.is_open is False)

    location = store.location
    check("location 已脱敏（不含口令）", "***" in location and _app_password() not in location, location)
    check("mask_dsn 幂等且不泄露", _app_password() not in mask_dsn(dsn()))

    # Windows 上的硬前置——**注意断言的是“当前策略”而不是“刚切换过”**：
    # 策略必须在 asyncio.run() 之前生效，一旦 loop 已创建再改就晚了。
    # 本脚本由 __main__ 里的 _bootstrap_loop_policy() 在最前面完成，这里只做校验；
    # 实测踩过：把切换写在 phase_a 里会在 [B] 段报 psycopg ProactorEventLoop 错误。
    policy_name = type(asyncio.get_event_loop_policy()).__name__
    check(
        "事件循环策略就绪（Windows 需 Selector）",
        sys.platform != "win32" or policy_name == "WindowsSelectorEventLoopPolicy",
        f"platform={sys.platform} policy={policy_name}",
    )


def _app_password() -> str:
    """从 DSN 里取出口令，仅用于**负向断言**（确认它没出现在任何输出里）。"""
    body = dsn().split("://", 1)[-1]
    if "@" in body and ":" in body.split("@", 1)[0]:
        return body.split("@", 1)[0].split(":", 1)[1]
    return ""


# ---------------------------------------------------------------------------
# [B] 生命周期 + setup 幂等
# ---------------------------------------------------------------------------


async def phase_b() -> None:
    print("\n[B] setup 幂等 / 生命周期")
    from app.checkpoint import build_checkpoint_store

    s = _settings_with_pg()

    store1 = build_checkpoint_store(s)
    await store1.open()
    check("第 1 次 open 成功", store1.is_open is True)
    tables = _checkpoint_tables()
    check(
        "setup() 建出 checkpoint 表",
        {"checkpoints", "checkpoint_writes", "checkpoint_blobs"} <= set(tables),
        ",".join(sorted(tables)),
    )
    await store1.close()
    check("close() 后 is_open=False", store1.is_open is False)

    store2 = build_checkpoint_store(s)
    await store2.open()
    check("第 2 次 open 成功（setup 幂等）", store2.is_open is True)
    await store2.close()

    await store2.close()  # 幂等关闭
    check("close() 重复调用不抛异常", True)


def _checkpoint_tables() -> list[str]:
    with psycopg.connect(dsn(), connect_timeout=8) as c, c.cursor() as cur:
        cur.execute(
            "select tablename from pg_tables where schemaname='public' order by tablename"
        )
        return [row[0] for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# [C] 双进程：写 → 新进程读回 → 继续追加
# ---------------------------------------------------------------------------


async def phase_c_write() -> None:
    print(f"\n[C-写] 进程 A：写入 thread_id={THREAD_ID}")
    from langchain_core.messages import HumanMessage

    from app.checkpoint import build_checkpoint_store
    from app.graphs.chat_graph import build_chat_graph

    store = build_checkpoint_store(_settings_with_pg())
    saver = await store.open()
    try:
        graph = build_chat_graph(checkpointer=saver, use_llm=False)
        config = {"configurable": {"thread_id": THREAD_ID}}
        before = await graph.aget_state(config)
        check(
            "首次进入时该会话无历史（干净起点）",
            len(before.values.get("messages", [])) == 0,
            f"messages={len(before.values.get('messages', []))}",
        )

        await graph.ainvoke(
            {
                "messages": [HumanMessage(content="PG checkpointer 验收：进程 A 的第 1 轮")],
                "scene": 1,
                "reply": "",
                "model": "",
                "tokens_in": 0,
                "tokens_out": 0,
            },
            config=config,
        )
        state = await graph.aget_state(config)
        count = len(state.values.get("messages", []))
        check("写入后历史为 2 条（1 human + 1 ai）", count == 2, f"messages={count}")
        print("  → 进程 A 结束。请另起一个**全新进程**执行 --phase read")
    finally:
        await store.close()


async def phase_c_read() -> None:
    print(f"\n[C-读] 进程 B（全新进程）：读回并继续追加 thread_id={THREAD_ID}")
    from langchain_core.messages import HumanMessage

    from app.checkpoint import build_checkpoint_store
    from app.graphs.chat_graph import build_chat_graph

    store = build_checkpoint_store(_settings_with_pg())
    saver = await store.open()
    try:
        graph = build_chat_graph(checkpointer=saver, use_llm=False)
        config = {"configurable": {"thread_id": THREAD_ID}}

        state = await graph.aget_state(config)
        messages = state.values.get("messages", [])
        check(
            "新进程能读回进程 A 写入的历史（跨进程恢复）",
            len(messages) == 2,
            f"messages={len(messages)}",
        )
        if messages:
            check(
                "读回内容与写入一致（不是空壳状态）",
                "进程 A 的第 1 轮" in str(messages[0].content),
                str(messages[0].content)[:40],
            )

        await graph.ainvoke(
            {
                "messages": [HumanMessage(content="PG checkpointer 验收：进程 B 的第 2 轮")],
                "scene": 1,
                "reply": "",
                "model": "",
                "tokens_in": 0,
                "tokens_out": 0,
            },
            config=config,
        )
        state2 = await graph.aget_state(config)
        count = len(state2.values.get("messages", []))
        check("在恢复的状态上继续追加成功（累计 4 条）", count == 4, f"messages={count}")
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# [D] 负向权限（越权必须被拒）
# ---------------------------------------------------------------------------


def phase_d() -> None:
    print("\n[D] 负向权限（应用角色越权必须被拒）")

    def denied(name: str, statement: str, *, dbname: str | None = None) -> None:
        try:
            with psycopg.connect(dsn() if dbname is None else _dsn_with_db(dbname),
                                 connect_timeout=8, autocommit=True) as c, c.cursor() as cur:
                cur.execute(statement)
            check(name, False, "未被拒绝（权限过宽）")
        except psycopg.Error as exc:
            check(name, True, type(exc).__name__)

    denied("越权建库被拒（NOCREATEDB）", "CREATE DATABASE dkd_should_not_exist")
    denied("越权建角色被拒（NOCREATEROLE）", "CREATE ROLE dkd_should_not_exist")
    denied("越权改系统参数被拒（非超级用户）", "ALTER SYSTEM SET log_statement='all'")
    denied("安装非 trusted 扩展被拒（非超级用户）", "CREATE EXTENSION IF NOT EXISTS postgres_fdw")

    # ⚠️ 实测修正（2026-10-01）：原先想用 pg_trgm 当“建扩展被拒”的样本，结果**成功**了。
    # 原因不是权限过宽，而是 PostgreSQL 13+ 的 trusted extension 机制：
    # **库 owner 可以安装 trusted 扩展**（pg_trgm 属此类），这是 owner 形态的固有含义。
    # 因此这里不把它判为 FAIL，而是作为**已知且已接受的边界**显式记录：
    # 若业务上不能接受“应用角色能装 trusted 扩展”，授权方式必须改成评审稿「方案 2」
    # （DBA 预建表 + 只授 DML，角色不是 owner）。
    trusted_ok = _try_install("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    check(
        "已记录边界：库 owner 可安装 trusted 扩展（pg_trgm）",
        True,
        "成功=库 owner 形态固有；要禁止则需改用方案 2" if trusted_ok else "本次被拒（环境差异）",
    )

    # 在**别的库**（postgres）里建表：证明该角色不能把自己的对象撒到自有库之外
    denied("在别的库建表被拒", "CREATE TABLE dkd_should_not_exist(id int)", dbname="postgres")
    check(
        "确认该角色没有超级用户属性",
        not _role_flag("rolsuper") and not _role_flag("rolcreatedb") and not _role_flag("rolcreaterole"),
        "rolsuper/rolcreatedb/rolcreaterole 均为 false",
    )


def _try_install(statement: str) -> bool:
    """尝试执行一条语句，返回是否成功（用于记录“允许的边界”，不做 PASS/FAIL 判定）。"""
    try:
        with psycopg.connect(dsn(), connect_timeout=8, autocommit=True) as c, c.cursor() as cur:
            cur.execute(statement)
        return True
    except psycopg.Error:
        return False


def _role_name() -> str:
    return dsn().split("://", 1)[-1].split("@", 1)[0].split(":", 1)[0]


def _dsn_with_db(dbname: str) -> str:
    """把 DSN 的库名换掉（用于「在别的库建表」这类越权探测）；查询参数丢弃。"""
    scheme, _, rest = dsn().partition("://")
    creds, _, hostdb = rest.partition("@")
    hostport, _, _db = hostdb.partition("/")
    return f"{scheme}://{creds}@{hostport}/{dbname}"


def _role_flag(column: str) -> bool:
    with psycopg.connect(_super_dsn(), connect_timeout=8) as c, c.cursor() as cur:
        cur.execute(sql.SQL("select {} from pg_roles where rolname=%s").format(sql.Identifier(column)),
                    (_role_name(),))
        row = cur.fetchone()
        return bool(row and row[0])


def _super_dsn() -> str:
    password = os.environ.get("DKD_PG_SUPERUSER_PASSWORD", "")
    if not password:
        raise SystemExit("缺少 DKD_PG_SUPERUSER_PASSWORD（[D]/[E] 段需要超级用户）")
    user = os.environ.get("DKD_PG_SUPERUSER", "postgres")
    body = dsn().split("://", 1)[-1]
    hostport_db = body.split("@", 1)[-1]
    return f"postgresql://{user}:{password}@{hostport_db}"


# ---------------------------------------------------------------------------
# [E] 评审稿 §8-4 的未验证假设：只授 DML 时 setup() 还能不能过
# ---------------------------------------------------------------------------


async def phase_e() -> None:
    print("\n[E] 探针：只授 DML（无 CREATE）的账号跑 setup() 会怎样（评审稿 §8-4 的未验证假设）")
    from app.checkpoint import PostgresCheckpointStore

    probe_pwd = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(24))
    dbname = dsn().split("://", 1)[-1].split("@", 1)[-1].split("/", 1)[-1].split("?", 1)[0]
    # PG 的中文错误消息在本机 Windows 控制台会被按 GBK 解码成乱码；显式要 UTF-8
    # （否则最关键的「权限不足」四个字恰好是看不懂的那四个）
    os.environ.setdefault("PGCLIENTENCODING", "UTF8")

    with psycopg.connect(_super_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute("select 1 from pg_roles where rolname=%s", (PROBE_ROLE,))
        if cur.fetchone():
            cur.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(PROBE_ROLE)))
            cur.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(PROBE_ROLE)))
        cur.execute(sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
            sql.Identifier(PROBE_ROLE), sql.Literal(probe_pwd)))

    try:
        with psycopg.connect(_super_dsn_for_db(dbname), autocommit=True) as c, c.cursor() as cur:
            # ⚠️ CONNECT 必须显式授：create_agent_pg_role.sql 第 3 步做了
            # `REVOKE ALL ON DATABASE ... FROM PUBLIC`，因此**任何**其他角色都连不上这个库。
            # 这是有意为之的收紧（等价于 MySQL 侧只授 127.0.0.1 的做法），
            # 代价是新增使用者时必须显式 GRANT CONNECT —— 本行就是实例。
            cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(dbname), sql.Identifier(PROBE_ROLE)))
            # 只授「已有对象」的 DML；**不给** CREATE（这正是评审稿「方案 2」的形态）
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {} ").format(sql.Identifier(PROBE_ROLE)))
            cur.execute(sql.SQL(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}"
            ).format(sql.Identifier(PROBE_ROLE)))
            # 反向确认：探针角色确实没有 CREATE 权限
            cur.execute(
                "select has_schema_privilege(%s, 'public', 'CREATE')", (PROBE_ROLE,)
            )
            has_create = bool(cur.fetchone()[0])

        probe_dsn = f"postgresql://{PROBE_ROLE}:{probe_pwd}@{dbname_hostport()}/{dbname}"
        check("探针角色已确认无 CREATE 权限", has_create is False, f"has_schema_privilege(CREATE)={has_create}")

        store = PostgresCheckpointStore(probe_dsn)
        error: str | None = None
        try:
            await store.open()
            await store.close()
        except Exception as exc:  # noqa: BLE001 —— 这里就是要看它到底能不能过
            error = f"{type(exc).__name__}: {exc}"

        check(
            "只授 DML 的账号能跑 setup()（方案 2 可行）",
            error is None,
            error or "setup() 幂等通过",
        )
        if error is not None:
            print(
                "  → 结论：**方案 2 不可行**，必须用方案 1（角色拥有独立库）。"
                " 请把本结论回写 create_agent_pg_role.sql 的「第 4 步」与评审稿 §8-4。"
            )
    finally:
        with psycopg.connect(_super_dsn(), autocommit=True) as c, c.cursor() as cur:
            cur.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(PROBE_ROLE)))
            cur.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(PROBE_ROLE)))
        print(f"  → 已回收探针角色 {PROBE_ROLE}")


def dbname_hostport() -> str:
    return dsn().split("://", 1)[-1].split("@", 1)[-1].split("/", 1)[0]


def _super_dsn_for_db(dbname: str) -> str:
    return _super_dsn().rsplit("/", 1)[0] + "/" + dbname


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


async def phase_cleanup() -> None:
    print(f"\n[清理] 删除验收会话 thread_id={THREAD_ID}（不删库、不改授权）")
    with psycopg.connect(dsn(), connect_timeout=8, autocommit=True) as c, c.cursor() as cur:
        cur.execute("delete from checkpoint_writes where thread_id=%s", (THREAD_ID,))
        w = cur.rowcount
        cur.execute("delete from checkpoint_blobs where thread_id=%s", (THREAD_ID,))
        b = cur.rowcount
        cur.execute("delete from checkpoints where thread_id=%s", (THREAD_ID,))
        cp = cur.rowcount
        print(f"  → checkpoint_writes={w} / checkpoint_blobs={b} / checkpoints={cp}")
    check("验收会话数据已清理", True)


# ---------------------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser(description="PG checkpointer 端到端验收")
    parser.add_argument(
        "--phase",
        default="all",
        choices=["all", "a", "b", "write", "read", "d", "e", "cleanup"],
        help="all=只跑 A/B/D（不跨进程）；跨进程必须分两次跑 write/read",
    )
    args = parser.parse_args()

    if args.phase in ("all", "a"):
        phase_a()
    if args.phase in ("all", "b"):
        await phase_b()
    if args.phase in ("all", "d"):
        phase_d()
    if args.phase == "write":
        await phase_c_write()
    if args.phase == "read":
        await phase_c_read()
    if args.phase == "e":
        await phase_e()
    if args.phase == "cleanup":
        await phase_cleanup()

    print(f"\n结果：PASS={_passed} FAIL={_failed}")
    return 1 if _failed else 0


def _bootstrap_loop_policy() -> None:
    """**必须在 `asyncio.run()` 之前调用**：Windows + pg 后端要求 SelectorEventLoop。

    为什么不能放在某个 phase 里：那时 loop 已用 Proactor 建好，再改策略也来不及——
    实测现象是 `[A]` 段全 PASS，`[B]` 段第一行就抛 psycopg.InterfaceError。
    这与 `app/main.py` 把调用放在模块级是同一个原因。
    """
    from app.checkpoint import ensure_windows_event_loop_policy
    from app.config import Settings

    ensure_windows_event_loop_policy(
        Settings(DKD_AGENT_CHECKPOINT_BACKEND="postgres", DKD_AGENT_PG_DSN=dsn())
    )


if __name__ == "__main__":
    # 顺序要紧：先定策略，再建事件循环
    _bootstrap_loop_policy()
    sys.exit(asyncio.run(main()))
