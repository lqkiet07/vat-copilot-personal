"""Tests for automated ablation benchmark runner and CLI execution."""
import csv
import json
import subprocess
import sys
from pathlib import Path
import pytest

from src.benchmark.models import AblationReport
from src.benchmark.runner import run_ablation_benchmark

EXPECTED_CSV_HEADER = [
    "question_id",
    "category",
    "is_temporal_trap",
    "as_of_date",
    "pipeline",
    "predicted_citations",
    "precision",
    "recall",
    "temporal_accuracy",
    "hallucination_rate",
]


def test_run_ablation_benchmark_smoke(tmp_path: Path):
    """Smoke test running 3 benchmark items and exporting CSV + JSON."""
    report = run_ablation_benchmark(limit=3, top_k=5, output_dir=tmp_path)

    assert isinstance(report, AblationReport)
    assert report.total_questions == 3
    assert report.pipelines == ["P1_FlatRAG", "P2_TemporalGraphRAG", "P3_FullSystem"]
    assert len(report.runs) == 9  # 3 items * 3 pipelines

    for p in report.pipelines:
        assert p in report.metrics_summary
        m = report.metrics_summary[p]
        assert 0.0 <= m.precision <= 1.0
        assert 0.0 <= m.recall <= 1.0
        assert 0.0 <= m.temporal_accuracy <= 1.0
        assert 0.0 <= m.hallucination_rate <= 1.0

    csv_path = tmp_path / "benchmark_results.csv"
    assert csv_path.exists()
    with open(csv_path, mode="r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        assert reader[0] == EXPECTED_CSV_HEADER
        assert len(reader) == 10  # 1 header + 9 rows

    json_path = tmp_path / "benchmark_summary.json"
    assert json_path.exists()
    with open(json_path, mode="r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["total_questions"] == 3
        assert "metrics_summary" in data
        assert "P1_FlatRAG" in data["metrics_summary"]


def test_run_ablation_benchmark_no_output_dir():
    """Verify in-memory execution when output_dir is None."""
    report = run_ablation_benchmark(limit=1, top_k=3, output_dir=None)

    assert isinstance(report, AblationReport)
    assert report.total_questions == 1
    assert len(report.runs) == 3


def test_run_benchmark_script_cli(tmp_path: Path):
    """Verify scripts/run_benchmark.py CLI execution and printed ASCII table."""
    cmd = [
        sys.executable,
        "scripts/run_benchmark.py",
        "--limit",
        "2",
        "--top-k",
        "3",
        "--output-dir",
        str(tmp_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)

    assert "P1_FlatRAG" in res.stdout
    assert "P2_TemporalGraphRAG" in res.stdout
    assert "P3_FullSystem" in res.stdout

    csv_path = tmp_path / "benchmark_results.csv"
    assert csv_path.exists()
    with open(csv_path, mode="r", encoding="utf-8") as f:
        rows = list(csv.reader(f))
        assert len(rows) == 7  # 1 header + 6 data rows
