"""Tests for demo service and UI corpus viewer components."""
from unittest.mock import patch
import pytest
from src.ui.corpus_viewer import (
    load_corpus_tree,
    render_article_html,
    render_document_tree_html,
)
from src.ui.demo_service import (
    DEMO_SCENARIOS,
    execute_demo_query,
    generate_printable_html,
)


@pytest.fixture
def sample_article() -> dict:
    return {
        "id": "LUAT_48_2024_D1",
        "code": "Dieu 1",
        "title": "Pham vi dieu chinh",
        "lead": "Luat nay quy dinh ve doi tuong chiu thue...",
        "clauses": [
            {
                "id": "LUAT_48_2024_D1_K1",
                "code": "Khoan 1",
                "content": "Noi dung khoan 1",
                "points": [
                    {
                        "id": "LUAT_48_2024_D1_K1_a",
                        "code": "Diem a",
                        "content": "Noi dung diem a",
                    }
                ],
            }
        ],
    }


@pytest.fixture
def sample_document(sample_article: dict) -> dict:
    return {
        "id": "LUAT_48_2024",
        "code": "48/2024/QH15",
        "title": "Luat Thue gia tri gia tang",
        "chapters": [
            {
                "id": "LUAT_48_2024_CI",
                "code": "Chuong I",
                "title": "Chuong I NHUNG QUY DINH CHUNG",
                "articles": [sample_article],
            }
        ],
    }


def test_load_corpus_tree():
    docs = load_corpus_tree("data/processed/vat_corpus.json")
    assert isinstance(docs, list)
    assert len(docs) > 0
    assert any(d.get("id") == "LUAT_48_2024" for d in docs)
    cached = load_corpus_tree("data/processed/vat_corpus.json")
    assert cached is docs


def test_render_article_html_normal(sample_article: dict):
    html = render_article_html(sample_article, active_ids=[])
    assert 'id="art-LUAT_48_2024_D1"' in html
    assert "highlighted-article" not in html
    assert "Dieu 1" in html
    assert "Pham vi dieu chinh" in html
    assert "Khoan 1" in html
    assert "Noi dung khoan 1" in html
    assert "Diem a" in html
    assert "Noi dung diem a" in html


def test_render_article_html_highlighted(sample_article: dict):
    active_ids = ["LUAT_48_2024_D1"]
    html = render_article_html(sample_article, active_ids=active_ids)
    assert 'id="art-LUAT_48_2024_D1"' in html
    assert "highlighted-article" in html
    assert "background-color: #fff9db" in html
    assert "border-left: 4px solid #f59f00" in html
    assert "Dieu 1" in html
    assert "Pham vi dieu chinh" in html


def test_render_document_tree_html(sample_document: dict):
    html = render_document_tree_html(sample_document, active_ids=["LUAT_48_2024_D1"])
    assert "Luat Thue gia tri gia tang" in html
    assert "48/2024/QH15" in html
    assert "Chuong I" in html
    assert 'id="art-LUAT_48_2024_D1"' in html
    assert "highlighted-article" in html


def test_demo_scenarios_structure():
    assert isinstance(DEMO_SCENARIOS, list)
    assert len(DEMO_SCENARIOS) == 3
    colors = [s.get("badge_color") for s in DEMO_SCENARIOS]
    assert colors == ["green", "red", "yellow"]
    for s in DEMO_SCENARIOS:
        for k in ("name", "query", "as_of_date", "badge_color"):
            assert k in s
            assert isinstance(s[k], str)
            assert len(s[k]) > 0


@pytest.mark.requires_neo4j
def test_execute_demo_query_p3():
    scenario = DEMO_SCENARIOS[0]
    res = execute_demo_query(scenario["query"], scenario["as_of_date"], pipeline="P3_FullSystem")
    assert isinstance(res, dict)
    assert res["query"] == scenario["query"]
    assert res["as_of_date"] == scenario["as_of_date"]
    assert res["pipeline"] == "P3_FullSystem"
    assert "citations" in res
    assert "active_article_ids" in res
    assert "risk_score" in res
    assert "risk_level" in res
    assert "warnings" in res
    assert "irac_sections" in res
    assert isinstance(res["irac_sections"], dict)
    for sec in ("issue", "rule", "application", "conclusion"):
        assert sec in res["irac_sections"]


