from collections.abc import Callable
from dataclasses import dataclass, field
from src.api.chat_domain import normalize_query
from src.engine.models import ArticleNode
from src.engine.temporal_retriever import query_temporal_rag
from src.verifier.citation_verifier import verify_citations
from src.verifier.relevance import build_relevance_map

_ALLOWED_STATUSES = {"VALID", "AMENDED"}


@dataclass
class RetrievalResult:
    query: str
    normalized_query: str
    as_of_date: str
    articles: list[ArticleNode] = field(default_factory=list)
    active_ids: list[str] = field(default_factory=list)
    raw_article_count: int = 0
    gated_article_count: int = 0
    relevance_map: dict[str, bool] = field(default_factory=dict)


def _handle_p1(query: str, normalized_q: str, as_of_date: str, top_k: int) -> RetrievalResult:
    from src.benchmark.baselines import run_p1_baseline_rag

    active_ids = run_p1_baseline_rag(normalized_q, as_of_date, top_k=top_k)
    return RetrievalResult(
        query=query,
        normalized_query=normalized_q,
        as_of_date=as_of_date,
        active_ids=active_ids,
    )


def _gate_p3_articles(
    raw_articles: list[ArticleNode],
    normalized_q: str,
    as_of_date: str,
    verifier_fn: Callable | None,
) -> tuple[list[ArticleNode], dict[str, bool]]:
    if not raw_articles:
        return [], {}
    rel_map = build_relevance_map(normalized_q, raw_articles)
    cite_text = " ".join(f"[{a.id}]" for a in raw_articles)
    checker = verifier_fn or verify_citations
    report = checker(cite_text, as_of_date, relevance_map=rel_map)
    valid_ids = {c.id for c in report.citations if c.status in _ALLOWED_STATUSES}
    return [a for a in raw_articles if a.id in valid_ids], rel_map


def retrieve_and_gate(
    query: str,
    as_of_date: str,
    top_k: int = 5,
    pipeline: str = "P3_FullSystem",
    retriever_fn: Callable | None = None,
    verifier_fn: Callable | None = None,
) -> RetrievalResult:
    normalized_q = normalize_query(query)
    if pipeline == "P1_FlatRAG":
        return _handle_p1(query, normalized_q, as_of_date, top_k)

    fetcher = retriever_fn or query_temporal_rag
    res = fetcher(normalized_q, as_of_date)
    raw_articles = getattr(res, "articles", []) or []

    if pipeline in ("P2", "P2_TemporalGraphRAG"):
        selected = raw_articles[:top_k]
        return RetrievalResult(
            query=query,
            normalized_query=normalized_q,
            as_of_date=as_of_date,
            articles=selected,
            active_ids=[a.id for a in selected],
            raw_article_count=len(raw_articles),
            gated_article_count=len(selected),
        )

    verified, rel_map = _gate_p3_articles(raw_articles, normalized_q, as_of_date, verifier_fn)
    selected = verified[:top_k]
    return RetrievalResult(
        query=query,
        normalized_query=normalized_q,
        as_of_date=as_of_date,
        articles=selected,
        active_ids=[a.id for a in selected],
        raw_article_count=len(raw_articles),
        gated_article_count=len(verified),
        relevance_map=rel_map,
    )
