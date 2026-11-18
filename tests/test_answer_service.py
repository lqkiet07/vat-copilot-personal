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

    assert res.risk_score == 0
    assert res.risk_level == "LOW"
    assert "LUAT_48_2024_D9" not in res.citations
    assert "LUAT_48_2024_D9" not in "\n".join(res.irac_sections.values())
    assert any("Bản nháp LLM đã bị thay" in w for w in res.warnings)


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


def _offline_score(text, as_of_date, context_ids=None, relevance_map=None):
    from src.verifier.citation_verifier import extract_citations
    from src.verifier.models import CitationStatus, VerificationReport, VerificationResult
    from src.verifier.risk_score import calculate_risk_score

    ids = extract_citations(text)
    statuses = []
    for cid in ids:
        valid = cid in (context_ids or set()) and (relevance_map or {}).get(cid, True)
        statuses.append(CitationStatus(
            id=cid, status="VALID" if valid else "IRRELEVANT", exists=True,
            is_active=True, message="" if valid else f"Không có căn cứ cho {cid}.",
        ))
    report = VerificationReport(
        as_of_date=as_of_date, total_citations=len(ids),
        valid_citations=sum(c.status == "VALID" for c in statuses),
        citations=statuses, is_clean=all(c.status == "VALID" for c in statuses),
    )
    score, level, warnings = calculate_risk_score(report)
    return VerificationResult(
        as_of_date=as_of_date, risk_score=score, risk_level=level,
        warnings=warnings, report=report,
    )


def test_llm_without_extracted_citations_does_not_leak_active_ids_into_citations():
    """When LLM response does not cite any bracketed citations, citations must not be filled with active_ids."""
    from src.engine.models import ArticleNode

    mock_article = ArticleNode(
        id="LUAT_48_2024_D5",
        doc_id="LUAT_48_2024",
        code="D5",
        title="Đối tượng không chịu thuế",
        content="Hàng hóa, dịch vụ của hộ, cá nhân kinh doanh có mức doanh thu hằng năm từ 200 triệu đồng trở xuống.",
        valid_from="2026-01-01",
        valid_to="9999-12-31",
    )

    def mock_llm_no_citations(*args, **kwargs):
        return (
            "Issue: Thuế GTGT quán net dưới quê.\n"
            "Rule: Quy định chung về thuế GTGT.\n"
            "Application: Quán net dưới quê không cần nộp thuế.\n"
            "Conclusion: Được miễn thuế hoàn toàn."
        )

    res = answer_query(
        question="quán net dưới quê tôi có dc miễn thuế kh",
        as_of_date="2026-03-20",
        use_llm=True,
        llm_fn=mock_llm_no_citations,
        score_fn=_offline_score,
        fetch_fn=lambda *args, **kwargs: (["LUAT_48_2024_D5"], ["LUAT_48_2024_D5"], [mock_article]),
    )

    # Citations of the answer must NOT claim to be [LUAT_48_2024_D5] when LLM didn't cite it!
    # And an unsupported unconditional exemption conclusion must trigger fallback or rejection!
    assert res.citations == [] or "200 triệu" in res.irac_sections["conclusion"]
    assert "miễn thuế hoàn toàn" not in res.irac_sections["conclusion"].lower()
    assert not any("không viện dẫn bất kỳ" in w for w in res.warnings)
    from src.verifier.citation_verifier import extract_citations
    final_text = "\n".join(res.irac_sections.values())
    assert set(extract_citations(final_text)) == set(res.citations)
    assert "phương pháp khoán" not in final_text
    assert "không viện dẫn bất kỳ" not in final_text


