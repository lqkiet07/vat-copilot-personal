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


def test_understanding_prompt_enforces_accented_vietnamese_missing_facts():
    from src.engine.query_understanding import _build_understanding_prompt

    prompt = _build_understanding_prompt("quán net có phải đóng thuế kh", [])
    assert "tiếng Việt có dấu đầy đủ" in prompt
    assert "hộ kinh doanh, cá nhân kinh doanh hoặc doanh nghiệp" in prompt


def test_needs_query_understanding_handles_colloquial_and_chip_followups():
    assert needs_query_understanding("bán quần áo online tại nhà có phải đóng thuế GTGT không") is True
    hist = [{"role": "user", "content": "quán net tôi có phải đóng thuế kh"}]
    assert needs_query_understanding(
        "Bổ sung thông tin (Loại hình đăng ký kinh doanh): hộ kinh doanh doanh thu 700 triệu",
        history=hist,
    ) is True


@pytest.mark.parametrize(
    "drifted",
    [
        "Chủ thể kinh doanh quán nét phải nộp những loại thuế nào?",
        "Hộ kinh doanh có phải nộp thuế thu nhập cá nhân và thuế GTGT không?",
        "Cửa hàng có phải đóng lệ phí môn bài không?",
    ],
)
def test_validate_rewritten_query_rejects_general_non_vat_tax_drift(drifted):
    ok, reason = validate_rewritten_query("mở tiệm tôi phải đóng thuế hem", drifted, history=None)
    assert ok is False
    assert reason == "added_non_vat_scope"

    # Explicitly asked tax lists or non-VAT taxes in user text remain allowed.
    ok_explicit, reason_explicit = validate_rewritten_query(
        "cần nộp những loại thuế nào gồm thuế thu nhập cá nhân và lệ phí môn bài?",
        drifted,
        history=None,
    )
    assert ok_explicit is True
    assert reason_explicit == "accepted"


def test_understanding_prompt_enforces_general_vat_scope_without_case_hardcode():
    from src.engine.query_understanding import _build_understanding_prompt

    prompt = _build_understanding_prompt("mở tiệm tôi phải đóng thuế hem", [])
    assert "những/các loại thuế" in prompt
    assert "KHÔNG tự suy diễn" in prompt
    assert "trò chơi điện tử" not in prompt


def test_answer_query_understand_progress_hides_raw_rewritten_query():
    from src.api.answer_service import answer_query

    events = []

    def fake_llm(prompt: str, **kwargs):
        return json.dumps(
            {
                "scope": "vat",
                "legal_query": "Nghĩa vụ thuế GTGT đối với quán net là gì?",
                "missing_facts": ["Loại hình kinh doanh"],
            },
            ensure_ascii=False,
        )

    answer_query(
        "quán nét tôi phải đóng thuế hem",
        "2026-03-20",
        use_llm=True,
        pipeline="P1_FlatRAG",
        fetch_fn=lambda *a, **k: ([], [], []),
        llm_fn=fake_llm,
        progress_cb=events.append,
    )
    understand_events = [e for e in events if e.get("stage") == "understand"]
    assert len(understand_events) == 1
    assert "«" not in understand_events[0]["message"]
    assert "Đã chuẩn hóa câu hỏi" not in understand_events[0]["message"]


def test_needs_query_understanding_routes_declarative_long_followup_with_history():
    hist = [{"role": "user", "content": "quán nét tôi phải đóng thuế hem"}]
    assert needs_query_understanding(
        "hộ kinh doanh, doanh thu khoảng 800 triệu một năm",
        history=hist,
    ) is True
    # Standalone formal question without follow-up cues still bypasses extra understanding.
    assert needs_query_understanding(
        "Thuế suất GTGT đối với dịch vụ xuất khẩu phần mềm là bao nhiêu?",
        history=hist,
    ) is False


