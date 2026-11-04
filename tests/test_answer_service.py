"""Tests for canonical answer_service orchestration and parity."""
import pytest
from src.api.answer_service import AnswerResult, answer_query
from src.api.chat_service import build_response


@pytest.mark.requires_neo4j
def test_answer_query_domestic_household_returns_200m_and_citations():
    query = "tôi mở tiệm net dưới quê thì có phải đóng thuế không"
    as_of_date = "2026-03-20"
    res = answer_query(query, as_of_date=as_of_date)

    assert isinstance(res, AnswerResult)
    assert res.query == query
    assert res.as_of_date == as_of_date
    assert res.answer_status == "ANSWERED"
    assert len(res.citations) > 0
    assert any("D5" in cid or "48" in cid for cid in res.citations)

    conc = res.irac_sections["conclusion"].lower()
    assert "200 triệu" in conc
    assert "thuế suất 0%" not in conc
    assert "xuất khẩu" not in conc


@pytest.mark.requires_neo4j
def test_answer_query_export_query_returns_zero_percent():
    query = "Doanh nghiệp tại Cần Thơ xuất khẩu phần mềm sang Nhật Bản vào tháng 03/2026 áp dụng thuế suất thuế GTGT bao nhiêu?"
    as_of_date = "2026-03-20"
    res = answer_query(query, as_of_date=as_of_date)

    assert isinstance(res, AnswerResult)
    assert res.answer_status == "ANSWERED"
    assert len(res.citations) > 0
    assert any("181" in cid or "48" in cid for cid in res.citations)

    conc = res.irac_sections["conclusion"]
    assert "0%" in conc


def test_answer_query_out_of_domain_returns_polite_refusal():
    query = "thuật toán quy hoạch động balo giải như thế nào"
    as_of_date = "2026-03-20"
    res = answer_query(query, as_of_date=as_of_date)

    assert isinstance(res, AnswerResult)
    assert res.is_out_of_domain is True
    assert res.answer_status == "OUT_OF_SCOPE"
    assert res.risk_score == 0
    assert res.citations == []
    assert "thuế GTGT" in res.irac_sections["conclusion"] or "phạm vi" in res.irac_sections["conclusion"].lower()


@pytest.mark.requires_neo4j
def test_answer_query_and_build_response_produce_identical_citations():
    query = "Doanh nghiệp tại Cần Thơ xuất khẩu phần mềm sang Nhật Bản vào tháng 03/2026 áp dụng thuế suất thuế GTGT bao nhiêu?"
    as_of_date = "2026-03-20"

    ans_res = answer_query(query, as_of_date=as_of_date)
    chat_res = build_response([{"role": "user", "content": query}], as_of_date=as_of_date)

    assert ans_res.citations == chat_res.citations
    assert ans_res.risk_score == chat_res.risk_score
    assert ans_res.risk_level == chat_res.risk_level
    assert ans_res.answer_status == chat_res.answer_status
    assert "0%" in chat_res.message


def test_build_irac_sections_evidence_driven():
    from src.api.answer_service import _build_irac_sections

    q = "hộ kinh doanh cá nhân nộp thuế thế nào"
    # Case A: with D5 which contains 200 triệu in evidence
    irac_with_200m = _build_irac_sections(q, "2026-03-20", ["LUAT_48_2024_D5"], [])
    assert "200 triệu" in irac_with_200m["conclusion"]

    # Case B: without 200M in evidence (e.g. only D1)
    irac_no_200m = _build_irac_sections(q, "2026-03-20", ["LUAT_48_2024_D1"], [])
    assert "200 triệu" not in irac_no_200m["conclusion"]
    assert "LUAT_48_2024_D1" in irac_no_200m["conclusion"]


@pytest.mark.requires_neo4j
def test_answer_query_temporal_trap():
    query = "Doanh nghiệp xuất khẩu dịch vụ vào tháng 05/2024 áp dụng điều kiện theo Luật 48/2024 có được không?"
    as_of_date = "2024-05-15"
    res = answer_query(query, as_of_date=as_of_date)

    assert isinstance(res, AnswerResult)
    assert res.risk_score == 100
    assert res.risk_level == "CRITICAL"
    assert any("chưa có hiệu lực" in w.lower() for w in res.warnings)


