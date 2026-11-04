"""Pure domain helpers for VAT Copilot: scope classification, date resolution, query rewriting.

This module is intentionally free of I/O: no network calls, no database access and no
imports from the API layer, so its behaviour is fully deterministic and unit testable.
"""
import calendar
import re
from dataclasses import dataclass, field
from typing import Literal

ScopeKind = Literal["vat", "greeting", "out_of_scope"]

_TELEX_MAP: dict[str, str] = {
    "kh": "không",
    "ko": "không",
    "k": "không",
    "hok": "không",
    "hem": "không",
    "dc": "được",
    "đc": "được",
    "j": "gì",
    "bnh": "bao nhiêu",
    "bn": "bao nhiêu",
    "nhiu": "nhiêu",
    "mik": "mình",
    "mk": "mình",
    "ntn": "như thế nào",
    "sđt": "số điện thoại",
    "cccd": "căn cước công dân",
    "mst": "mã số thuế",
}

_TELEX_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_TELEX_MAP.keys(), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

_INDUSTRY_MAP: dict[tuple[str, ...], tuple[str, ...]] = {
    ("tiệm net", "quán net", "cyber", "internet", "game net"): (
        "dịch vụ internet",
        "kinh doanh trò chơi điện tử",
        "dịch vụ",
    ),
    ("quán cafe", "quán cà phê", "cà phê", "cafe"): ("dịch vụ ăn uống",),
    ("tiệm tạp hóa", "quán tạp hóa", "tạp hóa"): ("bán lẻ hàng hóa",),
    ("rửa xe", "tiệm rửa xe"): ("dịch vụ rửa xe",),
}

_REVENUE_PATTERN = re.compile(r"(\d+(?:[.,]\d+)?)\s*(triệu|tr|tỷ|tỉ)\b", re.IGNORECASE)


@dataclass(frozen=True)
class QueryEntities:
    raw: str
    normalized: str
    business_type: str | None
    industry_synonyms: tuple[str, ...] = field(default_factory=tuple)
    revenue_vnd: int | None = None


def normalize_query(text: str) -> str:
    """Normalize colloquial Vietnamese telex and abbreviations using word boundaries."""
    if not text:
        return ""

    def _replace(match: re.Match) -> str:
        word = match.group(1)
        lower = word.lower()
        repl = _TELEX_MAP.get(lower, word)
        if word.isupper():
            return repl.upper()
        if word[0].isupper():
            return repl.capitalize()
        return repl

    replaced = _TELEX_PATTERN.sub(_replace, text)
    return " ".join(replaced.split())


def extract_entities(text: str) -> QueryEntities:
    """Extract business type, industry synonyms, and revenue figures from query."""
    norm = normalize_query(text)
    norm_lower = norm.lower()

    # 1. Industry extraction
    industry_synonyms: list[str] = []
    for triggers, synonyms in _INDUSTRY_MAP.items():
        if any(trigger in norm_lower for trigger in triggers):
            industry_synonyms.extend(synonyms)

    # 2. Business type classification
    business_type = None
    if any(k in norm_lower for k in ("hộ kinh doanh", "hộ cá thể", "tiệm", "quán", "dưới quê")):
        business_type = "HỘ_KINH_DOANH"
    elif any(k in norm_lower for k in ("công ty", "doanh nghiệp", "tổng công ty", "tập đoàn")):
        business_type = "DOANH_NGHIEP"

    # 3. Revenue extraction
    revenue_vnd = None
    rev_match = _REVENUE_PATTERN.search(norm_lower)
    if rev_match:
        val_str = rev_match.group(1).replace(",", ".")
        unit = rev_match.group(2).lower()
        try:
            val = float(val_str)
            if unit in ("triệu", "tr"):
                revenue_vnd = int(val * 1_000_000)
            elif unit in ("tỷ", "tỉ"):
                revenue_vnd = int(val * 1_000_000_000)
        except ValueError:
            pass

    return QueryEntities(
        raw=text,
        normalized=norm,
        business_type=business_type,
        industry_synonyms=tuple(dict.fromkeys(industry_synonyms)),
        revenue_vnd=revenue_vnd,
    )


def understand_query(text: str) -> QueryEntities:
    """Convenience entry point for full query normalization and entity understanding."""
    return extract_entities(text)


VAT_KEYWORDS: tuple[str, ...] = (
    "thuế",
    "gtgt",
    "vat",
    "hóa đơn",
    "khấu trừ",
    "hoàn thuế",
    "xuất khẩu",
    "nhập khẩu",
    "kê khai",
    "mã số thuế",
    "miễn giảm",
    "giảm trừ",
    "thuế suất",
)

