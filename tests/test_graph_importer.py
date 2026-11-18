from pathlib import Path

import pytest
from src.graph.connection import get_neo4j_driver
from tests.conftest import expected_graph_snapshot, read_graph_snapshot

pytestmark = pytest.mark.requires_neo4j


@pytest.mark.usefixtures("isolated_graph")
def test_neo4j_import_and_constraints():
    from src.graph.importer import import_corpus_to_neo4j
    from src.graph.schema import init_schema

    init_schema()
    stats = import_corpus_to_neo4j("data/processed/vat_corpus.json")
    assert stats["articles"] >= 70, f"Qua it Dieu: {stats['articles']}"

    driver = get_neo4j_driver()
    with driver.session() as session:
        names = [c["name"] for c in session.run("SHOW CONSTRAINTS").data()]
        assert any("rticle" in n for n in names), f"Thieu constraint Article: {names}"
        total = session.run("MATCH (a:Article) RETURN count(a) AS t").single()["t"]
        assert total == stats["articles"]
        idx = [i["name"] for i in session.run("SHOW INDEXES").data()]
        assert any("valid_from" in n for n in idx), f"Thieu range index: {idx}"

        chapter_count = session.run("MATCH (c:Chapter) RETURN count(c) AS count").single()["count"]
        assert chapter_count > 0, "No Chapter nodes found in Neo4j"

        missing_status = session.run(
            "MATCH (a:Article) WHERE a.status IS NULL RETURN count(a) AS c"
        ).single()["c"]
        assert missing_status == 0, f"Found {missing_status} Article nodes without status"

        doc_count = session.run("MATCH (d:Document) RETURN count(d) AS count").single()["count"]
        assert doc_count > 0, "No Document nodes found in Neo4j"
        missing_issue_date = session.run(
            "MATCH (d:Document) WHERE d.issue_date IS NULL RETURN count(d) AS c"
        ).single()["c"]
        assert missing_issue_date == 0, f"Found {missing_issue_date} Document nodes without issue_date"


@pytest.mark.usefixtures("isolated_graph")
def test_staging_corpus_parity_and_temporal_replay_in_neo4j():
    from src.graph.importer import import_amends_relations, import_corpus_to_neo4j, plan_corpus_import

    staging_path = Path(__file__).resolve().parents[1] / "data/processed/vat_corpus.staging.json"
    plan = plan_corpus_import(str(staging_path))
    assert plan["stats"]["documents"] == 33
    expected = expected_graph_snapshot(staging_path)
    driver = get_neo4j_driver()
    for _ in range(2):
        stats = import_corpus_to_neo4j(str(staging_path))
        import_amends_relations(str(staging_path))
        assert stats == plan["stats"]
        assert read_graph_snapshot(driver) == expected
    _assert_temporal_replay(driver)


def _assert_temporal_replay(driver):
    with driver.session() as session:
        d17_active_2025 = session.run(
            "MATCH (a:Article {id: 'LUAT_48_2024_D17'}) "
            "WHERE a.valid_from <= date('2025-07-01') AND a.valid_to >= date('2025-07-01') "
            "RETURN count(a) AS c"
        ).single()["c"]
        assert d17_active_2025 == 0
        l09_2026_01 = session.run(
            "MATCH (a:Article {doc_id: 'LUAT_09_2026'}) "
            "WHERE a.valid_from <= date('2026-01-01') AND a.valid_to >= date('2026-01-01') "
            "RETURN collect(a.id) AS ids"
        ).single()["ids"]
        assert set(l09_2026_01) == {"LUAT_09_2026_D1", "LUAT_09_2026_D2", "LUAT_09_2026_D3"}
        nd144_mar = session.run(
            "MATCH (d:Document {id: 'ND_144_2026'}) "
            "WHERE d.valid_from <= date('2026-03-20') AND d.valid_to >= date('2026-03-20') "
            "RETURN count(d) AS c"
        ).single()["c"]
        nd144_jun = session.run(
            "MATCH (d:Document {id: 'ND_144_2026'}) "
            "WHERE d.valid_from <= date('2026-06-20') AND d.valid_to >= date('2026-06-20') "
            "RETURN count(d) AS c"
        ).single()["c"]
        assert nd144_mar == 0
        assert nd144_jun == 1
