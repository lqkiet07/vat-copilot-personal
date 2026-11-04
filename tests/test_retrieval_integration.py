import pytest
from src.engine.retrieval_service import RetrievalResult, retrieve_and_gate
from src.benchmark.baselines import run_p3_full_system

pytestmark = pytest.mark.requires_neo4j


def test_retrieve_and_gate_domestic_household_query():
    query = "tiệm net dưới quê đóng thuế như thế nào"
    as_of_date = "2026-03-20"
    res = retrieve_and_gate(query, as_of_date=as_of_date, top_k=5, pipeline="P3_FullSystem")

    assert isinstance(res, RetrievalResult)
    assert res.query == query
    assert "tiệm net" in res.normalized_query
    assert "LUAT_48_2024_D5" in res.active_ids
    assert "LUAT_48_2024_D9" not in res.active_ids
    assert any(a.id == "LUAT_48_2024_D5" for a in res.articles)
    assert not any(a.id == "LUAT_48_2024_D9" for a in res.articles)
    assert res.gated_article_count <= res.raw_article_count
    assert res.relevance_map.get("LUAT_48_2024_D9") is False


def test_retrieve_and_gate_export_query():
    query = "thuế suất 0% xuất khẩu dịch vụ phần mềm Điều 9 Luật 48"
    as_of_date = "2026-03-20"
    res = retrieve_and_gate(query, as_of_date=as_of_date, top_k=5, pipeline="P3_FullSystem")

    assert isinstance(res, RetrievalResult)
    assert "LUAT_48_2024_D9" in res.active_ids
    assert any(a.id == "LUAT_48_2024_D9" for a in res.articles)
    assert res.relevance_map.get("LUAT_48_2024_D9") is True


def test_p3_baseline_excludes_export_articles_for_domestic_query():
    query = "tiệm net dưới quê doanh thu 150 triệu đóng thuế kh"
    as_of_date = "2026-03-20"
    p3_ids = run_p3_full_system(query, as_of_date=as_of_date, top_k=5)

    assert "LUAT_48_2024_D5" in p3_ids
    assert "LUAT_48_2024_D9" not in p3_ids


def test_retrieve_and_gate_p1_flat_rag_contract():
    query = "tiệm net đóng thuế"
    as_of_date = "2026-03-20"
    res = retrieve_and_gate(query, as_of_date=as_of_date, top_k=3, pipeline="P1_FlatRAG")

    assert isinstance(res, RetrievalResult)
    assert res.articles == []
    assert isinstance(res.active_ids, list)
    assert len(res.active_ids) <= 3


def test_retrieve_and_gate_p2_returns_raw_articles():
    query = "tiệm net đóng thuế"
    as_of_date = "2026-03-20"
    res = retrieve_and_gate(query, as_of_date=as_of_date, top_k=5, pipeline="P2_TemporalGraphRAG")

    assert isinstance(res, RetrievalResult)
    assert len(res.articles) > 0
    assert len(res.active_ids) == len(res.articles)
    assert res.gated_article_count == len(res.articles)
