"""Cross-Encoder provision reranker with dynamic threshold, article quota, and VRAM-aware device routing."""
from __future__ import annotations

import logging
import math
import os
import re
import threading
from typing import Any, Callable, Sequence

from src.engine.models import ArticleNode

_CPU_STOPWORDS = frozenset({
    "tôi", "cho", "tại", "năm", "tháng", "ngày", "được", "theo", "quy", "định",
    "với", "là", "bao", "nhiêu", "của", "và", "các", "có", "phải", "không", "thì",
    "trên", "trong", "về", "một", "số", "khi", "ra", "sao", "gì", "nào",
})


def reranker_mode() -> str:
    """Return reranker mode ('cross_encoder' or 'off'), defaulting off under pytest unless explicitly set."""
    raw = os.getenv("RERANKER_MODE")
    if raw is not None and raw.strip():
        mode = raw.strip().lower()
        return mode if mode in {"cross_encoder", "off"} else "off"
    return "off" if os.getenv("PYTEST_CURRENT_TEST") else "cross_encoder"


def reranker_device() -> str:
    """Select reranker device: use CPU under local Ollama to preserve 4 GB VRAM for qwen2.5:7b."""
    raw = os.getenv("RERANKER_DEVICE", "auto").strip().lower()
    if raw in {"cpu", "cuda"}:
        return raw
    if os.getenv("LLM_PROVIDER", "auto").strip().lower() == "ollama":
        return "cpu"
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def reranker_min_score() -> float:
    raw = os.getenv("RERANKER_MIN_SCORE", "0.15").strip()
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return 0.15


def reranker_article_quota() -> int:
    raw = os.getenv("RERANKER_ARTICLE_QUOTA", "2").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 2

logger = logging.getLogger(__name__)

DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
_MAX_TEXT_CHARS = 1000
_MODEL_LOCK = threading.Lock()
_MODEL_CACHE: dict[tuple[str, str], Any] = {}
_BOILERPLATE_CLAUSE_RE = re.compile(
    r"\b(?:hiệu lực thi hành|phạm vi điều chỉnh|tổ chức thực hiện|trách nhiệm thi hành)\b",
    re.IGNORECASE,
)
_EFFECTIVE_ASK_RE = re.compile(
    r"\b(?:hiệu lực|hết hiệu lực|thi hành|phạm vi điều chỉnh|bắt đầu áp dụng)\b",
    re.IGNORECASE,
)


def _adjust_candidate_score(query: str, art: ArticleNode, base_score: float) -> float:
    """Demote non-operative VBHN consolidation texts and pure boilerplate clauses unless queried."""
    score = base_score
    if art.doc_id.startswith("VBHN_") or art.id.startswith("VBHN_"):
        score *= 0.35
    if _BOILERPLATE_CLAUSE_RE.search(f"{art.title} {art.content}") and not _EFFECTIVE_ASK_RE.search(query or ""):
        score *= 0.65
    return score


def _is_explicit(article_id: str, explicit_ids: Sequence[str]) -> bool:
    return any(article_id == eid or article_id.startswith(eid + "_") for eid in explicit_ids)


def _parent_article_id(provision_id: str) -> str:
    return provision_id.split("_K", 1)[0]


def _normalize_scores(raw_scores: Any) -> list[float]:
    scores: list[float] = []
    for raw in raw_scores:
        val = float(raw)
        if val < 0.0 or val > 1.0:
            # Convert raw logits (outside [0, 1]) safely via bounded sigmoid.
            clamped = max(-50.0, min(50.0, val))
            val = 1.0 / (1.0 + math.exp(-clamped))
        scores.append(val)
    return scores


def _get_cross_encoder(model_name: str, device: str):
    key = (model_name, device)
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached
    with _MODEL_LOCK:
        cached = _MODEL_CACHE.get(key)
        if cached is not None:
            return cached
        import torch
        from sentence_transformers import CrossEncoder

        model_kwargs = {"dtype": torch.float16} if device == "cuda" else {}
        model = CrossEncoder(
            model_name,
            device=device,
            max_length=512,
            model_kwargs=model_kwargs,
        )
        _MODEL_CACHE[key] = model
        return model


