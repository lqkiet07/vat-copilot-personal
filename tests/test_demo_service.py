"""Tests for demo service and UI corpus viewer components."""
from unittest.mock import patch
from copy import deepcopy
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path

import pytest
from src.ui.corpus_viewer import (
    load_corpus_index,
    load_corpus_tree,
    render_article_html,
    render_document_tree_html,
)
from src.ui.demo_service import (
    DEMO_SCENARIOS,
    execute_demo_query,
    generate_printable_html,
)


class RenderedHTML(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


def test_step86_demo_actual_fetch_preserves_uncertainty(monkeypatch):
    from types import SimpleNamespace
    import src.ui.demo_service as demo
    import src.engine.retrieval_service as retrieval
    from src.engine.models import ArticleNode
    from src.verifier.models import CitationStatus

    article = ArticleNode(
        id="VBHN_25_2026_D1", doc_id="VBHN_25_2026", code="Điều 1", title="Thuế GTGT",
        content="Thuế GTGT dịch vụ xuất khẩu.", valid_from="2025-01-01", valid_to="9999-12-31",
    )
    monkeypatch.setattr(demo, "query_temporal_rag", lambda *a: SimpleNamespace(articles=[article]))
    monkeypatch.setattr(retrieval, "verify_citations", lambda *a, **k: SimpleNamespace(citations=[
        CitationStatus(id=article.id, status="UNRESOLVED", exists=True, is_active=None, message="Exact demo warning"),
    ]))
    trace = {}
    result = demo.execute_demo_query("Thuế GTGT dịch vụ xuất khẩu?", "2026-03-20", trace=trace)
    assert result["answer_status"] == "INSUFFICIENT_BASIS"
    assert result["citations"] == [article.id]
    assert result["active_article_ids"] == []
    assert "Exact demo warning" in result["warnings"]
    assert trace["unresolved_citations"] == [article.id]
    assert trace["unresolved_warnings"] == ["Exact demo warning"]


@pytest.mark.parametrize("as_of", ["2026-03-20", "2026-06-20"])
def test_r7_actual_demo_fetch_bypasses_generation_for_reviewed_export(monkeypatch, as_of):
    from types import SimpleNamespace
    import src.ui.demo_service as demo
    from src.engine.models import ArticleNode
    from unittest.mock import MagicMock

    root = Path(__file__).resolve().parents[1]
    corpus = str(root / "data/processed/vat_corpus.staging.json")
    ledger = str(root / "tests/fixtures/amendment_review.staging.json")
    documents = json.loads(Path(corpus).read_text(encoding="utf-8"))
    meta = next(a for d in documents for a in d["articles"] if a["id"] == "LUAT_48_2024_D9")
    article = ArticleNode(id="LUAT_48_2024_D9", doc_id="LUAT_48_2024", code=meta["code"],
                          title=meta["title"], content=meta["content"],
                          valid_from=meta["valid_from"], valid_to=meta["valid_to"])
    monkeypatch.setattr(demo, "selected_corpus_kwargs", lambda: {
        "corpus_path": corpus, "reviewed_amendment_path": ledger})
    monkeypatch.setattr(demo, "query_temporal_rag", lambda *a: SimpleNamespace(articles=[article]))
    provider = MagicMock(side_effect=AssertionError("Generation must not run"))
    monkeypatch.setattr(demo, "generate_llm_response", provider)
    with patch("src.verifier.citation_verifier.get_neo4j_driver") as factory:
        session = factory.return_value.session.return_value.__enter__.return_value
        session.run.side_effect = lambda query, **kw: MagicMock(data=lambda: [
            {"cid": cid, "exists": True, "valid_from": "2025-07-01",
             "valid_to": "9999-12-31", "amendments": []} for cid in kw["ids"]])
        trace = {}
        result = demo.execute_demo_query(
            "Doanh nghiệp tại Cần Thơ xuất khẩu phần mềm sang Nhật Bản áp dụng thuế suất thuế GTGT bao nhiêu?",
            as_of, use_llm=True, trace=trace)
    provider.assert_not_called()
    assert result["answer_status"] == "INSUFFICIENT_BASIS"
    assert result["llm_used"] is False and result["raw_llm_response"] is None
    assert result["citations"] == [article.id] and result["active_article_ids"] == []
    assert any("LUAT_90_2025_D4" in w and "LUAT_90_2025:552:40" in w for w in result["warnings"])
    assert trace["generation_disposition"] == "safety_bypass"
    assert trace["generation_reason"] == "unresolved_evidence"
    assert trace["generation_attempted"] is False and trace["generated_delivered"] is False


STAGING_PATH = Path(__file__).resolve().parents[1] / "data/processed/vat_corpus.staging.json"


def write_corpus(tmp_path, documents):
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(documents, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_actual_kind_and_pdf_blocks_keep_table_order_and_drop_source_attributes():
    doc = {
        "id": "D", "articles": [],
        "appendices": [{"id": "A", "title": "Appendix", "blocks": [
            {"kind": "paragraph", "text": "before <img src=x onerror=boom>"},
            {"kind": "table", "text": "cell one cell two", "html":
             '<table onclick="boom"><tr><td rowspan="2" onmouseover="boom">cell one'
             '<img src=x onerror="boom"><script>boom()</script></td><td>cell two</td></tr></table>'},
            "after PDF <script>unsafe</script>",
        ]}],
        "forms": [{"id": "F", "title": "Form", "blocks": [
            {"kind": "paragraph", "text": "form before"},
            {"kind": "table", "text": "", "html": "<table><tr><td>form cell</td></tr></table>"},
            {"kind": "unsupported", "text": "form after"},
        ]}],
    }
    original = deepcopy(doc)
    html = render_document_tree_html(doc, [])
    parsed = RenderedHTML(html)
    assert any(tag == "table" for tag, _ in parsed.tags)
    assert html.index("before") < html.index("cell one") < html.index("cell two") < html.index("after PDF")
    assert html.index("form before") < html.index("form cell") < html.index("form after")
    assert "&lt;img src=x onerror=boom&gt;" in html
    assert "&lt;script&gt;unsafe&lt;/script&gt;" in html
    assert not any(tag in {"script", "img"} for tag, _ in parsed.tags)
    assert not any(k.startswith("on") for _, attrs in parsed.tags for k in attrs)
    assert doc == original


def test_markdown_and_legacy_tables_render_cells_without_untrusted_html():
    doc = {"id": "D", "articles": [{"id": "D1", "tables": [
        "| Head | Value |\n| --- | --- |\n| A | <svg onload=boom> |",
    ]}], "appendices": [{"id": "A", "blocks": [
        {"type": "table", "rows": [["legacy", "<img src=x>"]]},
    ], "tables": ["| Extra |\n| --- |\n| markdown only |"]}]}
    html = render_document_tree_html(doc, [])
    assert "<td>Head</td>" in html
    assert "<td>A</td>" in html
    assert "&lt;svg onload=boom&gt;" in html
    assert "<td>legacy</td>" in html
    assert "&lt;img src=x&gt;" in html
    assert "<td>markdown only</td>" in html


def test_all_staging_documents_render_real_tables_in_source_order():
    documents = json.loads(STAGING_PATH.read_text(encoding="utf-8"))
    assert len(documents) == 33
    table_count = 0
    pdf_count = 0
    for doc in documents:
        html = render_document_tree_html(doc, [])
        assert escape(doc["title"]) in html
        for section in doc.get("appendices", []) + doc.get("forms", []):
            section_html = html.split(f'id="app-{escape(section["id"])}"', 1)[1]
            cursor = 0
            for block in section.get("blocks", []):
                if isinstance(block, str):
                    pdf_count += 1
                    needle = escape(block)
                elif block.get("kind") == "table":
                    table_count += 1
                    source = RenderedHTML(block["html"])
                    cells = [t.strip() for t in source.text if t.strip()]
                    assert cells
                    cursor = section_html.index("<table", cursor)
                    for cell in cells:
                        cursor = section_html.index(escape(cell), cursor) + len(escape(cell))
                    continue
                else:
                    needle = escape(block.get("text", ""))
                if needle:
                    cursor = section_html.index(needle, cursor) + len(needle)
    assert table_count == 42
    assert pdf_count == 39


def test_staging_index_keeps_exact_point_ownership_and_separate_production_cache():
    import src.ui.corpus_viewer as viewer

    production = load_corpus_tree()
    staging = load_corpus_tree(str(STAGING_PATH))
    assert staging is not production
    assert load_corpus_tree() is production
    index = load_corpus_index(str(STAGING_PATH))
    expected_ids = {a["id"] for d in staging for a in d.get("articles", [])}
    assert set(index) == expected_ids
    meta = index["LUAT_48_2024_D5"]
    clause = meta["provisions"]["LUAT_48_2024_D5_K25"]
    assert meta["valid_from"] == "2025-07-01"
    assert clause["valid_from"] == "2025-07-01"
    assert clause["provision_spans"][0]["valid_from"] == "2026-01-01"
    assert meta["provision_spans"][0]["source_id"] == clause["id"]
    sibling = meta["provisions"]["LUAT_48_2024_D5_K24"]
    assert sibling["provision_spans"] == []
    assert sibling["inherited_provision_spans"] == []
    assert index["VBHN_25_2026_D1"]["status"] == "CONSOLIDATION_REFERENCE"
    assert index["VBHN_25_2026_D1"]["provenance"]
    for document in staging:
        for article in document.get("articles", []):
            assert index[article["id"]]["has_appendices"] == bool(document.get("appendices"))
    assert str(STAGING_PATH.resolve()) in viewer._CORPUS_CACHE


def test_dates_are_ordinary_and_exceptions_stay_at_article_clause_point():
    span = {"subject": "subject", "valid_from": "2026-01-01", "valid_to": "9999-12-31", "evidence": "evidence"}
    article = {"id": "D1", "valid_from": "2025-07-01", "valid_to": "2025-12-31",
               "provision_spans": [{**span, "subject": "article exception"}], "clauses": [
                   {"id": "K1", "content": "clause", "valid_from": "2025-08-01", "valid_to": "2025-11-30",
                    "provision_spans": [{**span, "subject": "clause exception"}], "points": [
                        {"id": "P1", "content": "point", "valid_from": "2025-09-01", "valid_to": "2025-10-31",
                         "provision_spans": [{**span, "subject": "point exception"}]}]}]}
    original = deepcopy(article)
    html = render_article_html(article, [])
    assert "Hiệu lực chung: 2025-07-01 → 2025-12-31" in html
    assert "Hiệu lực chung: 2025-08-01 → 2025-11-30" in html
    assert "Hiệu lực chung: 2025-09-01 → 2025-10-31" in html
    assert html.index("article exception") < html.index('id="cls-K1"')
    assert html.index("clause exception") < html.index('id="pt-P1"') < html.index("point exception")
    assert html.count("evidence") == 3
    assert article == original


def test_all_renderer_ids_and_metadata_are_escaped():
    attack = '\"><svg onload="boom">&'
    span = {k: attack for k in ("subject", "valid_from", "valid_to", "evidence")}
    point = {"id": attack, "code": attack, "content": attack, "provision_spans": [span]}
    clause = {**point, "points": [point]}
    article = {**point, "title": attack, "lead": attack, "valid_from": attack, "valid_to": attack, "clauses": [clause]}
    doc = {"id": attack, "title": attack, "code": attack, "status": attack,
           "document_role": attack, "valid_from": attack, "valid_to": attack,
           "provenance": {"source": attack},
           "chapters": [{"id": attack, "title": attack, "articles": [article]}],
           "appendices": [{"id": attack, "title": attack, "blocks": [attack]}]}
    html = render_document_tree_html(doc, [])
    parsed = RenderedHTML(html)
    assert not any(tag == "svg" for tag, _ in parsed.tags)
    assert not any(k.startswith("on") for _, attrs in parsed.tags for k in attrs)
    for prefix in ("doc", "ch", "art", "cls", "pt", "app"):
        assert f'id="{prefix}-{escape(attack)}"' in html
    assert "&lt;svg" in html


def test_index_exact_owners_inherit_only_ancestor_spans_and_keep_parent_dates(tmp_path):
    span = lambda subject: {"subject": subject, "valid_from": "2026-01-01", "valid_to": "9999-12-31", "evidence": subject}
    provenance = {"primary_body_source": "local.pdf", "sources": [{"path": "local.pdf"}]}
    doc = {"id": "D", "code": "DOC", "title": "Document", "valid_from": "2025-07-01", "valid_to": "2025-12-31",
           "status": "CONSOLIDATION_REFERENCE", "document_role": "consolidation_reference", "provenance": provenance,
           "appendices": [{"id": "APP", "title": "Annex", "blocks": ["PDF"]}],
           "articles": [{"id": "D1", "code": "Article 1", "title": "Title", "lead": "Lead",
                         "provision_spans": [span("article")], "clauses": [
                             {"id": "K1", "code": "Clause 1", "content": "Clause", "provision_spans": [span("clause")],
                              "points": [{"id": "P1", "content": "One", "provision_spans": [span("one")]},
                                         {"id": "P2", "content": "Two", "valid_from": "2025-08-01"}]},
                             {"id": "K2", "content": "Sibling"}]}]}
    index = load_corpus_index(write_corpus(tmp_path, [doc]))
    assert set(index) == {"D1"}
    meta = index["D1"]
    assert meta["valid_from"] == "2025-07-01"
    assert meta["valid_to"] == "2025-12-31"
    assert [s["source_id"] for s in meta["provision_spans"]] == ["D1", "K1", "P1"]
    provisions = meta["provisions"]
    assert set(provisions) == {"D1", "K1", "K2", "P1", "P2"}
    assert provisions["P1"]["parent_id"] == "K1"
    assert provisions["P1"]["article_id"] == "D1"
    assert provisions["P1"]["ancestor_ids"] == ["D1", "K1"]
    assert provisions["D1"]["descendant_ids"] == ["K1", "P1", "P2", "K2"]
    assert provisions["P1"]["provision_spans"] == [{**span("one"), "source_id": "P1"}]
    assert [s["source_id"] for s in provisions["P2"]["inherited_provision_spans"]] == ["D1", "K1"]
    assert provisions["P2"]["provision_spans"] == []
    assert [s["source_id"] for s in provisions["K2"]["inherited_provision_spans"]] == ["D1"]
    assert provisions["K2"]["provision_spans"] == []
    assert provisions["K1"]["valid_from"] == "2025-07-01"
    assert provisions["P2"]["valid_from"] == "2025-08-01"
    for item in provisions.values():
        assert item["doc_id"] == "D"
        assert item["status"] == "CONSOLIDATION_REFERENCE"
        assert item["provenance"] == provenance
        assert item["document_role"] == "consolidation_reference"
    assert meta["has_appendices"] is True
    assert meta["appendices"][0]["id"] == "APP"
    assert meta["appendices"][0]["has_content"] is True
    assert meta["appendices"][0]["provenance"] == provenance
    assert "Clause 1: Clause" in meta["text"]
    assert meta["provenance"] == provenance
    assert "consolidation-banner" in render_document_tree_html(doc, [])


def test_cache_identity_is_resolved_path_not_relative_spelling(tmp_path, monkeypatch):
    import src.ui.corpus_viewer as viewer

    viewer._CORPUS_CACHE.clear()
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for directory, doc_id in ((first, "FIRST"), (second, "SECOND")):
        (directory / "corpus.json").write_text(json.dumps([{"id": doc_id}]), encoding="utf-8")
    monkeypatch.chdir(first)
    one = load_corpus_tree("corpus.json")
    assert load_corpus_tree(str(first / "corpus.json")) is one
    monkeypatch.chdir(second)
    assert load_corpus_tree("corpus.json")[0]["id"] == "SECOND"
    assert load_corpus_tree(str(first / "corpus.json")) is one
    assert set(viewer._CORPUS_CACHE) == {str((first / "corpus.json").resolve()), str((second / "corpus.json").resolve())}
    with pytest.raises(FileNotFoundError):
        load_corpus_tree(str(tmp_path / "missing.json"))
    viewer._CORPUS_CACHE.clear()


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
    assert (
        any("không liên quan" in w.lower() or "bản nháp llm đã bị thay" in w.lower() for w in result["warnings"])
        or result["risk_score"] > 0
    )


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


def test_render_article_html_with_provision_spans():
    article = {
        "id": "LUAT_48_2024_D5",
        "code": "Dieu 5",
        "title": "Doi tuong khong chiu thue",
        "clauses": [
            {
                "id": "LUAT_48_2024_D5_K25",
                "code": "Khoan 25",
                "content": "Hàng hóa dịch vụ...",
                "valid_from": "2025-07-01",
                "provision_spans": [
                    {
                        "subject": "<script>alert('xss')</script>Hộ kinh doanh",
                        "valid_from": "2026-01-01",
                        "valid_to": "9999-12-31",
                        "evidence": "Article 18(2)",
                    }
                ],
                "points": [],
            }
        ],
    }
    html = render_article_html(article, active_ids=[])
    assert "Khoan 25" in html
    assert "2026-01-01" in html
    assert "Article 18(2)" in html
    assert "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;Hộ kinh doanh" in html or "&lt;script&gt;alert(&#39;xss&#39;)&lt;/script&gt;Hộ kinh doanh" in html


def test_render_document_tree_html_consolidation_banner():
    doc = {
        "id": "VBHN_25_2026",
        "code": "25/2026/VBHN-BTC",
        "title": "Văn bản hợp nhất 25",
        "status": "CONSOLIDATION_REFERENCE",
        "articles": [],
    }
    html = render_document_tree_html(doc, active_ids=[])
    assert "Văn bản hợp nhất — tài liệu đối chiếu, không phải văn bản sửa đổi độc lập" in html


def test_render_document_tree_html_with_appendices_escaped():
    doc = {
        "id": "TT_89_2026",
        "code": "89/2026/TT-BTC",
        "title": "Thông tư 89",
        "articles": [],
        "appendices": [
            {
                "id": "PL1",
                "title": "Phụ lục I",
                "blocks": [
                    {"type": "paragraph", "text": "<img src=x onerror=alert(1)>Bảng biểu"}
                ],
            }
        ],
    }
    html = render_document_tree_html(doc, active_ids=[])
    assert "Phụ lục I" in html
    assert "&lt;img src=x onerror=alert(1)&gt;Bảng biểu" in html


def test_load_corpus_index_with_custom_path_preserves_metadata(tmp_path):
    sample = [
        {
            "id": "DOC_TEST",
            "code": "01/TEST",
            "title": "Test Doc",
            "status": "CONSOLIDATION_REFERENCE",
            "valid_from": "2025-01-01",
            "valid_to": "9999-12-31",
            "articles": [
                {
                    "id": "DOC_TEST_D1",
                    "code": "Dieu 1",
                    "title": "Test Art",
                    "clauses": [
                        {
                            "id": "DOC_TEST_D1_K1",
                            "code": "Khoan 1",
                            "content": "Noi dung",
                            "provision_spans": [
                                {
                                    "subject": "Doi tuong A",
                                    "valid_from": "2026-01-01",
                                    "valid_to": "9999-12-31",
                                }
                            ],
                        }
                    ],
                }
            ],
            "appendices": [{"id": "PL1", "title": "Phu luc"}],
        }
    ]
    p = tmp_path / "custom.json"
    p.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

    idx = load_corpus_index(str(p))
    assert "DOC_TEST_D1" in idx
    art_meta = idx["DOC_TEST_D1"]
    assert art_meta["status"] == "CONSOLIDATION_REFERENCE"
    assert art_meta["has_provision_spans"] is True
    assert len(art_meta["provision_spans"]) == 1
    assert art_meta["has_appendices"] is True
