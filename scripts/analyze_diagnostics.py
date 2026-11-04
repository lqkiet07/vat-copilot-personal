import collections
import json
from pathlib import Path
import sys

FALSE_NEGATIVE_REASONS = (
    "GATING_OVERREJECT",
    "LLM_REFUSED_WITH_EVIDENCE",
    "IRAC_PARSE_FAILED",
)


def summarize(path: str | Path) -> dict[str, int]:
    p = Path(path)
    counter: collections.Counter[str] = collections.Counter()
    if not p.is_file():
        return dict(counter)
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(record, dict) and "empty_reason" in record:
                reason = record["empty_reason"]
                if reason:
                    counter[str(reason)] += 1
    return dict(counter)


def calculate_false_negatives(counts: dict[str, int]) -> int:
    return sum(counts.get(reason, 0) for reason in FALSE_NEGATIVE_REASONS)


def format_report(counts: dict[str, int]) -> str:
    total_queries = sum(counts.values())
    fn_total = calculate_false_negatives(counts)

    lines = [
        "| Reason | Count | Percentage |",
        "| :--- | :---: | :---: |",
    ]
    for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        pct = (count / total_queries * 100) if total_queries > 0 else 0.0
        lines.append(f"| {reason} | {count} | {pct:.1f}% |")

    lines.append("")
    lines.append(f"Total Queries: {total_queries}")
    lines.append(f"False Negative Total: {fn_total}")
    return "\n".join(lines)


def main() -> None:
    path_arg = sys.argv[1] if len(sys.argv) > 1 else ".scratch/diag.jsonl"
    path = Path(path_arg)
    if not path.is_file():
        print(f"Warning: Trace file not found at {path}")
        sys.exit(0)
    counts = summarize(path)
    report = format_report(counts)
    print(report)


if __name__ == "__main__":
    main()
