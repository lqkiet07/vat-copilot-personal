"""Domain-neutral Clause/Point evidence reranker using structural BM25 and parent priors."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
import math
import re
import unicodedata

from src.api.chat_domain import normalize_query, understand_query
from src.engine.models import ArticleNode

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _fold_tokens(text: str) -> list[str]:
    cleaned = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFKD", cleaned)
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()
    return _TOKEN_RE.findall(ascii_text)


def _weighted_terms(text: str) -> dict[str, float]:
    tokens = _fold_tokens(text)
    terms: dict[str, float] = {}
    for size, weight in ((1, 1.0), (2, 1.6), (3, 2.0)):
        if len(tokens) < size:
            continue
        for i in range(len(tokens) - size + 1):
            term = "_".join(tokens[i:i + size])
            terms[term] = terms.get(term, 0.0) + weight
    return terms


def _bm25_scores(query_terms: dict[str, float], docs: list[dict[str, float]]) -> list[float]:
    if not docs or not query_terms:
        return [0.0] * len(docs)
    n_docs = len(docs)
    lengths = [sum(tf for term, tf in doc.items() if "_" not in term) or 1.0 for doc in docs]
    avg_len = sum(lengths) / n_docs
    df = Counter(term for doc in docs for term in doc if term in query_terms)
    k1, b = 1.2, 0.75
    scores = []
    for doc, length in zip(docs, lengths):
        norm = k1 * (1.0 - b + b * (length / avg_len))
        total = 0.0
        for term, q_weight in query_terms.items():
            tf = doc.get(term, 0.0)
            if tf <= 0.0:
                continue
            idf = math.log((n_docs - df[term] + 0.5) / (df[term] + 0.5) + 1.0)
            total += q_weight * idf * ((tf * (k1 + 1.0)) / (tf + norm))
        scores.append(total)
    return scores


def _parent_rank_lookup(parent_order: Sequence[str] | None) -> dict[str, int]:
    if not parent_order:
        return {}
    ranks: dict[str, int] = {}
    for idx, pid in enumerate(parent_order):
        if pid not in ranks:
            ranks[pid] = idx
    return ranks


def _parent_prior(article: ArticleNode, ranks: dict[str, int]) -> float:
    if not ranks:
        return 0.0
    best = ranks.get(article.id)
    if best is None:
        # Match the closest parent ID prefix when clause/point IDs were projected from an article hit.
        for candidate, rank in ranks.items():
            if article.id.startswith(candidate + "_") and (best is None or rank < best):
                best = rank
    if best is None:
        best = ranks.get(article.doc_id)
    return (0.05 / (best + 1)) if best is not None else 0.0


def rank_provisions(
    query: str,
    articles: list[ArticleNode],
    *,
    explicit_ids: Sequence[str] = (),
    parent_order: Sequence[str] | None = None,
    synonyms: Sequence[str] = (),
) -> list[ArticleNode]:
    """Rank projected provisions by explicit citation match, lexical BM25, and parent RRF prior."""
    if len(articles) <= 1:
        return list(articles)
    norm_query = normalize_query(query)
    entities = understand_query(norm_query)
    query_terms = _weighted_terms(norm_query)
    for syn in (*entities.industry_synonyms, *synonyms):
        for term, weight in _weighted_terms(syn).items():
            query_terms[term] = max(query_terms.get(term, 0.0), weight * 0.8)

    content_docs = [_weighted_terms(article.content) for article in articles]
    docs = []
    for article, content_terms in zip(articles, content_docs):
        combined = dict(content_terms)
        for term, weight in _weighted_terms(article.title).items():
            combined[term] = combined.get(term, 0.0) + 0.5 * weight
        docs.append(combined)

    scores = _bm25_scores(query_terms, docs)
    n_docs = len(content_docs)
    content_unigrams = [{term for term in c if "_" not in term} for c in content_docs]
    content_df = Counter(unigram for cu in content_unigrams for unigram in cu)
    pool_idf = {
        unigram: math.log((n_docs - count + 0.5) / (count + 0.5) + 1.0)
        for unigram, count in content_df.items()
    }
    q_unigrams = {term for term in query_terms if "_" not in term}
    for i, c_unigrams in enumerate(content_unigrams):
        matched = c_unigrams & q_unigrams
        if len(matched) >= 2 and c_unigrams:
            # Reward concise statutory provisions whose whole body matches the query over long incidental hits.
            total_idf = sum(pool_idf[u] for u in c_unigrams)
            matched_idf = sum(pool_idf[u] for u in matched)
            coverage = (matched_idf / total_idf) if total_idf > 0 else 0.0
            scores[i] *= 1.0 + 1.2 * coverage

    ranks = _parent_rank_lookup(parent_order)
    explicit_list = list(explicit_ids)

    def _explicit_priority(aid: str) -> tuple[int, int]:
        for idx, eid in enumerate(explicit_list):
            if aid == eid or aid.startswith(eid + "_"):
                return (1, -idx)
        return (0, 0)

    indexed = list(enumerate(articles))
    indexed.sort(
        key=lambda item: (
            _explicit_priority(item[1].id),
            scores[item[0]] + _parent_prior(item[1], ranks),
            -item[0],
        ),
        reverse=True,
    )
    return [article for _, article in indexed]
