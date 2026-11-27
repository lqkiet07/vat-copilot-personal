"""Before/After generalization & upgrade benchmark runner for Phase 5b."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

DEFAULT_FIXTURE_PATH = "tests/fixtures/upgrade_eval_25.json"
DEFAULT_REPORT_PATH = "benchmarks/upgrade_eval_report.json"
DEFAULT_MARKDOWN_PATH = "benchmarks/upgrade_eval_summary.md"


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


def match_answer_groups(text: str, groups: Sequence[Sequence[str]]) -> bool:
    """Return True when every required synonym group matches at least once in text."""
    if not groups:
        return True
    lowered = (text or "").lower()
    for group in groups:
        if not group:
            continue
        if not any(str(term).lower() in lowered for term in group if str(term).strip()):
            return False
    return True


def has_forbidden_answer_pattern(text: str, patterns: Sequence[str]) -> bool:
    """Return True if text matches any forbidden legal misconception regex."""
    if not patterns:
        return False
    body = text or ""
    return any(re.search(pat, body, re.IGNORECASE) is not None for pat in patterns)


def score_retrieval_case(
    case: dict[str, Any], candidate_ids: Sequence[str], candidate_texts: Sequence[str]
) -> dict[str, bool]:
    """Score one retrieval result against expected prefixes, primary basis, and noise checks."""
    primary_prefixes = case.get("primary_doc_prefixes", case.get("expected_doc_prefixes", []))
    top5 = list(candidate_ids)[:5]
    return {
        "hit_top5": match_prefix_hit(top5, case.get("expected_doc_prefixes", [])),
        "primary_hit_top5": match_prefix_hit(top5, primary_prefixes),
        "hit_any": match_prefix_hit(list(candidate_ids), case.get("expected_doc_prefixes", [])),
        "has_noise": has_forbidden_noise(candidate_texts, case.get("forbidden_substrings", [])),
    }


def score_e2e_case(case: dict[str, Any], result: dict[str, Any]) -> dict[str, bool]:
    """Score one end-to-end QA response against scope, status, primary citations, and conclusion."""
    expected_scope = case.get("expected_scope", "in_scope")
    status_upper = str(result.get("answer_status") or "").strip().upper()
    is_out = bool(result.get("is_out_of_domain")) or status_upper in {"OUT_OF_SCOPE", "OUT_OF_DOMAIN"}
    scope_ok = is_out if expected_scope == "out_of_scope" else not is_out

    expected_statuses = {str(s).strip().upper() for s in (case.get("expected_statuses") or []) if str(s).strip()}
    status_ok = (status_upper in expected_statuses) if expected_statuses else True

    citations = list(result.get("citations") or [])
    active_ids = list(result.get("active_article_ids") or [])
    if expected_scope == "out_of_scope":
        citation_ok = True
        primary_citation_ok = len(citations) == 0
    else:
        citation_ok = match_prefix_hit(citations or active_ids, case.get("expected_doc_prefixes", []))
        primary_prefixes = case.get("primary_doc_prefixes", case.get("expected_doc_prefixes", []))
        # Enforce verified answer citations only, never un-cited retrieved candidates.
        primary_citation_ok = match_prefix_hit(citations, primary_prefixes)

    irac = result.get("irac_sections") or {}
    full_blob = "\n".join([
        str(irac.get("issue", "")),
        str(irac.get("rule", "")),
        str(irac.get("application", "")),
        str(irac.get("conclusion", "")),
        str(result.get("raw_llm_response") or ""),
    ])
    conclusion_blob = "\n".join([
        str(irac.get("application", "")),
        str(irac.get("conclusion", "")),
    ]).strip() or str(result.get("raw_llm_response") or "")

    noise = has_forbidden_noise([full_blob], case.get("forbidden_substrings", []))
    conclusion_ok = match_answer_groups(full_blob, case.get("required_answer_groups", [])) and not has_forbidden_answer_pattern(
        conclusion_blob, case.get("forbidden_answer_patterns", [])
    )
    has_missing_facts = bool(result.get("missing_facts"))
    strict_pass = bool(scope_ok and status_ok and primary_citation_ok and conclusion_ok and not noise)
    return {
        "scope_ok": scope_ok,
        "status_ok": status_ok,
        "answered_ok": status_upper == "ANSWERED",
        "citation_ok": citation_ok,
        "primary_citation_ok": primary_citation_ok,
        "conclusion_ok": conclusion_ok,
        "strict_pass": strict_pass,
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
        "baseline": {"hits_top5": 0, "primary_hits_top5": 0, "noise": 0, "latency_ms": 0.0},
        "upgraded": {"hits_top5": 0, "primary_hits_top5": 0, "noise": 0, "latency_ms": 0.0},
    }
    for case in in_scope:
        row, scores = _evaluate_retrieval_case(case, base_fn, upg_fn)
        for mode, sc, ms_key in (("baseline", scores[0], "baseline_ms"), ("upgraded", scores[1], "upgraded_ms")):
            agg[mode]["hits_top5"] += int(sc["hit_top5"])
            agg[mode]["primary_hits_top5"] += int(sc["primary_hit_top5"])
            agg[mode]["noise"] += int(sc["has_noise"])
            agg[mode]["latency_ms"] += row[ms_key]
        rows.append(row)

    n = max(1, len(in_scope))
    summary = {
        mode: {
            "total_in_scope": len(in_scope),
            "top5_hit_rate": round(stats["hits_top5"] / n, 4),
            "primary_top5_hit_rate": round(stats["primary_hits_top5"] / n, 4),
            "forbidden_noise_rate": round(stats["noise"] / n, 4),
            "mean_latency_ms": round(stats["latency_ms"] / n, 1),
        }
        for mode, stats in agg.items()
    }
    return {"summary": summary, "cases": rows}


def _evaluate_retrieval_case(
    case: dict[str, Any],
    base_fn: Callable[..., list[Any]],
    upg_fn: Callable[..., list[Any]],
) -> tuple[dict[str, Any], tuple[dict[str, bool], dict[str, bool]]]:
    """Execute baseline and upgraded retrieval on one case and build its result row."""
    q, day, history = case["question"], case["as_of_date"], case.get("history") or []
    t0 = time.perf_counter()
    base_arts = base_fn(q, day, top_k=5)
    base_ms = (time.perf_counter() - t0) * 1000.0
    base_score = score_retrieval_case(case, [a.id for a in base_arts], [f"{a.title}\n{a.content}" for a in base_arts])

    t1 = time.perf_counter()
    upg_arts = upg_fn(q, day, history, top_k=5)
    upg_ms = (time.perf_counter() - t1) * 1000.0
    upg_score = score_retrieval_case(case, [a.id for a in upg_arts], [f"{a.title}\n{a.content}" for a in upg_arts])

    row = {
        "id": case["id"],
        "category": case["category"],
        "baseline_top5": [a.id for a in base_arts],
        "baseline_hit_top5": base_score["hit_top5"],
        "baseline_primary_hit_top5": base_score["primary_hit_top5"],
        "baseline_noise": base_score["has_noise"],
        "baseline_ms": round(base_ms, 1),
        "upgraded_top5": [a.id for a in upg_arts],
        "upgraded_hit_top5": upg_score["hit_top5"],
        "upgraded_primary_hit_top5": upg_score["primary_hit_top5"],
        "upgraded_noise": upg_score["has_noise"],
        "upgraded_ms": round(upg_ms, 1),
    }
    return row, (base_score, upg_score)


def _evaluate_single_e2e_case(case: dict[str, Any], executor: Callable[..., dict[str, Any]], use_llm: bool) -> dict[str, Any]:
    """Run and score one E2E case while preserving telemetry and answer previews for audit."""
    t0 = time.perf_counter()
    trace_data: dict[str, Any] = {}
    result = executor(
        query=case["question"],
        as_of_date=case["as_of_date"],
        pipeline="P3_FullSystem",
        use_llm=use_llm,
        history=case.get("history") or [],
        trace=trace_data,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    sc = score_e2e_case(case, result)
    irac = result.get("irac_sections") or {}
    return {
        "id": case["id"],
        "category": case["category"],
        "expected_scope": case.get("expected_scope", "in_scope"),
        "answer_status": result.get("answer_status"),
        "llm_used": bool(result.get("llm_used")),
        "citations": result.get("citations", []),
        "missing_facts": result.get("missing_facts", []),
        "rewritten_query": result.get("rewritten_query"),
        "answer_preview": {
            "conclusion": str(irac.get("conclusion", ""))[:500],
            "rule": str(irac.get("rule", ""))[:300],
            "application": str(irac.get("application", ""))[:300],
        },
        "understanding_source": trace_data.get("understanding_source"),
        "generation_disposition": trace_data.get("generation_disposition"),
        "generation_reason": trace_data.get("generation_reason"),
        "assessment_outcome": trace_data.get("assessment_outcome"),
        "assessment_reason": trace_data.get("assessment_reason"),
        "latency_ms": round(elapsed_ms, 1),
        **sc,
    }


def _compute_category_breakdown(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate strict pass, citation, conclusion, and latency metrics per benchmark category."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("category") or "default")].append(row)

    breakdown: dict[str, dict[str, Any]] = {}
    for cat, items in grouped.items():
        n = max(1, len(items))
        in_scope_items = [r for r in items if r.get("expected_scope", "in_scope") == "in_scope"]
        answered_rate = (
            round(sum(int(r["answered_ok"]) for r in in_scope_items) / len(in_scope_items), 4)
            if in_scope_items
            else None
        )
        breakdown[cat] = {
            "total": len(items),
            "strict_pass_rate": round(sum(int(r["strict_pass"]) for r in items) / n, 4),
            "answered_rate": answered_rate,
            "primary_citation_hit_rate": round(sum(int(r["primary_citation_ok"]) for r in items) / n, 4),
            "conclusion_accuracy": round(sum(int(r["conclusion_ok"]) for r in items) / n, 4),
            "mean_latency_ms": round(sum(float(r["latency_ms"]) for r in items) / n, 1),
        }
    return breakdown


def _summarize_e2e_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Compute overall and per-category summary metrics from evaluated E2E case rows."""
    n = max(1, len(rows))
    in_scope = [r for r in rows if r.get("expected_scope", "in_scope") == "in_scope"]
    cond = [r for r in rows if r.get("category") == "conditional_needs_facts"]
    return {
        "total_cases": len(rows),
        "scope_accuracy": round(sum(int(r["scope_ok"]) for r in rows) / n, 4),
        "status_accuracy": round(sum(int(r["status_ok"]) for r in rows) / n, 4),
        "answered_rate": round(sum(int(r["answered_ok"]) for r in in_scope) / len(in_scope), 4) if in_scope else None,
        "citation_hit_rate": round(sum(int(r["citation_ok"]) for r in rows) / n, 4),
        "primary_citation_hit_rate": round(sum(int(r["primary_citation_ok"]) for r in rows) / n, 4),
        "conclusion_accuracy": round(sum(int(r["conclusion_ok"]) for r in rows) / n, 4),
        "strict_pass_rate": round(sum(int(r["strict_pass"]) for r in rows) / n, 4),
        "forbidden_noise_rate": round(sum(int(r["has_noise"]) for r in rows) / n, 4),
        "conditional_missing_facts_rate": round(sum(int(r["has_missing_facts"]) for r in cond) / len(cond), 4) if cond else None,
        "mean_latency_ms": round(sum(float(r["latency_ms"]) for r in rows) / n, 1),
        "by_category": _compute_category_breakdown(rows),
    }


