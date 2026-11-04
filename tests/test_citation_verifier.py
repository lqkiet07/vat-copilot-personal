import pytest
from src.graph.connection import get_neo4j_driver
from src.verifier.citation_verifier import (
    extract_citations,
    prune_invalid_citations,
    verify_citations,
)

pytestmark = pytest.mark.requires_neo4j


@pytest.fixture(scope="module", autouse=True)
def setup_test_point():
    """Ensure test point node exists in Neo4j hierarchy."""
    driver = get_neo4j_driver()
    with driver.session() as session:
        session.run(
            """
            MATCH (a:Article {id: 'LUAT_48_2024_D1'})
            MERGE (c:Clause {id: 'LUAT_48_2024_D1_K2'})
              ON CREATE SET c.valid_from = date('2025-07-01'), c.valid_to = date('9999-12-31'), c.code = 'Khoan 2'
            MERGE (a)-[:CONTAINS]->(c)
            MERGE (p:Point {id: 'LUAT_48_2024_D1_K2_a'})
              ON CREATE SET p.valid_from = date('2025-07-01'), p.valid_to = date('9999-12-31'), p.code = 'Diem a'
            MERGE (c)-[:CONTAINS]->(p)
            MERGE (base:Article {id: 'LUAT_999_2024_D1'})
              ON CREATE SET base.valid_from = date('2025-01-01'), base.valid_to = date('9999-12-31'), base.code = 'D1'
            MERGE (amender:Article {id: 'ND_999_2025_D1'})
              ON CREATE SET amender.valid_from = date('2025-01-01'), amender.valid_to = date('2025-12-31'), amender.code = 'D1'
            MERGE (amender)-[:AMENDS]->(base)
            """
        )
    yield
    with driver.session() as session:
        session.run("MATCH (a:Article) WHERE a.id IN ['LUAT_999_2024_D1', 'ND_999_2025_D1'] DETACH DELETE a")
        session.run("MATCH (p:Point {id: 'LUAT_48_2024_D1_K2_a'}) DETACH DELETE p")
        session.run("MATCH (c:Clause {id: 'LUAT_48_2024_D1_K2'}) DETACH DELETE c")


def test_extract_citations():
    text = (
        "Theo quy định tại [LUAT_48_2024_D9] và hướng dẫn tại [ND_181_2025_D9_K1], "
        "ngoài ra còn có căn cứ theo [LUAT_48_2024_D5]."
    )
    citations = extract_citations(text)
    assert citations == ["LUAT_48_2024_D9", "ND_181_2025_D9_K1", "LUAT_48_2024_D5"]


def test_extract_citations_deduplicates_preserving_order():
    raw_text = "Căn cứ [LUAT_48_2024_D4] và Điều 4 [LUAT_48_2024_D4], đối chiếu [LUAT_48_2024_D5] rồi lại [LUAT_48_2024_D4]."
    cites = extract_citations(raw_text)
    assert cites == ["LUAT_48_2024_D4", "LUAT_48_2024_D5"]


def test_verify_valid_citation():
    text = "Áp dụng căn cứ [LUAT_48_2024_D9] để tính thuế giá trị gia tăng."
    report = verify_citations(text=text, as_of_date="2026-04-01")

    assert report.as_of_date == "2026-04-01"
    assert report.total_citations == 1
    assert report.valid_citations == 1
    assert report.is_clean is True
    assert len(report.citations) == 1

    c = report.citations[0]
    assert c.id == "LUAT_48_2024_D9"
    assert c.status == "VALID"
    assert c.exists is True
    assert c.is_active is True
    assert c.amended_by == []


def test_verify_valid_point_citation():
    text = "Theo quy định tại điểm a khoản 2 [LUAT_48_2024_D1_K2_a] về phạm vi đối tượng."
    report = verify_citations(text=text, as_of_date="2026-04-01")

    assert report.as_of_date == "2026-04-01"
    assert report.total_citations == 1
    assert report.valid_citations == 1
    assert report.is_clean is True
    assert len(report.citations) == 1

    c = report.citations[0]
    assert c.id == "LUAT_48_2024_D1_K2_a"
    assert c.status == "VALID"
    assert c.exists is True
    assert c.is_active is True


