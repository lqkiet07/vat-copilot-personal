from dataclasses import asdict, replace
import json
import os
from unittest.mock import patch
import pytest

from src.engine.diagnostics import EmptyReason, QueryTrace, classify_empty, emit_trace


def test_classify_retrieval_miss_when_raw_zero():
    trace = QueryTrace(
        query="test query",
        as_of_date="2024-01-01",
        pipeline="baseline",
        raw_article_count=0,
    )
    assert classify_empty(trace) == EmptyReason.RETRIEVAL_MISS


def test_classify_gating_overreject_when_raw_positive_but_gated_zero():
    trace = QueryTrace(
        query="test query",
        as_of_date="2024-01-01",
        pipeline="baseline",
        raw_article_count=5,
        gated_article_count=0,
    )
    assert classify_empty(trace) == EmptyReason.GATING_OVERREJECT


def test_classify_llm_refused_when_prompt_articles_exist():
    trace = QueryTrace(
        query="test query",
        as_of_date="2024-01-01",
        pipeline="baseline",
        raw_article_count=5,
        gated_article_count=3,
        prompt_article_count=2,
        llm_used=True,
        llm_citation_count=0,
    )
    assert classify_empty(trace) == EmptyReason.LLM_REFUSED_WITH_EVIDENCE


def test_classify_irac_parse_failed_when_rule_empty():
    trace = QueryTrace(
        query="test query",
        as_of_date="2024-01-01",
        pipeline="baseline",
        raw_article_count=5,
        gated_article_count=3,
        prompt_article_count=2,
        llm_used=True,
        llm_citation_count=1,
        irac_rule_empty=True,
        irac_conclusion_empty=False,
    )
    assert classify_empty(trace) == EmptyReason.IRAC_PARSE_FAILED


def test_classify_irac_parse_failed_when_conclusion_empty():
    trace = QueryTrace(
        query="test query",
        as_of_date="2024-01-01",
        pipeline="baseline",
        raw_article_count=5,
        gated_article_count=3,
        prompt_article_count=2,
        llm_used=True,
        llm_citation_count=1,
        irac_rule_empty=False,
        irac_conclusion_empty=True,
    )
    assert classify_empty(trace) == EmptyReason.IRAC_PARSE_FAILED


def test_classify_answered_takes_precedence():
    trace = QueryTrace(
        query="test query",
        as_of_date="2024-01-01",
        pipeline="baseline",
        raw_article_count=5,
        gated_article_count=3,
        prompt_article_count=2,
        llm_used=True,
        llm_citation_count=1,
        irac_rule_empty=False,
        irac_conclusion_empty=False,
    )
    assert classify_empty(trace) == EmptyReason.ANSWERED


def test_classify_out_of_scope():
    trace = QueryTrace(
        query="thời tiết hôm nay",
        as_of_date="2024-01-01",
        pipeline="baseline",
        answer_status="OUT_OF_SCOPE",
        raw_article_count=0,
    )
    assert classify_empty(trace) == EmptyReason.OUT_OF_SCOPE


def test_emit_trace_is_noop_without_env(monkeypatch, tmp_path):
    monkeypatch.delenv("VAT_DIAGNOSTICS", raising=False)
    trace = QueryTrace(
        query="test query",
        as_of_date="2024-01-01",
        pipeline="baseline",
    )
    emit_trace(trace)
    assert list(tmp_path.iterdir()) == []


def test_emit_trace_writes_jsonl_when_env_set(monkeypatch, tmp_path):
    log_file = tmp_path / "subdir" / "traces.jsonl"
    monkeypatch.setenv("VAT_DIAGNOSTICS", str(log_file))

    trace1 = QueryTrace(
        query="q1",
        as_of_date="2024-01-01",
        pipeline="baseline",
        raw_article_count=2,
    )
    trace2 = QueryTrace(
        query="q2",
        as_of_date="2024-01-01",
        pipeline="baseline",
        raw_article_count=0,
        empty_reason=EmptyReason.RETRIEVAL_MISS,
    )

    emit_trace(trace1)
    emit_trace(trace2)

    assert log_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2

    data1 = json.loads(lines[0])
    data2 = json.loads(lines[1])

    assert data1["query"] == "q1"
    assert data1["raw_article_count"] == 2
    assert data2["query"] == "q2"
    assert data2["empty_reason"] == "RETRIEVAL_MISS"


