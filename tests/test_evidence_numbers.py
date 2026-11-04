from src.verifier.evidence_numbers import extract_revenue_thresholds, extract_tax_rates


def test_extract_revenue_threshold_pulls_200_trieu():
    text = "hộ, cá nhân sản xuất, kinh doanh có mức doanh thu hằng năm từ 200 triệu đồng trở xuống"
    assert extract_revenue_thresholds(text) == ["200 triệu"]


def test_extract_revenue_thresholds_deduplicates_and_orders():
    text = "từ 200 triệu đồng trở xuống; trên 200 triệu đồng"
    assert extract_revenue_thresholds(text) == ["200 triệu"]


def test_extract_revenue_thresholds_ignores_300_trieu_refund_context_when_scoped():
    text = "doanh thu hằng năm từ 200 triệu đồng trở xuống; hoàn thuế khi chưa khấu trừ hết từ 300 triệu đồng"
    assert "300 triệu" not in extract_revenue_thresholds(text)


def test_extract_tax_rates_standalone_only():
    assert extract_tax_rates("Mức thuế suất 5% áp dụng đối với") == ["5%"]
    assert extract_tax_rates("chiếm 35% giá bán lẻ điện bình quân") == []
    assert extract_tax_rates("100% vốn điều lệ") == []


def test_extract_tax_rates_multiple_dedup():
    assert extract_tax_rates("thuế suất 0% hoặc 5% và 10%") == ["0%", "5%", "10%"]
