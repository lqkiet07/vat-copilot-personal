"""Extract legal document relationships: AMENDS, CITES.

Uses ASCII-folding before matching to support diverse font encodings.
"""
import re

from src.parser.utils import fold_ascii

RE_AMEND_DOC = re.compile(r"Nghi dinh so 181/2025/ND-CP", re.IGNORECASE)
RE_DIEU_NUM = re.compile(r"Dieu\s+(\d+)", re.IGNORECASE)
RE_AMEND_VERB = re.compile(r"(Sua doi|Bo sung|Thay the|Bai bo)", re.IGNORECASE)
RE_AMEND_TARGET = re.compile(
    r"(?:diem\s+([a-z])\s+)?(?:khoan\s+(\d+)\s+)?Dieu\s+(\d+[a-z]*)(?!\w)",
    re.IGNORECASE,
)
RE_UNRESOLVED_SUBARTICLE = re.compile(
    r"(?:mot so|cac)\s+(?:diem|khoan)\b|\bdiem\s+(?![a-z]\s+khoan\b)\w+",
    re.IGNORECASE,
)

_DOC_TERMS = r"(?:Luat|Nghi dinh|Thong tu|Nghi quyet|Quyet dinh|Van ban hop nhat)"
_PHRASE_PAT = (
    rf"(?:cua\s+)?({_DOC_TERMS}\s+nay"
    rf"|{_DOC_TERMS}(?:\s+so)?\s+\d+[\w/.-]*"
    rf"|Luat\s+Thue\s+gia\s+tri\s+gia\s+tang(?:\s+so\s+\d+[\w/.-]*)?"
    rf"|Luat\s+Quan\s+ly\s+thue(?:\s+so\s+\d+[\w/.-]*)?"
    rf"|{_DOC_TERMS}(?:\s+so\s+[\w/.-]+|\s+[\w\s]{{1,40}}?))"
)

RE_CITE = re.compile(
    rf"(?:quy dinh tai|can cu|theo|tai)\s+"
    rf"(?:khoan\s+(\d+)\s+)?Dieu\s+(\d+)"
    rf"(?:\s+{_PHRASE_PAT})?",
    re.IGNORECASE,
)

INVENTORY_DOC_CODES: dict[str, str] = {
    "174/2025/ND-CP": "ND_174_2025",
    "181/2025/ND-CP": "ND_181_2025",
    "245/2026/ND-CP": "ND_245_2026",
    "252/2026/ND-CP": "ND_252_2026",
    "359/2025/ND-CP": "ND_359_2025",
    "68/2026/ND-CP": "ND_68_2026",
    "141/2026/ND-CP": "ND_141_2026",
    "144/2026/ND-CP": "ND_144_2026",
    "48/2024/QH15": "LUAT_48_2024",
    "90/2025/QH15": "LUAT_90_2025",
    "108/2025/QH15": "LUAT_108_2025",
    "149/2025/QH15": "LUAT_149_2025",
    "09/2026/QH16": "LUAT_09_2026",
    "9/2026/QH16": "LUAT_09_2026",
    "77/2025/TT-BTC": "TT_77_2025",
    "101/2025/TT-BNNMT": "TT_101_2025",
    "18/2026/TT-BTC": "TT_18_2026",
    "32/2026/TT-BTC": "TT_32_2026",
    "50/2026/TT-BTC": "TT_50_2026",
    "84/2026/TT-BTC": "TT_84_2026",
    "89/2026/TT-BTC": "TT_89_2026",
    "97/2026/TT-BTC": "TT_97_2026",
    "69/2025/TT-BTC": "TT_69_2025",
    "72/2014/TT-BTC": "TT_72_2014",
    "92/2019/TT-BTC": "TT_92_2019",
    "41/2026/TT-BTC": "TT_41_2026",
    "34/2026/NQ-CP": "NQ_34_2026",
    "43/2026/NQ-CP": "NQ_43_2026",
    "19/2026/QH16": "NQ_19_2026",
    "204/2025/QH15": "NQ_204_2025",
    "05/2025/NQ-CP": "NQ_05_2025",
    "5/2025/NQ-CP": "NQ_05_2025",
    "482/QD-TTG": "QD_482_2026",
    "2551/QD-BTC": "QD_2551_2026",
    "25/2026/VBHN-BTC": "VBHN_25_2026",
    "25/VBHN-BTC": "VBHN_25_2026",
}

DOC_ALIASES = {
    "LUAT_48_2024": "Luat Thue gia tri gia tang",
    "LUAT_108_2025": "Luat Quan ly thue",
}