OUT_OF_SCOPE_KEYWORDS: tuple[str, ...] = (
    "ly hôn",
    "hình sự",
    "đất đai",
    "thừa kế",
    "lao động",
    "tố tụng",
    "chứng khoán",
    "lưu trú",
    "bảo hiểm xã hội",
    "cướp",
    "bị cáo",
    "tội phạm",
    "đánh bạc",
    "ma túy",
)

GREETING_KEYWORDS: tuple[str, ...] = ("chào", "hello", "hey")

_GREETING_MAX_CHARS = 40
_FOLLOW_UP_MAX_CHARS = 25
_QUERY_JOINER = " ; "
_MIN_YEAR = 2000
_MAX_YEAR = 2100

_DAY_PATTERN = re.compile(r"ngày\s+(\d{1,2})[/-](\d{1,2})[/-](\d{4})")
_MONTH_PATTERN = re.compile(r"tháng\s+(\d{1,2})[/-](\d{4})")
_YEAR_PATTERN = re.compile(r"năm\s+(\d{4})")


@dataclass(frozen=True)
class ScopeDecision:
    """Outcome of the scope classification of a user message."""

    kind: ScopeKind
    in_scope: bool
    reason: str


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace so keyword matching ignores formatting."""
    return " ".join(text.lower().split())


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    """Report whether the text contains at least one of the given keywords."""
    return any(keyword in text for keyword in keywords)


def classify(text: str) -> ScopeDecision:
    """Classify a user message as VAT question, greeting or out-of-scope request."""
    lowered = text.lower()
    if _contains_any(lowered, VAT_KEYWORDS):
        return ScopeDecision(
            kind="vat",
            in_scope=True,
            reason="Câu hỏi chứa từ khóa thuế GTGT nên được xử lý theo phạm vi thuế.",
        )

    normalized = _normalize(text)
    if len(normalized) <= _GREETING_MAX_CHARS and _contains_any(normalized, GREETING_KEYWORDS):
        return ScopeDecision(
            kind="greeting",
            in_scope=False,
            reason="Câu chào ngắn, chưa có nội dung pháp lý cần tra cứu.",
        )

    if _contains_any(lowered, OUT_OF_SCOPE_KEYWORDS):
        return ScopeDecision(
            kind="out_of_scope",
            in_scope=False,
            reason="Câu hỏi nằm ngoài phạm vi thuế GTGT của trợ lý.",
        )

    return ScopeDecision(
        kind="vat",
        in_scope=True,
        reason="Không phát hiện từ khóa ngoài phạm vi nên áp dụng mặc định thuế GTGT.",
    )


def _is_valid_year(year: int) -> bool:
    """Report whether the year falls inside the supported legal corpus range."""
    return _MIN_YEAR <= year <= _MAX_YEAR


def _to_iso_date(year: int, month: int, day: int) -> str | None:
    """Convert a date triple to ISO format, or None when the date is not a real date."""
    if not _is_valid_year(year) or not 1 <= month <= 12:
        return None
    if not 1 <= day <= calendar.monthrange(year, month)[1]:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _resolve_day(lowered: str) -> str | None:
    """Resolve an explicit 'ngày DD/MM/YYYY' reference."""
    match = _DAY_PATTERN.search(lowered)
    if match is None:
        return None
    day, month, year = (int(group) for group in match.groups())
    return _to_iso_date(year, month, day)


def _resolve_month(lowered: str) -> str | None:
    """Resolve a 'tháng MM/YYYY' reference to the last day of that month."""
    match = _MONTH_PATTERN.search(lowered)
    if match is None:
        return None
    month, year = int(match.group(1)), int(match.group(2))
    if not _is_valid_year(year) or not 1 <= month <= 12:
        return None
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-{last_day:02d}"


def _resolve_year(lowered: str) -> str | None:
    """Resolve a 'năm YYYY' reference to the end of that year."""
    match = _YEAR_PATTERN.search(lowered)
    if match is None:
        return None
    year = int(match.group(1))
    if not _is_valid_year(year):
        return None
    return f"{year:04d}-12-31"


def resolve_date(text: str, default: str) -> str:
    """Resolve the effective reference date mentioned in the text, else keep the default."""
    lowered = text.lower()
    for resolver in (_resolve_day, _resolve_month, _resolve_year):
        resolved = resolver(lowered)
        if resolved is not None:
            return resolved
    return default


def rewrite_query(history: list[tuple[str, str]]) -> str:
    """Rewrite the conversation history into a single retrieval query.

    A short follow-up turn carries no subject on its own, so the previous user turn is
    prepended to keep the retrieval anchored on the same legal topic.
    """
    user_turns = [content for role, content in history if role == "user"]
    if not user_turns:
        return ""
    last_turn = user_turns[-1]
    if len(last_turn) <= _FOLLOW_UP_MAX_CHARS and len(user_turns) >= 2:
        return f"{user_turns[-2]}{_QUERY_JOINER}{last_turn}"
    return last_turn
