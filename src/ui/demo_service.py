from html import escape
import re
from src.api.answer_service import (
    AnswerResult,
    _build_candidate_relevance_map,
    _build_irac_sections,
    _check_temporal_trap,
    _extract_best_clause,
    _extract_citation_evidence,
    _needs_irac_fallback,
    _parse_irac_sections,
    answer_query,
)
from src.benchmark.baselines import run_p1_baseline_rag
from src.engine.diagnostics import EmptyReason, QueryTrace, classify_empty, emit_trace
from src.engine.llm_client import generate_llm_response
from src.engine.models import ArticleNode
from src.engine.prompt import build_irac_prompt
from src.engine.retrieval_service import retrieve_and_gate
from src.engine.temporal_retriever import query_temporal_rag
from src.ui.corpus_viewer import load_corpus_index, load_corpus_tree
from src.verifier.citation_verifier import extract_citations, verify_citations
from src.verifier.guardrails import check_query_domain
from src.verifier.relevance import build_relevance_map, score_article_relevance
from src.verifier.risk_score import verify_and_score

DEMO_SCENARIOS: list[dict] = [
    {
        "name": "Kịch bản 1 (Xanh - An toàn): Thuế suất 0% phần mềm xuất khẩu năm 2026",
        "query": "Doanh nghiệp tại Cần Thơ xuất khẩu phần mềm sang Nhật Bản vào tháng 03/2026 áp dụng thuế suất thuế GTGT bao nhiêu?",
        "as_of_date": "2026-03-20",
        "badge_color": "green",
    },
    {
        "name": "Kịch bản 2 (Đỏ - Bẫy thời gian): Áp dụng Luật 48 trước ngày 01/07/2025",
        "query": "Doanh nghiệp xuất khẩu dịch vụ vào tháng 05/2024 áp dụng điều kiện theo Luật 48/2024 có được không?",
        "as_of_date": "2024-05-15",
        "badge_color": "red",
    },
    {
        "name": "Kịch bản 3 (Vàng - Sửa đổi): Điều 9 NĐ 181 bị sửa đổi bởi NĐ 144/2026",
        "query": "Quy định về hóa đơn chứng từ đối với dịch vụ xuất khẩu theo Điều 9 Nghị định 181/2025 áp dụng như thế nào vào tháng 08/2026?",
        "as_of_date": "2026-08-10",
        "badge_color": "yellow",
    },
]


def _fetch_pipeline_articles(
    pipeline: str, query: str, as_of_date: str, trace: dict | None = None
) -> tuple[list[str], list[str], list[ArticleNode]]:
    """Retrieve citations, active IDs, and articles for the selected pipeline."""
    res = retrieve_and_gate(query, as_of_date, top_k=5, pipeline=pipeline, retriever_fn=query_temporal_rag)
    if trace is not None:
        trace["raw_article_count"] = res.raw_article_count
        trace["gated_article_count"] = res.gated_article_count
        if pipeline == "P1_FlatRAG":
            trace["pipeline_note"] = "flat_rag_no_article_objects"
    return res.active_ids, res.active_ids, res.articles


def execute_demo_query(
    query: str,
    as_of_date: str,
    pipeline: str = "P3_FullSystem",
    use_llm: bool = False,
    model: str = "qwen2.5:7b",
    history: list[dict] | None = None,
    trace: dict | None = None,
) -> dict:
    """Execute a query against specified pipeline and return IRAC analysis with risk score."""
    res = answer_query(
        question=query,
        as_of_date=as_of_date,
        pipeline=pipeline,
        use_llm=use_llm,
        model=model,
        history=history,
        trace=trace,
        fetch_fn=_fetch_pipeline_articles,
        llm_fn=generate_llm_response,
    )
    return res.to_dict()


def generate_printable_html(query: str, as_of_date: str, result: dict) -> str:
    """Generate clean printable HTML report with print-optimized CSS."""
    # ponytail: pure HTML/CSS without external PDF binary dependencies
    sections = result.get("irac_sections", {})
    warnings = result.get("warnings", [])
    warn_html = "".join(f"<li>{escape(w)}</li>" for w in warnings) or "<li>Không phát hiện rủi ro</li>"
    cites = ", ".join(result.get("citations", [])) or "None"

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Báo cáo tư vấn thuế GTGT - {escape(as_of_date)}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; line-height: 1.5; color: #212529; margin: 20px; }}
.header {{ border-bottom: 2px solid #228be6; padding-bottom: 10px; margin-bottom: 20px; }}
.badge {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-weight: bold; background: #e7f5ff; color: #1c7ed6; }}
.risk-box {{ background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 6px; padding: 12px; margin: 15px 0; }}
.section-title {{ font-size: 14px; font-weight: bold; text-transform: uppercase; color: #495057; margin-top: 15px; margin-bottom: 5px; }}
.section-content {{ margin-bottom: 15px; white-space: pre-line; }}
@media print {{
  body {{ margin: 0; font-size: 12pt; }}
  .no-print {{ display: none; }}
  .page-break {{ page-break-after: always; }}
}}
</style>
</head>
<body>
<div class="header">
  <h2>BÁO CÁO PHÂN TÍCH THUẾ GTGT THEO THỜI ĐIỂM</h2>
  <div><strong>Ngày tra cứu (As-of Date):</strong> {escape(as_of_date)} | <strong>Pipeline:</strong> {escape(result.get("pipeline", "N/A"))}</div>
</div>
<div class="risk-box">
  <div><strong>Câu hỏi tra cứu:</strong> {escape(query)}</div>
  <div><strong>Điểm rủi ro (Risk Score):</strong> {result.get("risk_score", 0)}/100 (Mức độ: {escape(result.get("risk_level", "LOW"))})</div>
  <div><strong>Căn cứ trích dẫn:</strong> {escape(cites)}</div>
  <div><strong>Cảnh báo:</strong> <ul>{warn_html}</ul></div>
</div>
<div class="section-title">1. Vấn đề pháp lý (Issue)</div>
<div class="section-content">{escape(sections.get("issue", ""))}</div>
<div class="section-title">2. Quy phạm áp dụng (Rule)</div>
<div class="section-content">{escape(sections.get("rule", ""))}</div>
<div class="section-title">3. Áp dụng phân tích (Application)</div>
<div class="section-content">{escape(sections.get("application", ""))}</div>
<div class="section-title">4. Kết luận (Conclusion)</div>
<div class="section-content">{escape(sections.get("conclusion", ""))}</div>
</body>
</html>"""