AMENDMENT_DOCS = {
    "ND_359_2025": "ND_181_2025",
    "ND_144_2026": "ND_181_2025",
    "TT_50_2026": "TT_18_2026",
    "TT_92_2019": "TT_72_2014",
}

ARTICLE_AMENDMENT_TARGETS: dict[tuple[str, str], str | None] = {
    ("ND_141_2026", "Dieu 1"): "ND_68_2026",
    ("ND_141_2026", "Dieu 2"): None,  # ND 320/2025 out of VAT scope
    ("LUAT_90_2025", "Dieu 4"): "LUAT_48_2024",
    ("LUAT_09_2026", "Dieu 2"): "LUAT_48_2024",
    ("LUAT_149_2025", "Dieu 1"): "LUAT_48_2024",
}


def _resolve_doc(phrase: str | None, source_doc_id: str) -> str | None:
    """Resolve target document ID based on textual phrase or fallback to source."""
    if not phrase:
        return source_doc_id
    clean = phrase.rstrip(".,;() ").strip()
    low = fold_ascii(clean).lower()
    if not low:
        return source_doc_id
    codes = re.findall(r"(?<![\w/.-])\d+[\w/.-]*/[\w/.-]+", low)
    if codes:
        code = codes[0].rstrip(".")
        doc_id = INVENTORY_DOC_CODES.get(code.upper())
        if doc_id is None:
            return None
        families = {"ND": "nghi dinh", "LUAT": "luat", "TT": "thong tu",
                    "NQ": "nghi quyet", "QD": "quyet dinh", "VBHN": "van ban hop nhat"}
        family = re.search(_DOC_TERMS, low, re.IGNORECASE)
        if family and family.group(0) != families[doc_id.split("_")[0]]:
            return None
        return doc_id
    if re.fullmatch(rf"(?:cua\s+)?{_DOC_TERMS}\s+nay", low, re.IGNORECASE):
        return source_doc_id
    for doc_id, alias in DOC_ALIASES.items():
        if alias.lower() in low:
            return doc_id
    doc_keywords = ["luat", "nghi dinh", "thong tu", "nghi quyet", "quyet dinh", "van ban hop nhat"]
    if any(kw in low for kw in doc_keywords):
        return None
    return source_doc_id


def extract_cites_relations(source_doc_id: str, source_clause_id: str, text: str) -> list[dict]:
    """Extract citation relations from text matching 'quy dinh tai [khoan Y] Dieu X'.

    Returns list of dicts with source_clause, target_doc, target_article, target_clause,
    resolved, unresolved_reason, source_text.
    """
    rels, seen = [], set()
    for m in RE_CITE.finditer(fold_ascii(text)):
        khoan, dieu, phrase = m.group(1), m.group(2), m.group(3)
        key = (dieu, khoan, phrase or "")
        if key in seen:
            continue
        seen.add(key)
        target_doc = _resolve_doc(phrase, source_doc_id)
        resolved = target_doc is not None
        unresolved_reason = None if resolved else "unknown_document"
        rels.append({
            "source_clause": source_clause_id,
            "target_doc": target_doc,
            "target_article": f"Dieu {dieu}",
            "target_clause": f"Khoan {khoan}" if khoan else None,
            "resolved": resolved,
            "unresolved_reason": unresolved_reason,
            "source_text": m.group(0),
        })
    return rels


def _action_of(text: str) -> str:
    """Determine amendment action type from text."""
    t = fold_ascii(text).lower()
    if "bai bo" in t:
        return "bai_bo"
    if "thay the" in t:
        return "thay_the"
    return "sua_doi"


def _resolve_amendment_doc(
    source_doc_id: str,
    source_article_code: str,
    heading: str,
    article_title: str,
) -> tuple[str | None, bool, str | None]:
    """Resolve target document for amendment chunk."""
    key = (source_doc_id, source_article_code)
    if source_doc_id not in AMENDMENT_DOCS and not any(
        doc_id == source_doc_id for doc_id, _ in ARTICLE_AMENDMENT_TARGETS
    ):
        return None, False, "unknown_amendment_source"

    for text_to_check in [heading, article_title]:
        res = _resolve_doc(text_to_check, source_doc_id)
        if res != source_doc_id:
            if res is None:
                return None, False, "target_doc_out_of_scope"
            return res, True, None

    if key in ARTICLE_AMENDMENT_TARGETS:
        tgt = ARTICLE_AMENDMENT_TARGETS[key]
        if tgt is None:
            return None, False, "target_doc_out_of_scope"
        return tgt, True, None

    default_target = AMENDMENT_DOCS.get(source_doc_id)
    if default_target:
        return default_target, True, None

    return None, False, "unknown_amendment_source"


