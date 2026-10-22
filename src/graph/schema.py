"""Initialize constraints and indexes for temporal legal graph."""
from .connection import get_neo4j_driver

CONSTRAINTS = [
    ("unique_document_id", "Document", "id"),
    ("unique_chapter_id", "Chapter", "id"),
    ("unique_article_id", "Article", "id"),
    ("unique_clause_id", "Clause", "id"),
    ("unique_point_id", "Point", "id"),
]

RANGE_INDEXES = [
    ("article_valid_from", "Article", "valid_from"),
    ("article_valid_to", "Article", "valid_to"),
    ("clause_valid_from", "Clause", "valid_from"),
    ("clause_valid_to", "Clause", "valid_to"),
]


def init_schema():
    """Create constraints, range indexes, fulltext index, and vector index. Idempotent."""
    driver = get_neo4j_driver()
    with driver.session() as session:
        for name, label, prop in CONSTRAINTS:
            session.run(
                f"CREATE CONSTRAINT {name} IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
            )
        for name, label, prop in RANGE_INDEXES:
            session.run(
                f"CREATE RANGE INDEX {name} IF NOT EXISTS "
                f"FOR (n:{label}) ON (n.{prop})"
            )
        session.run(
            "CREATE FULLTEXT INDEX article_content_fulltext IF NOT EXISTS "
            "FOR (n:Article|Clause) ON EACH [n.content]"
        )
        session.run(
            "CREATE VECTOR INDEX article_embedding IF NOT EXISTS "
            "FOR (a:Article) ON (a.embedding) "
            "OPTIONS {indexConfig: {"
            "  `vector.dimensions`: 1024,"
            "  `vector.similarity_function`: 'cosine'"
            "}}"
        )
