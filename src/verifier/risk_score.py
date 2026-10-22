from src.verifier.citation_verifier import verify_citations
from src.verifier.models import CitationStatus, RiskLevel, VerificationReport, VerificationResult

_WEIGHTS: dict[str, int] = {
    "AMENDED": 25,
    "NOT_YET_EFFECTIVE": 40,
    "EXPIRED": 60,
    "NOT_FOUND": 80,
}

_NO_CITATION_WARNING = "Câu trả lời không viện dẫn bất kỳ căn cứ pháp lý nào."


def _get_risk_level(score: int) -> RiskLevel:
    """Map numeric risk score (0-100) to qualitative risk category."""
    if score <= 20:
        return "LOW"
    if score <= 50:
        return "MEDIUM"
    if score <= 75:
        return "HIGH"
    return "CRITICAL"


def _format_warning(citation: CitationStatus) -> str:
    """Format warning message for a non-valid citation."""
    if citation.message:
        return citation.message
    if citation.status == "AMENDED":
        amenders = ", ".join(citation.amended_by) if citation.amended_by else "văn bản sửa đổi"
        return f"Điều khoản {citation.id} đã bị sửa đổi/bổ sung bởi {amenders}."
    if citation.status == "NOT_YET_EFFECTIVE":
        return f"Điều khoản {citation.id} chưa có hiệu lực."
    if citation.status == "EXPIRED":
        return f"Điều khoản {citation.id} đã hết hiệu lực."
    if citation.status == "NOT_FOUND":
        return f"Không tìm thấy điều khoản {citation.id} trong cơ sở dữ liệu tri thức."
    return f"Điều khoản {citation.id} không hợp lệ ({citation.status})."


def calculate_risk_score(report: VerificationReport) -> tuple[int, RiskLevel, list[str]]:
    """Compute 0-100 risk score and level from citation verification report."""
    if report.total_citations == 0:
        return 50, _get_risk_level(50), [_NO_CITATION_WARNING]

    non_valid = [c for c in report.citations if c.status != "VALID"]
    if not non_valid:
        return 0, "LOW", []

    raw_score = sum(_WEIGHTS.get(c.status, 0) for c in non_valid)
    score = min(100, raw_score)
    level = _get_risk_level(score)
    warnings = [_format_warning(c) for c in non_valid]
    return score, level, warnings


def verify_and_score(text: str, as_of_date: str) -> VerificationResult:
    """End-to-end verification and risk scoring for response text."""
    report = verify_citations(text, as_of_date)
    score, level, warnings = calculate_risk_score(report)
    return VerificationResult(
        as_of_date=as_of_date,
        risk_score=score,
        risk_level=level,
        warnings=warnings,
        report=report,
    )

