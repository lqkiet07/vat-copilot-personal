import re
from src.graph.connection import get_neo4j_driver
from src.verifier.models import CitationStatus, VerificationReport

_CITATION_REGEX = re.compile(r"\[([A-Z0-9_]+_D\d+(?:_K\d+)?(?:_[A-Za-z0-9_]+)?)\]", re.IGNORECASE)

_VERIFY_CYPHER = """
UNWIND $ids AS cid
OPTIONAL MATCH (n) WHERE (n:Article OR n:Clause OR n:Point) AND n.id = cid
OPTIONAL MATCH (a:Article)-[:CONTAINS*0..2]->(n)
OPTIONAL MATCH (amender:Article)-[:AMENDS]->(a)
  WHERE amender.valid_from <= date($as_of_date) AND amender.valid_to >= date($as_of_date)
RETURN cid,
       n IS NOT NULL AS exists,
       toString(n.valid_from) AS valid_from,
       toString(n.valid_to) AS valid_to,
       [x IN collect(DISTINCT amender.id) WHERE x IS NOT NULL] AS amended_by
"""


def _canonicalize_citation(cid: str) -> str:
    """Canonicalize citation ID to uppercase doc/article/clause, preserving lowercase point suffixes."""
    parts = re.split(r"_[kK]", cid, maxsplit=1)
    if len(parts) == 2:
        doc_art = parts[0].upper()
        clause_and_point = parts[1].split("_", 1)
        if len(clause_and_point) == 2:
            return f"{doc_art}_K{clause_and_point[0]}_{clause_and_point[1].lower()}"
        return f"{doc_art}_K{clause_and_point[0]}"
    return cid.upper()


def extract_citations(text: str) -> list[str]:
    """Extract citation IDs enclosed in brackets matching the canonical pattern."""
    return list(dict.fromkeys(_canonicalize_citation(cid) for cid in _CITATION_REGEX.findall(text)))


def _evaluate_status(
    row: dict,
    as_of_date: str,
    is_relevant: bool = True,
    in_context: bool = True,
) -> CitationStatus:
    """Evaluate validity status of a single citation record against as_of_date and relevance."""
    cid = row["cid"]
    if not in_context:
        return CitationStatus(
            id=cid,
            status="IRRELEVANT",
            exists=row["exists"],
            is_active=False,
            message=f"Điều khoản {cid} không nằm trong tài liệu căn cứ được cung cấp cho câu hỏi.",
            amended_by=[],
            in_context=False,
        )

    if not row["exists"]:
        return CitationStatus(
            id=cid, status="NOT_FOUND", exists=False, is_active=False,
            message=f"Không tìm thấy điều khoản {cid} trong cơ sở dữ liệu tri thức.",
            amended_by=[],
            in_context=True,
        )

    if not is_relevant:
        return CitationStatus(
            id=cid, status="IRRELEVANT", exists=True, is_active=False,
            message=f"Điều khoản {cid} không liên quan trực tiếp đến nội dung câu hỏi.",
            amended_by=[],
            in_context=True,
        )

    valid_from = row["valid_from"] or ""
    valid_to = row["valid_to"] or "9999-12-31"
    amended_by = row.get("amended_by") or []

    if as_of_date < valid_from:
        return CitationStatus(
            id=cid, status="NOT_YET_EFFECTIVE", exists=True, is_active=False,
            message=f"Điều khoản {cid} chưa có hiệu lực tại ngày {as_of_date} (hiệu lực từ {valid_from}).",
            amended_by=amended_by,
            in_context=True,
        )
    if as_of_date > valid_to:
        return CitationStatus(
            id=cid, status="EXPIRED", exists=True, is_active=False,
            message=f"Điều khoản {cid} đã hết hiệu lực trước ngày {as_of_date} (hết hiệu lực từ {valid_to}).",
            amended_by=amended_by,
            in_context=True,
        )
    if amended_by:
        return CitationStatus(
            id=cid, status="AMENDED", exists=True, is_active=True,
            message=f"Điều khoản {cid} đã bị sửa đổi/bổ sung bởi {', '.join(amended_by)}.",
            amended_by=amended_by,
            in_context=True,
        )
    return CitationStatus(
        id=cid, status="VALID", exists=True, is_active=True,
        message=f"Điều khoản {cid} có đầy đủ hiệu lực tại ngày {as_of_date}.",
        amended_by=[],
        in_context=True,
    )


def verify_citations(
    text: str,
    as_of_date: str,
    relevance_map: dict[str, bool] | None = None,
    context_ids: set[str] | None = None,
) -> VerificationReport:
    """Query Neo4j to verify status of all extracted citations as of given date."""
    citation_ids = extract_citations(text)
    if not citation_ids:
        return VerificationReport(
            as_of_date=as_of_date,
            total_citations=0,
            valid_citations=0,
            citations=[],
            is_clean=False,
        )

    driver = get_neo4j_driver()
    with driver.session() as session:
        records = session.run(_VERIFY_CYPHER, ids=citation_ids, as_of_date=as_of_date).data()

    row_map = {r["cid"]: r for r in records}
    statuses = []
    for cid in citation_ids:
        row = row_map.get(cid, {"cid": cid, "exists": False, "valid_from": None, "valid_to": None, "amended_by": []})
        in_ctx = True
        if context_ids is not None:
            in_ctx = (cid in context_ids) or (cid.split("_K")[0] in context_ids)

        is_rel = True
        if relevance_map is not None:
            if cid in relevance_map:
                is_rel = relevance_map[cid]
            else:
                art_id = cid.split("_K")[0]
                is_rel = relevance_map.get(art_id, True)

        statuses.append(_evaluate_status(row, as_of_date, is_relevant=is_rel, in_context=in_ctx))

    total = len(statuses)
    valid_count = sum(1 for s in statuses if s.status == "VALID")
    is_clean = total > 0 and valid_count == total

    return VerificationReport(
        as_of_date=as_of_date,
        total_citations=total,
        valid_citations=valid_count,
        citations=statuses,
        is_clean=is_clean,
    )


def prune_invalid_citations(text: str, report: VerificationReport) -> str:
    """Remove hallucinated, expired, or irrelevant citations from text and clean up spacing."""
    invalid_ids = {c.id for c in report.citations if c.status in {"NOT_FOUND", "EXPIRED", "IRRELEVANT"}}
    if not invalid_ids:
        return text

    result = text
    for cid in invalid_ids:
        result = re.sub(rf"\[{re.escape(cid)}\]", "", result, flags=re.IGNORECASE)

    result = re.sub(r"[ \t]+([,.\?!;:])", r"\1", result)
    result = re.sub(r"[ \t]{2,}", " ", result)
    return result.strip()