def _default_score(pairs: list[tuple[str, str]]) -> list[float]:
    model_name = os.getenv("RERANKER_MODEL", DEFAULT_RERANKER_MODEL).strip() or DEFAULT_RERANKER_MODEL
    device = reranker_device()
    model = _get_cross_encoder(model_name, device)
    raw_scores = model.predict(pairs, batch_size=16, show_progress_bar=False)
    return _normalize_scores(raw_scores)


def _apply_article_quota(items: list[ArticleNode], quota: int) -> list[ArticleNode]:
    counts: dict[str, int] = {}
    kept: list[ArticleNode] = []
    for art in items:
        parent = _parent_article_id(art.id)
        if counts.get(parent, 0) < quota:
            kept.append(art)
            counts[parent] = counts.get(parent, 0) + 1
    return kept


def warmup_reranker() -> None:
    """Warm up Cross-Encoder weights once on startup when cross_encoder mode is active."""
    if reranker_mode() != "cross_encoder":
        return
    try:
        model_name = os.getenv("RERANKER_MODEL", DEFAULT_RERANKER_MODEL).strip() or DEFAULT_RERANKER_MODEL
        _get_cross_encoder(model_name, reranker_device())
    except Exception as exc:
        logger.warning("Reranker warmup skipped: %s", exc)


def _tokenize_for_prefilter(text: str) -> set[str]:
    return {
        tok for tok in re.findall(r"\w+", (text or "").lower())
        if len(tok) >= 2 and tok not in _CPU_STOPWORDS
    }


def _prefilter_for_cpu(
    query: str,
    candidates: list[ArticleNode],
    *,
    vector_keep: int = 20,
    lexical_keep: int = 15,
    max_total: int = 35,
) -> list[ArticleNode]:
    """Keep top vector/graph candidates plus best lexical matches so CPU reranking stays fast."""
    if len(candidates) <= max_total:
        return list(candidates)

    head = candidates[:vector_keep]
    seen_ids = {art.id for art in head}
    q_tokens = _tokenize_for_prefilter(query)

    scored_tail: list[tuple[int, int, ArticleNode]] = []
    for idx, art in enumerate(candidates[vector_keep:]):
        if art.id in seen_ids:
            continue
        doc_tokens = _tokenize_for_prefilter(f"{art.title} {art.content}")
        overlap = len(q_tokens & doc_tokens)
        if overlap > 0:
            scored_tail.append((overlap, -idx, art))

    scored_tail.sort(key=lambda item: (item[0], item[1]), reverse=True)
    kept = list(head)
    for _, _, art in scored_tail[:lexical_keep]:
        if len(kept) >= max_total:
            break
        kept.append(art)
        seen_ids.add(art.id)

    for art in candidates[vector_keep:]:
        if len(kept) >= max_total:
            break
        if art.id not in seen_ids:
            kept.append(art)
            seen_ids.add(art.id)
    return kept


_POINT_PARENT_RE = re.compile(r"^(.+_K\d+[a-z]?)_.+$", re.IGNORECASE)


def _parent_clause_id(provision_id: str) -> str | None:
    """Return parent Clause ID (`X_Km`) when provision_id is a nested Point leaf."""
    match = _POINT_PARENT_RE.match(provision_id or "")
    return match.group(1) if match else None


