import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.api import answer_service, chat_service
from src.api.chat_domain import understand_query
from src.engine.models import ArticleNode
from src.engine.retrieval_service import retrieve_and_gate
from src.verifier.guardrails import check_query_domain


@pytest.mark.parametrize("text", ["quán net dưới quê", "tiệm cà phê", "quán tạp hóa"])
def test_colloquial_activity_is_not_legal_household_status(text):
    entities = understand_query(text)
    assert entities.business_type is None
    assert entities.revenue_vnd is None


@pytest.mark.parametrize("text", ["quyền giữ hồ sơ ra sao", "Điều 37 Nghị định 812/2031", "quán net dưới quê"])
def test_unclear_or_new_legal_references_reach_retrieval(text):
    assert check_query_domain(text).is_allowed


@pytest.mark.parametrize("threshold,rate,cid", [(317, 7, "LUAT_731_2024_D17"), (641, 13, "ND_812_2025_D32")])
@pytest.mark.parametrize("use_llm", [False, True])
def test_missing_review_is_extractive_without_generation(monkeypatch, threshold, rate, cid, use_llm):
    monkeypatch.setattr("src.config.selected_review_path", lambda p: None)
    article = ArticleNode(id=cid, doc_id=cid.split("_D")[0], code="Điều 17", title="Thuế GTGT",
                          content=f"Hộ kinh doanh có doanh thu {threshold} triệu đồng. Xuất khẩu chịu thuế suất {rate}%.",
                          valid_from="2025-01-01", valid_to="9999-12-31")
    provider = Mock(side_effect=AssertionError("No review must not invoke provider"))
    score = lambda *a, **k: SimpleNamespace(risk_score=0, risk_level="LOW", warnings=[])
    fetch = lambda *a, **k: ([cid], [cid], [article])
    trace = {}
    result = answer_service.answer_query("Hộ kinh doanh nộp thuế GTGT thế nào?", "2026-03-20",
                                        use_llm=use_llm, llm_fn=provider, fetch_fn=fetch, score_fn=score, trace=trace)
    assert result.answer_status == "INSUFFICIENT_BASIS"
    assert article.content in result.irac_sections["application"]
    assert str(threshold) not in result.irac_sections["conclusion"]
    assert "%" not in result.irac_sections["conclusion"]
    assert "thẩm định" in " ".join(result.warnings).lower()
    assert trace["generation_reason"] == "review_required_for_generation"
    assert not trace["generation_attempted"]
    provider.assert_not_called()
    monkeypatch.setattr(chat_service, "selected_corpus_kwargs", lambda: {})
    monkeypatch.setattr(chat_service, "selected_review_path", lambda p: None)
    monkeypatch.setattr(chat_service, "query_temporal_rag", lambda *a: SimpleNamespace(articles=[article]))
    monkeypatch.setattr(chat_service, "verify_and_score", score)
    monkeypatch.setattr(chat_service, "_chat_verifier", lambda *a, **k: SimpleNamespace(citations=[SimpleNamespace(id=cid, status="VALID")]))
    monkeypatch.setattr(chat_service, "generate_llm_response", provider)
    monkeypatch.setattr(chat_service, "generate_llm_response_stream", provider)
    events = list(chat_service.stream_chat([{"role": "user", "content": result.query}], result.as_of_date))
    done = events[-1][1]
    assert done["answer_status"] == result.answer_status
    assert done["message"] == "\n".join(result.irac_sections.values())
    assert "".join(p["delta"] for name, p in events if name == "token") == done["message"]
    provider.assert_not_called()


def test_retrieval_does_not_send_topic_blacklist_as_legal_veto():
    node = ArticleNode(id="LUAT_48_2024_D9", doc_id="LUAT_48_2024", code="Điều 9",
                       title="Thuế suất xuất khẩu", content="Tín dụng, vũ khí, casino, 0%",
                       valid_from="2025-01-01", valid_to="9999-12-31")
    seen = []
    def verifier(text, day, relevance_map=None, **kwargs):
        seen.append(relevance_map)
        return SimpleNamespace(citations=[SimpleNamespace(id=node.id, status="VALID")])
    result = retrieve_and_gate("Hộ kinh doanh lưu hồ sơ thuế?", "2026-03-20",
                               retriever_fn=lambda *a: SimpleNamespace(articles=[node]), verifier_fn=verifier)
    assert result.active_ids == [node.id]
    assert not seen[0] or all(seen[0].values())


@pytest.mark.parametrize("text", ["Tôi mua máy 317 triệu", "Thuế GTGT cho khoản vay 641 triệu"])
def test_money_without_explicit_revenue_is_not_revenue(text):
    assert understand_query(text).revenue_vnd is None


