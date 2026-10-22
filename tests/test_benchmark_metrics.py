import pytest
from src.benchmark.models import BenchmarkMetrics
from src.benchmark.metrics import (
    compute_citation_precision,
    compute_citation_recall,
    compute_temporal_accuracy,
    compute_hallucination_rate,
    calculate_benchmark_metrics,
    evaluate_prediction,
)


def test_citation_precision_and_recall_perfect_match():
    pred = ["LUAT_48_2024_D9", "LUAT_48_2024_D10"]
    gold = ["LUAT_48_2024_D9", "LUAT_48_2024_D10"]
    assert compute_citation_precision(pred, gold) == 1.0
    assert compute_citation_recall(pred, gold) == 1.0


def test_citation_precision_and_recall_partial_match():
    pred = ["LUAT_48_2024_D9", "LUAT_48_2024_D10"]
    gold = ["LUAT_48_2024_D9", "LUAT_48_2024_D11"]
    assert compute_citation_precision(pred, gold) == 0.5
    assert compute_citation_recall(pred, gold) == 0.5


def test_citation_precision_and_recall_disjoint():
    pred = ["LUAT_48_2024_D9"]
    gold = ["LUAT_48_2024_D10"]
    assert compute_citation_precision(pred, gold) == 0.0
    assert compute_citation_recall(pred, gold) == 0.0


def test_citation_precision_and_recall_deduplication():
    pred = ["LUAT_48_2024_D9", "LUAT_48_2024_D9"]
    gold = ["LUAT_48_2024_D9"]
    assert compute_citation_precision(pred, gold) == 1.0
    assert compute_citation_recall(pred, gold) == 1.0


def test_citation_precision_and_recall_empty_both():
    assert compute_citation_precision([], []) == 1.0
    assert compute_citation_recall([], []) == 1.0


def test_citation_precision_and_recall_empty_predicted():
    assert compute_citation_precision([], ["LUAT_48_2024_D9"]) == 0.0
    assert compute_citation_recall([], ["LUAT_48_2024_D9"]) == 0.0


def test_citation_precision_and_recall_empty_gold():
    assert compute_citation_precision(["LUAT_48_2024_D9"], []) == 0.0
    assert compute_citation_recall(["LUAT_48_2024_D9"], []) == 0.0


def test_temporal_accuracy_and_hallucination_empty_predicted():
    assert compute_temporal_accuracy([], as_of_date="2026-01-01") == 1.0
    assert compute_hallucination_rate([], as_of_date="2026-01-01") == 0.0


def test_temporal_accuracy_valid_citation():
    pred = ["LUAT_48_2024_D9"]
    assert compute_temporal_accuracy(pred, as_of_date="2026-01-01") == 1.0
    assert compute_hallucination_rate(pred, as_of_date="2026-01-01") == 0.0


def test_temporal_accuracy_not_yet_effective():
    pred = ["LUAT_48_2024_D9"]
    assert compute_temporal_accuracy(pred, as_of_date="2025-01-01") == 0.0
    assert compute_hallucination_rate(pred, as_of_date="2025-01-01") == 0.0


def test_temporal_accuracy_amended_citation():
    pred = ["ND_181_2025_D9"]
    assert compute_temporal_accuracy(pred, as_of_date="2026-04-01") == 1.0
    assert compute_hallucination_rate(pred, as_of_date="2026-04-01") == 0.0


def test_hallucination_rate_fake_citation():
    pred = ["FAKE_DOC_D99"]
    assert compute_temporal_accuracy(pred, as_of_date="2026-01-01") == 0.0
    assert compute_hallucination_rate(pred, as_of_date="2026-01-01") == 1.0


def test_temporal_accuracy_and_hallucination_mixed():
    pred = ["LUAT_48_2024_D9", "FAKE_DOC_D99"]
    assert compute_temporal_accuracy(pred, as_of_date="2026-01-01") == 0.5
    assert compute_hallucination_rate(pred, as_of_date="2026-01-01") == 0.5


def test_temporal_metrics_deduplication():
    pred = ["LUAT_48_2024_D9", "LUAT_48_2024_D9"]
    assert compute_temporal_accuracy(pred, as_of_date="2026-01-01") == 1.0
    assert compute_hallucination_rate(pred, as_of_date="2026-01-01") == 0.0


def test_calculate_benchmark_metrics_full():
    metrics = calculate_benchmark_metrics(
        predicted=["LUAT_48_2024_D9"],
        gold=["LUAT_48_2024_D9"],
        as_of_date="2026-01-01",
    )
    assert isinstance(metrics, BenchmarkMetrics)
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.temporal_accuracy == 1.0
    assert metrics.hallucination_rate == 0.0


def test_calculate_benchmark_metrics_empty_inputs():
    metrics = calculate_benchmark_metrics(
        predicted=[],
        gold=[],
        as_of_date="2026-01-01",
    )
    assert isinstance(metrics, BenchmarkMetrics)
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.temporal_accuracy == 1.0
    assert metrics.hallucination_rate == 0.0


def test_evaluate_prediction_alias():
    assert evaluate_prediction is calculate_benchmark_metrics