def test_company_fallback_does_not_infer_household_from_legal_text():
    from src.api.answer_service import _build_irac_sections
    from src.engine.models import ArticleNode

    article = ArticleNode(
        id="LUAT_48_2024_D4", doc_id="LUAT_48_2024", code="D4",
        title="Người nộp thuế",
        content="Tổ chức, hộ, cá nhân sản xuất kinh doanh hàng hóa, dịch vụ chịu thuế GTGT.",
        valid_from="2025-07-01", valid_to="9999-12-31",
    )
    irac = _build_irac_sections(
        "tôi đăng ký công ty TNHH, doanh thu 1 tỷ một năm thì sao?",
        "2026-03-20", [article.id], [], articles=[article],
    )
    assert "đối với hộ, cá nhân" not in irac["conclusion"]
    assert "chưa đủ" in irac["conclusion"].lower()


def test_fallback_uses_retrieved_evidence_instead_of_local_corpus():
    from src.api.answer_service import _build_irac_sections
    from src.engine.models import ArticleNode

    article = ArticleNode(
        id="LUAT_48_2024_D5", doc_id="LUAT_48_2024", code="D5",
        title="Đối tượng không chịu thuế",
        content="Hộ, cá nhân kinh doanh có doanh thu hằng năm từ 500 triệu đồng trở xuống không chịu thuế GTGT.",
        valid_from="2026-01-01", valid_to="9999-12-31",
    )
    irac = _build_irac_sections(
        "hộ kinh doanh nộp thuế thế nào", "2026-03-20", [article.id], [], articles=[article],
    )
    assert "500 triệu" in irac["conclusion"]
    assert "200 triệu" not in "\n".join(irac.values())


def test_llm_hallucinated_invalid_citation_triggers_fallback_or_safe_conclusion():
    """When LLM response cites an invalid or out-of-context citation, the hallucinated claim must not be preserved."""
    from src.engine.models import ArticleNode

    mock_article = ArticleNode(
        id="LUAT_48_2024_D5",
        doc_id="LUAT_48_2024",
        code="D5",
        title="Đối tượng không chịu thuế",
        content="Khoản 25: Hộ, cá nhân kinh doanh doanh thu từ 200 triệu đồng trở xuống.",
        valid_from="2026-01-01",
        valid_to="9999-12-31",
    )

    def mock_llm_hallucinated_d9(*args, **kwargs):
        return (
            "Issue: Xác định thuế quán net.\n"
            "Rule: Điều 4 của [LUAT_48_2024_D9] quy định chi tiết về đối tượng không chịu thuế.\n"
            "Application: Áp dụng quy định trên cho quán net.\n"
            "Conclusion: Các tổ chức, cá nhân không cần phải chịu thuế theo quy định của [LUAT_48_2024_D9]."
        )

    res = answer_query(
        question="quán net dưới quê tôi có dc miễn thuế kh",
        as_of_date="2026-03-20",
        use_llm=True,
        llm_fn=mock_llm_hallucinated_d9,
        score_fn=_offline_score,
        fetch_fn=lambda *args, **kwargs: (["LUAT_48_2024_D5"], ["LUAT_48_2024_D5"], [mock_article]),
    )

    # D9 is out of context and invalid here. It must not be in citations.
    assert "LUAT_48_2024_D9" not in res.citations
    # The conclusion must NOT preserve the hallucinated assertion attributing non-taxable status to D9!
    assert "LUAT_48_2024_D9" not in res.irac_sections["conclusion"]
    assert "Điều 4 của LUAT_48_2024_D9" not in res.irac_sections["rule"]


