"""知识库 L2（文档语料层）存储、切片与本地向量检索（排期 3-6）。

## 为什么切片/向量/检索都在 Python

知识库文档、切片、检索留痕都是**智能体自有数据**（`agent_kb_*`，AGENTS §7.3）：
Java 只做管理台代理（鉴权 + 分页 + 错误映射），切片与向量化的唯一事实源在 Python。
若 Java 再写一套切片/向量逻辑，两处必然漂移，同一份语料在两个入口切片结果不同，
检索命中率就没法归因（哪套切法出了问题说不清）。

## 本地确定性向量（为什么不是 embedding 服务）

当前没有 embedding 服务、也不允许新增外部依赖（task-16 约束）。本模块用
**带符号哈希 bag-of-words**：中文单字 + 相邻 bigram、ASCII 词，`blake2b` 确定性哈希到
256 维，L2 归一化。三条边界必须守住：

1. **确定性**：同一文本任何进程/任何时刻结果逐位相同（禁止 `hash()`——它按进程加盐）；
2. **可检索性**：bigram 让「补货建议」与「补货」共享特征，避免只有单字导致的过度碰撞；
3. **可替换性**：向量只落 `vector_json`，将来换 pgvector / 真 embedding 时只改本模块，
   检索语义（cosine + 阈值 + 回退原因）与表结构不变。

## 三条纪律（改之前先读）

1. **只写 `agent_kb_*`**，通篇不出现 DELETE：更新切片内容用 `ON DUPLICATE KEY UPDATE`，
   多余旧切片只 `enabled=0`（AGENTS §7.4 软删除；账号本身也无 DELETE 权限）。
2. **ingest 幂等**：`content_hash`（归一化正文的 SHA-256）相同且已有切片 → `skipped=True`，
   绝不双写（重复上传/增量重建的入口都走这里，4-4 的“向量库重建”依赖该语义）。
3. **检索必须留痕**：每次 `search()` 都写一条 `agent_kb_retrieval`（只增），
   `hits_json` 只存摘要（chunkId/docCode/title/score/excerpt），不存整段正文。

## 事务边界

本模块的写函数（ingest / search 的留痕 / mark_used / set_enabled）**自己 commit**：
调用方（API）不掌握“检索已写但留痕没写”的半截状态；失败由 API 层统一映射 5xx。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

logger = logging.getLogger("dkd.agent.kb_store")

# ------------------------------------------------------------------------------------
# 常量：分类/编码/上限（与 docs/ddl/agent_kb_mcp.sql 列注释、契约 §3.6 同口径）
# ------------------------------------------------------------------------------------
CATEGORIES: frozenset[str] = frozenset({"manual", "fault_code", "sop", "faq", "other"})
SOURCE_TYPES: frozenset[str] = frozenset({"text", "file", "url"})
DOC_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_\-]{2,63}$")

MAX_CONTENT_BYTES = 1024 * 1024  # 1MB：页面先拦、后端再校验（DDL LONGTEXT，应用层兜底）
MAX_TITLE_CHARS = 200
MAX_SOURCE_REF_CHARS = 500
MAX_OPERATOR_CHARS = 64
MAX_QUERY_CHARS = 500
MAX_EXCERPT_CHARS = 300

DEFAULT_DIM = 256
MIN_CHUNK_SIZE = 50
MAX_CHUNK_SIZE = 2000  # DDL 注释：切片正文 ≤2000 字符（TEXT 列的口径）
MAX_TOP_K = 20  # 与前端滑杆一致（1~20），也避免一次召回把整库塞进 prompt

# 单次检索候选上限：本机只有单主库（AGENTS §1 约束 5），知识库虽小也不做无界全表扫描。
# 5000 条切片 ≈ 250 万字符语料，首期（厂商手册/故障码表/SOP）量级远低于此；
# 超出时应先调大该值并评估内存，而不是让每次检索都扫全表。
CANDIDATE_LIMIT = 5000

# 文本归一化：行尾统一、行内空白折叠、3 个以上换行折成段落分隔
_WS_RE = re.compile(r"[ \t\u3000]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")

# 句子切分（保留句末标点在句内）：中文/英文句末标点与单换行都作为候选切点
_SENTENCE_RE = re.compile(r"[^。！？!?；;\n]*[。！？!?；;\n]|[^。！？!?；;\n]+$")

# 中文连续段（含扩展 A 区）、ASCII 词/数字
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_ASCII_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*|[0-9]+")


class KbValidationError(ValueError):
    """入参不合法（API 层映射 400 信封，不写库）。"""


class KbNotFoundError(LookupError):
    """目标文档不存在（API 层映射 404 信封）。"""


# ------------------------------------------------------------------------------------
# 文本归一化与切片
# ------------------------------------------------------------------------------------
def _normalize_text(text_value: str) -> str:
    """统一换行、折叠行内空白与连续空行；**不丢正文内容**（切片/哈希都用这一份）。"""
    normalized = text_value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _WS_RE.sub(" ", normalized)
    normalized = _BLANK_LINES_RE.sub("\n\n", normalized)
    return normalized.strip()


def _split_sentences(normalized: str) -> list[str]:
    """按段落 → 句末标点切分；段内单换行也视为切点（手册条目常一行一条）。"""
    sentences: list[str] = []
    for paragraph in normalized.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for piece in _SENTENCE_RE.findall(paragraph):
            piece = piece.strip()
            if piece:
                sentences.append(piece)
    return sentences


def _join_piece(current: str, piece: str) -> str:
    """拼接时只在“两侧都是 ASCII 词字符”处补一个空格（中文直接相连，避免句内多空格）。"""
    if not current:
        return piece
    left_tail = current[-1]
    right_head = piece[0]
    both_word_chars = (
        left_tail.isascii()
        and left_tail.isalnum()
        and right_head.isascii()
        and right_head.isalnum()
    )
    if both_word_chars:
        return f"{current} {piece}"
    return current + piece


def _hard_split(sentence: str, size: int, overlap: int) -> list[str]:
    """超长句（无标点）硬切：步长 = size - overlap，保证每片 ≤ size。"""
    step = max(1, size - overlap)
    pieces: list[str] = []
    start = 0
    while start < len(sentence):
        end = min(start + size, len(sentence))
        pieces.append(sentence[start:end])
        if end >= len(sentence):
            break
        start += step
    return pieces


def chunk_text(text_value: str, size: int = 500, overlap: int = 50) -> list[str]:
    """把正文切成 ≤size 字符的切片，尽量在段落/句号处断开。

    为什么带 overlap：一句话被切在两个切片中间时，任一片单独看都缺上下文；
    重叠 50 字符是契约拍板值（`DKD_AGENT_KB_CHUNK_OVERLAP`，默认 50），不是“越多越好”——
    重叠越大存储与检索候选越多。

    边界处理：
    - 入参不是 str / 超过 1MB → `KbValidationError`（页面先拦，后端兜底）；
    - 空白正文 → `[]`（ingest 会拒绝空正文，这里返回空列表让调用方自己判断）；
    - size/overlap 越界按 [50, 2000] / [0, size-1] 夹取（配置写错不应让切片无法进行）。

    @param text_value 原始正文（未归一化）
    @param size 目标切片长度（字符）
    @param overlap 相邻切片重叠字符数
    @return 顺序切片列表
    """
    if not isinstance(text_value, str):
        raise KbValidationError("正文必须是字符串")
    if len(text_value.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise KbValidationError("正文超过 1MB 上限，请拆分文档后再入库")
    normalized = _normalize_text(text_value)
    if not normalized:
        return []

    size = max(MIN_CHUNK_SIZE, min(int(size), MAX_CHUNK_SIZE))
    overlap = max(0, min(int(overlap), size - 1))

    chunks: list[str] = []
    current = ""
    for sentence in _split_sentences(normalized):
        if len(sentence) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_split(sentence, size, overlap))
            continue
        if not current:
            current = sentence
            continue
        if len(_join_piece(current, sentence)) <= size:
            current = _join_piece(current, sentence)
            continue
        chunks.append(current)
        # 新切片以“上一片尾部 overlap 字符”起头；尾部长度同时受剩余空间限制，
        # 否则 tail+句子可能再次超过 size，后续贪心判断就失真了。
        room = size - len(sentence) - 1
        tail = current[-overlap:] if overlap > 0 and room > 0 else ""
        if len(tail) > room:
            tail = tail[-room:]
        current = _join_piece(tail, sentence) if tail else sentence
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk]


# ------------------------------------------------------------------------------------
# 本地确定性向量
# ------------------------------------------------------------------------------------
def _tokens(text_value: str) -> list[str]:
    """词元：中文单字 + 相邻 bigram、ASCII 词（小写）、数字。"""
    tokens: list[str] = [f"w:{word.lower()}" for word in _ASCII_WORD_RE.findall(text_value)]
    for run in _CJK_RUN_RE.findall(text_value):
        tokens.extend(f"c:{char}" for char in run)
        tokens.extend(f"b:{run[index : index + 2]}" for index in range(len(run) - 1))
    return tokens


def _signed_index(token: str, dim: int) -> tuple[int, float]:
    """确定性哈希到 [0, dim) 并给出 ±1 符号。

    为什么用 `blake2b` 而不是内置 `hash()`：`hash()` 对 str 按进程加盐，
    同一份语料不同进程算出的向量不同 → 入库后检索分数漂移（最坏是永远低分回退）。
    """
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    index = value % dim
    sign = 1.0 if (value >> 63) & 1 else -1.0
    return index, sign


def vectorize(text_value: str, dim: int = DEFAULT_DIM) -> list[float]:
    """文本 → L2 归一化的固定维度向量（确定性、无外部依赖）。

    带符号哈希（signed hashing）的意义：同一维度上的不同词元可能落在同一桶，
    符号随机化后冲突项期望相互抵消，而不是一致地“加在一起”放大伪相似度。

    @param text_value 任意文本（空文本 → 全零向量）
    @param dim 维度（8~4096；入库与检索必须使用同一维度，否则 cosine 返回 0）
    @return 长度 = dim 的 float 列表，L2 范数为 1（全零向量除外）
    """
    dim = int(dim)
    if dim < 8 or dim > 4096:
        raise KbValidationError(f"向量维度非法：{dim}（允许 8~4096）")
    vector = [0.0] * dim
    for token in _tokens(text_value or ""):
        index, sign = _signed_index(token, dim)
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [round(value / norm, 6) for value in vector]


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """余弦相似度（维度不一致/空向量/零向量 → 0.0，绝不抛异常打断检索）。"""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def _parse_vector(raw: Any) -> list[float] | None:
    """`vector_json` 容错解析（MySQL JSON 列可能以 str/dict 返回；坏行跳过而不是整页 500）。"""
    payload: Any = raw
    if isinstance(raw, (bytes, bytearray)):
        try:
            payload = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, list):
        return None
    try:
        return [float(value) for value in payload]
    except (TypeError, ValueError):
        return None


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit]


def _excerpt(content: str) -> str:
    """命中摘要 ≤300 字符（前端直接展示；完整正文不回传给检索方，避免把语料带进 prompt）。"""
    excerpt = content.strip()
    if len(excerpt) <= MAX_EXCERPT_CHARS:
        return excerpt
    return excerpt[: MAX_EXCERPT_CHARS - 1] + "…"


# ------------------------------------------------------------------------------------
# ingest：文档 upsert + 切片重建（不双写、不 DELETE）
# ------------------------------------------------------------------------------------
_SELECT_DOCUMENT_SQL = """
SELECT id, content_hash
  FROM agent_kb_document
 WHERE doc_code = :doc_code
   AND del_flag = '0'
