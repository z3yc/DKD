"""智能体取数评测流水线（排期 5-1 / 5-2）。

## 设计原则（先读，避免把“假绿”当质量）

1. **只有 `status=verified` 的用例参与准确率分母**；`draft`/`example-only` 只做结构校验，
   绝不拿来算准确率或对外汇报（见 docs/agent-eval-set-2026-11-04.md §1）。
2. 没有 verified 用例时输出 **BLOCKED（退出码 2）**，而不是 0% / 100%：
   “未验到”与“失败/通过”必须分开（AGENTS §10）。
3. `--offline`（默认）不调 LLM，只校验评测集结构与字段一致性，供 CI 每次 prompt/配置改动跑；
   `--live` 才真实调用 `/agent/chat`（消耗 token），只在人工/定时任务使用。
4. 报告 JSON 可附到配置版本历史（`--post-report` + `DKD_EVAL_JWT`），Java 侧据此做保存护栏。

用法：
    python docs/scripts/run-agent-eval.py --offline --out docs/agent-eval-set/reports/eval-offline.json
    python docs/scripts/run-agent-eval.py --live --config-code chat --out .run/eval-live.json
    python docs/scripts/run-agent-eval.py --live --post-report http://127.0.0.1:8080/manage/agent/eval/report

退出码：0=通过（或有 verified 但未回归）；1=回归（低于基线/有失败）；2=没有可评分用例/前置未就绪。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_CASES = Path("docs/agent-eval-set/starter-cases.json")
REQUIRED_CASE_FIELDS = (
    "id", "question", "scene", "expected_metric", "expected_tables",
    "expected_params", "expected_answer_type", "must_refuse", "tags", "status",
)
VALID_SCENES = {1, 2, 3, 4}
VALID_STATUS = {"draft", "verified", "retired", "example-only"}
VALID_ANSWER_TYPES = {"value", "rank", "ratio", "evidence", "refuse"}


def load_cases(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sys.exit(f"评测集读取失败：{path} ({type(exc).__name__})")
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        sys.exit("评测集格式错误：缺少 cases 数组")
    return payload


def validate_case(case: dict[str, Any]) -> list[str]:
    """结构校验：返回问题列表（空=通过）。"""
    problems: list[str] = []
    for field in REQUIRED_CASE_FIELDS:
        if field not in case:
            problems.append(f"缺少字段 {field}")
    if case.get("scene") not in VALID_SCENES:
        problems.append(f"scene 非法：{case.get('scene')!r}")
    if case.get("status") not in VALID_STATUS:
        problems.append(f"status 非法：{case.get('status')!r}")
    if case.get("expected_answer_type") not in VALID_ANSWER_TYPES:
        problems.append("expected_answer_type 非法")
    tables = case.get("expected_tables")
    if not isinstance(tables, list) or any(not str(t).startswith("tb_") for t in tables):
        problems.append("expected_tables 必须是非空 tb_* 列表")
    if not str(case.get("expected_metric") or "").strip():
        problems.append("expected_metric 为空")
    if case.get("must_refuse") and case.get("expected_answer_type") != "refuse":
        problems.append("must_refuse=true 时 expected_answer_type 必须是 refuse")
    if case.get("expected_answer_type") == "refuse" and not case.get("must_refuse"):
        problems.append("refuse 类型必须 must_refuse=true")
    return problems


def _http_json(url: str, payload: dict[str, Any], token: str | None) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 —— 本地/内网显式 URL
        return json.loads(resp.read().decode("utf-8"))


def _chat_once(base: str, token: str, question: str, scene: int) -> str:
    """调用 Java 网关的 /agent/chat（SSE），返回拼接后的回复文本。"""
    body = json.dumps({"message": question, "scene": scene}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base.rstrip("/") + "/agent/chat", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    text = ""
    with urllib.request.urlopen(req, timeout=180) as resp:  # noqa: S310
        event = None
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                try:
                    data = json.loads(line.split(":", 1)[1].strip())
                except ValueError:
                    continue
                if event == "delta":
                    text += str(data.get("text") or "")
    return text


def _login(base: str) -> str:
    payload = json.dumps({"username": "admin", "password": "admin123"}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/login", data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
        token = json.loads(resp.read().decode("utf-8")).get("token")
    if not token:
        sys.exit("登录失败：未拿到 token")
    return token


def evaluate_live(case: dict[str, Any], reply: str) -> tuple[str, str | None]:
    """返回 (pass|fail|blocked, errorType)。没有 expected_value 的用例不评分。"""
    if case.get("must_refuse"):
        # 拒答不能靠 substring 否证，也没有统一措辞；没有人工标注的拒答语料前不评分。
        return "blocked", "refusal-not-scorable"
    expected = case.get("expected_value")
    if expected is None:
        return "blocked", "no-expected-value"
    try:
        actual = float(reply.replace(",", "").strip())
    except ValueError:
        return "fail", "answer-not-numeric"
    tolerance = case.get("tolerance")
    if tolerance is None:
        tolerance = 0.01 if isinstance(expected, float) else 0
    return ("pass" if abs(actual - float(expected)) <= float(tolerance) else "fail", None)


def build_report(
    payload: dict[str, Any], *, mode: str, live_replies: dict[str, str] | None = None
) -> dict[str, Any]:
    cases = payload.get("cases") or []
    verified = [c for c in cases if c.get("status") == "verified"]
    results: list[dict[str, Any]] = []
    scorable = passed = failed = blocked = 0
    error_types: dict[str, int] = {}
    by_tag: dict[str, dict[str, int]] = {}
    for case in cases:
        problems = validate_case(case)
        if problems:
            status, error = "fail", "schema-invalid"
        elif case.get("status") != "verified":
            status, error = "blocked", "not-verified"
        elif mode == "live":
            reply = (live_replies or {}).get(str(case["id"]), "")
            status, error = evaluate_live(case, reply)
        else:
            status, error = "blocked", "offline-mode"
        if status != "blocked":
            scorable += 1
            passed += status == "pass"
            failed += status == "fail"
        else:
            blocked += 1
        if error:
            error_types[error] = error_types.get(error, 0) + 1
        for tag in case.get("tags") or []:
            bucket = by_tag.setdefault(str(tag), {"pass": 0, "fail": 0, "blocked": 0})
            bucket[status] += 1
        results.append(
            {
                "id": case.get("id"),
                "scene": case.get("scene"),
                "status": status,
                "errorType": error,
                "problems": problems,
            }
        )
    accuracy = round(passed / scorable, 4) if scorable else None
    baseline = (payload.get("meta") or {}).get("baseline") or {}
    baseline_accuracy = baseline.get("accuracy")
    if scorable == 0:
        overall = "blocked"
    elif baseline_accuracy is not None and accuracy is not None and accuracy < float(baseline_accuracy):
        overall = "regressed"
    else:
        overall = "passed" if failed == 0 else "failed"
    return {
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "mode": mode,
        "evalSet": {
            "name": (payload.get("meta") or {}).get("name"),
            "version": (payload.get("meta") or {}).get("version"),
            "status": (payload.get("meta") or {}).get("status"),
        },
        "status": overall,
        "casesTotal": len(cases),
        "verified": len(verified),
        "scorable": scorable,
        "passed": passed,
        "failed": failed,
        "blocked": blocked,
        "accuracy": accuracy,
        "baselineAccuracy": baseline_accuracy,
        "baselineN": baseline.get("n"),
        "errorTypes": error_types,
        "byTag": by_tag,
        "cases": results,
    }


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 智能体取数评测报告",
        "",
        f"- 模式：`{report['mode']}`；状态：**{report['status']}**",
        f"- 用例：{report['casesTotal']}（verified {report['verified']}，可评分 {report['scorable']}）",
        f"- 通过/失败/阻塞：{report['passed']}/{report['failed']}/{report['blocked']}",
        f"- 准确率：{report['accuracy'] if report['accuracy'] is not None else '—（无可评分 verified 用例）'}",
        f"- 基线：{report['baselineAccuracy'] if report['baselineAccuracy'] is not None else '未设基线'}",
        "",
        "> 没有 verified 用例时**不能**把 blocked 当作 0%/100%；本报告不用于对外准确率口径。",
        "",
        "## 错误类型",
        "",
    ]
    if report["errorTypes"]:
        lines += ["| 类型 | 数量 |", "| --- | --- |"]
        lines += [f"| {k} | {v} |" for k, v in sorted(report["errorTypes"].items())]
    else:
        lines.append("无。")
    lines += ["", "## 逐条结果", "", "| 用例 | 场景 | 状态 | 错误类型 |", "| --- | --- | --- | --- |"]
    lines += [
        f"| {c['id']} | {c['scene']} | {c['status']} | {c['errorType'] or '—'} |"
        for c in report["cases"]
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--config-code", default="chat")
    parser.add_argument("--config-version", type=int, default=0)
    parser.add_argument("--out", default="")
    parser.add_argument("--post-report", default="")
    parser.add_argument("--token-env", default="DKD_EVAL_JWT")
    args = parser.parse_args()

    payload = load_cases(Path(args.cases))
    live_replies: dict[str, str] = {}
    if args.mode == "live":
        try:
            token = _login(args.base)
        except (urllib.error.URLError, OSError) as exc:
            print(f"BLOCKED：无法连接 {args.base}（{type(exc).__name__}）")
            return 2
        for case in payload.get("cases") or []:
            if case.get("status") != "verified":
                continue
            reply = _chat_once(args.base, token, str(case["question"]), int(case["scene"]))
            live_replies[str(case["id"])] = reply

    report = build_report(payload, mode=args.mode, live_replies=live_replies)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.out or f"docs/agent-eval-set/reports/eval-{args.mode}-{stamp}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(to_markdown(report), encoding="utf-8")
    print(f"report={out} status={report['status']} accuracy={report['accuracy']} "
          f"passed={report['passed']} failed={report['failed']} blocked={report['blocked']}")

    if args.post_report:
        token = os.environ.get(args.token_env)
        if not token:
            print(f"WARN：未设置 {args.token_env}，跳过上报评测报告")
        else:
            body = {
                "configCode": args.config_code,
                "configVersion": args.config_version,
                "mode": report["mode"],
                "status": "scored" if report["scorable"] else (
                    "failed" if report["failed"] else "blocked"
                ),
                "accuracy": report["accuracy"],
                "total": report["scorable"],
                "passed": report["passed"],
                "failed": report["failed"],
                "blocked": report["blocked"],
                "baselineAccuracy": report["baselineAccuracy"],
                "baselineN": report["baselineN"],
                "reportJson": json.dumps(report, ensure_ascii=False),
                "runBy": "eval-script",
            }
            try:
                resp = _http_json(args.post_report, body, token)
                print("POST_REPORT", resp.get("code"), resp.get("msg"))
            except (urllib.error.URLError, OSError) as exc:
                print(f"WARN：评测报告上报失败（{type(exc).__name__}），不阻断本次运行")

    if report["status"] == "regressed":
        return 1
    if report["scorable"] == 0:
        return 2
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