def test_household_query_without_revenue_must_branch_not_assert_unconditional_deduction():
    """When question is a household query without revenue, conclusion must branch by revenue threshold."""
    from src.engine.models import ArticleNode

    mock_d5 = ArticleNode(
        id="LUAT_48_2024_D5",
        doc_id="LUAT_48_2024",
        code="D5",
        title="Đối tượng không chịu thuế",
        content="Hàng hóa, dịch vụ của hộ, cá nhân kinh doanh có mức doanh thu hằng năm từ 200 triệu đồng trở xuống.",
        valid_from="2026-01-01",
        valid_to="9999-12-31",
    )
    mock_d21 = ArticleNode(
        id="ND_181_2025_D21",
        doc_id="ND_181_2025",
        code="D21",
        title="Phương pháp khấu trừ",
        content="Đối tượng áp dụng phương pháp khấu trừ thuế là cơ sở kinh doanh, trừ hộ, cá nhân sản xuất, kinh doanh.",
        valid_from="2026-01-01",
        valid_to="9999-12-31",
    )

    def mock_llm_asserts_deduction(*args, **kwargs):
        return (
            "Issue: Nghĩa vụ thuế quán net mới mở dưới quê.\n"
            "Rule: Căn cứ [ND_181_2025_D21] và [LUAT_48_2024_D5].\n"
            "Application: Quán net áp dụng phương pháp khấu trừ theo [ND_181_2025_D21].\n"
            "Conclusion: Quán net mới mở dưới quê thuộc đối tượng chịu thuế GTGT và áp dụng phương pháp khấu trừ theo [ND_181_2025_D21]."
        )

    res = answer_query(
        question="quán net tôi mới mở dưới quê có phải đóng thuế kh và đóng bnh",
        as_of_date="2026-03-20",
        use_llm=True,
        llm_fn=mock_llm_asserts_deduction,
        score_fn=_offline_score,
        fetch_fn=lambda *args, **kwargs: (["LUAT_48_2024_D5", "ND_181_2025_D21"], ["LUAT_48_2024_D5", "ND_181_2025_D21"], [mock_d5, mock_d21]),
    )

    # Deduction method ND_181_2025_D21 explicitly excludes household business!
    # Conclusion must not assert that household business applies deduction method [ND_181_2025_D21]!
    conc_lower = res.irac_sections["conclusion"].lower()
    assert "khấu trừ" not in conc_lower or "trừ hộ" in conc_lower
    # Must branch by revenue threshold
    assert "200 triệu" in res.irac_sections["conclusion"]


def test_unresolved_basis_does_not_return_low_risk_or_definitive_absence():
    from src.api.answer_service import answer_query
    from src.verifier.models import CitationStatus, VerificationReport, VerificationResult

    def mock_fetch_with_unresolved(*args, **kwargs):
        # Empty valid articles because candidates were UNRESOLVED
        trace = kwargs.get("trace")
        if trace is not None:
            trace["unresolved_citations"] = ["VBHN_25_2026_D1"]
        return [], [], []

    def mock_scorer(text, as_of_date, **kwargs):
        report = VerificationReport(
            as_of_date=as_of_date,
            total_citations=1,
            valid_citations=0,
            citations=[
                CitationStatus(id="VBHN_25_2026_D1", status="UNRESOLVED", exists=True, is_active=None, message="Chưa đủ căn cứ xác minh."),
            ],
            is_clean=False,
        )
        return VerificationResult(
            as_of_date=as_of_date,
            risk_score=50,
            risk_level="MEDIUM",
            warnings=["Chưa đủ căn cứ xác minh hiệu lực."],
            report=report,
        )

    res = answer_query(
        question="Hộ kinh doanh nộp thuế theo văn bản hợp nhất 25 thế nào?",
        as_of_date="2026-03-20",
        use_llm=False,
        score_fn=mock_scorer,
        fetch_fn=mock_fetch_with_unresolved,
    )

    assert res.risk_score == 50
    assert res.risk_level == "MEDIUM"
    assert res.answer_status == "INSUFFICIENT_BASIS"
    assert any("chưa đủ căn cứ" in w.lower() for w in res.warnings)
    assert "không tìm thấy điều khoản" not in res.irac_sections["rule"].lower()