"""

_COUNT_CHUNKS_SQL = """
SELECT COUNT(*) AS chunk_count
  FROM agent_kb_chunk
 WHERE document_id = :document_id
   AND enabled = 1
"""

_UPSERT_DOCUMENT_SQL = """
INSERT INTO agent_kb_document
    (doc_code, title, category, source_type, source_ref, content, content_hash,
     chunk_count, status, enabled, create_by, create_time, update_by, update_time, del_flag)
VALUES
    (:doc_code, :title, :category, :source_type, :source_ref, :content, :content_hash,
     :chunk_count, 1, 1, :operator, NOW(), :operator, NOW(), '0')
ON DUPLICATE KEY UPDATE
    title = VALUES(title),
    category = VALUES(category),
    source_type = VALUES(source_type),
    source_ref = VALUES(source_ref),
    content = VALUES(content),
    content_hash = VALUES(content_hash),
    chunk_count = VALUES(chunk_count),
    status = 1,
    enabled = 1,
    update_by = VALUES(update_by),
    update_time = NOW(),
    del_flag = '0'
"""

_SELECT_DOCUMENT_ID_SQL = """
SELECT id FROM agent_kb_document WHERE doc_code = :doc_code
"""

_UPSERT_CHUNK_SQL = """
INSERT INTO agent_kb_chunk
    (document_id, doc_code, chunk_no, content, content_length, vector_json, dim,
     enabled, create_time, update_time)
