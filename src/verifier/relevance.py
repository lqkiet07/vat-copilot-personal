"""Semantic and lexical relevance gating for legal articles."""
import math
import re

from src.api.chat_domain import normalize_query
from src.engine.models import ArticleNode

_STOPWORDS = {
    "tôi", "cho", "tại", "năm", "tháng", "ngày", "được", "theo", "quy", "định",
    "với", "là", "bao", "nhiêu", "của", "và", "các", "có", "phải", "không", "thì",
    "trên", "trong", "về", "một", "số", "khi", "ra", "sao", "tiền",
}


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Calculate cosine similarity between two numeric vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"\w+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def score_article_relevance(
    query: str,
    article: ArticleNode,
    query_vec: list[float] | None = None,
) -> bool:
    """Determine whether an article is substantively relevant via semantic cosine or lexical overlap."""
    q_norm = normalize_query(query).lower()
    art_text = f"{article.title} {article.content}".lower()

    if query_vec and article.embedding:
        sim = cosine_similarity(query_vec, article.embedding)
        if sim >= 0.70:
            return True
        if sim < 0.30:
            return False

    q_tokens = _tokenize(q_norm)
    art_tokens = _tokenize(art_text)
    if not q_tokens or not art_tokens:
        return True

    return len(q_tokens.intersection(art_tokens)) > 0


def build_relevance_map(
    query: str,
    articles: list[ArticleNode],
    query_vec: list[float] | None = None,
) -> dict[str, bool]:
    """Map article IDs to boolean relevance flags for citation verification."""
    return {
        art.id: score_article_relevance(query, art, query_vec=query_vec)
        for art in articles
    }