def test_answer_query_forwards_corpus_path_to_scorer():
    from src.api.answer_service import answer_query
    from src.engine.models import ArticleNode
    from src.verifier.models import CitationStatus, VerificationReport, VerificationResult

    captured = {}

    def mock_scorer_with_corpus(text, as_of_date, **kwargs):
        captured["corpus_path"] = kwargs.get("corpus_path")
        report = VerificationReport(
            as_of_date=as_of_date,
            total_citations=1,
            valid_citations=1,
            citations=[
                CitationStatus(id="LUAT_48_2024_D9", status="VALID", exists=True, is_active=True, message=""),
            ],
            is_clean=True,
        )
        return VerificationResult(
            as_of_date=as_of_date,
            risk_score=0,
            risk_level="LOW",
            warnings=[],
            report=report,
        )

    mock_art = ArticleNode(
        id="LUAT_48_2024_D9",
        doc_id="LUAT_48_2024",
        code="D9",
        title="Thuế suất",
        content="Thuế suất 0%",
        valid_from="2025-07-01",
        valid_to="9999-12-31",
    )

    answer_query(
        question="Thuế suất phần mềm",
        as_of_date="2026-03-20",
        use_llm=False,
        score_fn=mock_scorer_with_corpus,
        corpus_path="custom/test_corpus.json",
        fetch_fn=lambda *args, **kwargs: (["LUAT_48_2024_D9"], ["LUAT_48_2024_D9"], [mock_art]),
    )

    assert captured.get("corpus_path") == "custom/test_corpus.json"


def _step86_article():
    from src.engine.models import ArticleNode
    return ArticleNode(
        id="TT_86_2026_D1", doc_id="TT_86_2026", code="Điều 1",
        title="Thuế GTGT", content="Thuế GTGT đối với dịch vụ xuất khẩu, thuế suất 0%.",
        valid_from="2025-01-01", valid_to="9999-12-31",
    )


@pytest.mark.parametrize("mode", ["deterministic", "llm", "fallback"])
def test_step86_internal_scorer_typeerror_is_not_retried(mode):
    calls = []

    def scorer(text, date, **kwargs):
        calls.append(kwargs)
        raise TypeError("internal scorer failure")

    article = _step86_article()
    with pytest.raises(TypeError, match="internal scorer failure"):
        answer_query(
            "Thuế GTGT dịch vụ xuất khẩu?", "2026-03-20",
            use_llm=mode != "deterministic",
            llm_fn=lambda *a, **k: "draft" if mode == "fallback" else f"Rule: [{article.id}]\nConclusion: Áp dụng 0%.",
            fetch_fn=lambda *a, **k: ([article.id], [article.id], [article]),
            score_fn=scorer,
        )
    assert len(calls) == 1


def test_step86_nondefault_path_rejects_legacy_scorer_before_invocation():
    calls = []
    article = _step86_article()

    def scorer(text, date):
        calls.append(text)
        return _offline_score(text, date)

    with pytest.raises(TypeError, match="corpus_path"):
        answer_query(
            "Thuế GTGT dịch vụ xuất khẩu?", "2026-03-20", corpus_path="custom.json",
            fetch_fn=lambda *a, **k: ([article.id], [article.id], [article]), score_fn=scorer,
        )
    assert calls == []


def test_step86_actual_gate_receives_answer_corpus_path(monkeypatch):
    from types import SimpleNamespace
    import src.engine.retrieval_service as retrieval
    from src.verifier.models import CitationStatus

    article = _step86_article()
    paths = []

    def verifier(text, date, relevance_map=None, *, corpus_path="default"):
        paths.append(corpus_path)
        return SimpleNamespace(citations=[CitationStatus(
            id=article.id, status="VALID", exists=True, is_active=True, message="",
        )])

    monkeypatch.setattr(retrieval, "verify_citations", verifier)
    answer_query(
        "Thuế GTGT dịch vụ xuất khẩu?", "2026-03-20", corpus_path="custom.json",
        retriever_fn=lambda *a: SimpleNamespace(articles=[article]),
        score_fn=lambda *a, **k: SimpleNamespace(risk_score=0, risk_level="LOW", warnings=[]),
    )
    assert paths == ["custom.json"]


def test_step86_relevance_and_trap_load_selected_corpus(monkeypatch):
    import src.api.answer_service as service
    paths = []
    article = _step86_article()
    monkeypatch.setattr(service, "load_corpus_index", lambda corpus_path: paths.append(corpus_path) or {})
    service._build_candidate_relevance_map(
        "Thuế GTGT dịch vụ?", "", [article.id], [], corpus_path="custom.json",
    )
    monkeypatch.setattr(service, "load_corpus_tree", lambda corpus_path: paths.append(corpus_path) or [])
    answer_query(
        "Thuế GTGT dịch vụ?", "2026-03-20", corpus_path="custom.json",
        fetch_fn=lambda *a, **k: ([], [], []),
    )
    assert paths == ["custom.json", "custom.json"]