VALUES
    (:document_id, :doc_code, :chunk_no, :content, :content_length, :vector_json, :dim,
     1, NOW(), NOW())
ON DUPLICATE KEY UPDATE
    content = VALUES(content),
    content_length = VALUES(content_length),
    vector_json = VALUES(vector_json),
    dim = VALUES(dim),
    enabled = 1,
    update_time = NOW()
"""

# 旧切片只**停用**不移除：内容变短后多出来的 chunk_no 行保留可追溯（4-4 重建/回滚要用）
_DISABLE_EXTRA_CHUNKS_SQL = """
UPDATE agent_kb_chunk
   SET enabled = 0,
       update_time = NOW()
 WHERE document_id = :document_id
   AND chunk_no > :chunk_count
"""


def _validate_doc_code(doc_code: str) -> str:
    if not isinstance(doc_code, str) or not DOC_CODE_PATTERN.match(doc_code.strip()):
        raise KbValidationError(
            r"文档编码不合法：需匹配 ^[a-z0-9][a-z0-9_\-]{2,63}$（小写字母/数字/下划线/连字符）"
        )
    return doc_code.strip()


def _validate_title(title: str) -> str:
    value = (title or "").strip()
    if not 1 <= len(value) <= MAX_TITLE_CHARS:
        raise KbValidationError(f"标题长度必须为 1~{MAX_TITLE_CHARS} 字")
    return value


def _validate_category(category: str) -> str:
    value = (category or "").strip()
    if value not in CATEGORIES:
        raise KbValidationError(f"分类非法：{value!r}（允许：{sorted(CATEGORIES)}）")
    return value


def _validate_source_type(source_type: str | None) -> str:
    value = (source_type or "text").strip()
    if value not in SOURCE_TYPES:
        raise KbValidationError(f"来源类型非法：{value!r}（允许：{sorted(SOURCE_TYPES)}）")
    return value


def _validate_source_ref(source_ref: str | None) -> str | None:
    value = (source_ref or "").strip() or None
    if value is not None and len(value) > MAX_SOURCE_REF_CHARS:
        raise KbValidationError(f"来源引用长度不能超过 {MAX_SOURCE_REF_CHARS} 字")
    return value


def _validate_operator(operator: str | None) -> str | None:
    value = (operator or "").strip() or None
    if value is not None and len(value) > MAX_OPERATOR_CHARS:
        raise KbValidationError(f"操作人长度不能超过 {MAX_OPERATOR_CHARS} 字")
    return value


async def ingest_document(
    session: AsyncSession,
    *,
    doc_code: str,
    title: str,
    category: str,
    source_type: str | None = "text",
    source_ref: str | None = None,
    content: str,
    operator: str | None = None,
) -> dict[str, Any]:
    """文档入库：校验 → 切片 → 本地向量化 → upsert 文档与切片（幂等）。

    幂等语义（task-16 验收点）：
    - `content_hash` 相同且已有启用切片 → 直接 `skipped=True`，**不写任何一行**；
    - 内容变化 → 文档 upsert（version 事实由 content_hash 承担）、逐切片 upsert、
      超出新切片数的旧行 `enabled=0`（不 DELETE）。

    为什么先查 hash 再切：切片 + 向量化是 CPU 重操作，重复上传同一份手册时
    没必要重算；且“重算再覆盖”会短暂出现新旧切片混合的检索窗口。

    @return `{docCode, chunks, chunkCount, skipped, skippedChunks, contentHash}`
    """
    code = _validate_doc_code(doc_code)
    normalized = _normalize_text(content or "")
    if not normalized:
        raise KbValidationError("正文不能为空")
    if len(normalized.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise KbValidationError("正文超过 1MB 上限，请拆分文档后再入库")
    title_value = _validate_title(title)
    category_value = _validate_category(category)
    source_type_value = _validate_source_type(source_type)
    source_ref_value = _validate_source_ref(source_ref)
    operator_value = _validate_operator(operator)
    content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    existing_rows = (
        (await session.execute(text(_SELECT_DOCUMENT_SQL), {"doc_code": code})).mappings().all()
    )
    existing = existing_rows[0] if existing_rows else None
    if existing is not None and existing.get("content_hash") == content_hash:
        count_rows = (
            (await session.execute(text(_COUNT_CHUNKS_SQL), {"document_id": existing.get("id")}))
            .mappings()
            .all()
        )
        existing_chunks = int(count_rows[0].get("chunk_count") or 0) if count_rows else 0
        if existing_chunks > 0:
            logger.info(
                "知识库文档内容未变化，跳过重建：docCode=%s chunks=%s", code, existing_chunks
            )
            return {
                "docCode": code,
                "chunks": existing_chunks,
                "chunkCount": existing_chunks,
                "skipped": True,
                "skippedChunks": existing_chunks,
                "contentHash": content_hash,
            }

    settings = get_settings()
    chunks = chunk_text(normalized, size=settings.kb_chunk_size, overlap=settings.kb_chunk_overlap)
    if not chunks:
        raise KbValidationError("正文切片结果为空（正文只有空白字符？）")

    await session.execute(
        text(_UPSERT_DOCUMENT_SQL),
        {
            "doc_code": code,
            "title": title_value,
            "category": category_value,
            "source_type": source_type_value,
            "source_ref": source_ref_value,
            "content": normalized,
            "content_hash": content_hash,
            "chunk_count": len(chunks),
            "operator": operator_value,
        },
    )
    document_rows = (
        (await session.execute(text(_SELECT_DOCUMENT_ID_SQL), {"doc_code": code})).mappings().all()
    )
    if not document_rows:
        raise KbNotFoundError(f"文档 upsert 后未能读回：{code}")
    document_id = int(document_rows[0].get("id") or 0)

    for chunk_no, chunk in enumerate(chunks, start=1):
        vector = vectorize(chunk, dim=DEFAULT_DIM)
        await session.execute(
            text(_UPSERT_CHUNK_SQL),
            {
                "document_id": document_id,
                "doc_code": code,
                "chunk_no": chunk_no,
                "content": chunk,
                "content_length": len(chunk),
                "vector_json": json.dumps(vector, ensure_ascii=False),
                "dim": len(vector),
            },
        )
    await session.execute(
        text(_DISABLE_EXTRA_CHUNKS_SQL),
        {"document_id": document_id, "chunk_count": len(chunks)},
    )
    await session.commit()
    logger.info(
        "知识库文档入库完成：docCode=%s chunks=%s hash=%s operator=%s",
        code,
        len(chunks),
        content_hash[:12],
        operator_value,
    )
    return {
        "docCode": code,
        "chunks": len(chunks),
        "chunkCount": len(chunks),
        "skipped": False,
        "skippedChunks": 0,
        "contentHash": content_hash,
    }


# ------------------------------------------------------------------------------------
# search：cosine 排序 + 阈值过滤 + 检索留痕
# ------------------------------------------------------------------------------------
_SEARCH_CANDIDATES_SQL = """
SELECT c.id AS chunk_id,
       c.content AS content,
       c.vector_json AS vector_json,
       c.doc_code AS doc_code,
       d.title AS title
  FROM agent_kb_chunk c
  JOIN agent_kb_document d ON d.id = c.document_id
 WHERE c.enabled = 1
   AND d.enabled = 1
   AND d.del_flag = '0'
 ORDER BY c.id
 LIMIT :candidate_limit
