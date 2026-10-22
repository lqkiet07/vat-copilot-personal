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
