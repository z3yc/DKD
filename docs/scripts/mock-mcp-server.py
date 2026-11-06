"""本地 mock MCP server（排期 3-7 验收用；只读工具/写工具/超时/截断四类场景）。

## 为什么需要它

真实 MCP server 清单还没提供，但“只接只读工具、超时/行数上限、密钥只按 env 名”这些
**安全闸必须在真 HTTP 链路上验证**，不能只靠单测打桩。本脚本用标准库起一个最小
JSON-RPC over HTTP server：

- `read_machine_manual`：readOnlyHint=true，返回手册片段（可带 rows 参数制造超长结果）；
- `reset_device`：readOnlyHint=false（**写工具，客户端必须拒绝同步/调用**）；
- `no_hint_tool`：没有 annotations（客户端必须按“非只读”拒绝）；
- 带 `slow=true` 参数时睡 6 秒（验证客户端 5s 超时）；
- 可选 `--token`：要求请求头 `X-Test-Token` 等于它（验证 env_keys_json → 请求头解析）。

用法：
    python docs/scripts/mock-mcp-server.py --port 19090 --token test-token

仅本机联调用，**不是生产代码**；不写库、不读业务数据。
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

READ_TOOL = {
    "name": "read_machine_manual",
    "description": "查询售货机故障码手册片段（mock，只读）",
    "annotations": {"readOnlyHint": True},
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "故障码/现象"},
            "rows": {"type": "integer", "description": "制造超长结果的行数（默认 1）"},
        },
        "required": ["query"],
    },
}
WRITE_TOOL = {
    "name": "reset_device",
    "description": "复位设备（mock，写工具；客户端必须拒绝）",
    "annotations": {"readOnlyHint": False},
    "inputSchema": {"type": "object", "properties": {"innerCode": {"type": "string"}}},
}
NO_HINT_TOOL = {
    "name": "no_hint_tool",
    "description": "没有 readOnlyHint 的工具（客户端必须按非只读拒绝）",
    "inputSchema": {"type": "object", "properties": {}},
}
TOOLS = [READ_TOOL, WRITE_TOOL, NO_HINT_TOOL]

TOKEN = ""


def _result(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: ANN002, ANN003 —— 基类签名
        print("[mock-mcp]", fmt % args, flush=True)

    def do_POST(self):  # noqa: N802 —— BaseHTTPRequestHandler 约定
        if self.path.rstrip("/") != "/mcp":
            self._send(404, {"error": "not found"})
            return
        if TOKEN and self.headers.get("X-Test-Token") != TOKEN:
            self._send(401, {"error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        req_id = body.get("id")
        method = body.get("method")
        params = body.get("params") or {}
        if method == "initialize":
            self._send(200, _result(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock-mcp", "version": "0.1"},
            }))
            return
        if method == "tools/list":
            self._send(200, _result(req_id, {"tools": TOOLS}))
            return
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            if name in ("reset_device", "no_hint_tool"):
                self._send(200, _result(req_id, {
                    "isError": True,
                    "content": [{"type": "text", "text": "写工具/未声明只读的工具禁止调用"}],
                }))
                return
            if name != "read_machine_manual":
                self._send(200, _error(req_id, -32601, f"unknown tool: {name}"))
                return
            if args.get("slow"):
                time.sleep(6)
            rows = max(1, int(args.get("rows") or 1))
            text = "\n".join(
                f"E-777 制冷压缩机过载（mock 依据行 {i + 1}）" for i in range(rows)
            )
            self._send(200, _result(req_id, {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            }))
            return
        self._send(200, _error(req_id, -32601, f"unknown method: {method}"))

    def _send(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    global TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=19090)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    TOKEN = args.token
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mock MCP server on http://127.0.0.1:{args.port}/mcp token={'yes' if TOKEN else 'no'}",
          flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