@pytest.mark.requires_neo4j
def test_answer_result_to_dict_matches_demo_service_contract():
    from src.ui.demo_service import execute_demo_query

    query = "tôi mở tiệm net dưới quê thì có phải đóng thuế không"
    as_of_date = "2026-03-20"
    demo_dict = execute_demo_query(query, as_of_date=as_of_date)
    ans_res = answer_query(query, as_of_date=as_of_date)

    assert set(demo_dict.keys()) == set(ans_res.to_dict().keys())
    assert demo_dict["citations"] == ans_res.citations
    assert demo_dict["risk_score"] == ans_res.risk_score
    assert demo_dict["answer_status"] == ans_res.answer_status


@pytest.mark.requires_neo4j
def test_answer_query_hallucinated_citation_outside_context_marked_irrelevant():
    from src.engine.models import ArticleNode

    mock_article = ArticleNode(
        id="LUAT_48_2024_D5",
        doc_id="LUAT_48_2024",
        code="D5",
        title="Đối tượng không chịu thuế",
        content="Khoản 25: Hộ cá nhân kinh doanh doanh thu dưới 200 triệu đồng.",
        valid_from="2026-01-01",
        valid_to="9999-12-31",
    )

    def mock_llm_fn(*args, **kwargs):
        return (
            "### 1. Vấn đề pháp lý\nThuế GTGT đối với dịch vụ.\n"
            "### 2. Căn cứ pháp lý\nCăn cứ theo [LUAT_48_2024_D9].\n"
            "### 3. Áp dụng pháp luật\nÁp dụng điều khoản này.\n"
            "### 4. Kết luận\nKết luận theo [LUAT_48_2024_D9]."
        )

    res = answer_query(
        question="Hộ cá nhân kinh doanh nộp thuế ra sao?",
        as_of_date="2026-04-01",
        use_llm=True,
        llm_fn=mock_llm_fn,
        fetch_fn=lambda *args, **kwargs: (["LUAT_48_2024_D5"], ["LUAT_48_2024_D5"], [mock_article]),
    )

    assert res.risk_score >= 70
    assert res.risk_level in {"HIGH", "CRITICAL"}
    assert "LUAT_48_2024_D9" not in res.citations
    assert any("không nằm trong tài liệu căn cứ" in w for w in res.warnings)


def test_household_conclusion_has_no_unsupported_five_percent():
    from src.api.answer_service import _build_irac_sections
    irac = _build_irac_sections(
        "tôi mở tiệm net dưới quê có phải đóng thuế không", "2026-03-20",
        ["LUAT_48_2024_D5"], [],
    )
    assert "200 triệu" in irac["conclusion"]
    assert "5%" not in irac["conclusion"]


def test_household_conclusion_threshold_disappears_without_evidence():
    from src.api.answer_service import _build_irac_sections
    irac = _build_irac_sections(
        "hộ kinh doanh nộp thuế thế nào", "2026-03-20", ["LUAT_48_2024_D1"], [],
    )
    assert "200 triệu" not in irac["conclusion"]
    assert "5%" not in irac["conclusion"]
    assert "LUAT_48_2024_D1" in irac["conclusion"]


def test_deterministic_multi_turn_uses_rewritten_query():
    from src.api.answer_service import answer_query

    captured: dict[str, str] = {}

    def _build(query, as_of_date, citations, warnings, articles=None):
        captured["issue_query"] = query
        return {"issue": query, "rule": "R", "application": "A", "conclusion": "C"}

    def _fetch(pipeline, query, as_of_date, trace=None):
        captured["retrieval_query"] = query
        return ["LUAT_48_2024_D5"], ["LUAT_48_2024_D5"], []

    class _V:
        risk_score = 0
        risk_level = "LOW"
        warnings = []

    import src.api.answer_service as svc
    original = svc._build_irac_sections
    svc._build_irac_sections = _build
    try:
        answer_query(
            question="còn thuế suất thì sao?",
            as_of_date="2026-03-20",
            history=[{"role": "user", "content": "tôi mở tiệm net dưới quê"}],
            use_llm=False,
            fetch_fn=_fetch,
            score_fn=lambda *a, **k: _V(),
        )
    finally:
        svc._build_irac_sections = original

    assert "tiệm net" in captured["issue_query"].lower()
    assert captured["issue_query"] == captured["retrieval_query"]