@pytest.mark.requires_neo4j
def test_execute_demo_query_p2():
    scenario = DEMO_SCENARIOS[2]
    res = execute_demo_query(scenario["query"], scenario["as_of_date"], pipeline="P2_TemporalGraphRAG")
    assert isinstance(res, dict)
    assert res["pipeline"] == "P2_TemporalGraphRAG"
    assert "citations" in res
    assert "risk_score" in res


@pytest.mark.requires_neo4j
def test_execute_demo_query_p1():
    scenario = DEMO_SCENARIOS[1]
    res = execute_demo_query(scenario["query"], scenario["as_of_date"], pipeline="P1_FlatRAG")
    assert isinstance(res, dict)
    assert res["pipeline"] == "P1_FlatRAG"
    assert "citations" in res
    assert "risk_score" in res


def test_generate_printable_html():
    query = "Doanh nghiệp xuất khẩu thuế suất bao nhiêu?"
    as_of_date = "2026-03-20"
    result = {
        "query": query,
        "as_of_date": as_of_date,
        "pipeline": "P3_FullSystem",
        "citations": ["LUAT_48_2024_D9"],
        "active_article_ids": ["LUAT_48_2024_D9"],
        "risk_score": 0,
        "risk_level": "LOW",
        "warnings": [],
        "irac_sections": {
            "issue": "Xác định thuế suất GTGT xuất khẩu.",
            "rule": "Căn cứ [LUAT_48_2024_D9].",
            "application": "Dịch vụ phần mềm thuộc nhóm 0%.",
            "conclusion": "Thuế suất 0%.",
        },
    }
    html = generate_printable_html(query, as_of_date, result)
    assert isinstance(html, str)
    assert "@media print" in html
    assert query in html
    assert "0" in html or "LOW" in html
    assert "LUAT_48_2024_D9" in html
    assert "Xác định thuế suất GTGT xuất khẩu." in html


@pytest.mark.requires_neo4j
def test_execute_demo_query_with_llm_success():
    query = "Doanh nghiệp tại Cần Thơ xuất khẩu phần mềm sang Nhật Bản vào tháng 03/2026 áp dụng thuế suất thuế GTGT bao nhiêu?"
    as_of_date = "2026-03-20"
    mock_llm_text = (
        "1. Issue: Vấn đề thuế 0%\n"
        "2. Rule: [LUAT_48_2024_D9]\n"
        "3. Application: Phù hợp\n"
        "4. Conclusion: Áp dụng 0%."
    )
    with patch("src.ui.demo_service.generate_llm_response", return_value=mock_llm_text):
        result = execute_demo_query(
            query,
            as_of_date,
            pipeline="P3_FullSystem",
            use_llm=True,
            model="qwen2.5:7b",
        )
    assert result["llm_used"] is True
    assert result["raw_llm_response"] is not None
    assert "LUAT_48_2024_D9" in result["citations"]
    assert "risk_level" in result


@pytest.mark.requires_neo4j
def test_execute_demo_query_with_llm_fallback():
    query = "Doanh nghiệp tại Cần Thơ xuất khẩu phần mềm sang Nhật Bản vào tháng 03/2026 áp dụng thuế suất thuế GTGT bao nhiêu?"
    as_of_date = "2026-03-20"
    with patch("src.ui.demo_service.generate_llm_response", return_value=None):
        result = execute_demo_query(
            query,
            as_of_date,
            pipeline="P3_FullSystem",
            use_llm=True,
            model="qwen2.5:7b",
        )
    assert result["llm_used"] is False
    assert result["raw_llm_response"] is None
    assert "irac_sections" in result
    for sec in ("issue", "rule", "application", "conclusion"):
        assert sec in result["irac_sections"]


@pytest.mark.requires_neo4j
def test_execute_demo_query_with_multi_turn_followup():
    """Verify short follow-up inherits previous turn context for retrieval."""
    history = [
        {"role": "user", "content": "Doanh nghiệp tại Cần Thơ xuất khẩu phần mềm sang Nhật Bản vào tháng 03/2026 áp dụng thuế suất thuế GTGT bao nhiêu?"},
        {"role": "assistant", "content": "Áp dụng thuế suất thuế GTGT là 0% đối với phần mềm xuất khẩu theo Điều 9 Luật 48/2024."}
    ]
    follow_up = "Thế còn điều kiện?"
    res = execute_demo_query(
        query=follow_up,
        as_of_date="2026-03-20",
        pipeline="P3_FullSystem",
        history=history,
    )
    assert any("181" in cid or "48" in cid for cid in res["citations"])
    assert res["risk_level"] in ["LOW", "MEDIUM"]


