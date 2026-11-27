"""Domain guardrail and intent classifier for Vietnamese VAT legal QA."""
import re
from dataclasses import dataclass

VAT_DOMAIN_KEYWORDS = [
    # Tax core
    "thuế", "thue", "gtgt", "vat", "giá trị gia tăng", "gia tri gia tang", "thuế suất", "thue suat",
    "kê khai", "ke khai", "hoàn thuế", "hoan thue", "khấu trừ", "khau tru", "nộp thuế", "nop thue",
    "miễn thuế", "mien thue", "giảm thuế", "giam thue", "quyết toán", "quyet toan", "nghĩa vụ thuế",
    # Invoice & documents
    "hóa đơn", "hoa don", "chứng từ", "chung tu", "hóa đơn điện tử", "hoa don dien tu",
    "bảng kê", "bang ke", "điều chỉnh hóa đơn", "dieu chinh hoa don", "thay thế hóa đơn", "sai sót",
    "hợp đồng", "hop dong", "thanh toán", "thanh toan", "ngân hàng", "ngan hang", "tờ khai", "to khai",
    # Commerce, goods, services
    "xuất khẩu", "xuat khau", "nhập khẩu", "nhap khau", "hàng hóa", "hang hoa", "dịch vụ", "dich vu",
    "gia công", "gia cong", "phần mềm", "phan mem", "tiêu dùng", "tieu dung", "thương mại", "thuong mai",
    "cung ứng", "cung ung", "kinh doanh", "doanh nghiệp", "doanh nghiep", "công ty", "cong ty",
    "người nộp thuế", "nguoi nop thue", "người mua", "nguoi mua", "người bán", "nguoi ban",
    # Legal terms & references
    "luật", "luat", "nghị định", "nghi dinh", "thông tư", "thong tu", "điều", "dieu", "khoản", "khoan",
    "hiệu lực", "hieu luc", "thi hành", "thi hanh", "áp dụng", "ap dung", "văn bản", "van ban",
]

GREETING_KEYWORDS = [
    "hi", "hello", "alo", "chào", "chao", "xin chào", "xin chao", "hey", "hế lô", "he lo", "test",
]

CLEAR_OUT_OF_DOMAIN_PATTERNS = [
    r"thuật toán", r"quy hoạch động", r"giải thuật", r"đồ thị euler", r"dijkstra", r"machine learning",
    r"thời tiết", r"nấu ăn", r"bóng đá", r"viết thơ", r"bài thơ", r"công thức hóa học", r"vật lý",
    r"lập trình java", r"lập trình c", r"lập trình python", r"react js", r"html css",
    r"vượt đèn đỏ", r"vi phạm giao thông", r"bằng lái xe", r"giấy phép lái xe", r"nồng độ cồn",
]

CORE_TAX_KEYWORDS = [
    "thuế", "thue", "gtgt", "vat", "giá trị gia tăng", "gia tri gia tang", "thuế suất", "thue suat",
    "hoàn thuế", "hoan thue", "khấu trừ", "khau tru", "hóa đơn", "hoa don", "chứng từ", "chung tu",
    "kê khai", "ke khai", "nộp thuế", "nop thue", "miễn thuế", "mien thue", "tờ khai", "to khai",
    "quyết toán", "quyet toan",
]

UNRELATED_INTENT_PATTERNS = (
    r"\b(?:viết|tạo|sinh|generate|write|create)\b.*\b(?:mã|code|script|program)\b",
    r"\b(?:lập trình|lap trinh|coding|programming)\b",
    r"\b(?:dịch|translate)\b.*\b(?:văn|đoạn|tiếng|text|passage|into|to)\b",
    r"\b(?:viết|sáng tác|write|compose)\b.*\b(?:thơ|truyện|poem|story|song)\b",
)

LEGAL_BUSINESS_CONTEXT = re.compile(
    r"\b(?:pháp luật|phap luat|luật|luat|nghị định|nghi dinh|thông tư|thong tu|"
    r"kinh doanh|doanh nghiệp|doanh nghiep|công ty|cong ty|hợp đồng|hop dong|"
    r"xuất khẩu|xuat khau|nhập khẩu|nhap khau|quán|tiệm|legal|tax|business|contract)\b"
    r"|\b(?:điều|dieu|khoản|khoan)\s+\d+|\b\d+/\d{4}\b",
    re.IGNORECASE,
)

CORE_TAX_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(CORE_TAX_KEYWORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


@dataclass
class GuardrailResult:
    is_allowed: bool
    reason: str
    is_greeting: bool = False
    refusal_message: str = ""


def check_query_domain(query: str) -> GuardrailResult:
    """Verify whether user query pertains to Vietnamese VAT regulations."""
    q_clean = query.strip().lower()
    if not q_clean:
        return GuardrailResult(
            is_allowed=False,
            reason="EMPTY_QUERY",
            refusal_message="Vui lòng nhập câu hỏi pháp lý về thuế GTGT."
        )

    from src.api.chat_domain import OUT_OF_SCOPE_KEYWORDS
    has_core_tax = bool(CORE_TAX_PATTERN.search(q_clean))
    has_domain_kw = has_core_tax or any(kw in q_clean for kw in VAT_DOMAIN_KEYWORDS)

    # 1. Check greeting (skip if VAT domain terms appear; restrict "test" to exact standalone token)
    words = q_clean.split()
    is_greeting = (
        not has_domain_kw
        and len(words) <= 3
        and any(
            (q_clean == "test" if g == "test" else (q_clean == g or q_clean.startswith(g + " ")))
            for g in GREETING_KEYWORDS
        )
    )
    if is_greeting:
        return GuardrailResult(
            is_allowed=False,
            is_greeting=True,
            reason="GREETING",
            refusal_message="Xin chào! Tôi là Trợ lý Pháp lý ảo về Thuế Giá trị gia tăng (GTGT) Việt Nam. Hãy đặt câu hỏi về chính sách thuế, thuế suất hoặc quy định hóa đơn chứng từ để tôi hỗ trợ bạn."
        )

    ood_pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(w) for w in sorted(OUT_OF_SCOPE_KEYWORDS, key=len, reverse=True)) + r")\b",
        re.IGNORECASE,
    )
    if not has_core_tax and ood_pattern.search(q_clean):
        return GuardrailResult(is_allowed=False, reason="EXPLICIT_OUT_OF_DOMAIN",
                               refusal_message="Câu hỏi nằm ngoài phạm vi thuế GTGT.")
    has_legal_business_context = has_core_tax or bool(LEGAL_BUSINESS_CONTEXT.search(q_clean))
    if not has_legal_business_context:
        for pat in (*CLEAR_OUT_OF_DOMAIN_PATTERNS, *UNRELATED_INTENT_PATTERNS):
            if re.search(pat, q_clean, re.IGNORECASE):
                return GuardrailResult(
                    is_allowed=False,
                    reason="EXPLICIT_OUT_OF_DOMAIN",
                    refusal_message="Câu hỏi của bạn không thuộc phạm vi điều chỉnh của Hệ thống Pháp lý Thuế Giá trị gia tăng (GTGT). Vui lòng đặt câu hỏi liên quan đến chính sách thuế, thuế suất hoặc quy định hóa đơn chứng từ."
                )

    # 3. Check domain presence
    has_domain_keyword = (has_domain_kw or bool(re.search(r"\b\d+/\d{4}(?:/[\w-]+)?\b", q_clean)))
    if not has_domain_keyword:
        return GuardrailResult(is_allowed=True, reason="UNCLEAR_REQUIRES_RETRIEVAL")

    return GuardrailResult(is_allowed=True, reason="IN_DOMAIN")
