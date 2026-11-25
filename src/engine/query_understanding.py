"""General LLM-based query understanding with deterministic anti-hallucination validation."""
from __future__ import annotations

from dataclasses import dataclass
from inspect import Parameter, signature
import json
import re
from typing import Any, Literal

from src.api.chat_domain import normalize_query, rewrite_query
from src.engine.llm_client import generate_llm_response

ScopeLabel = Literal["vat", "greeting", "out_of_scope"]

_EXPLICIT_LEGAL_REF_RE = re.compile(
    r"\b(?:điều|khoản|điểm|luật|nghị định|thông tư|nghị quyết|quyết định)\s+\d+"
    r"|\b\d+/\d{4}(?:/[\w-]+)?\b"
    r"|\b(?:LUAT|ND|TT|NQ|QD|VBHN)_\d+_\d{4}(?:_[A-Z0-9đ_]+)?\b",
    re.IGNORECASE,
)
_FOLLOW_UP_CUE_RE = re.compile(
    r"^(?:còn|vậy|nếu|thế|với lại|ngoài ra|tôi là|bên tôi|mình là|em là)\b"
    r"|\b(?:thì sao|như vậy|trường hợp trên|quy định đó)\b",
    re.IGNORECASE,
)
_FORMAL_VAT_TERMS = (
    "thuế gtgt",
    "thuế giá trị gia tăng",
    "thuế suất",
    "khấu trừ",
    "hoàn thuế",
    "hóa đơn",
    "hộ kinh doanh",
    "người nộp thuế",
    "giá tính thuế",
    "phương pháp trực tiếp",
)
_COLLOQUIAL_TELEX_RE = re.compile(
    r"\b(?:kh|ko|k|hok|hem|dc|đc|j|bnh|bn|nhiu|mik|mk|ntn)\b",
    re.IGNORECASE,
)
_STATUTE_NUM_RE = re.compile(
    r"\b(?:luật|nghị định|thông tư|nghị quyết|quyết định|điều|khoản)\s+\d+|\b\d+/\d{4}\b",
    re.IGNORECASE,
)
_UNDERSTANDING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scope", "legal_query", "missing_facts"],
    "properties": {
        "scope": {"type": "string", "enum": ["vat", "greeting", "out_of_scope"]},
        "legal_query": {"type": "string"},
        "missing_facts": {"type": "array", "items": {"type": "string"}},
    },
}


@dataclass(frozen=True)
class QueryUnderstanding:
    """Normalized query scope, legal search query, and missing user facts."""

    scope: ScopeLabel
    legal_query: str
    missing_facts: tuple[str, ...] = ()
    source: Literal["llm", "bypass", "fallback"] = "fallback"


def _to_history_pairs(history: list[dict] | list[tuple[str, str]] | None) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if not history:
        return pairs
    for item in history:
        if isinstance(item, dict):
            pairs.append((str(item.get("role", "user")), str(item.get("content", ""))))
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            pairs.append((str(item[0]), str(item[1])))
    return pairs


def _has_prior_user_turn(history: list[dict] | list[tuple[str, str]] | None) -> bool:
    return any(role == "user" and content.strip() for role, content in _to_history_pairs(history))


def needs_query_understanding(question: str, history: list[dict] | None = None) -> bool:
    """Bypass extra LLM understanding for standalone formal VAT or explicit-statute queries."""
    q_clean = (question or "").strip()
    if not q_clean:
        return False
    lowered = q_clean.lower()
    if _COLLOQUIAL_TELEX_RE.search(lowered):
        return True
    if _has_prior_user_turn(history) and _FOLLOW_UP_CUE_RE.search(lowered):
        return True
    if _EXPLICIT_LEGAL_REF_RE.search(q_clean):
        return False
    return not any(term in lowered for term in _FORMAL_VAT_TERMS)


def validate_rewritten_query(
    original: str,
    rewritten: str,
    history: list[dict] | None = None,
) -> tuple[bool, str]:
    """Reject rewrites that add numbers, statute identifiers, or multi-line bloat."""
    candidate = (rewritten or "").strip().strip('"').strip("'")
    if not candidate:
        return False, "empty"
    if "\n" in candidate or "\r" in candidate:
        return False, "multiline"
    if len(candidate) > 360:
        return False, "too_long"

    allowed_parts = [original] + [
        content for role, content in _to_history_pairs(history) if role == "user" and content
    ]
    allowed_text = " ".join(allowed_parts)
    candidate_nums = set(re.findall(r"\d+", candidate))
    allowed_nums = set(re.findall(r"\d+", allowed_text))
    if not candidate_nums.issubset(allowed_nums):
        return False, "added_number"

    if _STATUTE_NUM_RE.search(candidate) and not _STATUTE_NUM_RE.search(allowed_text):
        return False, "added_statute"

    return True, "accepted"


