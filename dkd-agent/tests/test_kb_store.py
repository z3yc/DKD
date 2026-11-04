"""知识库 L2 存储/切片/本地向量检索测试（排期 3-6）。

不连真库（AGENTS §8）：全部用假 `AsyncSession`（记录 SQL 与参数、按调用序返回预置行）
与 `TestClient + dependency_overrides`。断言分三类：

1. **算法口径**：切片长度/重叠/归一化、向量确定性与 L2 归一化、cosine 边界；
2. **数据纪律**：ingest 幂等（同 hash 跳过）、切片 upsert、多余旧切片只 `enabled=0`、
   通篇无 DELETE、SQL 只参数化（注入串只出现在 params 里）；
3. **检索回退**：命中 / low_score / no_hit、检索留痕（hits_json 只存摘要）、
   enabled+del_flag 过滤、mark_used 回写。
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import kb
from app.config import get_settings
from app.services import kb_store
from app.services.kb_store import (
    CANDIDATE_LIMIT,
    DEFAULT_DIM,
    KbNotFoundError,
    KbValidationError,
    chunk_text,
    cosine,
    ingest_document,
    list_documents,
    mark_used,
    search,
    set_enabled,
    stats,
    vectorize,
)

TEST_SECRET = "unit-test-secret"
AUTH_HEADERS = {"X-Agent-Secret": TEST_SECRET}


@pytest.fixture(autouse=True)
def _kb_env(monkeypatch) -> Iterator[None]:
    """本文件需要服务间密钥（/agents/** 校验）；配置缓存要清，避免读到别的用例的设置。"""
    monkeypatch.setenv("DKD_AGENT_SERVICE_SECRET", TEST_SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ====================================================================================
# 假会话：记录 SQL / 参数，按调用序返回预置结果（与 test_restock_plan_store.py 同风格）
# ====================================================================================
class FakeResult:
    def __init__(
        self, rows: list[dict[str, Any]] | None = None, rowcount: int = 0, lastrowid: int = 0
    ) -> None:
        self._rows = rows or []
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def mappings(self) -> FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class FakeSession:
    def __init__(
        self,
        rows_by_call: list[list[dict[str, Any]]] | None = None,
        rowcount_by_call: list[int] | None = None,
        lastrowid_by_call: list[int] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0
        self._rows_by_call = rows_by_call or []
        self._rowcount_by_call = rowcount_by_call or []
        self._lastrowid_by_call = lastrowid_by_call or []
        self._error = error

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        if self._error is not None:
            raise self._error
        index = len(self.calls)
        self.calls.append((str(stmt), dict(params or {})))
        rows = self._rows_by_call[index] if index < len(self._rows_by_call) else []
        rowcount = self._rowcount_by_call[index] if index < len(self._rowcount_by_call) else 0
        lastrowid = self._lastrowid_by_call[index] if index < len(self._lastrowid_by_call) else 0
        return FakeResult(rows, rowcount=rowcount, lastrowid=lastrowid)

    async def commit(self) -> None:
        self.commits += 1


def _sql_text(session: FakeSession) -> str:
    return " ".join(sql for sql, _ in session.calls).lower()


def _chunk_row(
    chunk_id: int, content: str, *, doc_code: str = "restock_manual", title: str = "补货手册"
) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "content": content,
        "vector_json": json.dumps(vectorize(content), ensure_ascii=False),
        "doc_code": doc_code,
        "title": title,
    }


# ====================================================================================
# 1. 切片：句界优先、长度上限、重叠、空白归一化、1MB 限制
# ====================================================================================
def test_chunk_text_prefers_sentence_boundaries_and_keeps_overlap():
    text = "".join(f"第{index}条补货规则说明内容。" for index in range(80))
    chunks = chunk_text(text, size=100, overlap=20)

    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 100 for chunk in chunks)
    # 中文句子之间不补空格，重叠片段必须是上一片末尾的原样字符
    assert chunks[1].startswith(chunks[0][-20:])


def test_chunk_text_normalizes_whitespace():
    chunks = chunk_text("  补货   建议。\n\n\n\n库存 告警。  ", size=500)
    assert chunks == ["补货 建议。库存 告警。"]


def test_chunk_text_hard_splits_overlong_sentence():
    chunks = chunk_text("字" * 1000, size=100, overlap=20)
    assert all(len(chunk) <= 100 for chunk in chunks)
    # 步长 = size-overlap = 80，1000 字符 → 13 片（最后一片不足 100）
    assert len(chunks) == 13


def test_chunk_text_clamps_size_and_overlap():
    # size 过小 → 夹到 50；overlap >= size → 夹到 size-1（不会死循环/空片）
    chunks = chunk_text("规则" * 300, size=10, overlap=999)
    assert chunks and all(len(chunk) <= 50 for chunk in chunks)


def test_chunk_text_rejects_non_string_and_oversize_input():
    with pytest.raises(KbValidationError):
        chunk_text(["不是文本"])  # type: ignore[arg-type]
    with pytest.raises(KbValidationError, match="1MB"):
        chunk_text("a" * (kb_store.MAX_CONTENT_BYTES + 1))


def test_chunk_text_blank_returns_empty_list():
    assert chunk_text("   \n\n  ") == []


# ====================================================================================
# 2. 本地向量：确定性、维度、L2 归一化、cosine 边界
# ====================================================================================
def test_vectorize_is_deterministic_and_l2_normalized():
    first = vectorize("补货建议：A1000001 库存不足，需补 6 件", dim=128)
    second = vectorize("补货建议：A1000001 库存不足，需补 6 件", dim=128)

    assert first == second
    assert len(first) == 128
    assert math.sqrt(sum(value * value for value in first)) == pytest.approx(1.0, abs=1e-6)


def test_vectorize_empty_text_is_zero_vector():
    assert vectorize("   ", dim=16) == [0.0] * 16


def test_vectorize_rejects_illegal_dim():
    with pytest.raises(KbValidationError):
        vectorize("补货", dim=4)


def test_cosine_bounds_and_edge_cases():
    vector = vectorize("补货 库存 规则")
    assert cosine(vector, vector) == pytest.approx(1.0, abs=1e-6)
    assert cosine(vector, []) == 0.0
    assert cosine(vector, [0.0] * DEFAULT_DIM) == 0.0
    assert cosine([1.0, 0.0], [1.0]) == 0.0  # 维度不一致 → 0（不抛异常）
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_similar_text_scores_higher_than_unrelated():
    query = vectorize("补货库存不足")
    related = cosine(query, vectorize("补货建议：库存不足需要补货"))
    unrelated = cosine(query, vectorize("故障码 E01 电机异常"))
    assert related > unrelated


# ====================================================================================
# 3. ingest：首次入库 / 同 hash 跳过 / 变短后旧切片停用 / 参数校验 / 注入安全
# ====================================================================================
_BASE_INGEST: dict[str, Any] = {
    "doc_code": "restock_manual",
    "title": "补货作业手册",
    "category": "manual",
    "source_type": "text",
    "source_ref": "manual.txt",
    "content": "补货规则：库存不足时及时补货。",
    "operator": "tester",
}


async def test_ingest_first_time_upserts_document_and_chunks():
    content = "".join(f"第{index}条补货规则：库存不足时及时补货。" for index in range(80))
    expected_chunks = chunk_text(content)
    session = FakeSession(rows_by_call=[[], [], [{"id": 9}]])

    result = await ingest_document(session, **{**_BASE_INGEST, "content": content})

    assert result["skipped"] is False
    assert result["chunks"] == result["chunkCount"] == len(expected_chunks)
    assert result["skippedChunks"] == 0
    assert result["contentHash"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert session.commits == 1

    sql = _sql_text(session)
    assert "insert into agent_kb_document" in sql
    assert "on duplicate key update" in sql  # 重复上传走覆盖，不产生第二行
    assert "insert into agent_kb_chunk" in sql
    assert "update agent_kb_chunk" in sql
    assert "chunk_no > :chunk_count" in sql, "变短时旧切片只能停用，不能删除"
    assert "delete" not in sql, "AGENTS §7.4：知识库表不允许物理删除"

    document_params = session.calls[1][1]
    assert document_params["doc_code"] == "restock_manual"
    assert document_params["chunk_count"] == len(expected_chunks)
    assert document_params["operator"] == "tester"
    chunk_params = session.calls[3][1]
    assert chunk_params["dim"] == DEFAULT_DIM
    assert chunk_params["content_length"] == len(chunk_params["content"])
    assert len(json.loads(chunk_params["vector_json"])) == DEFAULT_DIM


async def test_ingest_same_hash_and_chunks_skips_without_writing():
    content = "重复内容。"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    session = FakeSession(
        rows_by_call=[[{"id": 3, "content_hash": content_hash}], [{"chunk_count": 4}]]
    )

    result = await ingest_document(session, **{**_BASE_INGEST, "content": content})

    assert result["skipped"] is True
    assert result["chunks"] == result["skippedChunks"] == 4
    assert len(session.calls) == 2, "跳过时不得发出任何写 SQL"
    assert session.commits == 0


async def test_ingest_changed_content_disables_extra_old_chunks():
    session = FakeSession(rows_by_call=[[{"id": 3, "content_hash": "old"}], [], [{"id": 3}]])

    result = await ingest_document(session, **{**_BASE_INGEST, "content": "很短的一段规则。"})

    assert result["skipped"] is False and result["chunks"] == 1
    disable_sql, disable_params = session.calls[-1]
    assert "update agent_kb_chunk" in disable_sql.lower()
    assert "chunk_no > :chunk_count" in disable_sql
    assert disable_params["chunk_count"] == 1
    assert "delete" not in _sql_text(session)


@pytest.mark.parametrize(
    "overrides",
    [
        {"doc_code": "AB"},  # 太短
        {"doc_code": "Upper_Case"},  # 含大写
        {"doc_code": "bad code"},  # 含空格
        {"title": ""},
        {"title": "x" * 201},
        {"category": "unknown"},
        {"source_type": "pdf"},
        {"source_ref": "x" * 501},
        {"operator": "x" * 65},
        {"content": "   "},
        {"content": "a" * (kb_store.MAX_CONTENT_BYTES + 1)},
    ],
)
async def test_ingest_rejects_invalid_params_before_any_sql(overrides: dict[str, Any]):
    session = FakeSession()
    with pytest.raises(KbValidationError):
        await ingest_document(session, **{**_BASE_INGEST, **overrides})
    assert session.calls == [], "非法入参不得连接/查询数据库"


async def test_ingest_sql_is_parameterized_against_injection():
    content = "正常正文。'; DROP TABLE agent_kb_document; --"
    session = FakeSession(rows_by_call=[[], [], [{"id": 5}]])

    await ingest_document(
        session,
        **{**_BASE_INGEST, "title": "标题'; DROP TABLE x; --", "content": content},
    )

    joined_sql = _sql_text(session)
    assert "drop table" not in joined_sql, "注入串只能作为参数值，不得拼进 SQL"
    values = [str(value) for _, params in session.calls for value in params.values()]
    assert any("DROP TABLE" in value for value in values)


# ====================================================================================
# 4. search：命中 / low_score / no_hit / 留痕 / 过滤口径
# ====================================================================================
async def test_search_returns_hits_and_writes_retrieval():
    match = _chunk_row(11, "补货建议：设备库存不足时，按分位销量补货。")
    other = _chunk_row(
        12, "故障码 E01：电机堵转，请检查货道。", doc_code="fault_code", title="故障码表"
    )
    session = FakeSession(rows_by_call=[[match, other]], lastrowid_by_call=[0, 321])

    result = await search(session, query="补货建议 库存不足", top_k=5, scene=3, request_id="rid-1")

    assert result["retrievalId"] == 321
    assert result["fallbackReason"] is None
    assert result["topK"] == 5 and result["minScore"] == 0.15
    assert result["hits"][0]["chunkId"] == 11
    assert result["hits"][0]["docCode"] == "restock_manual"
    assert len(result["hits"][0]["excerpt"]) <= 300
    assert result["hits"][0]["score"] >= 0.15

    assert session.commits == 1
    insert_sql, insert_params = session.calls[-1]
    assert "insert into agent_kb_retrieval" in insert_sql.lower()
    assert insert_params["scene"] == 3 and insert_params["request_id"] == "rid-1"
    hits_json = json.loads(insert_params["hits_json"])
    assert hits_json and set(hits_json[0]) == {"chunkId", "docCode", "title", "score", "excerpt"}

    candidates_sql = session.calls[0][0].lower()
    assert "c.enabled = 1" in candidates_sql
    assert "d.enabled = 1" in candidates_sql
    assert "d.del_flag = '0'" in candidates_sql, "软删文档不得参与检索"
    assert session.calls[0][1]["candidate_limit"] == CANDIDATE_LIMIT


async def test_search_below_threshold_falls_back_low_score():
    # 手工给一个零向量候选：cosine 恒为 0，必然低于阈值（避免依赖哈希碰撞的偶然分数）
    zero_vector_row = {
        "chunk_id": 1,
        "content": "补货库存规则",
        "vector_json": json.dumps([0.0] * DEFAULT_DIM),
        "doc_code": "restock_manual",
        "title": "补货手册",
    }
    session = FakeSession(rows_by_call=[[zero_vector_row]], lastrowid_by_call=[0, 7])

    result = await search(session, query="补货建议")

    assert result["hits"] == []
    assert result["fallbackReason"] == "low_score"
    assert result["retrievalId"] == 7


async def test_search_without_candidates_falls_back_no_hit():
    session = FakeSession(rows_by_call=[[]], lastrowid_by_call=[0, 8])

    result = await search(session, query="补货建议")

    assert result["hits"] == []
    assert result["fallbackReason"] == "no_hit"
    assert session.commits == 1, "无命中也必须留痕（回退率统计的数据源）"


async def test_search_skips_corrupt_vectors_and_warns(caplog):
    corrupt = {
        "chunk_id": 1,
        "content": "坏向量切片",
        "vector_json": "not-json",
        "doc_code": "x",
        "title": "x",
    }
    session = FakeSession(
        rows_by_call=[[corrupt, _chunk_row(2, "补货建议：库存不足")]], lastrowid_by_call=[0, 9]
    )

    with caplog.at_level("WARNING", logger="dkd.agent.kb_store"):
        result = await search(session, query="补货建议：库存不足")

    assert result["hits"] and result["hits"][0]["chunkId"] == 2
    assert any("向量损坏" in record.getMessage() for record in caplog.records)


async def test_search_truncates_query_for_retrieval_record():
    session = FakeSession(rows_by_call=[[]], lastrowid_by_call=[0, 10])

    await search(session, query="补货" * 300)

    _, params = session.calls[-1]
    assert len(params["query"]) == 500, "检索留痕的 query 必须截断到列宽 500"
    assert params["fallback_reason"] == "no_hit"


@pytest.mark.parametrize("bad_top_k", [0, 21, -1])
async def test_search_rejects_illegal_top_k_before_any_sql(bad_top_k: int):
    session = FakeSession()
    with pytest.raises(KbValidationError):
        await search(session, query="补货", top_k=bad_top_k)
    assert session.calls == []


async def test_search_rejects_blank_query():
    session = FakeSession()
    with pytest.raises(KbValidationError):
        await search(session, query="   ")
    assert session.calls == []


async def test_search_uses_configured_default_top_k():
    session = FakeSession(rows_by_call=[[]], lastrowid_by_call=[0, 11])
    result = await search(session, query="补货")
    assert result["topK"] == get_settings().kb_top_k == 5


async def test_search_top_k_caps_returned_hits():
    rows = [_chunk_row(index, "补货建议：库存不足") for index in range(1, 6)]
    session = FakeSession(rows_by_call=[rows], lastrowid_by_call=[0, 12])

    result = await search(session, query="补货建议：库存不足", top_k=2)

    assert len(result["hits"]) == 2
    assert result["hits"][0]["score"] >= result["hits"][1]["score"], "命中必须按分数降序"


# ====================================================================================
# 5. 文档列表 / 启停 / 统计 / mark_used
# ====================================================================================
def _document_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": 1,
        "doc_code": "restock_manual",
        "title": "补货手册",
        "category": "manual",
        "source_type": "text",
        "source_ref": "manual.txt",
        "content_hash": "abc",
        "chunk_count": 3,
        "status": 1,
        "enabled": 1,
        "create_by": "tester",
        "create_time": None,
        "update_by": "tester",
        "update_time": None,
    }
    row.update(overrides)
    return row


async def test_list_documents_filters_and_paginates():
    session = FakeSession(
        rows_by_call=[[{"total": 2}], [_document_row(), _document_row(id=2, enabled=0)]]
    )

    data = await list_documents(
        session, category="manual", enabled=1, keyword="50%_off", page=3, page_size=10
    )

    assert data["total"] == 2
    assert data["rows"][0]["docCode"] == "restock_manual"
    assert data["rows"][0]["chunkCount"] == 3
    assert data["rows"][0]["enabled"] == 1

    count_sql, count_params = session.calls[0]
    assert "count(*)" in count_sql.lower()
    assert count_params["category"] == "manual" and count_params["enabled"] == 1
    assert count_params["keyword"] == "%50\\%\\_off%", "LIKE 通配符必须转义"

    page_sql, page_params = session.calls[1]
    assert "limit :limit offset :offset" in page_sql.lower()
    assert page_params["limit"] == 10 and page_params["offset"] == 20
    assert page_params["keyword"] == "%50\\%\\_off%"


@pytest.mark.parametrize(
    "overrides",
    [
        {"category": "unknown"},
        {"enabled": 2},
        {"page": 0},
        {"page_size": 0},
        {"page_size": 101},
    ],
)
async def test_list_documents_rejects_illegal_params(overrides: dict[str, Any]):
    session = FakeSession()
    with pytest.raises(KbValidationError):
        await list_documents(session, **overrides)
    assert session.calls == []


async def test_set_enabled_updates_and_reports_missing_document():
    session = FakeSession(rowcount_by_call=[1])
    data = await set_enabled(session, "restock_manual", 0)
    assert data == {"docCode": "restock_manual", "enabled": 0}
    assert session.commits == 1
    sql, params = session.calls[0]
    assert "update agent_kb_document" in sql.lower()
    assert "del_flag = '0'" in sql
    assert params["enabled"] == 0

    missing = FakeSession(rowcount_by_call=[0])
    with pytest.raises(KbNotFoundError):
        await set_enabled(missing, "not_exists", True)


async def test_set_enabled_rejects_illegal_doc_code():
    session = FakeSession()
    with pytest.raises(KbValidationError):
        await set_enabled(session, "BAD", True)
    assert session.calls == []


async def test_stats_returns_single_query_counts():
    session = FakeSession(rows_by_call=[[{"documents": 2, "chunks": 5, "retrievals": 7}]])
    assert await stats(session) == {"documents": 2, "chunks": 5, "retrievals": 7}
    assert len(session.calls) == 1, "统计应单查询完成（页面顶部三张卡，不做 3 次往返）"


async def test_mark_used_updates_retrieval_and_reports_missing():
    session = FakeSession(rowcount_by_call=[1])
    assert await mark_used(session, 321, used=True, reason="被最终回答引用") is True
    sql, params = session.calls[0]
    assert "update agent_kb_retrieval" in sql.lower()
    assert params == {"used": 1, "reason": "被最终回答引用", "retrieval_id": 321}
    assert session.commits == 1

    missing = FakeSession(rowcount_by_call=[0])
    assert await mark_used(missing, 999) is False


# ====================================================================================
# 6. API：信封/鉴权/参数错误 400/DB 异常 500（假会话 + 假 store 函数，不起真服务）
# ====================================================================================
def _build_client(session: Any | None = None) -> TestClient:
    app = FastAPI()
    kb.register_error_handlers(app)
    app.include_router(kb.router)
    if session is not None:

        async def _override() -> AsyncIterator[Any]:
            yield session

        app.dependency_overrides[kb.get_session] = _override
    return TestClient(app)


def test_api_requires_service_secret():
    client = _build_client(FakeSession())
    assert client.get("/agents/kb/stats").status_code == 401
    assert client.get("/agents/kb/stats", headers={"X-Agent-Secret": "forged"}).status_code == 401


def test_api_documents_returns_envelope_and_accepts_filters(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_list_documents(session: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"total": 1, "rows": [_document_row()]}

    monkeypatch.setattr(kb, "list_documents", fake_list_documents)
    client = _build_client(FakeSession())

    response = client.get(
        "/agents/kb/documents",
        params={"category": "manual", "enabled": 1, "keyword": "补货", "pageNum": 2, "pageSize": 5},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200 and body["msg"] == "ok"
    assert body["data"]["total"] == 1
    assert captured == {
        "category": "manual",
        "enabled": 1,
        "keyword": "补货",
        "page": 2,
        "page_size": 5,
    }


def test_api_ingest_maps_validation_error_to_400():
    client = _build_client(FakeSession())
    response = client.post(
        "/agents/kb/documents",
        json={
            "docCode": "AB",
            "title": "标题",
            "category": "manual",
            "content": "正文",
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == 400 and "文档编码不合法" in body["msg"]
    assert "select" not in body["msg"].lower()


def test_api_ingest_returns_ingest_data(monkeypatch):
    async def fake_ingest(session: Any, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["doc_code"] == "restock_manual"
        return {"docCode": "restock_manual", "chunks": 2, "skipped": False}

    monkeypatch.setattr(kb, "ingest_document", fake_ingest)
    client = _build_client(FakeSession())

    response = client.post(
        "/agents/kb/documents",
        json={
            "docCode": "restock_manual",
            "title": "标题",
            "category": "manual",
            "content": "正文",
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"docCode": "restock_manual", "chunks": 2, "skipped": False}


def test_api_search_passes_top_k_alias(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_search(session: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "hits": [],
            "topK": 7,
            "minScore": 0.15,
            "retrievalId": 1,
            "fallbackReason": "no_hit",
        }

    monkeypatch.setattr(kb, "search", fake_search)
    client = _build_client(FakeSession())

    response = client.post(
        "/agents/kb/search",
        json={"query": "补货建议", "topK": 7, "scene": 2},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert captured["top_k"] == 7 and captured["scene"] == 2
    assert response.json()["data"]["fallbackReason"] == "no_hit"


def test_api_search_body_validation_maps_400(monkeypatch):
    client = _build_client(FakeSession())
    response = client.post("/agents/kb/search", json={"topK": 5}, headers=AUTH_HEADERS)
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == 400 and "参数校验失败" in body["msg"]


def test_api_update_enabled_returns_flag(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_set_enabled(session: Any, doc_code: str, enabled: Any) -> dict[str, Any]:
        captured.update({"doc_code": doc_code, "enabled": enabled})
        return {"docCode": doc_code, "enabled": 0 if not enabled else 1}

    monkeypatch.setattr(kb, "set_enabled", fake_set_enabled)
    client = _build_client(FakeSession())

    response = client.put(
        "/agents/kb/documents/restock_manual/enabled",
        json={"enabled": 0},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"docCode": "restock_manual", "enabled": 0}
    assert captured == {"doc_code": "restock_manual", "enabled": 0}


def test_api_missing_document_maps_404(monkeypatch):
    async def fake_set_enabled(session: Any, doc_code: str, enabled: Any) -> dict[str, Any]:
        raise KbNotFoundError(f"文档不存在：{doc_code}")

    monkeypatch.setattr(kb, "set_enabled", fake_set_enabled)
    client = _build_client(FakeSession())

    response = client.put(
        "/agents/kb/documents/not_exists/enabled",
        json={"enabled": 1},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 404
    assert response.json()["code"] == 404


def test_api_db_error_maps_500_without_leaking_sql():
    session = FakeSession(error=RuntimeError("boom: SELECT * FROM agent_kb_document"))
    client = _build_client(session)

    response = client.get("/agents/kb/stats", headers=AUTH_HEADERS)

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == 500
    assert "SELECT" not in body["msg"] and "agent_kb" not in body["msg"]
    assert body["msg"] == "知识库操作失败，请稍后重试或查看服务日志"


def test_api_stats_returns_counts(monkeypatch):
    async def fake_stats(session: Any) -> dict[str, int]:
        return {"documents": 2, "chunks": 5, "retrievals": 7}

    monkeypatch.setattr(kb, "stats", fake_stats)
    client = _build_client(FakeSession())

    response = client.get("/agents/kb/stats", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json()["data"] == {"documents": 2, "chunks": 5, "retrievals": 7}
