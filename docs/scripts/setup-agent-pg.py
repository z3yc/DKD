"""PG 角色与独立库的一次性初始化（`docs/ddl/create_agent_pg_role.sql` 的可执行等价物）。

## 为什么要有这个脚本，而不是只留 .sql

实测（2026-09-23 / 2026-10-01）：本机 **`psql` 不在 PATH**（未纳入开发链），
而 `.sql` 里用了 `\\connect` 与 `:'变量'` 这些 psql 元命令，没有 psql 就跑不了。
两者关系：**`.sql` 是评审与回滚的权威文本，本脚本是同一套语句的可执行实现**；
改授权策略时两边必须同步（脚本头部也标注了这一点）。

## 为什么口令由脚本生成、只写 .env

AGENTS §7.1：口令**不得**入仓、不得出现在模板里。所以：
- 超级用户口令从环境变量读（`DKD_PG_SUPERUSER_PASSWORD`），不进命令行历史；
- 应用角色口令由脚本随机生成，并提示写入 `.env` 的 `DKD_AGENT_PG_DSN`。
  不复用超级用户口令：同一口令挂两个账号会把“最小权限”的意义抹掉。

## 用法

```bash
cd dkd-agent
export DKD_PG_SUPERUSER_PASSWORD="<超级用户口令>"
uv run python ../docs/scripts/setup-agent-pg.py            # 建角色 + 建库 + 收紧 public
uv run python ../docs/scripts/setup-agent-pg.py --rotate   # 只轮换应用角色口令
```

执行后请把输出里的 DSN 写入 **本机 `.env`**（已 gitignore）。
"""

from __future__ import annotations

import argparse
import os
import secrets
import string
import sys
from pathlib import Path

import psycopg
from psycopg import sql

ROLE = "dkd_agent_pg"
DB = "dkd_agent_state"
HOST = os.environ.get("DKD_PG_HOST", "127.0.0.1")
PORT = int(os.environ.get("DKD_PG_PORT", "5432"))
SUPERUSER = os.environ.get("DKD_PG_SUPERUSER", "postgres")


def super_dsn(dbname: str = "postgres") -> str:
    password = os.environ.get("DKD_PG_SUPERUSER_PASSWORD", "")
    if not password:
        raise SystemExit(
            "缺少 DKD_PG_SUPERUSER_PASSWORD（不要写进命令行或入仓文件，用环境变量注入）"
        )
    return f"postgresql://{SUPERUSER}:{password}@{HOST}:{PORT}/{dbname}"


def new_password() -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(28))


def setup(rotate_only: bool, app_password: str | None) -> int:
    pwd = app_password or new_password()
    password_changed = False

    with psycopg.connect(super_dsn(), autocommit=True) as c, c.cursor() as cur:
        cur.execute("select 1 from pg_roles where rolname=%s", (ROLE,))
        role_exists = cur.fetchone() is not None
        cur.execute("select 1 from pg_database where datname=%s", (DB,))
        db_exists = cur.fetchone() is not None

        # 为什么用 sql.Literal 而不是 %s：CREATE/ALTER ROLE 是 utility 语句，
        # 不接受绑定参数（实测报语法错误），口令只能安全内联。
        if role_exists:
            if not rotate_only:
                print(f"[=] 角色 {ROLE} 已存在，跳过创建")
        else:
            if rotate_only:
                raise SystemExit(f"角色 {ROLE} 不存在，--rotate 无从轮换；先去掉 --rotate 执行一次")
            cur.execute(
                sql.SQL(
                    "CREATE ROLE {} WITH LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOINHERIT CONNECTION LIMIT 20"
                ).format(sql.Identifier(ROLE), sql.Literal(pwd))
            )
            print(f"[+] 已创建角色 {ROLE}")

        if rotate_only or not role_exists:
            cur.execute(
                sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                    sql.Identifier(ROLE), sql.Literal(pwd)
                )
            )
            password_changed = True
            print("[*] 已设置/轮换应用角色口令")

        cur.execute(
            sql.SQL("COMMENT ON ROLE {} IS {}").format(
                sql.Identifier(ROLE),
                sql.Literal(
                    "DKD 智能体 checkpoint 专用角色；只承载会话状态，绝不是业务数据通道（AGENTS §7.3）"
                ),
            )
        )

        if not db_exists:
            # 独立库 + owner = 应用角色：`AsyncPostgresSaver.setup()` 需要 CREATE 权限，
            # 而实测（2026-10-01）"只授 DML、不给 CREATE" 会让 setup() 直接失败
            # （InsufficientPrivilege: 对模式 public 权限不够）。见 create_agent_pg_role.sql。
            cur.execute(
                sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0").format(
                    sql.Identifier(DB), sql.Identifier(ROLE)
                )
            )
            print(f"[+] 已创建数据库 {DB}（owner={ROLE}）")
            cur.execute(
                sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                    sql.Identifier(DB),
                    sql.Literal("智能体会话 checkpoint 存储（LangGraph AsyncPostgresSaver）；不存放业务数据"),
                )
            )
        else:
            print(f"[=] 数据库 {DB} 已存在，跳过创建")

    with psycopg.connect(super_dsn(DB), autocommit=True) as c, c.cursor() as cur:
        # 收紧 public schema；注意这会连带收走**任何**其他角色的 CONNECT（等价于
        # MySQL 侧只授 127.0.0.1 的做法），新增使用者必须显式 GRANT CONNECT。
        cur.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        cur.execute(sql.SQL("GRANT ALL ON SCHEMA public TO {}").format(sql.Identifier(ROLE)))
        cur.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(DB)))
        print("[*] 已收紧 public schema 并收走 database 的 PUBLIC 权限")

    # ⚠️ 只在口令**真的变了**的时候才输出 DSN：
    # 本脚本每次都会生成一个新随机口令，若在“角色已存在、未轮换”的重复执行里照样打印，
    # 使用者会把一个**从未生效**的口令写进 .env，然后对着 1045 类报错查半天。
    if password_changed:
        print("\n把下面这行写入**本机 .env**（已 gitignore；不要提交）：")
        print(f"DKD_AGENT_PG_DSN=postgresql://{ROLE}:{pwd}@{HOST}:{PORT}/{DB}")
    else:
        print(
            "\n口令未变更（角色已存在且未指定 --rotate），.env 里的 DKD_AGENT_PG_DSN 无需更新；"
            "如需轮换请加 --rotate"
        )
    print("\n之后执行验收：uv run python ../docs/scripts/verify-checkpoint-pg.py --phase all")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PG 角色与独立库初始化（create_agent_pg_role.sql 的可执行等价物）")
    parser.add_argument("--rotate", action="store_true", help="只轮换应用角色口令")
    parser.add_argument(
        "--app-password",
        default=None,
        help="显式指定应用角色口令（默认随机生成；不建议，会把口令带进命令行历史）",
    )
    args = parser.parse_args()
    if not Path(__file__).exists():  # pragma: no cover - 防御性
        return 1
    return setup(rotate_only=args.rotate, app_password=args.app_password)


if __name__ == "__main__":
    sys.exit(main())
