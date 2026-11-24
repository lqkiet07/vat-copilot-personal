"""Offline unit tests for the Phase 5b 25-case upgrade evaluation fixture and scorer."""
from collections import Counter

from scripts.eval_upgrade_suite import (
    load_upgrade_cases,
    run_retrieval_ablation,
    score_e2e_case,
    score_retrieval_case,
    select_balanced_cases,
)
from src.engine.models import ArticleNode


def test_upgrade_eval_fixture_has_25_balanced_cases():
    cases = load_upgrade_cases()
    assert len(cases) == 25

    counts = Counter(case["category"] for case in cases)
    assert counts == {
        "colloquial_everyday": 5,
        "temporal_amendment": 5,
        "conditional_needs_facts": 5,
        "multi_turn_followup": 5,
        "out_of_scope_and_boundary": 5,
    }
    ids = [case["id"] for case in cases]
    assert len(set(ids)) == 25
    for case in cases:
        assert case["question"].strip()
        assert len(case["as_of_date"]) == 10
        assert case["expected_scope"] in {"in_scope", "out_of_scope"}
        assert isinstance(case["history"], list)


def test_select_balanced_cases_stratifies_categories():
    cases = load_upgrade_cases()
    sample = select_balanced_cases(cases, limit=10)
    assert len(sample) == 10
    counts = Counter(case["category"] for case in sample)
    assert all(v == 2 for v in counts.values())


def test_score_retrieval_and_e2e_helpers():
    case = {
        "id": "UPG_01",
        "category": "conditional_needs_facts",
        "expected_scope": "in_scope",
        "expected_statuses": ["INSUFFICIENT_BASIS", "ANSWERED"],
        "expected_doc_prefixes": ["LUAT_48_2024_D5", "ND_68_2026_D3"],
        "forbidden_substrings": ["casino", "trò chơi điện tử có thưởng"],
    }
    clean = score_retrieval_case(
        case,
        ["ND_68_2026_D3_K1", "LUAT_48_2024_D5_K25"],
        ["Miễn thuế hộ kinh doanh dưới ngưỡng doanh thu"],
    )
    assert clean == {"hit_top5": True, "hit_any": True, "has_noise": False}

    noisy = score_retrieval_case(
        case,
        ["ND_181_2025_D10"],
        ["Kinh doanh casino, trò chơi điện tử có thưởng"],
    )
    assert noisy == {"hit_top5": False, "hit_any": False, "has_noise": True}

    e2e_ok = score_e2e_case(
        case,
        {
            "is_out_of_domain": False,
            "answer_status": "INSUFFICIENT_BASIS",
            "citations": ["ND_68_2026_D3"],
            "missing_facts": ["Doanh thu dự kiến mỗi năm"],
            "irac_sections": {"conclusion": "Cần xác định doanh thu năm."},
        },
    )
    assert e2e_ok == {
        "scope_ok": True,
        "status_ok": True,
        "citation_ok": True,
        "has_noise": False,
        "has_missing_facts": True,
    }


def test_run_retrieval_ablation_with_mocked_retrievers():
    cases = [
        {
            "id": "UPG_01",
            "category": "colloquial_everyday",
            "question": "mở tiệm net ở quê",
            "as_of_date": "2026-11-24",
            "history": [],
            "expected_scope": "in_scope",
            "expected_doc_prefixes": ["ND_68_2026_D3"],
            "forbidden_substrings": ["casino"],
        }
    ]

    def fake_base(q, day, top_k=5):
        return [ArticleNode(id="ND_181_2025_D10", doc_id="ND_181_2025", code="Điều 10", title="Casino", content="casino có thưởng", valid_from="2025-07-01", valid_to="9999-12-31")]

    def fake_upg(q, day, history, top_k=5):
        return [ArticleNode(id="ND_68_2026_D3", doc_id="ND_68_2026", code="Điều 3", title="Thuế GTGT", content="Hộ kinh doanh", valid_from="2026-03-05", valid_to="9999-12-31")]

    report = run_retrieval_ablation(cases, baseline_fn=fake_base, upgraded_fn=fake_upg)
    assert report["summary"]["baseline"]["top5_hit_rate"] == 0.0
    assert report["summary"]["baseline"]["forbidden_noise_rate"] == 1.0
    assert report["summary"]["upgraded"]["top5_hit_rate"] == 1.0
    assert report["summary"]["upgraded"]["forbidden_noise_rate"] == 0.0