def _select_rerank_window(query: str, text: str, max_chars: int = _MAX_TEXT_CHARS) -> str:
    """Keep leading header plus the tail window covering query tokens absent from the prefix."""
    if len(text) <= max_chars:
        return text
    q_tokens = _tokenize_for_prefilter(query)
    if not q_tokens:
        return text[:max_chars]

    head_len = min(200, max_chars // 4)
    cutoff = max(head_len, max_chars - 120)
    missing_tokens = q_tokens - _tokenize_for_prefilter(text[:cutoff])
    if not missing_tokens:
        return text[:max_chars]

    best_score, best_offset = (0, 0), -1
    for start in range(cutoff, len(text), 180):
        seg_tokens = _tokenize_for_prefilter(text[start:start + 280])
        score = (len(missing_tokens & seg_tokens), len(q_tokens & seg_tokens))
        if score > best_score:
            best_score, best_offset = score, start

    if best_score[0] == 0 or best_offset < 0:
        return text[:max_chars]

    tail_budget = max(0, max_chars - head_len - 5)
    tail_start = max(head_len, best_offset - tail_budget // 3)
    tail_end = min(len(text), tail_start + tail_budget)
    tail_start = max(head_len, tail_end - tail_budget)
    return f"{text[:head_len].rstrip()} ... {text[tail_start:tail_end].lstrip()}"[:max_chars]


def _build_scoring_text(
    query: str,
    art: ArticleNode,
    by_id: dict[str, ArticleNode],
    max_chars: int = _MAX_TEXT_CHARS,
) -> str:
    """Format scoring input with parent lead-in for Point leaves without mutating source text."""
    parent_id = _parent_clause_id(art.id)
    parent = by_id.get(parent_id) if parent_id else None
    if parent is not None and parent.content:
        raw = f"{art.title}: {parent.content} {art.content}".strip(": ")
    else:
        raw = f"{art.title}: {art.content}".strip(": ")
    return _select_rerank_window(query, raw, max_chars=max_chars)


def _is_boilerplate_clause(art: ArticleNode) -> bool:
    return bool(_BOILERPLATE_CLAUSE_RE.search(art.content or ""))


def expand_companion_provisions(
    selected: Sequence[ArticleNode],
    pool: Sequence[ArticleNode],
    *,
    max_total: int = 12,
) -> list[ArticleNode]:
    """Interleave unmodified parent Clause lead-ins and counterpart clauses adjacent to each anchor."""
    if not selected or not pool:
        return list(selected)
    by_id = {art.id: art for art in pool}
    clauses_by_article: dict[str, list[ArticleNode]] = {}
    for art in pool:
        if "_K" in art.id and _parent_clause_id(art.id) is None and not _is_boilerplate_clause(art):
            clauses_by_article.setdefault(_parent_article_id(art.id), []).append(art)

    result: list[ArticleNode] = []
    seen: set[str] = set()

    def _add(candidate: ArticleNode | None) -> None:
        if candidate is not None and candidate.id not in seen and len(result) < max_total:
            result.append(candidate)
            seen.add(candidate.id)

    for art in selected:
        if len(result) >= max_total:
            break
        _add(art)
        pid = _parent_clause_id(art.id)
        if pid:
            parent = by_id.get(pid)
            if parent is not None and not _is_boilerplate_clause(parent):
                _add(parent)
        siblings = clauses_by_article.get(_parent_article_id(art.id), [])
        # Place symmetric threshold counterparts immediately after anchor so pack_evidence keeps both.
        if 2 <= len(siblings) <= 3:
            for sib in siblings:
                _add(sib)
    return result


def ensure_scope_doc_coverage(
    selected: Sequence[ArticleNode],
    pool: Sequence[ArticleNode],
    scope_docs: Sequence[str],
    *,
    per_doc_min: int = 2,
    insert_at: int = 4,
    max_total: int = 12,
) -> list[ArticleNode]:
    """Guarantee every explicitly scoped document has provisions inside the packable top window."""
    if not pool or not scope_docs or per_doc_min <= 0:
        return list(selected)
    window_end = max(8, insert_at + len(scope_docs) * per_doc_min)
    head = list(selected[:window_end])
    doc_of = lambda cid: cid.split("_D", 1)[0]

    candidates_by_doc: dict[str, list[ArticleNode]] = {}
    for art in list(selected[window_end:]) + list(pool):
        if _is_boilerplate_clause(art):
            continue
        candidates_by_doc.setdefault(doc_of(art.id), []).append(art)

    inject: list[ArticleNode] = []
    seen_head = {a.id for a in head}
    for doc in scope_docs:
        have = sum(1 for a in head if doc_of(a.id) == doc)
        need = max(0, per_doc_min - have)
        for cand in candidates_by_doc.get(doc, []):
            if need <= 0:
                break
            if cand.id in seen_head:
                continue
            inject.append(cand)
            seen_head.add(cand.id)
            need -= 1

    if not inject:
        return list(selected)[:max_total]
    inject_ids = {a.id for a in inject}
    pivot = min(insert_at, len(selected))
    tail = [a for a in selected[pivot:] if a.id not in inject_ids]
    return (list(selected[:pivot]) + inject + tail)[:max_total]


def preserve_top_retrieved_anchor(
    selected: Sequence[ArticleNode],
    pool: Sequence[ArticleNode],
    top_parent_id: str | None,
    *,
    insert_at: int = 5,
    max_total: int = 12,
) -> list[ArticleNode]:
    """Keep clauses of the #1 retrieved article inside the top pack window if reranking demotes them."""
    if not top_parent_id or top_parent_id.startswith("VBHN_") or not pool:
        return list(selected)
    top_window = {a.id for a in selected[:7]}
    anchors = [
        a for a in pool
        if a.id.startswith(top_parent_id + "_") and not _is_boilerplate_clause(a)
    ] or [a for a in pool if a.id == top_parent_id]
    missing = [a for a in anchors[:3] if a.id not in top_window][:2]
    if not missing:
        return list(selected)[:max_total]
    missing_ids = {a.id for a in missing}
    pivot = min(insert_at, len(selected))
    tail = [a for a in selected[pivot:] if a.id not in missing_ids]
    return (list(selected[:pivot]) + missing + tail)[:max_total]


def rerank_provisions(
    query: str,
    provisions: Sequence[ArticleNode],
    *,
    explicit_ids: Sequence[str] = (),
    min_score: float | None = None,
    article_quota: int | None = None,
    top_n: int = 5,
    min_k: int = 3,
    scorer: Callable[[list[tuple[str, str]]], Sequence[float]] | None = None,
) -> list[ArticleNode]:
    """Rerank provisions by semantic relevance with dynamic threshold and per-article quota."""
    items = list(provisions)
    if not items:
        return []
    if scorer is None and reranker_mode() == "off":
        return items

    threshold = reranker_min_score() if min_score is None else min_score
    quota = reranker_article_quota() if article_quota is None else max(1, article_quota)
    explicit = [p for p in items if _is_explicit(p.id, explicit_ids)]
    candidates = [p for p in items if not _is_explicit(p.id, explicit_ids)]
    remaining_slots = max(0, top_n - len(explicit))

    if not candidates or remaining_slots == 0:
        return explicit[:top_n] if top_n > 0 else explicit

    by_id = {p.id: p for p in items}
    if scorer is None and reranker_device() == "cpu" and len(candidates) > 35:
        candidates = _prefilter_for_cpu(query, candidates)

    pairs = [
        (query, _build_scoring_text(query, p, by_id, max_chars=_MAX_TEXT_CHARS))
        for p in candidates
    ]
    try:
        raw = scorer(pairs) if scorer is not None else _default_score(pairs)
        scores = _normalize_scores(raw)
        if len(scores) != len(candidates):
            raise ValueError("Reranker returned mismatched score count")
    except Exception as exc:
        logger.warning("Reranker failed-open (%s); keeping incoming candidate order.", exc)
        return (explicit + _apply_article_quota(candidates, quota)[:remaining_slots])

    adjusted_scores = [
        _adjust_candidate_score(query, art, sc)
        for art, sc in zip(candidates, scores)
    ]
    ranked = sorted(zip(candidates, adjusted_scores), key=lambda pair: pair[1], reverse=True)
    filtered = [art for art, score in ranked if score >= threshold]
    floor_k = max(1, min_k)
    if not explicit and len(filtered) < floor_k and ranked:
        # Preserve at least min_k top-ranked provisions so a single date/header clause cannot starve retrieval.
        filtered = [art for art, _ in ranked[:floor_k]]

    kept = _apply_article_quota(filtered, quota)[:remaining_slots]
    return explicit + kept