"""

_INSERT_RETRIEVAL_SQL = """
INSERT INTO agent_kb_retrieval
    (request_id, conversation_id, scene, query, top_k, hits_json, used,
     fallback_reason, create_time)
VALUES
    (:request_id, :conversation_id, :scene, :query, :top_k, :hits_json, 0,
     :fallback_reason, NOW(3))
"""


def _resolve_top_k(top_k: int | None) -> int:
    """检索条数：None → 配置默认 5；越界 → 400（不静默夹取，避免调用方以为生效了）。"""
    value = get_settings().kb_top_k if top_k is None else int(top_k)
    if not 1 <= value <= MAX_TOP_K:
        raise KbValidationError(f"topK 必须在 1~{MAX_TOP_K} 之间（实际 {value}）")
    return value


def _resolve_min_score() -> float:
    value = float(get_settings().kb_min_score)
    return max(0.0, min(1.0, value))


async def search(
    session: AsyncSession,
    *,
    query: str,
    top_k: int | None = None,
    scene: int | None = None,
    request_id: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """本地向量检索：取 enabled 文档+切片的全部候选 → cosine → 排序 → 阈值过滤。

    回退语义（契约 §3.6 / 5-3 的引用治理口径）：
    - 库里没有可检索切片（或向量全部损坏）→ `fallbackReason="no_hit"`；
    - 有候选但 topK 内最高分也 < 阈值 → `fallbackReason="low_score"`，`hits=[]`；
    - 有命中 → `fallbackReason=None`。**无论哪种都写一条检索留痕**，回退率靠它统计。

    “先取 topK 再按阈值过滤”（与前端提示一致）：阈值过滤不会把排名更低的切片顶上来，
    保证“是否引用”与“召回质量”两个口径可分别观察。

    @return `{hits, topK, minScore, retrievalId, fallbackReason, candidateCount}`
    """
    normalized_query = _normalize_text(query or "")
    if not normalized_query:
        raise KbValidationError("检索语句不能为空")
    top_k_value = _resolve_top_k(top_k)
    min_score = _resolve_min_score()

    rows = (
        (await session.execute(text(_SEARCH_CANDIDATES_SQL), {"candidate_limit": CANDIDATE_LIMIT}))
        .mappings()
        .all()
    )

    query_vector = vectorize(normalized_query, dim=DEFAULT_DIM)
    scored: list[tuple[float, int, Mapping[str, Any]]] = []
    corrupt = 0
    for row in rows:
        vector = _parse_vector(row.get("vector_json"))
        if vector is None or len(vector) != DEFAULT_DIM:
            corrupt += 1
            continue
        scored.append((cosine(query_vector, vector), int(row.get("chunk_id") or 0), row))
    if corrupt:
        # 只告警一次（按行刷屏会让真正的检索问题被淹没）：坏行跳过而不是整次检索失败
        logger.warning("知识库存在向量损坏的切片，已跳过：bad_rows=%s", corrupt)

    scored.sort(key=lambda item: (-item[0], item[1]))
    top_ranked = scored[:top_k_value]
    if not rows:
        fallback_reason: str | None = "no_hit"
    elif not top_ranked:
        fallback_reason = "no_hit"
    else:
        fallback_reason = None if top_ranked[0][0] >= min_score else "low_score"

    hits: list[dict[str, Any]] = []
    if fallback_reason is None:
        hits = [
            {
                "chunkId": chunk_id,
                "docCode": str(row.get("doc_code") or ""),
                "title": str(row.get("title") or ""),
                "score": round(score, 4),
                "excerpt": _excerpt(str(row.get("content") or "")),
            }
            for score, chunk_id, row in top_ranked
            if score >= min_score
        ]

    result = await session.execute(
        text(_INSERT_RETRIEVAL_SQL),
        {
            "request_id": _clip((request_id or "").strip(), 64) or None,
            "conversation_id": _clip((conversation_id or "").strip(), 64) or None,
            "scene": scene,
            "query": _clip(normalized_query, MAX_QUERY_CHARS),
            "top_k": top_k_value,
            "hits_json": json.dumps(hits, ensure_ascii=False),
            "fallback_reason": fallback_reason,
        },
    )
    await session.commit()
    retrieval_id = int(getattr(result, "lastrowid", 0) or 0)
    logger.info(
        "知识库检索完成：topK=%s hits=%s fallback=%s retrievalId=%s",
        top_k_value,
        len(hits),
        fallback_reason,
        retrieval_id,
    )
    return {
        "hits": hits,
        "topK": top_k_value,
        "minScore": round(min_score, 4),
        "retrievalId": retrieval_id,
        "fallbackReason": fallback_reason,
        "candidateCount": len(rows),
    }


async def mark_used(
    session: AsyncSession,
    retrieval_id: int,
    *,
    used: bool = True,
    reason: str | None = None,
) -> bool:
    """标记某次检索是否被最终回答引用（引用强制校验的落地口径，5-3 统计回退率）。

    `agent_kb_retrieval` 只增不删，但 `used`/`fallback_reason` 是可更新字段——
    “某次检索最终有没有被引用”在检索当时无法知道，必须允许事后回写。

    @return 是否更新到行（False = retrieval_id 不存在）
    """
    result = await session.execute(
        text(
            """
            UPDATE agent_kb_retrieval
               SET used = :used,
                   fallback_reason = COALESCE(:reason, fallback_reason)
             WHERE id = :retrieval_id
            """
        ),
        {
            "used": 1 if used else 0,
            "reason": _clip((reason or "").strip(), 64) or None,
            "retrieval_id": int(retrieval_id),
        },
    )
    await session.commit()
    return int(getattr(result, "rowcount", 0) or 0) > 0


# ------------------------------------------------------------------------------------
# 文档列表 / 启停 / 统计
# ------------------------------------------------------------------------------------
_DOCUMENT_COLUMNS = """
    id, doc_code, title, category, source_type, source_ref, content_hash,
    chunk_count, status, enabled, create_by, create_time, update_by, update_time