def test_step86_deterministic_metadata_uses_selected_corpus(monkeypatch):
    import src.api.answer_service as service
    paths = []
    article = _step86_article()
    monkeypatch.setattr(service, "load_corpus_index", lambda corpus_path: paths.append(corpus_path) or {
        article.id: {"text": article.content, "code": article.code, "title": article.title},
    })
    result = answer_query(
        "Thuế GTGT dịch vụ xuất khẩu?", "2026-03-20", corpus_path="custom.json",
        fetch_fn=lambda *a, **k: ([article.id], [article.id], []),
        score_fn=lambda *a, **k: type("Score", (), {"risk_score": 0, "risk_level": "LOW", "warnings": []})(),
    )
    assert paths == ["custom.json"]
    assert article.content in result.irac_sections["application"]
    assert result.answer_status == "ANSWERED"


@pytest.mark.parametrize("mode", ["deterministic", "llm", "fallback", "offline"])
def test_step86_final_unresolved_blocks_affirmative_conclusion(mode):
    from types import SimpleNamespace
    from src.verifier.models import CitationStatus
    article = _step86_article()
    calls = []

    def scorer(text, date, **kwargs):
        calls.append(text)
        unresolved = mode != "fallback" or len(calls) > 1
        return SimpleNamespace(
            risk_score=50 if unresolved else 0, risk_level="MEDIUM" if unresolved else "LOW",
            warnings=["Exact uncertain provision message"] if unresolved else [],
            report=SimpleNamespace(citations=[CitationStatus(
                id=article.id, status="UNRESOLVED" if unresolved else "VALID",
                exists=True, is_active=None if unresolved else True, message="Exact uncertain provision message",
            )]),
        )

    result = answer_query(
        "Thuế GTGT dịch vụ xuất khẩu?", "2026-03-20", use_llm=mode != "deterministic",
        fetch_fn=lambda *a, **k: ([article.id], [article.id], [article]), score_fn=scorer,
        llm_fn=lambda *a, **k: None if mode == "offline" else "draft" if mode == "fallback" else f"Rule: [{article.id}]\nConclusion: Áp dụng 0%.",
    )
    assert result.answer_status == "INSUFFICIENT_BASIS"
    assert "chưa đủ căn cứ" in result.irac_sections["conclusion"].lower()
    assert "0%" not in result.irac_sections["conclusion"]
    assert result.active_article_ids == []
    assert article.id in result.citations
    assert "Exact uncertain provision message" in result.warnings
    assert result.risk_score >= 50


def test_step86_mixed_gate_preserves_uncertain_ids_and_messages_without_generation(monkeypatch):
    from types import SimpleNamespace
    from src.engine.retrieval_service import retrieve_and_gate
    from src.verifier.models import CitationStatus
    article = _step86_article()
    uncertain = article.model_copy(update={"id": "VBHN_25_2026_D1"})

    def verifier(text, date, **kwargs):
        return SimpleNamespace(citations=[
            CitationStatus(id=article.id, status="VALID", exists=True, is_active=True, message="Valid"),
            CitationStatus(id=uncertain.id, status="UNRESOLVED", exists=True, is_active=None, message="Exact mixed span warning"),
        ])

    retrieval = retrieve_and_gate(
        "Thuế GTGT dịch vụ xuất khẩu?", "2026-03-20",
        retriever_fn=lambda *a: SimpleNamespace(articles=[article, uncertain]), verifier_fn=verifier,
    )
    assert retrieval.active_ids == [article.id]
    assert retrieval.unresolved_citations == [uncertain.id]
    assert retrieval.unresolved_warnings == ["Exact mixed span warning"]
    trace = {}

    def forbidden(*a, **k):
        pytest.fail("Uncertain canonical evidence must not enter generation")

    result = answer_query(
        "Thuế GTGT dịch vụ xuất khẩu?", "2026-03-20", use_llm=True, llm_fn=forbidden,
        retriever_fn=lambda *a: SimpleNamespace(articles=[article, uncertain]), verifier_fn=verifier,
        trace=trace,
    )
    assert result.answer_status == "INSUFFICIENT_BASIS"
    assert result.active_article_ids == [article.id]
    assert uncertain.id in result.citations
    assert "Exact mixed span warning" in result.warnings
    assert trace["unresolved_citations"] == [uncertain.id]
    assert trace["unresolved_warnings"] == ["Exact mixed span warning"]


