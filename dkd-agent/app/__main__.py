"""统一启动入口：`uv run python -m app`（也可 `python -m app`）。

## 为什么需要这个入口，而不是直接用 `uvicorn app.main:app`

**uvicorn 0.36+ 换了机制**：它不再改 asyncio 的全局事件循环策略，而是用
`loop_factory` **直接把事件循环类交给** `asyncio.Runner`（实测 uvicorn 0.53：

    LOOP_FACTORIES = {'none': None, 'auto': 'uvicorn.loops.auto:auto_loop_factory', ...}
    uvicorn.loops.asyncio.asyncio_loop_factory -> sys.platform == 'win32' 时返回 ProactorEventLoop

而 psycopg 的异步模式**不支持 ProactorEventLoop**。因此：

- 直接 `uvicorn app.main:app` 在 Windows 上 + `BACKEND=postgres` 会启动失败，
  报错是 `psycopg.InterfaceError: cannot use the 'ProactorEventLoop'` —— 指向 psycopg 内部，
  与真正的原因（事件循环机制变了）差得很远（本轮实测踩过，见排期 V2.9 补记）。
- 解决办法是本入口用 `loop="none"` 让 uvicorn 回到“**按全局策略建循环**”，
  并在建循环**之前**把策略设成 `SelectorEventLoop`。

linux/容器里本入口同样可用（策略设置是 no-op，`loop="none"` 等价于按默认策略建 Selector 循环）。
"""

from __future__ import annotations


def main() -> None:
    from app.checkpoint import ensure_windows_event_loop_policy
    from app.config import get_settings

    settings = get_settings()
    # 顺序要紧：先定策略，再建事件循环（uvicorn.run 内部才建）
    ensure_windows_event_loop_policy(settings)

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        # "none" = 不指定 loop_factory，交由全局策略决定（见模块 docstring）
        loop="none",
    )


if __name__ == "__main__":
    main()
