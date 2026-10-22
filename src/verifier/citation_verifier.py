import re
from src.graph.connection import get_neo4j_driver
from src.verifier.models import CitationStatus, VerificationReport

_CITATION_REGEX = re.compile(r"\[([A-Z0-9_]+_D\d+(?:_K\d+)?(?:_[a-z0-9]+)?)\]")

_VERIFY_CYPHER = """
UNWIND $ids AS cid
OPTIONAL MATCH (n) WHERE (n:Article OR n:Clause OR n:Point) AND n.id = cid
OPTIONAL MATCH (a:Article)-[:CONTAINS*0..2]->(n)
OPTIONAL MATCH (amender:Article)-[:AMENDS]->(a)
  WHERE amender.valid_from <= date($as_of_date)
RETURN cid,
       n IS NOT NULL AS exists,
       toString(n.valid_from) AS valid_from,
       toString(n.valid_to) AS valid_to,
       [x IN collect(DISTINCT amender.id) WHERE x IS NOT NULL] AS amended_by
"""


def extract_citations(text: str) -> list[str]:
    """Extract citation IDs enclosed in brackets matching the canonical pattern."""
    return _CITATION_REGEX.findall(text)


def _evaluate_status(row: dict, as_of_date: str) -> CitationStatus:
    """Evaluate validity status of a single citation record against as_of_date."""
    cid = row["cid"]
    if not row["exists"]:
        return CitationStatus(
            id=cid, status="NOT_FOUND", exists=False, is_active=False,
            message=f"Không tìm thấy điều khoản {cid} trong cơ sở dữ liệu tri thức.",
            amended_by=[],
        )

    valid_from = row["valid_from"] or ""
    valid_to = row["valid_to"] or "9999-12-31"
    amended_by = row.get("amended_by") or []

    if as_of_date < valid_from:
        return CitationStatus(
            id=cid, status="NOT_YET_EFFECTIVE", exists=True, is_active=False,
            message=f"Điều khoản {cid} chưa có hiệu lực tại ngày {as_of_date} (hiệu lực từ {valid_from}).",
            amended_by=amended_by,
        )
    if as_of_date > valid_to:
        return CitationStatus(
            id=cid, status="EXPIRED", exists=True, is_active=False,
            message=f"Điều khoản {cid} đã hết hiệu lực trước ngày {as_of_date} (hết hiệu lực từ {valid_to}).",
            amended_by=amended_by,
        )
    if amended_by:
        return CitationStatus(
            id=cid, status="AMENDED", exists=True, is_active=True,
            message=f"Điều khoản {cid} đã bị sửa đổi/bổ sung bởi {', '.join(amended_by)}.",
            amended_by=amended_by,
        )
    return CitationStatus(
        id=cid, status="VALID", exists=True, is_active=True,
        message=f"Điều khoản {cid} có đầy đủ hiệu lực tại ngày {as_of_date}.",
        amended_by=[],
    )


def verify_citations(text: str, as_of_date: str) -> VerificationReport:
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
    statuses = [_evaluate_status(row_map[cid], as_of_date) for cid in citation_ids if cid in row_map]

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
    """Remove hallucinated or expired citations from text and clean up spacing."""
    invalid_ids = {c.id for c in report.citations if c.status in {"NOT_FOUND", "EXPIRED"}}
    if not invalid_ids:
        return text

    result = text
    for cid in invalid_ids:
        result = result.replace(f"[{cid}]", "")

    result = re.sub(r"[ \t]+([,.\?!;:])", r"\1", result)
    result = re.sub(r"[ \t]{2,}", " ", result)
    return result.strip()

