"""Pydantic data models for temporal retrieval engine."""
from pydantic import BaseModel


class ArticleNode(BaseModel):
    """Article node with temporal validity and related references."""

    id: str
    doc_id: str
    code: str
    title: str
    content: str
    valid_from: str
    valid_to: str
    status: str = "ACTIVE"
    related_ids: list[str] = []


class QueryResult(BaseModel):
    """Container for retrieval query results."""

    query: str
    as_of_date: str
    articles: list[ArticleNode]