@pytest.mark.requires_neo4j
def test_execute_demo_query_fallback_dynamic_content():
    """Verify deterministic fallback summarizes relevant retrieved legal clauses for any topic."""
    query = "Mức thuế suất thuế GTGT đối với thiết bị y tế năm 2026 là bao nhiêu?"
    res = execute_demo_query(query=query, as_of_date="2026-03-20", use_llm=False)
    assert any("D9" in cid for cid in res["citations"])
    conc = res["irac_sections"]["conclusion"].lower()
    app = res["irac_sections"]["application"].lower()
    assert "y tế" in conc or "y tế" in app or "5%" in conc or "5%" in app


@pytest.mark.requires_neo4j
def test_execute_demo_query_llm_filters_irrelevant_citations():
    """Verify LLM branch filters irrelevant or hallucinated citations from response."""
    query = "tôi mở tiệm net dưới quê có phải đóng thuế không"
    as_of_date = "2026-03-20"
    mock_llm_text = (
        "1. Issue: Nghĩa vụ thuế\n"
        "2. Rule: [LUAT_48_2024_D9]\n"
        "3. Application: Phân tích\n"
        "4. Conclusion: Áp dụng 0%."
    )
    with patch("src.ui.demo_service.generate_llm_response", return_value=mock_llm_text):
        result = execute_demo_query(
            query,
            as_of_date,
            pipeline="P3_FullSystem",
            use_llm=True,
        )
    assert "LUAT_48_2024_D9" not in result["citations"]
    assert any("không liên quan" in w.lower() for w in result["warnings"]) or result["risk_score"] > 0


@pytest.mark.requires_neo4j
def test_fetch_pipeline_articles_records_raw_and_gated_counts(monkeypatch):
    import src.ui.demo_service as ds
    fake = [ds.ArticleNode(id="LUAT_48_2024_D5", doc_id="LUAT_48_2024", code="Dieu 5",
                           title="Doi tuong khong chiu thue", content="ho ca nhan kinh doanh",
                           valid_from="2025-07-01", valid_to="9999-12-31")]
    monkeypatch.setattr(ds, "query_temporal_rag",
                        lambda q, d: type("R", (), {"articles": fake})())
    trace = {}
    ds._fetch_pipeline_articles("P3_FullSystem", "tiem net dong thue", "2026-03-20", trace=trace)
    assert trace["raw_article_count"] == 1
    assert "gated_article_count" in trace


def test_execute_demo_query_emits_trace_for_empty_retrieval():
    with patch("src.ui.demo_service._fetch_pipeline_articles", return_value=([], [], [])):
        trace = {}
        res = execute_demo_query("thuế gtgt đối với dịch vụ không tồn tại", "2026-03-20", trace=trace)
        assert "raw_article_count" in trace
        assert trace["empty_reason"] in ("RETRIEVAL_MISS", "TEMPORAL_TRAP")


@pytest.mark.requires_neo4j
def test_execute_demo_query_trace_does_not_change_return_keys():
    scenario = DEMO_SCENARIOS[0]
    res_no_trace = execute_demo_query(scenario["query"], scenario["as_of_date"])
    trace = {}
    res_with_trace = execute_demo_query(scenario["query"], scenario["as_of_date"], trace=trace)
    assert set(res_no_trace.keys()) == set(res_with_trace.keys())
    assert "trace" not in res_with_trace
    assert "empty_reason" not in res_with_trace
    assert trace["empty_reason"] == "ANSWERED"


def test_execute_demo_query_marks_gating_overreject():
    def mock_fetch(pipeline, query, as_of_date, trace=None):
        if trace is not None:
            trace["raw_article_count"] = 5
            trace["gated_article_count"] = 0
        return [], [], []

    with patch("src.ui.demo_service._fetch_pipeline_articles", side_effect=mock_fetch):
        trace = {}
        res = execute_demo_query("tiem net dong thue", "2026-03-20", trace=trace)
        assert trace["empty_reason"] == "GATING_OVERREJECT"