def test_step86_empty_retrieval_does_not_prove_absence_of_law():
    result = answer_query(
        "Thuế GTGT dịch vụ?", "2026-03-20",
        fetch_fn=lambda *a, **k: ([], [], []), corpus_tree_fn=lambda: [],
    )
    assert result.risk_score == 50
    assert result.risk_level == "MEDIUM"
    assert result.answer_status == "INSUFFICIENT_BASIS"
    assert "chưa đủ căn cứ" in result.irac_sections["conclusion"].lower()
    assert "không có căn cứ pháp luật" not in result.irac_sections["conclusion"].lower()


def test_step86_fallback_internal_typeerror_is_not_retried():
    from types import SimpleNamespace
    article = _step86_article()
    calls = []

    def scorer(text, date, **kwargs):
        calls.append(text)
        if len(calls) > 1:
            raise TypeError("fallback internal failure")
        return SimpleNamespace(risk_score=0, risk_level="LOW", warnings=[])

    with pytest.raises(TypeError, match="fallback internal failure"):
        answer_query(
            "Thuế GTGT dịch vụ xuất khẩu?", "2026-03-20", use_llm=True,
            fetch_fn=lambda *a, **k: ([article.id], [article.id], [article]),
            llm_fn=lambda *a, **k: "draft", score_fn=scorer,
        )
    assert len(calls) == 2


@pytest.mark.parametrize("mode", ["deterministic", "llm", "fallback", "offline"])
def test_step86_actual_verifier_uses_custom_corpus_not_production(tmp_path, monkeypatch, mode):
    import json
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    import src.verifier.citation_verifier as verifier
    article = _step86_article()
    path = tmp_path / "uncertain.json"
    path.write_text(json.dumps([{
        "id": article.doc_id, "status": "CONSOLIDATION_REFERENCE",
        "valid_from": "2025-01-01", "valid_to": "9999-12-31",
        "articles": [{"id": article.id, "code": article.code, "content": article.content}],
    }]), encoding="utf-8")
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.side_effect = lambda query, **kw: MagicMock(data=lambda: [
        {"cid": cid, "exists": True, "valid_from": "2025-01-01", "valid_to": "9999-12-31", "amendments": []}
        for cid in kw["ids"]
    ])
    monkeypatch.setattr(verifier, "get_neo4j_driver", lambda: driver)
    calls = []
    result = answer_query(
        "Thuế GTGT dịch vụ xuất khẩu?", "2026-03-20", corpus_path=str(path),
        retriever_fn=lambda *a: SimpleNamespace(articles=[article]), use_llm=mode != "deterministic",
        llm_fn=lambda *a, **k: calls.append(a) or "draft",
    )
    assert result.answer_status == "INSUFFICIENT_BASIS"
    assert result.active_article_ids == []
    assert result.citations == [article.id]
    assert any("Văn bản hợp nhất" in warning for warning in result.warnings)
    assert calls == []


def test_step86_legacy_default_scorer_and_verifier_remain_compatible():
    from types import SimpleNamespace
    from src.verifier.models import CitationStatus
    article = _step86_article()
    calls = []

    def verifier(text, date, relevance_map=None):
        calls.append("gate")
        return SimpleNamespace(citations=[CitationStatus(
            id=article.id, status="VALID", exists=True, is_active=True, message="",
        )])

    def scorer(text, date):
        calls.append("score")
        return SimpleNamespace(risk_score=0, risk_level="LOW", warnings=[])

    result = answer_query(
        "Thuế GTGT dịch vụ xuất khẩu?", "2026-03-20",
        retriever_fn=lambda *a: SimpleNamespace(articles=[article]), verifier_fn=verifier, score_fn=scorer,
    )
    assert result.answer_status == "ANSWERED"
    assert calls == ["gate", "score"]