def test_emit_trace_never_crashes_on_exception(monkeypatch):
    monkeypatch.setenv("VAT_DIAGNOSTICS", "some/path/trace.jsonl")
    trace = QueryTrace(
        query="test query",
        as_of_date="2024-01-01",
        pipeline="baseline",
    )
    with patch("src.engine.diagnostics.Path.mkdir", side_effect=OSError("Disk failure")):
        emit_trace(trace)


def test_query_trace_with_context_counts():
    trace = QueryTrace(
        query="test query",
        as_of_date="2024-01-01",
        pipeline="baseline",
        context_article_count=5,
        citation_in_context_count=3,
    )
    assert trace.context_article_count == 5
    assert trace.citation_in_context_count == 3


def saved_insufficient_trace():
    return QueryTrace(
        query="Chuyển quyền sử dụng đất có chịu thuế GTGT?",
        as_of_date="2026-11-19", pipeline="P3_FullSystem",
        raw_article_count=15, gated_article_count=1, prompt_article_count=1,
        llm_used=True, llm_citation_count=1,
        answer_status="INSUFFICIENT_BASIS", generation_disposition="draft_replaced",
        generation_reason="unsupported_draft", generation_attempted=True,
        generated_delivered=False,
    )


def test_saved_insufficient_draft_with_valid_fallback_citation(tmp_path, monkeypatch):
    from scripts.analyze_diagnostics import summarize, format_report
    trace = saved_insufficient_trace()
    reason = classify_empty(trace)
    assert reason == "DRAFT_REPLACED"
    path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("VAT_DIAGNOSTICS", str(path))
    emit_trace(replace(trace, empty_reason=reason))
    counts = summarize(path)
    assert counts == {"DRAFT_REPLACED": 1}
    assert "DRAFT_REPLACED | 1" in format_report(counts)
    assert json.loads(path.read_text())["answer_status"] == "INSUFFICIENT_BASIS"


def test_saved_insufficient_draft_without_citations():
    trace = replace(saved_insufficient_trace(), llm_citation_count=0,
                    irac_rule_empty=True, irac_conclusion_empty=True)
    assert classify_empty(trace) == "DRAFT_REPLACED"


def test_evidence_only_insufficient_basis_precedes_citation_and_section_checks():
    for llm_used in (False, True):
        trace = replace(saved_insufficient_trace(), generation_disposition="safety_bypass",
                        generation_reason="review_required_for_generation", llm_used=llm_used,
                        llm_citation_count=0, irac_rule_empty=True, irac_conclusion_empty=True)
        assert classify_empty(trace) == "INSUFFICIENT_BASIS"
        assert classify_empty(replace(trace, raw_article_count=None, gated_article_count=0)) == "INSUFFICIENT_BASIS"


def test_supported_answer_and_legacy_deterministic_answer():
    trace = replace(saved_insufficient_trace(), answer_status="ANSWERED",
                    generation_disposition="generated_delivered", generated_delivered=True,
                    assessment_outcome="supported", assessment_coverage=True)
    assert classify_empty(QueryTrace(**asdict(trace))) == EmptyReason.ANSWERED
    assert classify_empty(replace(trace, generated_delivered=False, llm_used=False,
                                  generation_disposition="deterministic_off")) == EmptyReason.ANSWERED
    assert classify_empty(replace(trace, answer_status="INSUFFICIENT_BASIS")) == "INSUFFICIENT_BASIS"


def test_scope_and_known_no_evidence_precede_generation_disposition():
    trace = saved_insufficient_trace()
    assert classify_empty(replace(trace, answer_status="OUT_OF_SCOPE")) == EmptyReason.OUT_OF_SCOPE
    for generated_delivered in (False, True):
        candidate = replace(trace, generated_delivered=generated_delivered,
                            answer_status="ANSWERED" if generated_delivered else "INSUFFICIENT_BASIS")
        assert classify_empty(replace(candidate, raw_article_count=0, gated_article_count=0)) == EmptyReason.RETRIEVAL_MISS
        assert classify_empty(replace(candidate, gated_article_count=0)) == EmptyReason.GATING_OVERREJECT


def test_query_trace_default_context_counts():
    trace = QueryTrace(
        query="test query",
        as_of_date="2024-01-01",
        pipeline="baseline",
    )
    assert trace.context_article_count == 0
    assert trace.citation_in_context_count == 0
