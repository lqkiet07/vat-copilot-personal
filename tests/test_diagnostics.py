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


def test_query_trace_default_context_counts():
    trace = QueryTrace(
        query="test query",
        as_of_date="2024-01-01",
        pipeline="baseline",
    )
    assert trace.context_article_count == 0
    assert trace.citation_in_context_count == 0