@pytest.mark.parametrize("mode,expected,used", [
    ("off", "deterministic_off", False),
    ("unresolved", "safety_bypass", False),
    ("none", "provider_no_output", False),
    ("blank", "provider_no_output", True),
    ("good", "generated_delivered", True),
    ("bad", "draft_replaced", True),
    ("final_uncertain", "draft_replaced", True),
])
def test_r5_actual_generation_disposition(mode, expected, used):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from src.verifier.models import CitationStatus
    article = _step86_article()
    trace = {}
    good = f"Issue: Thuế GTGT\nRule: [{article.id}]\nApplication: Căn cứ được cung cấp.\nConclusion: Cần đối chiếu điều kiện."
    generator = Mock(return_value={"none": None, "blank": "  ", "bad": "draft"}.get(mode, good))

    def fetch(*args, **kwargs):
        if mode == "unresolved":
            kwargs["trace"].update(unresolved_citations=[article.id], unresolved_warnings=["review required"])
        return [article.id], [article.id], [article]

    def scorer(*args, **kwargs):
        uncertain = mode == "final_uncertain"
        return SimpleNamespace(
            risk_score=50 if uncertain else 0, risk_level="MEDIUM" if uncertain else "LOW", warnings=[],
            report=SimpleNamespace(citations=[CitationStatus(
                id=article.id, status="UNRESOLVED" if uncertain else "VALID",
                exists=True, is_active=None if uncertain else True, message="review required",
            )]),
        )

    result = answer_query(
        "Thuế GTGT dịch vụ xuất khẩu?", "2026-03-20", use_llm=mode != "off",
        fetch_fn=fetch, score_fn=scorer, llm_fn=generator, trace=trace,
    )
    assert trace["generation_disposition"] == expected
    assert trace["generation_attempted"] == (mode not in {"off", "unresolved"})
    assert trace["generated_delivered"] == (mode == "good")
    assert result.llm_used is used
    assert "generation_disposition" not in result.to_dict()
    assert generator.call_count == (0 if mode in {"off", "unresolved"} else 1)
    assert trace["generation_reason"] == {
        "off": "llm_disabled", "unresolved": "unresolved_evidence", "none": "empty_provider_output",
        "blank": "empty_provider_output", "good": "verified_draft",
        "bad": "unsupported_draft", "final_uncertain": "unresolved_evidence",
    }[mode]
    if mode == "final_uncertain":
        assert result.answer_status == "INSUFFICIENT_BASIS" and result.risk_score >= 50
        assert "chưa đủ căn cứ" in result.irac_sections["conclusion"].lower()
    if mode in {"none", "blank"}:
        assert not trace["generated_delivered"]
    if mode == "good":
        assert result.irac_sections["raw_text"] == good


def test_r5_explicit_review_path_reaches_gate_and_final_score():
    from types import SimpleNamespace
    article = _step86_article()
    seen = []

    def verifier(text, date, **kwargs):
        seen.append(kwargs.get("reviewed_amendment_path"))
        return SimpleNamespace(citations=[SimpleNamespace(id=article.id, status="VALID")])

    def scorer(text, date, **kwargs):
        seen.append(kwargs.get("reviewed_amendment_path"))
        return SimpleNamespace(risk_score=0, risk_level="LOW", warnings=[])

    answer_query(
        "Thuế GTGT dịch vụ xuất khẩu?", "2026-03-20",
        retriever_fn=lambda *args: SimpleNamespace(articles=[article]),
        verifier_fn=verifier, score_fn=scorer, reviewed_amendment_path="explicit-review.json",
    )
    assert seen == ["explicit-review.json", "explicit-review.json"]


