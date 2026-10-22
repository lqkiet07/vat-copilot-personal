from src.benchmark.models import BenchmarkMetrics
from src.verifier.citation_verifier import verify_citations


def _deduplicate(citations: list[str]) -> list[str]:
    """Deduplicate citations and clean enclosing brackets."""
    seen = {c.strip("[] ") for c in citations if c.strip("[] ")}
    return sorted(seen)


def _get_citation_statuses(citations: list[str], as_of_date: str) -> list[str]:
    """Query verifier for citations and return status per citation."""
    if not citations:
        return []
    tagged_text = " ".join(f"[{c}]" for c in citations)
    report = verify_citations(tagged_text, as_of_date)
    status_map = {item.id: item.status for item in report.citations}
    return [status_map.get(cid, "NOT_FOUND") for cid in citations]


def compute_citation_precision(predicted: list[str], gold: list[str]) -> float:
    """Compute citation precision with set deduplication."""
    pred_set = set(_deduplicate(predicted))
    gold_set = set(_deduplicate(gold))

    if not pred_set and not gold_set:
        return 1.0
    if not pred_set or not gold_set:
        return 0.0
    return len(pred_set & gold_set) / len(pred_set)


def compute_citation_recall(predicted: list[str], gold: list[str]) -> float:
    """Compute citation recall with set deduplication."""
    pred_set = set(_deduplicate(predicted))
    gold_set = set(_deduplicate(gold))

    if not pred_set and not gold_set:
        return 1.0
    if not pred_set or not gold_set:
        return 0.0
    return len(pred_set & gold_set) / len(gold_set)


def compute_temporal_accuracy(predicted: list[str], as_of_date: str) -> float:
    """Compute ratio of valid or amended citations over total unique predicted."""
    unique_pred = _deduplicate(predicted)
    if not unique_pred:
        return 1.0
    statuses = _get_citation_statuses(unique_pred, as_of_date)
    valid_count = sum(1 for s in statuses if s in ("VALID", "AMENDED"))
    return valid_count / len(unique_pred)


def compute_hallucination_rate(predicted: list[str], as_of_date: str) -> float:
    """Compute ratio of not-found or expired citations over total unique predicted."""
    unique_pred = _deduplicate(predicted)
    if not unique_pred:
        return 0.0
    statuses = _get_citation_statuses(unique_pred, as_of_date)
    hallucinated_count = sum(1 for s in statuses if s in ("NOT_FOUND", "EXPIRED"))
    return hallucinated_count / len(unique_pred)


def calculate_benchmark_metrics(
    predicted: list[str], gold: list[str], as_of_date: str
) -> BenchmarkMetrics:
    """Calculate all 4 scientific benchmark metrics for citation predictions."""
    unique_pred = _deduplicate(predicted)
    precision = compute_citation_precision(predicted, gold)
    recall = compute_citation_recall(predicted, gold)

    if not unique_pred:
        return BenchmarkMetrics(
            precision=precision,
            recall=recall,
            temporal_accuracy=1.0,
            hallucination_rate=0.0,
        )

    statuses = _get_citation_statuses(unique_pred, as_of_date)
    valid_count = sum(1 for s in statuses if s in ("VALID", "AMENDED"))
    hallucinated_count = sum(1 for s in statuses if s in ("NOT_FOUND", "EXPIRED"))
    total = len(unique_pred)

    return BenchmarkMetrics(
        precision=precision,
        recall=recall,
        temporal_accuracy=valid_count / total,
        hallucination_rate=hallucinated_count / total,
    )


evaluate_prediction = calculate_benchmark_metrics
