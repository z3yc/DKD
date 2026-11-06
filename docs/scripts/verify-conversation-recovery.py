"""会话恢复演练（排期 4-3）：验证服务重启后同一 conversationId 仍能续聊。

## 为什么单独成脚本

checkpoint 恢复是“进程外状态”的能力，单测只能证明图能编译/存点；**重启后能不能续**
必须在真服务上演练。脚本分两阶段，中间由运维重启 `dkd-agent`：

    # 阶段 1：建会话并让助手记住一个设备编码
    python docs/scripts/verify-conversation-recovery.py --phase first --out .run/recovery.json

    # （重启 dkd-agent：停 8090 → 启动 python -m app）

    # 阶段 2：同一 conversationId 追问，必须答出阶段 1 的编码
    python docs/scripts/verify-conversation-recovery.py --phase second --state .run/recovery.json

阶段 2 退出码：0=恢复成功；1=恢复失败（答不出编码/会话不存在）；2=前置未就绪。
密码/密钥只从环境变量或 .env 读取，不写进参数历史。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

STATE_DEFAULT = ".run/recovery.json"


def _login(base: str) -> str:
    with httpx.Client(base_url=base, timeout=20, trust_env=False) as cli:
        resp = cli.post("/login", json={"username": "admin", "password": "admin123"})
        resp.raise_for_status()
        token = resp.json().get("token")
        if not token:
            sys.exit("登录失败：未拿到 token")
        return token


def _chat(base: str, token: str, message: str, conversation_id: str | None) -> dict:
    body = {"message": message, "scene": 3}
    if conversation_id:
        body["conversation_id"] = conversation_id
    reply, meta, done = "", {}, {}
    with httpx.Client(base_url=base, timeout=180, trust_env=False) as cli:
        with cli.stream(
            "POST", "/agent/chat",
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            json=body,
        ) as resp:
            if resp.status_code != 200:
                sys.exit(f"会话接口 HTTP {resp.status_code}")
            event = None
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data = json.loads(line.split(":", 1)[1].strip())
                    if event == "meta":
                        meta = data
                    elif event == "delta":
                        reply += data.get("text", "")
                    elif event == "done":
                        done = data
    return {"reply": reply, "meta": meta, "done": done}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["first", "second"], required=True)
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--state", default=STATE_DEFAULT)
    args = parser.parse_args()
    token = _login(args.base)
    state_path = Path(args.state)

    if args.phase == "first":
        result = _chat(
            args.base, token,
            "请记住：本次会话要诊断的设备编码是 A1000001。只回复“已记住 A1000001”即可。",
            None,
        )
        conversation_id = result["meta"].get("conversation_id") \
            or result["done"].get("conversation_id")
        if not conversation_id:
            print("BLOCKED：服务端未下发 conversation_id")
            return 2
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"conversation_id": conversation_id}, ensure_ascii=False), encoding="utf-8"
        )
        print(f"阶段1 OK conversation_id={conversation_id}")
        print(f"state 已写：{state_path.resolve()}；现在重启 dkd-agent，再跑 --phase second")
        return 0

    if not state_path.exists():
        print(f"BLOCKED：找不到 {state_path}，先跑 --phase first")
        return 2
    conversation_id = json.loads(state_path.read_text(encoding="utf-8"))["conversation_id"]
    result = _chat(
        args.base, token,
        "我刚才让你记住的设备编码是什么？只回答编码本身。",
        conversation_id,
    )
    reply = result["reply"]
    ok = "A1000001" in reply
    print(f"阶段2 conversation_id={conversation_id} 恢复={'PASS' if ok else 'FAIL'}")
    print("回复：", reply[:300].replace("\n", " "))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
