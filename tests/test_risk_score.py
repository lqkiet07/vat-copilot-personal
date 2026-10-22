import pytest
from src.verifier.models import CitationStatus, VerificationReport, VerificationResult
from src.verifier.risk_score import calculate_risk_score, verify_and_score


def test_no_citations():
    report = VerificationReport(
        as_of_date="2026-04-01",
        total_citations=0,
        valid_citations=0,
        citations=[],
        is_clean=False,
    )
    score, level, warnings = calculate_risk_score(report)
    assert score == 50
    assert level == "MEDIUM"
    assert warnings == ["Câu trả lời không viện dẫn bất kỳ căn cứ pháp lý nào."]


def test_all_valid_citations():
    c1 = CitationStatus(
        id="LUAT_48_2024_D9",
        status="VALID",
        exists=True,
        is_active=True,
        message="Điều khoản LUAT_48_2024_D9 có đầy đủ hiệu lực tại ngày 2026-04-01.",
    )
    report = VerificationReport(
        as_of_date="2026-04-01",
        total_citations=1,
        valid_citations=1,
        citations=[c1],
        is_clean=True,
    )
    score, level, warnings = calculate_risk_score(report)
    assert score == 0
    assert level == "LOW"
    assert warnings == []


def test_single_amended_citation():
    c1 = CitationStatus(
        id="ND_181_2025_D9",
        status="AMENDED",
        exists=True,
        is_active=True,
        message="Điều khoản ND_181_2025_D9 đã bị sửa đổi/bổ sung bởi ND_144_2026_D1.",
        amended_by=["ND_144_2026_D1"],
    )
    report = VerificationReport(
        as_of_date="2026-04-01",
        total_citations=1,
        valid_citations=0,
        citations=[c1],
        is_clean=False,
    )
    score, level, warnings = calculate_risk_score(report)
    assert score == 25
    assert level == "MEDIUM"
    assert len(warnings) == 1
    assert "ND_181_2025_D9" in warnings[0]
    assert "sửa đổi" in warnings[0].lower()


def test_not_yet_effective_citation():
    c1 = CitationStatus(
        id="LUAT_48_2024_D9",
        status="NOT_YET_EFFECTIVE",
        exists=True,
        is_active=False,
        message="Điều khoản LUAT_48_2024_D9 chưa có hiệu lực tại ngày 2025-05-01.",
    )
    report = VerificationReport(
        as_of_date="2025-05-01",
        total_citations=1,
        valid_citations=0,
        citations=[c1],
        is_clean=False,
    )
    score, level, warnings = calculate_risk_score(report)
    assert score == 40
    assert level == "MEDIUM"
    assert len(warnings) == 1
    assert "chưa có hiệu lực" in warnings[0].lower()


def test_expired_citation():
    c1 = CitationStatus(
        id="ND_181_2025_D9",
        status="EXPIRED",
        exists=True,
        is_active=False,
        message="Điều khoản ND_181_2025_D9 đã hết hiệu lực trước ngày 2026-04-01.",
    )
    report = VerificationReport(
        as_of_date="2026-04-01",
        total_citations=1,
        valid_citations=0,
        citations=[c1],
        is_clean=False,
    )
    score, level, warnings = calculate_risk_score(report)
    assert score == 60
    assert level == "HIGH"
    assert len(warnings) == 1
    assert "hết hiệu lực" in warnings[0].lower()


def test_not_found_citation():
    c1 = CitationStatus(
        id="LUAT_48_2024_D999",
        status="NOT_FOUND",
        exists=False,
        is_active=False,
        message="Không tìm thấy điều khoản LUAT_48_2024_D999 trong cơ sở dữ liệu tri thức.",
    )
    report = VerificationReport(
        as_of_date="2026-04-01",
        total_citations=1,
        valid_citations=0,
        citations=[c1],
        is_clean=False,
    )
    score, level, warnings = calculate_risk_score(report)
    assert score == 80
    assert level == "CRITICAL"
    assert len(warnings) == 1
    assert "không tìm thấy" in warnings[0].lower()


def test_combination_exceeding_100():
    c1 = CitationStatus(
        id="ND_181_2025_D9",
        status="EXPIRED",
        exists=True,
        is_active=False,
        message="Điều khoản ND_181_2025_D9 đã hết hiệu lực.",
    )
    c2 = CitationStatus(
        id="LUAT_48_2024_D999",
        status="NOT_FOUND",
        exists=False,
        is_active=False,
        message="Không tìm thấy điều khoản LUAT_48_2024_D999.",
    )
    report = VerificationReport(
        as_of_date="2026-04-01",
        total_citations=2,
        valid_citations=0,
        citations=[c1, c2],
        is_clean=False,
    )
    score, level, warnings = calculate_risk_score(report)
    assert score == 100
    assert level == "CRITICAL"
    assert len(warnings) == 2


def test_verify_and_score_integration_valid():
    text = "Áp dụng căn cứ [LUAT_48_2024_D9] để tính thuế giá trị gia tăng."
    res = verify_and_score(text=text, as_of_date="2026-04-01")

    assert isinstance(res, VerificationResult)
    assert res.as_of_date == "2026-04-01"
    assert res.risk_score == 0
    assert res.risk_level == "LOW"
    assert res.warnings == []
    assert res.report.is_clean is True
    assert res.report.total_citations == 1


def test_verify_and_score_integration_amended():
    text = "Căn cứ theo [ND_181_2025_D9] vào ngày 2026-04-01."
    res = verify_and_score(text=text, as_of_date="2026-04-01")

    assert isinstance(res, VerificationResult)
    assert res.as_of_date == "2026-04-01"
    assert res.risk_score == 25
    assert res.risk_level == "MEDIUM"
    assert len(res.warnings) == 1
    assert "ND_181_2025_D9" in res.warnings[0]
    assert res.report.is_clean is False


def test_verify_and_score_integration_no_citations():
    text = "Theo quy định thông thường không cần viện dẫn."
    res = verify_and_score(text=text, as_of_date="2026-04-01")

    assert isinstance(res, VerificationResult)
    assert res.as_of_date == "2026-04-01"
    assert res.risk_score == 50
    assert res.risk_level == "MEDIUM"
    assert res.warnings == ["Câu trả lời không viện dẫn bất kỳ căn cứ pháp lý nào."]
    assert res.report.total_citations == 0
