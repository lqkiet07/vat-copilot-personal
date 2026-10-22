from src.graph.connection import get_neo4j_driver


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