def _build_understanding_prompt(question: str, history_pairs: list[tuple[str, str]]) -> str:
    history_text = "\n".join(
        f"{'Người dùng' if role == 'user' else 'Trợ lý'}: {content}"
        for role, content in history_pairs[-6:]
        if content.strip()
    )
    history_block = f"Hội thoại trước đó:\n{history_text}\n\n" if history_text else ""
    return (
        "Bạn là bộ phân tích và chuẩn hóa câu hỏi pháp lý Thuế GTGT Việt Nam.\n"
        "Trả về DUY NHẤT một JSON object theo schema "
        '{"scope":"vat|greeting|out_of_scope","legal_query":"...","missing_facts":["..."]}.\n'
        "Quy tắc:\n"
        "1. scope = greeting nếu chỉ là lời chào ngắn; out_of_scope nếu hoàn toàn ngoài pháp luật/nghiệp vụ thuế GTGT; "
        "còn lại chọn vat (bao gồm mọi hoạt động kinh doanh, hộ cá thể, dịch vụ, hàng hóa, hóa đơn).\n"
        "2. legal_query: viết lại thành MỘT câu truy vấn pháp lý tiếng Việt chuẩn xác, không telex/viết tắt, "
        "gộp đầy đủ ngữ cảnh hội thoại trước (nếu có) để tìm kiếm văn bản luật Thuế GTGT.\n"
        "3. TUYỆT ĐỐI KHÔNG tự bịa thêm con số, mức doanh thu, thuế suất %, hay số hiệu Luật/Nghị định/Điều/Khoản "
        "không có trong câu hỏi hoặc lịch sử.\n"
        "4. missing_facts: liệt kê tối đa 3 dữ kiện thực tế người dùng còn thiếu để xác định nghĩa vụ thuế, "
        "BẮT BUỘC viết bằng tiếng Việt có dấu đầy đủ, ngắn gọn, tự nhiên "
        "(ví dụ: 'Loại hình kinh doanh (hộ cá thể hay doanh nghiệp)', 'Doanh thu hằng năm'); "
        "để rỗng [] nếu đã đủ hoặc câu chào/ngoài phạm vi.\n\n"
        f"{history_block}"
        f"Câu hỏi hiện tại: {question}"
    )


def _invoke_understanding_llm(llm_fn: Any, prompt: str, model: str, timeout: float) -> str | None:
    kwargs: dict[str, Any] = {
        "model": model,
        "timeout": timeout,
        "options": {"temperature": 0.0, "num_predict": 256},
        "generation_config": {
            "temperature": 0.0,
            "maxOutputTokens": 256,
            "responseMimeType": "application/json",
            "responseJsonSchema": _UNDERSTANDING_SCHEMA,
        },
    }
    if model in {"gemini-2.5-flash", "gemini-2.5-flash-lite"}:
        kwargs["generation_config"]["thinkingConfig"] = {"thinkingBudget": 0}
    params = signature(llm_fn).parameters
    variadic = any(p.kind == Parameter.VAR_KEYWORD for p in params.values())
    filtered = kwargs if variadic else {k: v for k, v in kwargs.items() if k in params}
    return llm_fn(prompt, **filtered)


def _parse_understanding_json(raw: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?\s*", "", raw or "").strip().rstrip("`")
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError("No JSON object in understanding response")
    data = json.loads(match.group())
    if not isinstance(data, dict):
        raise ValueError("Understanding output must be a JSON object")
    return data


def understand_with_llm(
    question: str,
    history: list[dict] | None = None,
    *,
    llm_fn: Any = None,
    model: str = "qwen2.5:7b",
    use_llm: bool = True,
    timeout: float = 15.0,
) -> QueryUnderstanding:
    """Understand colloquial/multi-turn VAT queries via LLM with validated fallback."""
    history_pairs = _to_history_pairs(history)
    combined_pairs = [*history_pairs, ("user", question)]
    fallback_query = normalize_query(rewrite_query(combined_pairs) or question)

    if not use_llm:
        return QueryUnderstanding(scope="vat", legal_query=fallback_query, source="fallback")
    if not needs_query_understanding(question, history):
        return QueryUnderstanding(scope="vat", legal_query=fallback_query, source="bypass")

    caller = llm_fn or generate_llm_response
    prompt = _build_understanding_prompt(question, history_pairs)
    try:
        raw = _invoke_understanding_llm(caller, prompt, model, timeout)
        if not isinstance(raw, str) or not raw.strip():
            return QueryUnderstanding(scope="vat", legal_query=fallback_query, source="fallback")
        data = _parse_understanding_json(raw)
        scope_raw = str(data.get("scope", "vat")).strip().lower()
        scope: ScopeLabel = scope_raw if scope_raw in {"vat", "greeting", "out_of_scope"} else "vat"

        candidate_query = str(data.get("legal_query", "")).strip()
        ok, _ = validate_rewritten_query(question, candidate_query, history)
        legal_query = normalize_query(candidate_query) if ok else fallback_query

        raw_facts = data.get("missing_facts", [])
        missing_facts = tuple(
            str(item).strip() for item in (raw_facts if isinstance(raw_facts, list) else [])
            if isinstance(item, str) and item.strip()
        )[:6]
        return QueryUnderstanding(
            scope=scope,
            legal_query=legal_query or fallback_query,
            missing_facts=missing_facts,
            source="llm",
        )
    except Exception:
        return QueryUnderstanding(scope="vat", legal_query=fallback_query, source="fallback")
