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

    if scorer is None and reranker_device() == "cpu" and len(candidates) > 35:
        candidates = _prefilter_for_cpu(query, candidates)

    pairs = [
        (query, f"{p.title}: {p.content}".strip(": ")[:_MAX_TEXT_CHARS])
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

    ranked = sorted(zip(candidates, scores), key=lambda pair: pair[1], reverse=True)
    filtered = [art for art, score in ranked if score >= threshold]
    floor_k = max(1, min_k)
    if not explicit and len(filtered) < floor_k and ranked:
        # Preserve at least min_k top-ranked provisions so a single date/header clause cannot starve retrieval.
        filtered = [art for art, _ in ranked[:floor_k]]

    kept = _apply_article_quota(filtered, quota)[:remaining_slots]
    return explicit + kept