def run_e2e_evaluation(cases: list[dict[str, Any]], *, use_llm: bool = True) -> dict[str, Any]:
    """Run full QA pipeline across evaluated cases and summarize accuracy and latency."""
    from src.ui.demo_service import execute_demo_query

    rows = [_evaluate_single_e2e_case(case, execute_demo_query, use_llm) for case in cases]
    return {"summary": _summarize_e2e_rows(rows), "cases": rows}


def _build_run_metadata(fixture_path: Path, total_fixture_cases: int, e2e_case_count: int) -> dict[str, Any]:
    """Capture git, fixture hash, and active environment details for benchmark provenance."""
    git_head = "unknown"
    try:
        git_head = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        pass
    fixture_sha256 = hashlib.sha256(fixture_path.read_bytes()).hexdigest() if fixture_path.is_file() else None
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_head": git_head,
        "fixture_path": str(fixture_path),
        "fixture_sha256": fixture_sha256,
        "total_fixture_cases": total_fixture_cases,
        "e2e_case_count": e2e_case_count,
        "vat_env": os.getenv("VAT_ENV", "canonical"),
        "llm_provider": os.getenv("LLM_PROVIDER", "auto"),
        "gemini_model": os.getenv("GEMINI_MODEL", ""),
        "embedding_provider": os.getenv("EMBEDDING_PROVIDER", "auto"),
        "reranker_model": os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"),
    }


