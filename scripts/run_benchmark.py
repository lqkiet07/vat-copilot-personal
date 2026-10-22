"""CLI script to run ablation benchmark across retrieval baselines."""
import argparse
from pathlib import Path
import sys

# Ensure repository root is on sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.benchmark.models import AblationReport
from src.benchmark.runner import run_ablation_benchmark


def format_ascii_table(report: AblationReport) -> str:
    """Format benchmark metrics summary into an ASCII comparison table."""
    header = (
        f"{'Pipeline':<22} | {'Precision':<10} | {'Recall':<10} "
        f"| {'Temporal Acc':<12} | {'Hallucination':<14}"
    )
    divider = "-" * len(header)
    lines = [
        f"\nBenchmark Evaluation Summary (Total Questions: {report.total_questions})",
        divider,
        header,
        divider,
    ]
    for pipeline in report.pipelines:
        m = report.metrics_summary.get(pipeline)
        if m:
            lines.append(
                f"{pipeline:<22} | {m.precision:<10.4f} | {m.recall:<10.4f} "
                f"| {m.temporal_accuracy:<12.4f} | {m.hallucination_rate:<14.4f}"
            )
    lines.append(divider)
    return "\n".join(lines)


def main() -> None:
    """Parse CLI arguments and execute the ablation benchmark."""
    parser = argparse.ArgumentParser(description="Run VAT RAG ablation benchmark.")
    parser.add_argument("--limit", type=int, default=None, help="Max questions to evaluate.")
    parser.add_argument("--top-k", type=int, default=5, help="Top-K citations per pipeline.")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/benchmark",
        help="Directory to save CSV and JSON results.",
    )
    parser.add_argument(
        "--ground-truth",
        type=str,
        default="data/benchmark/ground_truth.json",
        help="Path to ground truth JSON file.",
    )
    args = parser.parse_args()

    report = run_ablation_benchmark(
        ground_truth_path=args.ground_truth,
        limit=args.limit,
        top_k=args.top_k,
        output_dir=args.output_dir,
    )
    print(format_ascii_table(report))


if __name__ == "__main__":
    main()
