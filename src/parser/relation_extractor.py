"""Extract legal document relationships: AMENDS, CITES.

Uses ASCII-folding before matching to support diverse font encodings.
"""
import re

from src.parser.utils import fold_ascii

RE_AMEND_DOC = re.compile(r"Nghi dinh so 181/2025/ND-CP", re.IGNORECASE)
RE_DIEU_NUM = re.compile(r"Dieu\s+(\d+)", re.IGNORECASE)
RE_AMEND_VERB = re.compile(r"(Sua doi|Bo sung|Thay the|Bai bo)", re.IGNORECASE)
RE_CITE = re.compile(
    r"(?:quy dinh tai|can cu|theo|tai)\s+"
    r"(?:khoan\s+(\d+)\s+)?Dieu\s+(\d+)"
    r"(?:\s+(?:cua\s+)?(Luat[^.,;()]*|Nghi dinh[^.,;()]*|Thong tu[^.,;]*))?",
    re.IGNORECASE,
)

DOC_ALIASES = {
    "LUAT_48_2024": "Luat",
    "ND_181_2025": "Nghi dinh",
    "TT_69_2025": "Thong tu",
}


def _resolve_doc(phrase: str, source_doc_id: str) -> str:
    """Resolve target document ID based on textual phrase or fallback to source."""
    if not phrase:
        return source_doc_id
    low = phrase.lower()
    if "nay" in low:
        return source_doc_id
    for doc_id, alias in DOC_ALIASES.items():
        if alias.lower() in low:
            return doc_id
    return source_doc_id


def extract_cites_relations(source_doc_id: str, source_clause_id: str, text: str) -> list[dict]:
    """Extract citation relations from text matching 'quy dinh tai [khoan Y] Dieu X'.

    Returns list of dicts with source_clause, target_doc, target_article, target_clause.
    """
    rels, seen = [], set()
    for m in RE_CITE.finditer(fold_ascii(text)):
        khoan, dieu, phrase = m.group(1), m.group(2), m.group(3)
        key = (dieu, khoan, phrase or "")
        if key in seen:
            continue
        seen.add(key)
        rels.append({
            "source_clause": source_clause_id,
            "target_doc": _resolve_doc(phrase, source_doc_id),
            "target_article": f"Dieu {dieu}",
            "target_clause": f"Khoan {khoan}" if khoan else None,
        })
    return rels


AMENDMENT_DOCS = {"ND_359_2025": "ND_181_2025", "ND_144_2026": "ND_181_2025"}


def _action_of(text: str) -> str:
    """Determine amendment action type from text."""
    t = fold_ascii(text).lower()
    if "bai bo" in t:
        return "bai_bo"
    if "thay the" in t:
        return "thay_the"
    return "sua_doi"


def extract_amends_from_article(source_doc_id: str, article: dict) -> list[dict]:
    """Extract amendment targets from an article's title, clauses, and points.

    Returns list of dicts with source_article, target_doc, target_article, action.
    """
    default_target = AMENDMENT_DOCS.get(source_doc_id)
    if default_target is None:
        return []
    chunks = [article.get("title", "")]
    for cl in article.get("clauses", []):
        chunks.append(cl.get("content", ""))
        for pt in cl.get("points", []):
            chunks.append(pt.get("content", ""))
    seen, rels = set(), []
    for chunk in chunks:
        ascii_text = fold_ascii(chunk)
        if not RE_AMEND_VERB.search(ascii_text):
            continue
        action = _action_of(chunk)
        for m in RE_DIEU_NUM.finditer(ascii_text):
            code = f"Dieu {m.group(1)}"
            key = (code, action)
            if key in seen:
                continue
            seen.add(key)
            rels.append({
                "source_article": article["id"],
                "target_doc": default_target,
                "target_article": code,
                "action": action,
            })
    return rels
