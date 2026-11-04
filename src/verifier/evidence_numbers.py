"""Utilities for extracting legally grounded numerical figures (thresholds and rates) from text."""
import re

_REVENUE = re.compile(r"(?<![\d,])(\d{1,3}(?:\.\d{3})*|\d+)\s*triệu", re.IGNORECASE)
_RATE = re.compile(r"(?<![\d,.])(\d{1,2})\s*%")
_VALID_VAT_RATES = {"0%", "5%", "10%"}
_REFUND_MARKERS = ("hoàn thuế", "khấu trừ")
_THRESHOLD_MARKERS = ("trở xuống", "doanh thu", "hằng năm", "hàng năm")


def extract_revenue_thresholds(text: str) -> list[str]:
    """Extract revenue threshold amounts (e.g. '200 triệu') scoped to annual threshold context."""
    results: list[str] = []
    text_lower = text.lower()
    for match in _REVENUE.finditer(text):
        start = max(0, match.start() - 80)
        end = min(len(text), match.end() + 80)
        window = text_lower[start:end]

        # Ignore refund / input deduction thresholds (e.g. 300 triệu hoàn thuế)
        if any(rm in window for rm in _REFUND_MARKERS):
            continue

        if any(tm in window for tm in _THRESHOLD_MARKERS):
            val = f"{match.group(1)} triệu"
            if val not in results:
                results.append(val)
    return results


def extract_tax_rates(text: str) -> list[str]:
    """Extract standalone statutory VAT tax rates (0%, 5%, 10%), ignoring non-VAT percentages."""
    results: list[str] = []
    for match in _RATE.finditer(text):
        val = f"{match.group(1)}%"
        if val in _VALID_VAT_RATES and val not in results:
            results.append(val)
    return results
