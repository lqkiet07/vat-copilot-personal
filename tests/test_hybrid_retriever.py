import pytest
from src.engine.hybrid_retriever import (
    query_hybrid_temporal,
    reciprocal_rank_fusion,
)
from src.engine.models import QueryResult
from src.engine.temporal_retriever import query_temporal_rag


def test_rrf_scoring_logic():
    ft_ranks = ["doc1", "doc2", "doc3", "doc4"]
    vec_ranks = ["doc2", "doc5", "doc1", "doc6"]

    fused = reciprocal_rank_fusion(ft_ranks, vec_ranks, k=60)
    assert isinstance(fused, list)
    fused_dict = dict(fused)

    assert fused_dict["doc2"] > fused_dict["doc3"]
    assert fused_dict["doc2"] > fused_dict["doc5"]
    assert fused_dict["doc1"] > fused_dict["doc3"]
    assert fused_dict["doc1"] > fused_dict["doc5"]

    top_ids = [doc_id for doc_id, _ in fused[:2]]
    assert set(top_ids) == {"doc1", "doc2"}

    expected_doc2 = (1.0 / (60 + 2)) + (1.0 / (60 + 1))
    assert abs(fused_dict["doc2"] - expected_doc2) < 1e-6
    expected_doc3 = 1.0 / (60 + 3)
    assert abs(fused_dict["doc3"] - expected_doc3) < 1e-6


@pytest.mark.requires_neo4j
def test_hybrid_temporal_filtering_and_traversal():
    past_results = query_hybrid_temporal(
        query="thuế giá trị gia tăng", as_of_date="2025-05-01"
    )
    assert not any(r.doc_id == "LUAT_48_2024" for r in past_results)

    results_2026 = query_hybrid_temporal(query="hóa đơn", as_of_date="2026-04-01")
    assert len(results_2026) > 0
    assert all(r.status == "ACTIVE" for r in results_2026)
    assert all(r.valid_from <= "2026-04-01" <= r.valid_to for r in results_2026)
    assert all(hasattr(r, "related_ids") for r in results_2026)

    amended = [r for r in results_2026 if r.doc_id == "ND_181_2025" and r.related_ids]
    assert amended, "Truy van ND 181 phai keo kem Dieu sua doi ND 359/144"

    qr = query_temporal_rag(query="hóa đơn", as_of_date="2026-04-01")
    assert isinstance(qr, QueryResult)
    assert qr.query == "hóa đơn"
    assert qr.as_of_date == "2026-04-01"
    assert len(qr.articles) > 0
    assert any(a.doc_id == "ND_181_2025" for a in qr.articles)

    qr_past = query_temporal_rag(
        query="thuế giá trị gia tăng", as_of_date="2025-05-01"
    )
    assert isinstance(qr_past, QueryResult)
    assert not any(a.doc_id == "LUAT_48_2024" for a in qr_past.articles)


@pytest.mark.requires_neo4j
def test_enrich_articles_contains_clauses_content():
    from src.graph.connection import get_neo4j_driver
    from src.engine.hybrid_retriever import _enrich_articles

    driver = get_neo4j_driver()
    with driver.session() as session:
        nodes = _enrich_articles(session, ["LUAT_48_2024_D5"], "2026-03-20")
        assert len(nodes) == 1
        d5 = nodes[0]
        assert "200 triệu" in d5.content or "Khoan 25" in d5.content


def test_detect_legal_anchors_and_weighted_rrf():
    from src.engine.hybrid_retriever import detect_legal_anchors

    assert detect_legal_anchors("Theo Khoản 7 Điều 5 Luật 48/2024 thuế suất 0%") is True
    assert detect_legal_anchors("tiệm net dưới quê có được miễn thuế không") is False

    weighted = reciprocal_rank_fusion(["doc1", "doc2"], ["doc2", "doc1"], k=60, ft_weight=1.0, vec_weight=1.5)
    assert weighted[0][0] == "doc2"


def test_expand_1hop_relations_uses_fake_session_safely():
    from src.engine.hybrid_retriever import _expand_1hop_relations

    class _FakeResult:
        def data(self):
            return [{"id": "ND_144_2026_D2"}, {"id": "LUAT_48_2024_D4"}]

    class _FakeSession:
        def run(self, cypher, **kwargs):
            self.last_kwargs = kwargs
            return _FakeResult()

    sess = _FakeSession()
    extra = _expand_1hop_relations(sess, ["LUAT_48_2024_D5"], "2026-03-20", max_extra=5)
    assert extra == ["ND_144_2026_D2", "LUAT_48_2024_D4"]
    assert sess.last_kwargs["as_of_date"] == "2026-03-20"


def test_clause_vector_search_and_merge_vector_ranks():
    from src.engine.hybrid_retriever import _merge_vector_ranks, _search_clause_vector

    class _FakeResult:
        def data(self):
            return [
                {"id": "LUAT_48_2024_D5", "score": 0.78},
                {"id": "ND_181_2025_D10", "score": 0.12},
            ]

    class _FakeSession:
        def run(self, cypher, **kwargs):
            return _FakeResult()

    clause_hits = _search_clause_vector(_FakeSession(), [0.1, 0.2], "2026-03-20", min_score=0.35)
    assert clause_hits == ["LUAT_48_2024_D5"]

    class _BrokenSession:
        def run(self, cypher, **kwargs):
            raise RuntimeError("No clause_embedding index yet")

    assert _search_clause_vector(_BrokenSession(), [0.1, 0.2], "2026-03-20") == []

    assert _merge_vector_ranks(["D4", "D9"], []) == ["D4", "D9"]
    merged = _merge_vector_ranks(["D9", "D4", "D5"], ["D5", "D10"])
    assert merged[0] == "D5"