@pytest.mark.requires_neo4j
def test_execute_demo_query_trace_out_of_scope_and_temporal_trap():
    trace_ood = {}
    execute_demo_query("hôm nay ăn gì ngon", "2026-03-20", trace=trace_ood)
    assert trace_ood["empty_reason"] == "OUT_OF_SCOPE"

    trap_scenario = DEMO_SCENARIOS[1]
    trace_trap = {}
    execute_demo_query(trap_scenario["query"], trap_scenario["as_of_date"], trace=trace_trap)
    assert trace_trap["empty_reason"] == "TEMPORAL_TRAP"


@pytest.mark.requires_neo4j
def test_execute_demo_query_emits_to_vat_diagnostics_file(tmp_path, monkeypatch):
    import json
    diag_file = tmp_path / "diagnostics.jsonl"
    monkeypatch.setenv("VAT_DIAGNOSTICS", str(diag_file))
    scenario = DEMO_SCENARIOS[0]
    execute_demo_query(scenario["query"], scenario["as_of_date"])
    assert diag_file.exists()
    data = json.loads(diag_file.read_text(encoding="utf-8").strip())
    assert data["query"] == scenario["query"]
    assert data["empty_reason"] == "ANSWERED"


@pytest.mark.requires_neo4j
def test_llm_freeform_prose_falls_back_to_deterministic_irac():
    query = "tôi mở tiệm net dưới quê thì có phải đóng thuế kh"
    as_of_date = "2026-03-20"
    prose = "Tiệm net dưới quê của bạn cần đóng thuế GTGT theo quy định hiện hành."
    with patch("src.ui.demo_service.generate_llm_response", return_value=prose):
        result = execute_demo_query(query, as_of_date, pipeline="P3_FullSystem", use_llm=True)
    assert result["llm_used"] is True
    assert result["raw_llm_response"] == prose
    irac = result["irac_sections"]
    assert irac["rule"].strip()
    assert irac["conclusion"].strip()
    assert "200 triệu" in irac["conclusion"]
    assert "thuế suất 0%" not in irac["conclusion"]
    assert "xuất khẩu" not in irac["conclusion"]


@pytest.mark.requires_neo4j
def test_llm_empty_response_falls_back_to_deterministic_irac():
    query = "tôi mở tiệm net dưới quê thì có phải đóng thuế kh"
    with patch("src.ui.demo_service.generate_llm_response", return_value=""):
        result = execute_demo_query(query, "2026-03-20", pipeline="P3_FullSystem", use_llm=True)
    irac = result["irac_sections"]
    assert irac["rule"].strip()
    assert irac["conclusion"].strip()
    assert "200 triệu" in irac["conclusion"]


@pytest.mark.requires_neo4j
def test_llm_partial_irac_falls_back_to_deterministic_irac():
    query = "tôi mở tiệm net dưới quê thì có phải đóng thuế kh"
    with patch("src.ui.demo_service.generate_llm_response",
               return_value="Issue: Nghĩa vụ thuế GTGT\n"):
        result = execute_demo_query(query, "2026-03-20", pipeline="P3_FullSystem", use_llm=True)
    irac = result["irac_sections"]
    assert irac["rule"].strip()
    assert irac["conclusion"].strip()
    assert "200 triệu" in irac["conclusion"]


@pytest.mark.requires_neo4j
def test_llm_wellformed_irac_is_not_overwritten():
    query = ("Doanh nghiệp tại Cần Thơ xuất khẩu phần mềm sang Nhật Bản "
             "vào tháng 03/2026 áp dụng thuế suất thuế GTGT bao nhiêu?")
    mock = (
        "Issue: Thuế suất GTGT phần mềm xuất khẩu\n"
        "Rule: [LUAT_48_2024_D9]\n"
        "Application: Đáp ứng điều kiện xuất khẩu.\n"
        "Conclusion: Áp dụng thuế suất 0%.\n"
    )
    with patch("src.ui.demo_service.generate_llm_response", return_value=mock):
        result = execute_demo_query(query, "2026-03-20", pipeline="P3_FullSystem", use_llm=True)
    irac = result["irac_sections"]
    assert irac["application"] == "Đáp ứng điều kiện xuất khẩu."
    assert irac["conclusion"] == "Áp dụng thuế suất 0%."







