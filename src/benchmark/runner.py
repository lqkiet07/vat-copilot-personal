"""Automated ablation benchmark runner for evaluating retrieval pipelines."""
import csv
from pathlib import Path
from typing import Callable

from src.benchmark.baselines import (
    run_p1_baseline_rag,
    run_p2_temporal_graphrag,
    run_p3_full_system,
)
from src.benchmark.metrics import calculate_benchmark_metrics
from src.benchmark.models import (
    AblationReport,
    BenchmarkItem,
    BenchmarkMetrics,
    PipelineRunResult,
)

PipelineFn = Callable[[str, str, int], list[str]]

PIPELINES: list[tuple[str, PipelineFn]] = [
    ("P1_FlatRAG", run_p1_baseline_rag),
    ("P2_TemporalGraphRAG", run_p2_temporal_graphrag),
    ("P3_FullSystem", run_p3_full_system),
]


def load_benchmark_dataset(
    path: str | Path, limit: int | None = None
) -> list[BenchmarkItem]:
    """Load benchmark ground truth dataset from a JSON file."""
    import json

    with open(Path(path), mode="r", encoding="utf-8") as f:
        data = json.load(f)
    items = [BenchmarkItem(**entry) for entry in data]
    return items[:limit] if limit is not None else items


def _compute_mean_metrics(runs: list[PipelineRunResult]) -> BenchmarkMetrics:
    """Calculate mean metric values across a collection of pipeline runs."""
    if not runs:
        return BenchmarkMetrics()
    n = len(runs)
    p = sum(r.metrics.precision for r in runs if r.metrics) / n
    r = sum(r.metrics.recall for r in runs if r.metrics) / n
    acc = sum(r.metrics.temporal_accuracy for r in runs if r.metrics) / n
    h = sum(r.metrics.hallucination_rate for r in runs if r.metrics) / n
    return BenchmarkMetrics(
        precision=round(p, 4),
        recall=round(r, 4),
        temporal_accuracy=round(acc, 4),
        hallucination_rate=round(h, 4),
    )


def _export_benchmark_artifacts(
    report: AblationReport,
    items: dict[str, BenchmarkItem],
    output_dir: Path,
) -> None:
    """Export benchmark runs to CSV and summary statistics to JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_file = output_dir / "benchmark_results.csv"
    with open(csv_file, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
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
        ])
        for run in report.runs:
            item = items[run.question_id]
            m = run.metrics or BenchmarkMetrics()
            writer.writerow([
                run.question_id,
                item.category,
                item.is_temporal_trap,
                run.as_of_date,
                run.pipeline,
                ";".join(run.predicted_citations),
                m.precision,
                m.recall,
                m.temporal_accuracy,
                m.hallucination_rate,
            ])

    json_file = output_dir / "benchmark_summary.json"
    with open(json_file, mode="w", encoding="utf-8") as f:
        f.write(report.model_dump_json(indent=2))


def run_ablation_benchmark(
    ground_truth_path: str | Path = "data/benchmark/ground_truth.json",
    limit: int | None = None,
    top_k: int = 5,
    output_dir: str | Path | None = None,
) -> AblationReport:
    """Execute ablation benchmark across retrieval pipelines and export results."""
    items = load_benchmark_dataset(ground_truth_path, limit=limit)
    pipeline_names = [name for name, _ in PIPELINES]
    all_runs: list[PipelineRunResult] = []
    runs_by_pipeline: dict[str, list[PipelineRunResult]] = {
        name: [] for name in pipeline_names
    }

    for item in items:
        for name, fn in PIPELINES:
            cites = fn(item.question, item.as_of_date, top_k)
            metrics = calculate_benchmark_metrics(
                predicted=cites,
                gold=item.gold_citations,
                as_of_date=item.as_of_date,
            )
            run = PipelineRunResult(
                question_id=item.question_id,
                pipeline=name,
                predicted_citations=cites,
                metrics=metrics,
                as_of_date=item.as_of_date,
            )
            all_runs.append(run)
            runs_by_pipeline[name].append(run)

    summary = {
        name: _compute_mean_metrics(runs_by_pipeline[name])
        for name in pipeline_names
    }
    report = AblationReport(
        total_questions=len(items),
        pipelines=pipeline_names,
        metrics_summary=summary,
        runs=all_runs,
    )
    if output_dir is not None:
        items_map = {item.question_id: item for item in items}
        _export_benchmark_artifacts(report, items_map, Path(output_dir))
    return report


run_full_ablation_study = run_ablation_benchmark
