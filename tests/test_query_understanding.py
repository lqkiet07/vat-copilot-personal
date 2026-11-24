import json
import pytest

from src.engine.query_understanding import (
    QueryUnderstanding,
    needs_query_understanding,
    understand_with_llm,
    validate_rewritten_query,
)


def test_validate_rewritten_query_accepts_clean_legal_paraphrase():
    ok, reason = validate_rewritten_query(
        "tiệm net dưới quê có đc miễn thuế kh",
        "Hộ kinh doanh dịch vụ truy cập internet có thuộc đối tượng không chịu thuế GTGT không?",
        history=None,
    )
    assert ok is True
    assert reason == "accepted"


@pytest.mark.parametrize(
    "rewritten",
    [
        "Hộ kinh doanh doanh thu dưới 200 triệu đồng có được miễn thuế GTGT không?",
        "Theo Luật 48/2024 thì tiệm net có chịu thuế suất 10% không?",
        "Áp dụng Điều 5 cho tiệm net như thế nào?",
    ],
)
def test_validate_rewritten_query_rejects_hallucinated_numbers_and_statutes(rewritten):
    ok, reason = validate_rewritten_query(
        "tiệm net dưới quê có đc miễn thuế kh",
        rewritten,
        history=None,
    )
    assert ok is False
    assert reason != "accepted"


def test_validate_rewritten_query_allows_numbers_present_in_original_or_history():
    history = [
        {"role": "user", "content": "tiệm net dưới quê doanh thu 150 triệu có đc miễn thuế kh"},
        {"role": "assistant", "content": "Cần thêm thông tin."},
    ]
    ok, reason = validate_rewritten_query(
        "nếu theo Luật 48/2024 thì sao?",
        "Hộ kinh doanh dịch vụ truy cập internet doanh thu 150 triệu đồng áp dụng Luật 48/2024 thế nào?",
        history=history,
    )
    assert ok is True
    assert reason == "accepted"


def test_understand_with_llm_rewrites_colloquial_and_preserves_long_multiturn_history():
    history = [
        {"role": "user", "content": "tiệm net dưới quê có đc miễn thuế kh"},
        {"role": "assistant", "content": "Bạn vui lòng cung cấp loại hình kinh doanh và doanh thu."},
    ]
    follow_up = "tôi là hộ kinh doanh doanh thu 150 triệu một năm"
    called = []

    def fake_llm(prompt: str, **kwargs):
        called.append((prompt, kwargs))
        return json.dumps(
            {
                "scope": "vat",
                "legal_query": "Hộ kinh doanh dịch vụ truy cập internet có mức doanh thu 150 triệu đồng một năm có thuộc đối tượng không chịu thuế GTGT không?",
                "missing_facts": [],
            },
            ensure_ascii=False,
        )

    result = understand_with_llm(follow_up, history=history, llm_fn=fake_llm, use_llm=True)
    assert isinstance(result, QueryUnderstanding)
    assert len(called) == 1
    assert result.scope == "vat"
    assert result.source == "llm"
    assert "internet" in result.legal_query.lower()
    assert "150" in result.legal_query


@pytest.mark.parametrize(
    "query",
    [
        "Hộ kinh doanh thuế GTGT thế nào?",
        "Thuế GTGT theo Luật 998/2030?",
        "Điều 9 Nghị định 181/2025 quy định thuế suất xuất khẩu thế nào?",
    ],
)
def test_understand_with_llm_bypasses_formal_single_turn_legal_query(query):
    assert needs_query_understanding(query, history=None) is False

    def forbidden_llm(*args, **kwargs):
        raise AssertionError("Formal single-turn legal query must bypass extra LLM call")

    result = understand_with_llm(query, history=None, llm_fn=forbidden_llm, use_llm=True)
    assert result.scope == "vat"
    assert result.source == "bypass"
    assert result.legal_query == query


def test_understand_with_llm_falls_back_safely_on_error_timeout_or_disabled():
    disabled = understand_with_llm("tiệm net dưới quê có đc miễn thuế kh", use_llm=False)
    assert disabled.scope == "vat"
    assert disabled.source == "fallback"
    assert "không" in disabled.legal_query

    def timeout_llm(*args, **kwargs):
        raise TimeoutError("Provider timed out")

    on_timeout = understand_with_llm(
        "tiệm net dưới quê có đc miễn thuế kh",
        llm_fn=timeout_llm,
        use_llm=True,
    )
    assert on_timeout.scope == "vat"
    assert on_timeout.source == "fallback"
    assert "không" in on_timeout.legal_query

    def bad_json_llm(*args, **kwargs):
        return "not valid json"

    on_bad_json = understand_with_llm(
        "tiệm net dưới quê có đc miễn thuế kh",
        llm_fn=bad_json_llm,
        use_llm=True,
    )
    assert on_bad_json.scope == "vat"
    assert on_bad_json.source == "fallback"
    assert "không" in on_bad_json.legal_query
