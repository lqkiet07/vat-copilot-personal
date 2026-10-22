from unittest.mock import patch
import pytest

from src.benchmark.baselines import (
    run_p1_baseline_rag,
    run_p2_temporal_graphrag,
    run_p3_full_system,
)


def test_temporal_trap_pre_effective_date():
    """Luật 48 is effective from 2025-07-01; at 2024-05-01 P1 leaks it, P2 excludes it."""
    trap_question = (
        "Doanh nghiệp xuất khẩu phần mềm vào ngày 10/01/2025, có thể áp dụng "
        "Điều 9 Luật 48/2024 để kê khai thuế suất 0% hay không?"
    )
    as_of_date = "2024-05-01"

    p1_results = run_p1_baseline_rag(trap_question, as_of_date=as_of_date, top_k=5)
    assert len(p1_results) > 0
    assert any(cid.startswith("LUAT_48_2024") for cid in p1_results), (
        "P1 must retrieve LUAT_48_2024 because it has no temporal filtering"
    )

    p2_results = run_p2_temporal_graphrag(trap_question, as_of_date=as_of_date, top_k=5)
    assert not any(cid.startswith("LUAT_48_2024") for cid in p2_results), (
        "P2 must NOT retrieve LUAT_48_2024 because of temporal filtering"
    )

    p3_results = run_p3_full_system(trap_question, as_of_date=as_of_date, top_k=5)
    assert not any(cid.startswith("LUAT_48_2024") for cid in p3_results), (
        "P3 must NOT return LUAT_48_2024 when queried before effective date"
    )


def test_standard_query_active_date():
    """At 2026-01-01 all three pipelines return non-empty active citations."""
    query = "thuế suất thuế giá trị gia tăng"
    as_of_date = "2026-01-01"

    p1_results = run_p1_baseline_rag(query, as_of_date=as_of_date, top_k=5)
    p2_results = run_p2_temporal_graphrag(query, as_of_date=as_of_date, top_k=5)
    p3_results = run_p3_full_system(query, as_of_date=as_of_date, top_k=5)

    assert isinstance(p1_results, list)
    assert len(p1_results) > 0
    assert all(isinstance(cid, str) for cid in p1_results)

    assert isinstance(p2_results, list)
    assert len(p2_results) > 0
    assert all(isinstance(cid, str) for cid in p2_results)

    assert isinstance(p3_results, list)
    assert len(p3_results) > 0
    assert all(isinstance(cid, str) for cid in p3_results)


def test_p3_filters_invalid_and_unverified_citations():
    """P3 must retain only citations with VALID or AMENDED status."""
    as_of_date = "2026-01-01"
    mock_candidates = ["LUAT_48_2024_D9", "FAKE_DOC_D99", "NOT_EXISTING_D1"]

    with patch(
        "src.benchmark.baselines.run_p2_temporal_graphrag",
        return_value=mock_candidates,
    ):
        p3_results = run_p3_full_system(
            "dummy question", as_of_date=as_of_date, top_k=5
        )

    assert "LUAT_48_2024_D9" in p3_results
    assert "FAKE_DOC_D99" not in p3_results
    assert "NOT_EXISTING_D1" not in p3_results


def test_p3_filters_not_yet_effective_citations():
    """P3 must filter out citations whose valid_from is after as_of_date."""
    pre_date = "2024-05-01"
    mock_candidates = ["LUAT_48_2024_D9"]

    with patch(
        "src.benchmark.baselines.run_p2_temporal_graphrag",
        return_value=mock_candidates,
    ):
        p3_results = run_p3_full_system(
            "dummy question", as_of_date=pre_date, top_k=5
        )

    assert p3_results == []


def test_top_k_limits():
    """Pipeline outputs must not exceed the specified top_k limit."""
    query = "thuế giá trị gia tăng"
    date = "2026-01-01"

    assert run_p1_baseline_rag(query, date, top_k=0) == []
    assert run_p2_temporal_graphrag(query, date, top_k=0) == []
    assert run_p3_full_system(query, date, top_k=0) == []

    p1_top2 = run_p1_baseline_rag(query, date, top_k=2)
    p2_top2 = run_p2_temporal_graphrag(query, date, top_k=2)
    p3_top2 = run_p3_full_system(query, date, top_k=2)

    assert len(p1_top2) <= 2
    assert len(p2_top2) <= 2
    assert len(p3_top2) <= 2


def test_p1_ignores_as_of_date():
    """P1 should return identical results irrespective of the as_of_date argument."""
    query = "thuế giá trị gia tăng"
    res_2020 = run_p1_baseline_rag(query, as_of_date="2020-01-01", top_k=5)
    res_2026 = run_p1_baseline_rag(query, as_of_date="2026-01-01", top_k=5)
    assert res_2020 == res_2026


def test_benchmark_package_exports():
    """Verify clean top-level re-exports in src.benchmark."""
    import src.benchmark as bm

    assert hasattr(bm, "run_p1_baseline_rag")
    assert hasattr(bm, "run_p2_temporal_graphrag")
    assert hasattr(bm, "run_p3_full_system")