@pytest.mark.parametrize("query", ["Hộ kinh doanh nộp thuế", "Xuất khẩu hàng hóa", "quán net thuế bao nhiêu"])
def test_hybrid_expansion_has_only_neutral_synonyms(monkeypatch, query):
    from src.engine import hybrid_retriever as hybrid
    from unittest.mock import MagicMock
    seen = []
    driver = MagicMock()
    monkeypatch.setattr(hybrid, "get_neo4j_driver", lambda: driver)
    monkeypatch.setattr(hybrid, "_search_fulltext", lambda *a, **k: seen.extend(k["synonyms"]) or [])
    monkeypatch.setattr(hybrid, "_search_vector", lambda *a, **k: [])
    monkeypatch.setattr(hybrid, "_enrich_articles", lambda *a: [])
    hybrid.query_hybrid_temporal(query, "2026-03-20", embedder=SimpleNamespace(embed_text=lambda text: [1.0]))
    assert seen == list(understand_query(query).industry_synonyms)


@pytest.mark.parametrize("threshold,rate,cid", [(317, 7, "LUAT_731_2024_D17"), (641, 13, "ND_812_2025_D32")])
def test_reviewed_metamorphic_query_chat_parity_and_authority_mismatch(tmp_path, monkeypatch, threshold, rate, cid):
    import hashlib
    from unittest.mock import MagicMock
    from src.verifier import citation_verifier
    doc_id = cid.split("_D")[0]
    text = f"Hộ kinh doanh có doanh thu {threshold} triệu đồng. Xuất khẩu có mức {rate}% khi đủ điều kiện."
    source = tmp_path / "source.txt"
    source.write_text(text, encoding="utf-8")
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"id": doc_id, "issue_date": "2024-01-01", "valid_from": "2025-01-01",
                                  "valid_to": "9999-12-31", "articles": [{"id": cid, "content": text}]}]), encoding="utf-8")
    ledger = tmp_path / "review.json"
    ledger.write_text(json.dumps({"schema_version": 1, "oracle": "independent_raw_source_review", "review_complete": True,
                                  "pins": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (corpus, source)},
                                  "documents": [{"doc_id": doc_id, "review_status": "reviewed", "source_paths": [source.name]}],
                                  "candidates": []}), encoding="utf-8")
    article = ArticleNode(id=cid, doc_id=doc_id, code="Điều 17", title="Xuất khẩu, tín dụng, casino",
                          content=text, valid_from="2025-01-01", valid_to="9999-12-31")
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.side_effect = lambda q, **k: SimpleNamespace(data=lambda: [
        {"cid": key, "exists": True, "valid_from": "2025-01-01", "valid_to": "9999-12-31", "amendments": []}
        for key in k["ids"]])
    monkeypatch.setattr(citation_verifier, "get_neo4j_driver", lambda: driver)
    monkeypatch.setattr("src.config.selected_review_path", lambda p: None)
    fetch = lambda *a: SimpleNamespace(articles=[article])
    draft = f"Issue: Hồ sơ\nRule: {text} [{cid}]\nApplication: Cần đối chiếu điều kiện trong nguồn. [{cid}]\nConclusion: Chưa có doanh thu thực tế, cần làm rõ. [{cid}]"
    assessment = json.dumps({"outcome": "supported", "coverage": True, "claims": [
        {"text": "Cần đối chiếu điều kiện", "status": "supported", "evidence_ids": [cid]}], "missing_facts": []})
    paths = {"corpus_path": str(corpus), "reviewed_amendment_path": str(ledger)}
    provider = Mock(side_effect=[draft, assessment])
    trace = {}
    result = answer_service.answer_query("Hộ kinh doanh thuế GTGT thế nào?", "2026-03-20", use_llm=True,
                                        retriever_fn=fetch, llm_fn=provider, trace=trace, **paths)
    assert result.answer_status == "ANSWERED"
    assert provider.call_count == 2
    assert trace["assessment_outcome"] == "supported"
    assert str(threshold) not in result.irac_sections["conclusion"]
    monkeypatch.setattr(chat_service, "selected_corpus_kwargs", lambda: {"corpus_path": str(corpus)})
    monkeypatch.setattr(chat_service, "selected_review_path", lambda p: str(ledger))
    monkeypatch.setattr(chat_service, "query_temporal_rag", fetch)
    monkeypatch.setattr(chat_service, "generate_llm_response", Mock(side_effect=[draft, assessment]))
    response = chat_service.build_response([{"role": "user", "content": result.query}], result.as_of_date)
    assert response.message == "\n".join(result.irac_sections.values())
    assert response.citations == result.citations
    assert response.answer_status == result.answer_status
    provider.reset_mock()
    mismatch = answer_service.answer_query("Thuế GTGT theo Luật 998/2030?", "2026-03-20", use_llm=True,
                                          retriever_fn=fetch, llm_fn=provider, **paths)
    assert mismatch.answer_status == "INSUFFICIENT_BASIS"
    assert mismatch.citations == [cid]
    assert "missing_explicit_authority_LUAT_998_2030" in mismatch.resolved_evidence.blocked
    assert "missing explicit authority LUAT 998 2030" in mismatch.irac_sections["conclusion"]
    provider.assert_not_called()
    source.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError):
        answer_service.answer_query(result.query, result.as_of_date, use_llm=True,
                                    retriever_fn=fetch, llm_fn=provider, **paths)
    provider.assert_not_called()


