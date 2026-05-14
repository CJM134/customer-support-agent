from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings
from app.models.schemas import Category, KnowledgeHit

logger = logging.getLogger(__name__)

VECTOR_DIMENSIONS = 384


def tokenize(text: str) -> set[str]:
    words = set(re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]+", text.lower()))
    chars = {char for char in text.lower() if "\u4e00" <= char <= "\u9fff"}
    return words | chars


def embedding_terms(text: str) -> list[str]:
    """Build lightweight local embedding terms for Chinese/English support text."""
    normalized = text.lower()
    latin_terms = re.findall(r"[a-z0-9_]+", normalized)
    chinese_chars = [char for char in normalized if "\u4e00" <= char <= "\u9fff"]
    chinese_ngrams: list[str] = []
    for size in (1, 2, 3):
        chinese_ngrams.extend(
            "".join(chinese_chars[index : index + size])
            for index in range(0, max(len(chinese_chars) - size + 1, 0))
        )
    return latin_terms + chinese_ngrams


def stable_bucket(term: str) -> int:
    digest = hashlib.blake2b(term.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, byteorder="big") % VECTOR_DIMENSIONS


def article_text(article: dict) -> str:
    return " ".join(
        [
            article["title"],
            article["answer"],
            " ".join(article.get("keywords", [])),
            article["category"],
        ]
    )


class KnowledgeBase:
    """RAG knowledge base with ZhipuAI embedding (fallback to local hashed-vector)."""

    def __init__(self, path: Path):
        self.path = path
        self.articles = self._load_articles()
        self.idf = self._build_idf()
        logger.info("知识库加载完成: %d 条规则, 来源: %s", len(self.articles), path)
        self.index = [
            {
                "article": article,
                "vector": self._embed(article_text(article)),
                "tokens": tokenize(article_text(article)),
                "keyword_tokens": tokenize(" ".join(article.get("keywords", []))),
            }
            for article in self.articles
        ]

    def _load_articles(self) -> list[dict]:
        with self.path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def _build_idf(self) -> dict[str, float]:
        documents = [set(embedding_terms(article_text(article))) for article in self.articles]
        document_count = len(documents)
        df: Counter[str] = Counter()
        for terms in documents:
            df.update(terms)
        return {
            term: math.log((1 + document_count) / (1 + frequency)) + 1
            for term, frequency in df.items()
        }

    def _embed(self, text: str) -> dict[int, float]:
        settings = get_settings()
        if settings.zhipu_embedding_api_key:
            try:
                return self._zhipu_embed(text)
            except Exception as exc:
                logger.warning("智谱 Embedding 失败，回退到本地向量化: %s", exc)
        return self._local_embed(text)

    def _zhipu_embed(self, text: str) -> dict[int, float]:
        from openai import OpenAI

        settings = get_settings()
        client = OpenAI(
            api_key=settings.zhipu_embedding_api_key,
            base_url=settings.zhipu_embedding_base_url,
            timeout=settings.external_api_timeout_seconds,
        )
        response = client.embeddings.create(
            model=settings.zhipu_embedding_model,
            input=text,
        )
        vector: list[float] = response.data[0].embedding
        return {idx: val for idx, val in enumerate(vector) if val != 0.0}

    ##本地兜底向量化模型
    def _local_embed(self, text: str) -> dict[int, float]:
        counts = Counter(embedding_terms(text))
        vector: dict[int, float] = {}
        for term, count in counts.items():
            bucket = stable_bucket(term)
            weight = (1 + math.log(count)) * self.idf.get(term, 1.0)
            vector[bucket] = vector.get(bucket, 0.0) + weight
        return normalize_sparse_vector(vector)

    def search(self, query: str, category: Category | None = None, limit: int = 3) -> list[KnowledgeHit]:
        query_vector = self._embed(query)
        query_tokens = tokenize(query)
        scored: list[KnowledgeHit] = []
        logger.debug("知识库检索: category=%s query_len=%d", category, len(query))

        for item in self.index:
            article = item["article"]
            semantic_score = cosine_similarity(query_vector, item["vector"])
            keyword_score = keyword_overlap_score(query_tokens, item["keyword_tokens"])
            category_score = 0.08 if category and article["category"] == category else 0
            mismatch_penalty = 0.18 if category and article["category"] != category else 0
            score = max(0.0, semantic_score * 0.82 + keyword_score * 0.24 + category_score - mismatch_penalty)

            if score < 0.04:
                continue

            scored.append(
                KnowledgeHit(
                    id=article["id"],
                    title=article["title"],
                    category=article["category"],
                    score=round(min(score, 1.0), 3),
                    answer=article["answer"],
                    retrieval_method="rag_vector",
                )
            )

        top = sorted(scored, key=lambda item: item.score, reverse=True)[:limit]
        if top:
            logger.debug("知识库最高匹配: id=%s title=%s score=%.3f", top[0].id, top[0].title, top[0].score)
        else:
            logger.debug("知识库检索结果为空")
        return top


def normalize_sparse_vector(vector: dict[int, float]) -> dict[int, float]:
    norm = math.sqrt(sum(value * value for value in vector.values()))
    if norm == 0:
        return vector
    return {key: value / norm for key, value in vector.items()}


def cosine_similarity(left: dict[int, float], right: dict[int, float]) -> float:
    if not left or not right:
        return 0.0
    smaller, larger = (left, right) if len(left) <= len(right) else (right, left)
    return sum(value * larger.get(key, 0.0) for key, value in smaller.items())


def keyword_overlap_score(query_tokens: set[str], keyword_tokens: set[str]) -> float:
    if not query_tokens or not keyword_tokens:
        return 0.0
    return len(query_tokens & keyword_tokens) / max(len(keyword_tokens), 1)


@lru_cache
def get_knowledge_base() -> KnowledgeBase:
    return KnowledgeBase(get_settings().knowledge_base_path)
