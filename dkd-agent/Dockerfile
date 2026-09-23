# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# dkd-agent 镜像（Python 3.11 / FastAPI / LangGraph / uv 锁版本）
#
# 为什么用 uv 官方镜像 + `uv sync --frozen`：
#   方案 V1.1 与排期 0-2 要求「锁定精确版本三元组」；`--frozen` 保证镜像里的依赖
#   与 `uv.lock` 逐字一致（不用 pip 重新求解，避免 langgraph 之类库的漂移）。
# 用法：
#   docker build -t dkd/dkd-agent:local --build-arg UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple dkd-agent
#
# ⚠️ 该容器**只在内网/容器网络可达**，不得发布到宿主机公网端口：
#    它不解析 JWT，只信任 Java 网关注入的 `X-Agent-*` 头（方案 §3.4），
#    一旦直接暴露，任何人伪造请求头即可冒充任意用户（AGENTS §7.5）。
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

ARG UV_INDEX_URL

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    TZ=Asia/Shanghai

WORKDIR /app

# 依赖层与源码层分开：只改 app/ 时不会重装依赖
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "$UV_INDEX_URL" ]; then export UV_DEFAULT_INDEX="$UV_INDEX_URL"; fi; \
    uv sync --frozen --no-dev --no-install-project

COPY app ./app
# var/ 放 SQLite checkpoint（会话状态）——必须挂卷，否则容器重建即丢会话（0-10 的运维要求）
RUN mkdir -p /app/var

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8090/health',timeout=3).read()" || exit 1

# 容器内监听 0.0.0.0 是**必需**的（Java 容器要从 docker 网络访问它），
# 安全性由「不发布端口 + 只在 compose 网络内」保证，而不是靠绑回环。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