def test_verify_not_yet_effective_citation():
    text = "Căn cứ theo [LUAT_48_2024_D9] vào ngày 2025-05-01."
    report = verify_citations(text=text, as_of_date="2025-05-01")

    assert report.as_of_date == "2025-05-01"
    assert report.total_citations == 1
    assert report.valid_citations == 0
    assert report.is_clean is False
    assert len(report.citations) == 1

    c = report.citations[0]
    assert c.id == "LUAT_48_2024_D9"
    assert c.status == "NOT_YET_EFFECTIVE"
    assert c.exists is True
    assert c.is_active is False


def test_verify_amended_citation():
    text = "Căn cứ theo [ND_181_2025_D9] vào ngày 2026-04-01."
    report = verify_citations(text=text, as_of_date="2026-04-01")

    assert report.as_of_date == "2026-04-01"
    assert report.total_citations == 1
    assert report.valid_citations == 0
    assert report.is_clean is False
    assert len(report.citations) == 1

    c = report.citations[0]
    assert c.id == "ND_181_2025_D9"
    assert c.status == "AMENDED"
    assert c.exists is True
    assert c.is_active is True
    assert any("ND_144_2026" in amender for amender in c.amended_by)


def test_verify_nonexistent_citation():
    text = "Viện dẫn điều không tồn tại [LUAT_48_2024_D999]."
    report = verify_citations(text=text, as_of_date="2026-04-01")

    assert report.as_of_date == "2026-04-01"
    assert report.total_citations == 1
    assert report.valid_citations == 0
    assert report.is_clean is False
    assert len(report.citations) == 1

    c = report.citations[0]
    assert c.id == "LUAT_48_2024_D999"
    assert c.status == "NOT_FOUND"
    assert c.exists is False
    assert c.is_active is False


def test_verify_mixed_citations():
    text = "Hợp lệ [LUAT_48_2024_D9] nhưng sửa đổi [ND_181_2025_D9]."
    report = verify_citations(text=text, as_of_date="2026-04-01")

    assert report.total_citations == 2
    assert report.valid_citations == 1
    assert report.is_clean is False
    statuses = {c.id: c.status for c in report.citations}
    assert statuses["LUAT_48_2024_D9"] == "VALID"
    assert statuses["ND_181_2025_D9"] == "AMENDED"


def test_prune_invalid_citations():
    text = "Hợp lệ [LUAT_48_2024_D9] nhưng viện dẫn sai [LUAT_48_2024_D999]."
    report = verify_citations(text=text, as_of_date="2026-04-01")

    pruned = prune_invalid_citations(text, report)
    assert "[LUAT_48_2024_D999]" not in pruned
    assert "[LUAT_48_2024_D9]" in pruned


def test_extract_and_verify_citations_lowercase_canonicalized():
    text = "Theo quy định tại [luat_48_2024_d9] áp dụng vào ngày 2026-04-01."
    extracted = extract_citations(text)
    assert extracted == ["LUAT_48_2024_D9"]

    report = verify_citations(text, as_of_date="2026-04-01")
    assert report.total_citations == 1
    assert report.valid_citations == 1
    assert report.citations[0].id == "LUAT_48_2024_D9"
    assert report.citations[0].status == "VALID"


def test_verify_citations_outside_context_ids_marked_irrelevant():
    text = "Căn cứ theo [LUAT_48_2024_D9] và [LUAT_48_2024_D1_K2_a]."
    report = verify_citations(
        text=text,
        as_of_date="2026-04-01",
        context_ids={"LUAT_48_2024_D1"},
    )
    statuses = {c.id: c for c in report.citations}
    d9 = statuses["LUAT_48_2024_D9"]
    assert d9.status == "IRRELEVANT"
    assert d9.in_context is False
    assert d9.message == "Điều khoản LUAT_48_2024_D9 không nằm trong tài liệu căn cứ được cung cấp cho câu hỏi."

    d1 = statuses["LUAT_48_2024_D1_K2_a"]
    assert d1.status == "VALID"
    assert d1.in_context is True


def test_verify_citations_expired_amender_not_returned():
    # Active on 2025-06-01: amender valid_to (2025-12-31) >= 2025-06-01
    report_active = verify_citations("Theo [LUAT_999_2024_D1]", as_of_date="2025-06-01")
    assert report_active.citations[0].status == "AMENDED"
    assert "ND_999_2025_D1" in report_active.citations[0].amended_by

    # Expired on 2026-04-01: amender valid_to (2025-12-31) < 2026-04-01
    report_expired = verify_citations("Theo [LUAT_999_2024_D1]", as_of_date="2026-04-01")
    assert report_expired.citations[0].status == "VALID"
    assert report_expired.citations[0].amended_by == []