"""


def _escape_like(keyword: str) -> str:
    """LIKE 通配符转义（用户搜 `%` 不应变成“全表匹配”）。MySQL 默认转义符是反斜杠。"""
    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _iso(value: Any) -> str | None:
    """datetime → 可展示字符串；None 保持 None（前端三态展示依赖它）。"""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat(sep=" ", timespec="seconds") if callable(isoformat) else str(value)


def _document_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "docCode": str(row.get("doc_code") or ""),
        "title": str(row.get("title") or ""),
        "category": str(row.get("category") or ""),
        "sourceType": str(row.get("source_type") or ""),
        "sourceRef": row.get("source_ref"),
        "contentHash": row.get("content_hash"),
        "chunkCount": int(row.get("chunk_count") or 0),
        "status": int(row.get("status") or 0),
        "enabled": int(row.get("enabled") or 0),
        "createBy": row.get("create_by"),
        "createTime": _iso(row.get("create_time")),
        "updateBy": row.get("update_by"),
        "updateTime": _iso(row.get("update_time")),
    }


async def list_documents(
    session: AsyncSession,
    *,
    category: str | None = None,
    enabled: int | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """文档分页列表（单查询 + count，无 N+1；关键词只落参数不拼 SQL）。

    @param category 分类过滤（None = 全部，非法分类 → 400）
    @param enabled 1/0 过滤（None = 全部）
    @param keyword 标题/编码模糊匹配（`%`/`_` 已转义）
    @return `{total, rows}`，rows 字段为 camelCase（与前端/Java 契约一致）
    """
    if category is not None and category.strip() and category.strip() not in CATEGORIES:
        raise KbValidationError(f"分类非法：{category!r}（允许：{sorted(CATEGORIES)}）")
    if enabled is not None and int(enabled) not in (0, 1):
        raise KbValidationError("enabled 只能是 1 或 0")
    page_value = int(page)
    page_size_value = int(page_size)
    if page_value < 1:
        raise KbValidationError("pageNum 必须 ≥ 1")
    if not 1 <= page_size_value <= 100:
        raise KbValidationError("pageSize 必须在 1~100 之间")

    where = ["del_flag = '0'"]
    params: dict[str, Any] = {}
    if category is not None and category.strip():
        where.append("category = :category")
        params["category"] = category.strip()
    if enabled is not None:
        where.append("enabled = :enabled")
        params["enabled"] = int(enabled)
    keyword_value = (keyword or "").strip()
    if keyword_value:
        where.append("(title LIKE :keyword OR doc_code LIKE :keyword)")
        params["keyword"] = f"%{_escape_like(keyword_value)}%"
    where_sql = " AND ".join(where)

    count_rows = (
        (
            await session.execute(
                text(f"SELECT COUNT(*) AS total FROM agent_kb_document WHERE {where_sql}"), params
            )
        )
        .mappings()
        .all()
    )
    total = int(count_rows[0].get("total") or 0) if count_rows else 0

    page_params = dict(params)
    page_params["limit"] = page_size_value
    page_params["offset"] = (page_value - 1) * page_size_value
    rows = (
        (
            await session.execute(
                text(
                    f"SELECT {_DOCUMENT_COLUMNS} FROM agent_kb_document "
                    f"WHERE {where_sql} ORDER BY update_time DESC, id DESC "
                    "LIMIT :limit OFFSET :offset"
                ),
                page_params,
            )
        )
        .mappings()
        .all()
    )
    return {"total": total, "rows": [_document_row(row) for row in rows]}


async def set_enabled(session: AsyncSession, doc_code: str, enabled: bool | int) -> dict[str, Any]:
    """启用/停用文档（只改 enabled 标记，不删切片、不动历史检索记录）。"""
    code = _validate_doc_code(doc_code)
    flag = 1 if bool(enabled) else 0
    result = await session.execute(
        text(
            """
            UPDATE agent_kb_document
               SET enabled = :enabled,
                   update_time = NOW()
             WHERE doc_code = :doc_code
               AND del_flag = '0'
            """
        ),
        {"doc_code": code, "enabled": flag},
    )
    await session.commit()
    if int(getattr(result, "rowcount", 0) or 0) == 0:
        raise KbNotFoundError(f"文档不存在：{code}")
    return {"docCode": code, "enabled": flag}


_STATS_SQL = """
SELECT
  (SELECT COUNT(*) FROM agent_kb_document WHERE del_flag = '0') AS documents,
  (SELECT COUNT(*) FROM agent_kb_chunk c
     JOIN agent_kb_document d ON d.id = c.document_id
    WHERE c.enabled = 1 AND d.enabled = 1 AND d.del_flag = '0') AS chunks,
  (SELECT COUNT(*) FROM agent_kb_retrieval) AS retrievals,
  (SELECT COUNT(*) FROM agent_kb_retrieval
    WHERE fallback_reason IS NOT NULL) AS fallback_retrievals,
  (SELECT COUNT(*) FROM agent_kb_retrieval WHERE used = 1) AS used_retrievals
