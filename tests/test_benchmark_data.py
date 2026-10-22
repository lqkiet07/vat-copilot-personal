import json
import re
from pathlib import Path
import pytest
from src.benchmark.models import (
    BenchmarkItem,
    BenchmarkMetrics,
    PipelineRunResult,
    AblationReport,
)

BENCHMARK_PATH = Path("data/benchmark/ground_truth.json")
CANONICAL_CITATION_PATTERN = re.compile(
    r"^(LUAT_48_2024|ND_181_2025|TT_69_2025|ND_359_2025|ND_144_2026)_D\d+(_K\d+)?(_[a-z0-9]+)?$"
)
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def test_models_instantiation():
    item = BenchmarkItem(
        question_id="VAT_001",
        question="Thuế suất dịch vụ phần mềm xuất khẩu?",
        as_of_date="2025-08-01",
        category="EXPORT_TAX",
        gold_citations=["LUAT_48_2024_D9"],
        gold_atomic_facts=["Thuế suất 0%"],
    )
    assert item.question_id == "VAT_001"
    assert item.is_temporal_trap is False

    metrics = BenchmarkMetrics(
        precision=1.0, recall=0.8, temporal_accuracy=1.0, hallucination_rate=0.0
    )
    assert metrics.precision == 1.0

    run_result = PipelineRunResult(
        question_id="VAT_001",
        pipeline="P2_TEMPORAL_GRAPHRAG",
        predicted_citations=["LUAT_48_2024_D9"],
        metrics=metrics,
    )
    assert run_result.pipeline == "P2_TEMPORAL_GRAPHRAG"

    report = AblationReport(
        total_questions=1,
        pipelines=["P2_TEMPORAL_GRAPHRAG"],
        metrics_summary={"P2_TEMPORAL_GRAPHRAG": metrics},
        runs=[run_result],
    )
    assert report.total_questions == 1


def test_ground_truth_file_exists_and_valid():
    assert BENCHMARK_PATH.exists(), f"Benchmark file missing: {BENCHMARK_PATH}"
    with open(BENCHMARK_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, list)
    assert len(data) == 100


def test_ground_truth_distribution():
    with open(BENCHMARK_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    traps = [d for d in data if d.get("is_temporal_trap") is True]
    normals = [d for d in data if not d.get("is_temporal_trap")]
    assert len(traps) == 20
    assert len(normals) == 80
    ids = [d["question_id"] for d in data]
    assert len(set(ids)) == 100


def test_ground_truth_item_schema():
    with open(BENCHMARK_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    for raw in data:
        item = BenchmarkItem(**raw)
        assert item.question_id.startswith("VAT_")
        assert len(item.question.strip()) > 10
        assert DATE_PATTERN.match(item.as_of_date)
        assert len(item.gold_atomic_facts) >= 1
        assert len(item.category.strip()) > 0


def test_ground_truth_canonical_citations():
    with open(BENCHMARK_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    for raw in data:
        item = BenchmarkItem(**raw)
        if not item.is_temporal_trap:
            assert len(item.gold_citations) >= 1, f"Empty gold_citations in normal question {item.question_id}"
        for cite in item.gold_citations:
            assert CANONICAL_CITATION_PATTERN.match(cite), f"Invalid canonical citation: {cite} in {item.question_id}"