def extract_amends_from_article(source_doc_id: str, article: dict) -> list[dict]:
    """Extract amendment targets from an article's title, clauses, and points.

    Returns list of dicts with source_article, source_unit, target_doc, target_article,
    target_clause, target_point, target_scope, resolved, unresolved_reason, and action.
    """
    chunks = [(article["id"], article.get("title", ""))]
    for cl in article.get("clauses", []):
        chunks.append((cl["id"], cl.get("content", "")))
        for pt in cl.get("points", []):
            chunks.append((pt["id"], pt.get("content", "")))
    seen, rels = set(), []
    for source_unit_id, chunk in chunks:
        ascii_text = fold_ascii(chunk)
        heading = re.split(r"(?:nhu\s+sau\s*:|:\s*[\"“'])", ascii_text, maxsplit=1, flags=re.IGNORECASE)[0]
        if not RE_AMEND_VERB.search(heading):
            continue
        action = _action_of(heading)
        article_heading = re.split(
            r"(?:nhu\s+sau\s*:|:\s*[\"“'])", fold_ascii(article.get("title", "")), maxsplit=1, flags=re.IGNORECASE
        )[0]
        target_doc, doc_resolved, doc_reason = _resolve_amendment_doc(
            source_doc_id, article.get("code", ""), heading, article_heading
        )
        if not doc_resolved:
            rels.append({
                "source_article": article["id"],
                "source_unit": source_unit_id,
                "target_doc": target_doc,
                "target_article": "unresolved",
                "target_clause": None,
                "target_point": None,
                "target_scope": "unresolved_subarticle",
                "resolved": False,
                "unresolved_reason": doc_reason,
                "action": action,
                "source_text": chunk,
            })
            continue

        if "cum tu" in heading.lower():
            rels.append({
                "source_article": article["id"],
                "source_unit": source_unit_id,
                "target_doc": target_doc,
                "target_article": "unresolved",
                "target_clause": None,
                "target_point": None,
                "target_scope": "unresolved_subarticle",
                "resolved": False,
                "unresolved_reason": "text_phrase_replacement",
                "action": action,
                "source_text": chunk,
            })
            continue

        has_unresolved = bool(RE_UNRESOLVED_SUBARTICLE.search(heading)) or bool(re.search(
            r"\b(khoan|diem)\s+[a-z0-9]+\s*(?:,\s*(?:va\s+)?|(?:va|den)\s+)(?:\1\s+)?[a-z0-9]+\b"
            r"|\bkhoan\s+\d+[a-z]+\b",
            heading, re.IGNORECASE,
        ))
        is_insertion_anchor = bool(re.search(r"(?:sau|truoc)\s+(?:khoan|diem|dieu)\s+\w+", heading, re.IGNORECASE))

        targets = list(RE_AMEND_TARGET.finditer(heading))
        if not targets and re.search(r"\b(?:khoan|diem)\b", heading, re.IGNORECASE):
            targets = [None]
        for m in targets:
            pt_raw, kl_raw, dieu_raw = m.groups() if m else (None, None, None)
            code = f"Dieu {dieu_raw}" if dieu_raw else "unresolved"
            target_clause = f"Khoan {kl_raw}" if kl_raw else None
            target_point = f"Diem {pt_raw.lower()}" if pt_raw else None

            if is_insertion_anchor:
                target_scope, resolved, reason = "unresolved_subarticle", False, "insertion_anchor"
            elif has_unresolved or m is None or not dieu_raw.isdigit() or (target_point and not target_clause):
                target_scope, resolved, reason = "unresolved_subarticle", False, "unresolved_subarticle"
            elif target_point and target_clause:
                target_scope, resolved, reason = "point", True, None
            elif target_clause:
                target_scope, resolved, reason = "clause", True, None
            else:
                target_scope, resolved, reason = "article", True, None

            key = (source_unit_id, code, target_clause, target_point, target_scope, action)
            if key in seen:
                continue
            seen.add(key)
            rels.append({
                "source_article": article["id"],
                "source_unit": source_unit_id,
                "target_doc": target_doc,
                "target_article": code,
                "target_clause": target_clause,
                "target_point": target_point,
                "target_scope": target_scope,
                "resolved": resolved,
                "unresolved_reason": reason,
                "action": action,
                "source_text": chunk,
            })
    return rels
