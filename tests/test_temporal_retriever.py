from src.engine.temporal_retriever import query_temporal_snapshot


def test_temporal_snapshot_filtering():
    past = query_temporal_snapshot(keyword="thuế giá trị gia tăng", as_of_date="2025-05-01")
    assert not any(r.doc_id == "LUAT_48_2024" for r in past)

    future = query_temporal_snapshot(keyword="thuế giá trị gia tăng", as_of_date="2025-08-01")
    assert any(r.doc_id == "LUAT_48_2024" for r in future)
    assert all(r.valid_from <= "2025-08-01" <= r.valid_to for r in future)


def test_retrieval_includes_amended_articles():
    from src.engine.temporal_retriever import query_temporal_snapshot

    results = query_temporal_snapshot(keyword="hóa đơn", as_of_date="2026-04-01")
    assert len(results) > 0
    assert all(hasattr(r, "related_ids") for r in results)
    amended = [r for r in results if r.doc_id == "ND_181_2025" and r.related_ids]
    assert amended, "Truy van ND 181 phai keo kem Dieu sua doi ND 359/144"