"""


async def stats(session: AsyncSession) -> dict[str, float | int]:
    """知识库概览 + 检索引用治理指标（单次聚合查询，页面顶部卡片的数据源）。

    除原有 `documents/chunks/retrievals` 外，增量返回 5-3 的回退率指标（**旧字段名不变**）：
    - `fallbackRetrievals`：`fallback_reason IS NOT NULL` 的检索次数（no_hit/low_score…）；
    - `fallbackRate`：`fallbackRetrievals / retrievals`，**无检索时为 0.0**（不做除法）；
    - `usedRetrievals`：`used=1` 的检索次数（被最终回答引用）。

    为什么用一条 SQL 的标量子查询而不是 5 次往返：这是页面首屏统计，
    多次往返会放大延迟；且 `agent_kb_retrieval` 只增，单次聚合口径天然一致（不会前后数字打架）。

    @return 字段值缺失/异常行按 0 处理（不能因为一行脏数据让统计接口 500）
    """
    rows = (await session.execute(text(_STATS_SQL))).mappings().all()
    row = rows[0] if rows else {}
    documents = int(row.get("documents") or 0)
    chunks = int(row.get("chunks") or 0)
    retrievals = int(row.get("retrievals") or 0)
    fallback_retrievals = int(row.get("fallback_retrievals") or 0)
    used_retrievals = int(row.get("used_retrievals") or 0)
    # 除零保护是 5-3 的明确要求：新库/未启用知识库时 retrievals=0 是常态，
    # 若抛 ZeroDivisionError，统计卡会把整页打成 500（看起来像“知识库坏了”）。
    fallback_rate = fallback_retrievals / retrievals if retrievals > 0 else 0.0
    return {
        "documents": documents,
        "chunks": chunks,
        "retrievals": retrievals,
        "fallbackRetrievals": fallback_retrievals,
        "fallbackRate": fallback_rate,
        "usedRetrievals": used_retrievals,
    }


__all__ = [
    "CATEGORIES",
    "CANDIDATE_LIMIT",
    "DEFAULT_DIM",
    "DOC_CODE_PATTERN",
    "KbNotFoundError",
    "KbValidationError",
    "MAX_CONTENT_BYTES",
    "SOURCE_TYPES",
    "chunk_text",
    "cosine",
    "ingest_document",
    "list_documents",
    "mark_used",
    "search",
    "set_enabled",
    "stats",
    "vectorize",
]