def _format_retrieval_markdown(ret: dict[str, Any]) -> list[str]:
    """Format retrieval ablation summary rows for Markdown audit report."""
    summary = ret.get("summary") or {}
    lines = [
        "## Retrieval Ablation",
        "",
        "| Mode | In-Scope | Top-5 Hit | Primary Top-5 Hit | Forbidden Noise | Mean Latency (ms) |",
        "| :--- | :---: | :---: | :---: | :---: | :---: |",
    ]
    for mode in ("baseline", "upgraded"):
        m = summary.get(mode)
        if not m:
            continue
        lines.append(
            f"| {mode} | {m.get('total_in_scope', 0)} | {m.get('top5_hit_rate', 0):.4f} "
            f"| {m.get('primary_top5_hit_rate', 0):.4f} | {m.get('forbidden_noise_rate', 0):.4f} "
            f"| {m.get('mean_latency_ms', 0):.1f} |"
        )
    lines.append("")
    return lines


def _format_e2e_markdown(e2e: dict[str, Any]) -> list[str]:
    """Format E2E summary, category breakdown, and per-case audit table for Markdown."""
    s = e2e.get("summary") or {}
    lines = [
        "## End-to-End QA Evaluation",
        "",
        f"- **Cases:** `{s.get('total_cases', 0)}` | **Strict Pass:** `{s.get('strict_pass_rate')}` | **Answered:** `{s.get('answered_rate')}` | **Scope:** `{s.get('scope_accuracy')}` | **Status:** `{s.get('status_accuracy')}` | **Primary Citation:** `{s.get('primary_citation_hit_rate')}` | **Conclusion:** `{s.get('conclusion_accuracy')}` | **Mean Latency:** `{s.get('mean_latency_ms')} ms`",
        "",
        "### By Category",
        "",
        "| Category | Total | Strict Pass | Answered | Primary Citation | Conclusion OK | Mean Latency (ms) |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for cat, m in (s.get("by_category") or {}).items():
        lines.append(
            f"| {cat} | {m['total']} | {m['strict_pass_rate']:.4f} | {m.get('answered_rate')} "
            f"| {m['primary_citation_hit_rate']:.4f} | {m['conclusion_accuracy']:.4f} | {m['mean_latency_ms']:.1f} |"
        )
    lines.extend(["", "### Per-Case Audit", "", "| ID | Category | Status | Primary Citation | Conclusion OK | Strict Pass | Latency (ms) | Conclusion Preview |", "| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- |"])
    for row in e2e.get("cases") or []:
        preview = str((row.get("answer_preview") or {}).get("conclusion") or "").replace("|", "/").replace("\n", " ")[:120]
        lines.append(
            f"| {row['id']} | {row['category']} | {row.get('answer_status')} "
            f"| {'PASS' if row.get('primary_citation_ok') else 'FAIL'} | {'PASS' if row.get('conclusion_ok') else 'FAIL'} "
            f"| {'PASS' if row.get('strict_pass') else 'FAIL'} | {row.get('latency_ms', 0):.1f} | {preview} |"
        )
    lines.append("")
    return lines


def _format_failed_cases_markdown(report: dict[str, Any]) -> list[str]:
    """List every E2E or upgraded retrieval failure with diagnostic reasons."""
    lines = ["## Failed / Partial Cases", ""]
    ret_misses = [r for r in (report.get("retrieval_ablation") or {}).get("cases", []) if not r.get("upgraded_hit_top5")]
    e2e_fails = [r for r in (report.get("e2e_evaluation") or {}).get("cases", []) if not r.get("strict_pass")]
    if not ret_misses and not e2e_fails:
        lines.append("- None (all evaluated cases passed).")
        return lines
    for r in ret_misses:
        lines.append(f"- **Retrieval Miss `{r['id']}`** ({r['category']}): `upgraded_top5={r.get('upgraded_top5')}`")
    for r in e2e_fails:
        conc = str((r.get("answer_preview") or {}).get("conclusion") or "").replace("\n", " ")[:180]
        lines.append(
            f"- **E2E Fail `{r['id']}`** ({r['category']}): status=`{r.get('answer_status')}`, "
            f"primary_citation_ok=`{r.get('primary_citation_ok')}`, conclusion_ok=`{r.get('conclusion_ok')}`, "
            f"citations=`{r.get('citations')}`, gen=`{r.get('generation_reason')}`, assess=`{r.get('assessment_reason')}`, conclusion=`{conc}`"
        )
    return lines


def render_markdown_report(report: dict[str, Any]) -> str:
    """Render a structured human-auditable Markdown summary from an evaluation report dict."""
    meta = report.get("run_metadata") or {}
    lines = ["# Upgrade Evaluation Audit Summary", ""]
    if meta:
        lines.extend([
            f"- **Generated UTC:** `{meta.get('generated_at_utc')}` | **Git HEAD:** `{meta.get('git_head')}`",
            f"- **Env:** `{meta.get('vat_env')}` | **LLM:** `{meta.get('llm_provider')}` (`{meta.get('gemini_model')}`) | **Embedding:** `{meta.get('embedding_provider')}` | **E2E Cases:** `{meta.get('e2e_case_count')}/{meta.get('total_fixture_cases')}`",
            "",
        ])
    if "retrieval_ablation" in report:
        lines.extend(_format_retrieval_markdown(report["retrieval_ablation"]))
    if "e2e_evaluation" in report:
        lines.extend(_format_e2e_markdown(report["e2e_evaluation"]))
    lines.extend(_format_failed_cases_markdown(report))
    return "\n".join(lines).rstrip() + "\n"


def _save_report(report_path: Path, section: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Merge a report section into the JSON report file and return the full report dict."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if report_path.is_file():
        try:
            existing = json.loads(report_path.read_bytes().decode("utf-8"))
        except Exception:
            existing = {}
    existing[section] = payload
    report_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return existing


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Run Phase 5b upgrade evaluation suite.")
    parser.add_argument("--mode", choices=["retrieval", "e2e", "both"], required=True)
    parser.add_argument("--fixture", default=DEFAULT_FIXTURE_PATH)
    parser.add_argument("--report", default=DEFAULT_REPORT_PATH)
    parser.add_argument("--markdown-report", default=DEFAULT_MARKDOWN_PATH)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--e2e-limit", type=int, default=None)
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args(argv)

    all_cases = load_upgrade_cases(args.fixture)
    report_path = Path(args.report)
    e2e_count = len(select_balanced_cases(all_cases, args.e2e_limit if args.e2e_limit is not None else args.limit)) if args.mode in {"e2e", "both"} else 0
    full_report = _save_report(report_path, "run_metadata", _build_run_metadata(Path(args.fixture), len(all_cases), e2e_count))

    if args.mode in {"retrieval", "both"}:
        ret_cases = select_balanced_cases(all_cases, args.limit)
        payload = run_retrieval_ablation(ret_cases)
        full_report = _save_report(report_path, "retrieval_ablation", payload)
        print("RETRIEVAL SUMMARY:", json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    if args.mode in {"e2e", "both"}:
        e2e_cases = select_balanced_cases(all_cases, args.e2e_limit if args.e2e_limit is not None else args.limit)
        payload = run_e2e_evaluation(e2e_cases, use_llm=not args.no_llm)
        full_report = _save_report(report_path, "e2e_evaluation", payload)
        print("E2E SUMMARY:", json.dumps(payload["summary"], ensure_ascii=False, indent=2))

    if str(args.markdown_report).strip():
        md_path = Path(args.markdown_report)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown_report(full_report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
