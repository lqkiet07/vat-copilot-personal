"""Semantic and lexical relevance gating for legal articles."""
import math
import re

from src.api.chat_domain import normalize_query, understand_query
from src.engine.models import ArticleNode

_STOPWORDS = {
    "tôi", "cho", "tại", "năm", "tháng", "ngày", "được", "theo", "quy", "định",
    "với", "là", "bao", "nhiêu", "của", "và", "các", "có", "phải", "không", "thì",
    "dưới", "trên", "trong", "về", "một", "số", "khi", "ra", "sao", "tiền",
}

_DISJOINT_SUBJECTS = {
    "hoa hồng đại lý": ("hoa hồng", "đại lý bảo hiểm", "xổ số", "đa cấp"),
    "vũ khí": ("vũ khí", "khí tài", "quân sự", "quốc phòng"),
    "tín dụng": ("tín dụng", "ngân hàng", "cho vay", "bảo lãnh"),
    "chứng khoán": ("chứng khoán", "cổ phiếu", "trái phiếu"),
    "chuyển quyền sử dụng đất": ("quyền sử dụng đất", "đất đai"),
}

_EXPORT_QUERY_MARKERS = ("xuất khẩu", "xuat khau", "0%", "nước ngoài", "nuoc ngoai", "phi thuế quan")


def _is_export_topic_article(article: ArticleNode) -> bool:
    title_l = (article.title or "").lower()
    code_l = (article.code or "").lower()
    if "xuất khẩu" in title_l or "xuat khau" in title_l or "xuất khẩu" in code_l or "xuat khau" in code_l:
        return True
    return bool(re.search(r"(?<!\d)0%", title_l)) and ("thuế suất" in title_l or "thue suat" in title_l)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Calculate cosine similarity between two numeric vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"\w+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def _matches_household(art_text: str, q_norm: str) -> bool:
    """Check if article governs household business, revenue threshold, or applicable rates."""
    household_markers = (
        "hộ, cá nhân sản xuất, kinh doanh",
        "hộ, cá nhân kinh doanh",
        "hộ kinh doanh",
        "cá nhân kinh doanh",
        "200 triệu",
        "phương pháp tính trực tiếp",
    )
    # If the article only mentions household to exclude them (e.g. "trừ hộ, cá nhân"), it does not govern them
    if "trừ hộ, cá nhân" in art_text and not any(k in art_text for k in ("200 triệu", "phương pháp tính trực tiếp")):
        return False

    if any(k in art_text for k in household_markers):
        return True
    if any(k in art_text for k in ("doanh thu hằng năm", "doanh thu hàng năm")) and "trừ hộ" not in art_text:
        return True
    return ("thuế suất 10%" in art_text or "thuế suất" in art_text) and any(
        w in q_norm for w in ("thuế", "thuế suất", "bao nhiêu", "đóng")
    )


def score_article_relevance(
    query: str,
    article: ArticleNode,
    query_vec: list[float] | None = None,
) -> bool:
    """Determine whether an article is substantively relevant to the user query."""
    q_norm = normalize_query(query).lower()
    art_text = f"{article.title} {article.content}".lower()
    entities = understand_query(query)

    gambling_activities = ("ca-si-nô", "casino", "trò chơi điện tử có thưởng", "đặt cược")
    if any(activity in (article.title or "").lower() for activity in gambling_activities):
        return any(activity in q_norm for activity in gambling_activities)

    is_household = entities.business_type == "HỘ_KINH_DOANH"
    has_export_marker = any(
        bool(re.search(r"(?<!\d)0%", q_norm)) if m == "0%" else m in q_norm
        for m in _EXPORT_QUERY_MARKERS
    )

    # 1. Export topic gating: reject export-specific articles for non-export domestic queries
    if _is_export_topic_article(article) and not has_export_marker:
        return False

    # 1b. Reject D9 (corporate tax rates / 0% export) specifically for domestic household queries
    if article.id == "LUAT_48_2024_D9" and is_household and not has_export_marker:
        return False

    # 1c. Reject corporate deduction-method articles for domestic household queries unless specifically asked
    has_deduction_query = any(k in q_norm for k in ("khấu trừ", "khau tru"))
    is_deduction_article = (
        article.id == "ND_181_2025_D21"
        or ("phương pháp khấu trừ" in (article.title or "").lower() and "trừ hộ" in art_text)
    )
    if is_deduction_article and is_household and not has_deduction_query:
        return False

    # 2. Cosine vector gating if embeddings are present
    if query_vec and article.embedding:
        sim = cosine_similarity(query_vec, article.embedding)
        if sim >= 0.70:
            return True
        if sim < 0.30:
            return False

    # 2. Colloquial / household business query matching
    if (entities.business_type == "HỘ_KINH_DOANH" or entities.industry_synonyms) and _matches_household(art_text, q_norm):
        return True

    # 3. Check for disjoint topic contamination (e.g. ND_144 agent commission for internet cafe)
    for topic, keywords in _DISJOINT_SUBJECTS.items():
        if any(kw in art_text for kw in keywords) and not any(kw in q_norm for kw in keywords):
            return False

    # 4. Lexical overlap check
    q_tokens = _tokenize(q_norm)
    art_tokens = _tokenize(art_text)
    if not q_tokens or not art_tokens:
        return True

    return len(q_tokens.intersection(art_tokens)) > 0


def build_relevance_map(
    query: str,
    articles: list[ArticleNode],
    query_vec: list[float] | None = None,
) -> dict[str, bool]:
    """Map article IDs to boolean relevance flags for citation verification."""
    return {
        art.id: score_article_relevance(query, art, query_vec=query_vec)
        for art in articles
    }