@pytest.mark.parametrize("use_llm", [False, True])
def test_r5_importer_application_uses_exact_raw_clause_and_real_verifier(monkeypatch, tmp_path, use_llm):
    import hashlib
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import MagicMock, Mock
    from html.parser import HTMLParser
    import src.verifier.citation_verifier as verifier
    from src.graph.importer import plan_corpus_import
    from src.engine.diagnostics import text_sha256
    from src.ui.corpus_viewer import load_corpus_index
    from src.engine.models import ArticleNode

    root = Path(__file__).resolve().parents[1]
    corpus = str(root / "data/processed/vat_corpus.staging.json")
    ledger = root / "tests/fixtures/amendment_review.staging.json"
    pins = json.loads(ledger.read_text(encoding="utf-8"))["pins"]
    raw = root / "data/raw/01_luat_48_2024.html"
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == pins[raw.relative_to(root).as_posix()]
    parser = HTMLParser()
    parts = []
    parser.handle_data = parts.append
    parser.feed(raw.read_text(encoding="utf-8"))
    oracle = "Tổ chức, cá nhân nhập khẩu hàng hóa chịu thuế giá trị gia tăng (sau đây gọi là người nhập khẩu)."
    assert oracle in " ".join(" ".join(parts).split())
    docs = json.loads(Path(corpus).read_text(encoding="utf-8"))
    article_data = next(a for d in docs for a in d["articles"] if a["id"] == "LUAT_48_2024_D4")
    clause = next(c for c in article_data["clauses"] if c["id"] == "LUAT_48_2024_D4_K2")
    assert clause["content"] == oracle
    plan = plan_corpus_import(corpus)
    assert not any(r.get("source_unit") == clause["id"] for r in plan["resolved_amends"] + plan["unresolved_amends"])
    meta = load_corpus_index(corpus)[article_data["id"]]
    article = ArticleNode(id=article_data["id"], doc_id=meta["doc_id"], code=meta["code"], title=meta["title"],
                          content=meta["text"], valid_from=meta["valid_from"], valid_to=meta["valid_to"])
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.side_effect = lambda query, **kw: MagicMock(data=lambda: [
        {"cid": cid, "exists": True, "valid_from": "2025-07-01", "valid_to": "9999-12-31", "amendments": []}
        for cid in kw["ids"]])
    monkeypatch.setattr(verifier, "get_neo4j_driver", lambda: driver)
    draft = f"Issue: Người nhập khẩu\nRule: [{clause['id']}] {oracle}\nApplication: Tổ chức nhập khẩu hàng hóa chịu thuế là người nộp thuế GTGT.\nConclusion: Tổ chức nhập khẩu thuộc người nộp thuế theo [{clause['id']}]."
    generator = Mock(return_value=draft)
    trace = {}
    monkeypatch.chdir(tmp_path)
    result = answer_query(
        "Tổ chức nhập khẩu hàng hóa chịu thuế có là người nộp thuế GTGT không?", "2026-03-20",
        corpus_path=corpus, reviewed_amendment_path=str(ledger), use_llm=use_llm,
        retriever_fn=lambda *a: SimpleNamespace(articles=[article]), llm_fn=generator, trace=trace,
    )
    assert result.answer_status == "ANSWERED"
    assert result.llm_used is use_llm
    assert trace["generation_disposition"] == ("generated_delivered" if use_llm else "deterministic_off")
    assert trace["generated_delivered"] is use_llm
    assert generator.call_count == int(use_llm)
    assert not any(rate in "\n".join(result.irac_sections.values()) for rate in ("0%", "5%", "10%"))
    if use_llm:
        assert oracle in generator.call_args.args[0]
        assert result.irac_sections["application"] == "Tổ chức nhập khẩu hàng hóa chịu thuế là người nộp thuế GTGT."
        assert trace["prompt_sha256"] == text_sha256(generator.call_args.args[0])
        assert trace["output_sha256"] == text_sha256(draft)
        assert result.citations == [clause["id"]]