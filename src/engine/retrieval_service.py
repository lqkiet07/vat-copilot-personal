from collections.abc import Callable
from dataclasses import dataclass, field
from inspect import Parameter, signature
from src.api.chat_domain import normalize_query
from src.engine.models import ArticleNode
from src.engine.temporal_retriever import query_temporal_rag
from src.verifier.citation_verifier import verify_citations
from src.verifier.relevance import build_relevance_map

_DEFAULT_CORPUS_PATH = "data/processed/vat_corpus.json"


def _invoke_callback(callback, *args, corpus_path=_DEFAULT_CORPUS_PATH, **kwargs):
    parameters = signature(callback).parameters
    variadic = any(p.kind == Parameter.VAR_KEYWORD for p in parameters.values())
    if variadic or "corpus_path" in parameters:
        kwargs["corpus_path"] = corpus_path
    elif corpus_path != _DEFAULT_CORPUS_PATH:
        raise TypeError("Callback must accept corpus_path for a nondefault corpus")
    accepted = kwargs if variadic else {k: v for k, v in kwargs.items() if k in parameters}
    signature(callback).bind(*args, **accepted)
    return callback(*args, **accepted)

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
    unresolved_citations: list[str] = field(default_factory=list)
    unresolved_warnings: list[str] = field(default_factory=list)


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
    corpus_path: str,
) -> tuple[list[ArticleNode], dict[str, bool], list[str], list[str]]:
    if not raw_articles:
        return [], {}, [], []
    rel_map = build_relevance_map(normalized_q, raw_articles)
    cite_text = " ".join(f"[{a.id}]" for a in raw_articles)
    checker = verifier_fn or verify_citations
    report = _invoke_callback(
        checker, cite_text, as_of_date, relevance_map=rel_map, corpus_path=corpus_path,
    )
    valid_ids = {c.id for c in report.citations if c.status in _ALLOWED_STATUSES}
    unresolved_ids = [c.id for c in report.citations if getattr(c, "status", "") == "UNRESOLVED"]
    unresolved_warnings = [c.message for c in report.citations if c.status == "UNRESOLVED" and c.message]
    return [a for a in raw_articles if a.id in valid_ids], rel_map, unresolved_ids, unresolved_warnings


def retrieve_and_gate(
    query: str,
    as_of_date: str,
    top_k: int = 5,
    pipeline: str = "P3_FullSystem",
    retriever_fn: Callable | None = None,
    verifier_fn: Callable | None = None,
    *,
    corpus_path: str = _DEFAULT_CORPUS_PATH,
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

    verified, rel_map, unresolved_ids, unresolved_warnings = _gate_p3_articles(raw_articles, normalized_q, as_of_date, verifier_fn, corpus_path)
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
        unresolved_citations=unresolved_ids,
        unresolved_warnings=unresolved_warnings,
    )