@pytest.mark.parametrize("question", [
    "Viết mã Python tính Fibonacci", "Generate JavaScript code to sort a list",
    "Dịch đoạn văn này sang tiếng Anh", "Write a poem about the moon",
])
@pytest.mark.parametrize("review", [None, "selected-review.json"])
def test_unrelated_intents_refused_by_query_chat_and_sse(monkeypatch, question, review):
    forbidden = Mock(side_effect=AssertionError("Unrelated intent must not retrieve or generate"))
    monkeypatch.setattr("src.config.selected_review_path", lambda p: review)
    monkeypatch.setattr(chat_service, "selected_review_path", lambda p: review)
    monkeypatch.setattr(chat_service, "selected_corpus_kwargs", lambda: {})
    monkeypatch.setattr(chat_service, "query_temporal_rag", forbidden)
    monkeypatch.setattr(chat_service, "generate_llm_response", forbidden)
    monkeypatch.setattr(chat_service, "generate_llm_response_stream", forbidden)
    result = answer_service.answer_query(question, "2026-03-20", use_llm=True,
                                        retriever_fn=forbidden, llm_fn=forbidden)
    assert result.answer_status == "OUT_OF_SCOPE"
    assert result.is_out_of_domain
    messages = [{"role": "user", "content": question}]
    response = chat_service.build_response(messages, result.as_of_date)
    events = list(chat_service.stream_chat(messages, result.as_of_date))
    assert not response.in_scope
    assert response.answer_status == result.answer_status
    assert events[-1][1] == response.model_dump()
    assert "".join(p["delta"] for name, p in events if name == "token") == response.message
    forbidden.assert_not_called()


@pytest.mark.parametrize("question", [
    "Viết mã Python tính thuế GTGT", "Lập trình công cụ kê khai cho doanh nghiệp",
    "Viết mã theo Điều 37 Nghị định 812/2031", "Thuật toán xử lý hóa đơn",
    "Điều 37 Nghị định 812/2031", "Khoản này áp dụng thế nào?",
])
def test_technical_intents_with_legal_business_context_are_not_refused(question):
    assert check_query_domain(question).is_allowed


@pytest.mark.parametrize("use_llm", [False, True])
@pytest.mark.parametrize("mode", ["empty", "temporal", "unresolved"])
def test_all_unreviewed_p3_early_exits_share_limitation_and_trace(monkeypatch, use_llm, mode):
    day = "2026-03-20"
    question = "Thuế GTGT theo Luật 812/2031?"
    provider = Mock(side_effect=AssertionError("Unreviewed early exit must not generate"))
    monkeypatch.setattr("src.config.selected_review_path", lambda p: None)
    monkeypatch.setattr(chat_service, "selected_review_path", lambda p: None)
    monkeypatch.setattr(chat_service, "selected_corpus_kwargs", lambda: {})
    docs = [{"code": "812/2031", "title": "Luật thử nghiệm", "valid_from": "2031-01-01"}] if mode == "temporal" else []
    monkeypatch.setattr(answer_service, "load_corpus_tree", lambda *a, **k: docs)
    def fetch(*args, **kwargs):
        if mode == "unresolved":
            kwargs["trace"].update(unresolved_citations=["LUAT_812_2031_D1"], unresolved_warnings=["Exact uncertainty"])
        return [], [], []
    trace = {}
    result = answer_service.answer_query(question, day, use_llm=use_llm, fetch_fn=fetch, llm_fn=provider, trace=trace)
    assert result.answer_status == "INSUFFICIENT_BASIS"
    assert any("thẩm định" in warning for warning in result.warnings)
    assert trace["generation_reason"] == "review_required_for_generation"
    assert not trace["generation_attempted"] and not trace["generated_delivered"]
    assert "chưa đủ căn cứ" in result.irac_sections["conclusion"].lower()
    if mode == "temporal":
        assert any("chưa có hiệu lực" in warning for warning in result.warnings)
        assert "không được áp dụng" not in result.irac_sections["conclusion"].lower()
    if mode == "unresolved":
        assert "Exact uncertainty" in result.warnings
    original_answer = chat_service.answer_query
    def chat_answer(*args, **kwargs):
        kwargs["fetch_fn"] = fetch
        return original_answer(*args, **kwargs)
    monkeypatch.setattr(chat_service, "answer_query", chat_answer)
    monkeypatch.setattr(chat_service, "generate_llm_response", provider)
    monkeypatch.setattr(chat_service, "generate_llm_response_stream", provider)
    rows = []
    monkeypatch.setattr(chat_service, "emit_trace", lambda row: rows.append(row.copy()))
    messages = [{"role": "user", "content": question}]
    response = chat_service.build_response(messages, day)
    events = list(chat_service.stream_chat(messages, day))
    assert response.answer_status == result.answer_status
    assert response.message == "\n".join(result.irac_sections.values())
    assert response.warnings == result.warnings
    assert events[-1][1] == response.model_dump()
    assert all(row["generation_reason"] == "review_required_for_generation" for row in rows)
    provider.assert_not_called()
