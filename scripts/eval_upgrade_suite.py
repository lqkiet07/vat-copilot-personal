"""Before/After generalization & upgrade benchmark runner for Phase 5b."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
import time
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

DEFAULT_FIXTURE_PATH = "tests/fixtures/upgrade_eval_25.json"
DEFAULT_REPORT_PATH = "benchmarks/upgrade_eval_report.json"


def load_upgrade_cases(path: str | Path = DEFAULT_FIXTURE_PATH) -> list[dict[str, Any]]:
    """Load the 25-case upgrade evaluation fixture."""
    return json.loads(Path(path).read_bytes().decode("utf-8"))


def select_balanced_cases(cases: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    """Select cases evenly across categories when a smaller limit is requested."""
    if limit is None or limit <= 0 or limit >= len(cases):
        return list(cases)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    order: list[str] = []
    for case in cases:
        cat = str(case.get("category") or "default")
        if cat not in buckets:
            order.append(cat)
        buckets[cat].append(case)
    selected: list[dict[str, Any]] = []
    idx = 0
    while len(selected) < limit:
        progressed = False
        for cat in order:
            if idx < len(buckets[cat]):
                selected.append(buckets[cat][idx])
                progressed = True
                if len(selected) >= limit:
                    break
        if not progressed:
            break
        idx += 1
    return selected


def match_prefix_hit(candidate_ids: Sequence[str], expected_prefixes: Sequence[str]) -> bool:
    """Return True if any candidate ID matches an expected document/article prefix."""
    if not expected_prefixes:
        return True
    for cid in candidate_ids:
        for prefix in expected_prefixes:
            if cid == prefix or cid.startswith(prefix + "_") or cid.startswith(prefix):
                return True
    return False


def has_forbidden_noise(texts: Sequence[str], forbidden_substrings: Sequence[str]) -> bool:
    """Detect whether any retrieved text or generated answer mentions a forbidden decoy phrase."""
    if not forbidden_substrings:
        return False
    combined = "\n".join(texts).lower()
    return any(needle.lower() in combined for needle in forbidden_substrings)


def score_retrieval_case(
    case: dict[str, Any], candidate_ids: Sequence[str], candidate_texts: Sequence[str]
) -> dict[str, bool]:
    """Score one retrieval result against expected prefixes and forbidden decoy phrases."""
    return {
        "hit_top5": match_prefix_hit(list(candidate_ids)[:5], case.get("expected_doc_prefixes", [])),
        "hit_any": match_prefix_hit(list(candidate_ids), case.get("expected_doc_prefixes", [])),
        "has_noise": has_forbidden_noise(candidate_texts, case.get("forbidden_substrings", [])),
    }


def score_e2e_case(case: dict[str, Any], result: dict[str, Any]) -> dict[str, bool]:
    """Score one end-to-end QA response against scope, status, citations, and noise checks."""
    expected_scope = case.get("expected_scope", "in_scope")
    status_upper = str(result.get("answer_status") or "").strip().upper()
    is_out = bool(result.get("is_out_of_domain")) or status_upper in {"OUT_OF_SCOPE", "OUT_OF_DOMAIN"}
    scope_ok = is_out if expected_scope == "out_of_scope" else not is_out

    expected_statuses = {str(s).strip().upper() for s in (case.get("expected_statuses") or []) if str(s).strip()}
    status_ok = (status_upper in expected_statuses) if expected_statuses else True

    if expected_scope == "out_of_scope":
        citation_ok = True
    else:
        citations = list(result.get("citations") or [])
        active_ids = list(result.get("active_article_ids") or [])
        citation_ok = match_prefix_hit(citations or active_ids, case.get("expected_doc_prefixes", []))

    irac = result.get("irac_sections") or {}
    text_blob = [
        str(irac.get("issue", "")),
        str(irac.get("rule", "")),
        str(irac.get("application", "")),
        str(irac.get("conclusion", "")),
        str(result.get("raw_llm_response") or ""),
    ]
    noise = has_forbidden_noise(text_blob, case.get("forbidden_substrings", []))
    has_missing_facts = bool(result.get("missing_facts"))
    return {
        "scope_ok": scope_ok,
        "status_ok": status_ok,
        "citation_ok": citation_ok,
        "has_noise": noise,
        "has_missing_facts": has_missing_facts,
    }


def _baseline_retrieve_articles(query: str, as_of_date: str, top_k: int = 5):
    """Simulate legacy article-only RRF baseline without clause vectors, 1-hop expansion, or reranker."""
    from src.api.chat_domain import normalize_query, understand_query
    from src.config import get_runtime_config
    from src.engine.embedding_meta import resolve_embedder_for_index
    from src.engine.hybrid_retriever import (
        _enrich_articles,
        _search_fulltext,
        _search_vector,
        reciprocal_rank_fusion,
    )
    from src.graph.connection import get_neo4j_driver

    clean_q = normalize_query(query)
    selected = get_runtime_config()
    resolver_kwargs = {}
    if selected.environment == "staging":
        resolver_kwargs = {
            "meta_path": selected.embedding_meta_path,
            "cache_path": selected.embeddings_path,
            "corpus_path": selected.corpus_path,
            "strict": True,
        }
    embedder = resolve_embedder_for_index(**resolver_kwargs)
    synonyms = list(understand_query(clean_q).industry_synonyms)
    driver = get_neo4j_driver()
    with driver.session() as session:
        ft_ranks = _search_fulltext(session, clean_q, as_of_date, limit=30, synonyms=synonyms)
        vec_ranks = _search_vector(session, embedder.embed_text(clean_q), as_of_date, top_k=30) if embedder else []
        fused = reciprocal_rank_fusion(ft_ranks, vec_ranks, k=60, ft_weight=1.0, vec_weight=1.0)
        return _enrich_articles(session, [doc_id for doc_id, _ in fused[:top_k]], as_of_date)


def _upgraded_retrieve_articles(query: str, as_of_date: str, history: list[dict[str, str]], top_k: int = 5):
    """Run upgraded retrieval: LLM Query Understanding + dual-level vector + 1-hop + Cross-Encoder."""
    from src.engine.hybrid_retriever import query_hybrid_temporal
    from src.engine.query_understanding import understand_with_llm
    from src.engine.reranker import rerank_provisions

    qu = understand_with_llm(query, history=history)
    effective_q = qu.legal_query or query
    candidates = query_hybrid_temporal(effective_q, as_of_date, top_k=15)
    reranked = rerank_provisions(effective_q, candidates)
    return reranked[:top_k]


def run_retrieval_ablation(
    cases: list[dict[str, Any]],
    *,
    baseline_fn: Callable[..., list[Any]] | None = None,
    upgraded_fn: Callable[..., list[Any]] | None = None,
) -> dict[str, Any]:
    """Compare baseline article-only retrieval against the upgraded pipeline across in-scope cases."""
    in_scope = [c for c in cases if c.get("expected_scope") == "in_scope"]
    base_fn = baseline_fn or _baseline_retrieve_articles
    upg_fn = upgraded_fn or _upgraded_retrieve_articles

    rows: list[dict[str, Any]] = []
    agg = {
        "baseline": {"hits_top5": 0, "noise": 0, "latency_ms": 0.0},
        "upgraded": {"hits_top5": 0, "noise": 0, "latency_ms": 0.0},
    }
    for case in in_scope:
        q = case["question"]
        day = case["as_of_date"]
        history = case.get("history") or []

        t0 = time.perf_counter()
        base_arts = base_fn(q, day, top_k=5)
        base_ms = (time.perf_counter() - t0) * 1000.0
        base_score = score_retrieval_case(
            case, [a.id for a in base_arts], [f"{a.title}\n{a.content}" for a in base_arts]
        )

        t1 = time.perf_counter()
        upg_arts = upg_fn(q, day, history, top_k=5)
        upg_ms = (time.perf_counter() - t1) * 1000.0
        upg_score = score_retrieval_case(
            case, [a.id for a in upg_arts], [f"{a.title}\n{a.content}" for a in upg_arts]
        )

        agg["baseline"]["hits_top5"] += int(base_score["hit_top5"])
        agg["baseline"]["noise"] += int(base_score["has_noise"])
        agg["baseline"]["latency_ms"] += base_ms
        agg["upgraded"]["hits_top5"] += int(upg_score["hit_top5"])
        agg["upgraded"]["noise"] += int(upg_score["has_noise"])
        agg["upgraded"]["latency_ms"] += upg_ms

        rows.append({
            "id": case["id"],
            "category": case["category"],
            "baseline_top5": [a.id for a in base_arts],
            "baseline_hit_top5": base_score["hit_top5"],
            "baseline_noise": base_score["has_noise"],
            "baseline_ms": round(base_ms, 1),
            "upgraded_top5": [a.id for a in upg_arts],
            "upgraded_hit_top5": upg_score["hit_top5"],
            "upgraded_noise": upg_score["has_noise"],
            "upgraded_ms": round(upg_ms, 1),
        })

    n = max(1, len(in_scope))
    summary = {
        mode: {
            "total_in_scope": len(in_scope),
            "top5_hit_rate": round(stats["hits_top5"] / n, 4),
            "forbidden_noise_rate": round(stats["noise"] / n, 4),
            "mean_latency_ms": round(stats["latency_ms"] / n, 1),
        }
        for mode, stats in agg.items()
    }
    return {"summary": summary, "cases": rows}


def run_e2e_evaluation(cases: list[dict[str, Any]], *, use_llm: bool = True) -> dict[str, Any]:
    """Run full QA pipeline across evaluated cases and summarize accuracy and latency."""
    from src.ui.demo_service import execute_demo_query

    rows: list[dict[str, Any]] = []
    totals = {"scope_ok": 0, "status_ok": 0, "citation_ok": 0, "noise": 0, "latency_ms": 0.0}
    cond_total = 0
    cond_with_facts = 0

    for case in cases:
        t0 = time.perf_counter()
        result = execute_demo_query(
            query=case["question"],
            as_of_date=case["as_of_date"],
            pipeline="P3_FullSystem",
            use_llm=use_llm,
            history=case.get("history") or [],
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        sc = score_e2e_case(case, result)

        totals["scope_ok"] += int(sc["scope_ok"])
        totals["status_ok"] += int(sc["status_ok"])
        totals["citation_ok"] += int(sc["citation_ok"])
        totals["noise"] += int(sc["has_noise"])
        totals["latency_ms"] += elapsed_ms

        if case.get("category") == "conditional_needs_facts":
            cond_total += 1
            if sc["has_missing_facts"]:
                cond_with_facts += 1

        rows.append({
            "id": case["id"],
            "category": case["category"],
            "answer_status": result.get("answer_status"),
            "citations": result.get("citations", []),
            "missing_facts": result.get("missing_facts", []),
            "rewritten_query": result.get("rewritten_query"),
            "latency_ms": round(elapsed_ms, 1),
            **sc,
        })

    n = max(1, len(cases))
    return {
        "summary": {
            "total_cases": len(cases),
            "scope_accuracy": round(totals["scope_ok"] / n, 4),
            "status_accuracy": round(totals["status_ok"] / n, 4),
            "citation_hit_rate": round(totals["citation_ok"] / n, 4),
            "forbidden_noise_rate": round(totals["noise"] / n, 4),
            "conditional_missing_facts_rate": round(cond_with_facts / cond_total, 4) if cond_total else None,
            "mean_latency_ms": round(totals["latency_ms"] / n, 1),
        },
        "cases": rows,
    }


def _save_report(report_path: Path, section: str, payload: dict[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if report_path.is_file():
        try:
            existing = json.loads(report_path.read_bytes().decode("utf-8"))
        except Exception:
            existing = {}
    existing[section] = payload
    report_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Run Phase 5b upgrade evaluation suite.")
    parser.add_argument("--mode", choices=["retrieval", "e2e"], required=True)
    parser.add_argument("--fixture", default=DEFAULT_FIXTURE_PATH)
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args(argv)

    cases = select_balanced_cases(load_upgrade_cases(args.fixture), args.limit)
    if args.mode == "retrieval":
        payload = run_retrieval_ablation(cases)
        _save_report(Path(args.report), "retrieval_ablation", payload)
        print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    else:
        payload = run_e2e_evaluation(cases, use_llm=not args.no_llm)
        _save_report(Path(args.report), "e2e_evaluation", payload)
        print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
