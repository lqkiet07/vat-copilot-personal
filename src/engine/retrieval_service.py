from collections.abc import Callable
from dataclasses import dataclass, field
from inspect import Parameter, signature
from src.api.chat_domain import normalize_query
from src.engine.models import ArticleNode
from src.engine.provision_evidence import EvidenceResolution, resolve_provision_evidence
from dataclasses import replace
import re
from src.ui.corpus_viewer import load_corpus_tree
from src.verifier.citation_verifier import extract_citations
import unicodedata
from src.engine.temporal_retriever import query_temporal_rag
from src.verifier.citation_verifier import verify_citations

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
    resolved_evidence: EvidenceResolution | None = None


def _unit_authorities(text, unit, authorities):
    candidates = []
    for hint, docs in authorities:
        start, end = (unit.end(), hint.start()) if unit.end() <= hint.start() else (hint.end(), unit.start())
        gap = text[start:end]
        if start <= end and not re.search(r"[;.\n]|\b(?:sua doi|bo sung|sau)\b", gap):
            candidates.append((end - start, docs))
    if not candidates:
        return authorities[0][1] if len(authorities) == 1 else []
    distance = min(size for size, _ in candidates)
    closest = [docs for size, docs in candidates if size == distance]
    return closest[0] if len(closest) == 1 else []


def explicit_evidence_ids(query, corpus_path):
    folded = unicodedata.normalize("NFD", query.lower()).replace("đ", "d")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    units = list(re.finditer(
        r"(?:diem\s*([a-z])\s+)?(?:khoan\s*(\d+[a-z]?)\s+)?dieu\s*(\d+[a-z]?)\b"
        r"(?:\s+khoan\s*(\d+[a-z]?))?(?:\s+diem\s*([a-z]))?", folded))
    hints = list(re.finditer(
        r"\b(luat|nghi dinh|thong tu|nghi quyet|quyet dinh)\s*(?:so\s+)?(\d+)(?:[/-](\d{4}))?\b", folded))
    families = {"luat": "LUAT", "nghi dinh": "ND", "thong tu": "TT", "nghi quyet": "NQ", "quyet dinh": "QD"}
    ids = list(extract_citations(query))
    if not units and not hints:
        return ids
    docs = load_corpus_tree(corpus_path)
    authorities = []
    for hint in hints:
        family, number, year = hint.groups()
        matches = [d for d in docs if d["id"].split("_")[:2] == [families[family], number]
                   and (not year or d["id"].split("_")[2] == year)]
        if not matches:
            ids.append(f"missing_explicit_authority_{families[family]}_{number}_{year or ''}")
        authorities.append((hint, matches))
    if not units:
        ids.extend(a["id"] for _, selected in authorities for d in selected for a in d.get("articles", []))
    for unit in units:
        point, clause, article, trailing_clause, trailing_point = unit.groups()
        selected = _unit_authorities(folded, unit, authorities) if authorities else docs
        suffix = "_D" + article
        clause, point = clause or trailing_clause, point or trailing_point
        suffix += "_K" + clause if clause else ""
        suffix += "_" + point if point else ""
        matches = [a["id"] + suffix[len("_D" + article):] for d in selected for a in d.get("articles", [])
                   if a["id"].endswith("_D" + article)]
        ids.extend(matches or ["missing_explicit_unit_" + suffix])
    return list(dict.fromkeys(ids))


def _provision_title(provision, owner):
    if owner and owner.title:
        return owner.title
    for node in reversed(getattr(provision, "hierarchy", ())):
        if node.get("title"):
            return node["title"]
    return ""


def project_reviewed_articles(raw_articles, query, day, corpus_path, review_path, verifier_fn):
    from src.engine.provision_selector import rank_provisions
    explicit = explicit_evidence_ids(query, corpus_path)
    resolution = resolve_provision_evidence(
        [a.id for a in raw_articles] + explicit, day, corpus_path=corpus_path,
        reviewed_amendment_path=review_path,
    )
    projected = {}
    for item in resolution.items:
        owner = next((a for a in raw_articles if a.id == item.id), None)
        for p in item.provisions:
            projected[p.id] = ArticleNode(
                id=p.id, doc_id=p.id.split("_D")[0], code=owner.code if owner else p.id,
                title=_provision_title(p, owner), content=p.text,
                valid_from=p.valid_from, valid_to=p.valid_to, status=p.status,
            )
    rel_map = {}
    report = _invoke_callback(
        verifier_fn or verify_citations, " ".join(f"[{cid}]" for cid in projected), day,
        relevance_map=rel_map, context_ids=set(projected), resolved_evidence=resolution,
        corpus_path=corpus_path, reviewed_amendment_path=review_path,
    )
    valid = {c.id for c in report.citations if c.status in _ALLOWED_STATUSES}
    selected = rank_provisions(
        query,
        [a for cid, a in projected.items() if cid in valid],
        explicit_ids=explicit,
        parent_order=[a.id for a in raw_articles],
    )
    return selected, replace(resolution, items=tuple(
        replace(item, provisions=tuple(p for p in item.provisions if p.id in valid))
        for item in resolution.items
    )), rel_map


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
    rel_map = {}
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
    reviewed_amendment_path: str | None = None,
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

    if reviewed_amendment_path is not None:
        selected, resolution, rel_map = project_reviewed_articles(
            raw_articles, normalized_q, as_of_date, corpus_path, reviewed_amendment_path, verifier_fn,
        )
        return RetrievalResult(
            query=query, normalized_query=normalized_q, as_of_date=as_of_date,
            articles=selected, active_ids=[a.id for a in selected],
            raw_article_count=len(raw_articles), gated_article_count=len(selected),
            relevance_map=rel_map, resolved_evidence=resolution,
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
