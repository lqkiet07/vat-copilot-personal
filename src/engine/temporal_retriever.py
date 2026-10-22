"""Temporal snapshot retrieval on Neo4j."""
from ..graph.connection import get_neo4j_driver
from .hybrid_retriever import _enrich_articles, query_hybrid_temporal
from .models import ArticleNode, QueryResult

_CANDIDATE_CYPHER = """
MATCH (art:Article)
WHERE art.valid_from <= date($as_of_date)
  AND art.valid_to >= date($as_of_date)
  AND toLower(art.content) CONTAINS toLower($keyword)
RETURN art.id AS id
LIMIT 25
"""


def query_temporal_snapshot(keyword: str, as_of_date: str) -> list[ArticleNode]:
    """Retrieve articles containing keyword valid as of given date."""
    driver = get_neo4j_driver()
    with driver.session() as session:
        rows = session.run(
            _CANDIDATE_CYPHER, keyword=keyword, as_of_date=as_of_date
        ).data()
        candidate_ids = [r["id"] for r in rows if r.get("id")]
        return _enrich_articles(session, candidate_ids, as_of_date)


def query_temporal_rag(query: str, as_of_date: str) -> QueryResult:
    """Primary retrieval interface using hybrid search."""
    return QueryResult(
        query=query,
        as_of_date=as_of_date,
        articles=query_hybrid_temporal(query, as_of_date),
    )

