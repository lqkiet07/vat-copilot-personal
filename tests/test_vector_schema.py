from src.graph.connection import get_neo4j_driver
from src.graph.importer import import_article_embeddings, import_corpus_to_neo4j
from src.graph.schema import init_schema


def test_vector_index_exists_in_neo4j():
    init_schema()
    driver = get_neo4j_driver()
    with driver.session() as session:
        indexes = session.run("SHOW INDEXES YIELD name, type WHERE type = 'VECTOR'").data()
        names = [idx["name"] for idx in indexes]
        assert "article_embedding" in names


def test_import_article_embeddings():
    init_schema()
    import_corpus_to_neo4j("data/processed/vat_corpus.json")
    dummy_vec = [0.0] * 1024
    dummy_vec[0] = 1.0
    updated = import_article_embeddings({"LUAT_48_2024_D1": dummy_vec})
    assert updated >= 1

    driver = get_neo4j_driver()
    with driver.session() as session:
        record = session.run(
            "MATCH (a:Article {id: 'LUAT_48_2024_D1'}) RETURN a.embedding AS emb"
        ).single()
        assert record is not None
        emb = record["emb"]
        assert emb is not None
        assert len(emb) == 1024
