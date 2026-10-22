from src.graph.connection import get_neo4j_driver
from src.graph.importer import import_amends_relations, import_corpus_to_neo4j
from src.graph.schema import init_schema


def test_amends_relations_in_neo4j():
    init_schema()
    import_corpus_to_neo4j("data/processed/vat_corpus.json")
    created = import_amends_relations("data/processed/vat_corpus.json")
    assert created > 0, "Khong co quan he AMENDS nao duoc tao"

    driver = get_neo4j_driver()
    with driver.session() as session:
        total = session.run(
            "MATCH (:Article)-[r:AMENDS]->(:Article) RETURN count(r) AS c"
        ).single()["c"]
        assert total == created
        nd144 = session.run(
            "MATCH (a:Article {doc_id: 'ND_144_2026'})-[r:AMENDS]->(b:Article {doc_id: 'ND_181_2025'}) "
            "RETURN count(r) AS c"
        ).single()["c"]
        assert nd144 > 0, "Thieu AMENDS tu ND 144 sang ND 181"


def test_guides_relations_in_neo4j():
    from src.graph.importer import import_guides_relations

    created = import_guides_relations()
    assert created == 3, f"GUIDES doc-level phai co 3 canh, duoc {created}"

    driver = get_neo4j_driver()
    with driver.session() as session:
        total = session.run(
            "MATCH (:Document)-[r:GUIDES]->(:Document) RETURN count(r) AS c"
        ).single()["c"]
        assert total == 3
        row = session.run(
            "MATCH (d:Document {id: 'ND_181_2025'})-[r:GUIDES]->(l:Document {id: 'LUAT_48_2024'}) "
            "RETURN count(*) AS c"
        ).single()["c"]
        assert row == 1, "Thieu GUIDES ND 181 -> Luat 48"


def test_cites_relations_in_neo4j():
    from src.graph.importer import import_cites_relations

    created = import_cites_relations("data/processed/vat_corpus.json")
    assert created >= 15, f"CITES qua it: {created}"

    driver = get_neo4j_driver()
    with driver.session() as session:
        total = session.run(
            "MATCH (:Clause)-[r:CITES]->(:Article) RETURN count(r) AS c"
        ).single()["c"]
        assert total == created
        cross = session.run(
            "MATCH (c:Clause)-[r:CITES]->(a:Article) "
            "WHERE c.doc_id <> a.doc_id RETURN count(r) AS c"
        ).single()["c"]
        assert cross > 0, "Thieu CITES lien van ban"
