"""API request and response data models."""
import re

from pydantic import BaseModel, Field, field_validator

ALLOWED_PIPELINES = (
    "P3_FullSystem",
    "P2_TemporalGraphRAG",
    "P1_FlatRAG",
    "P3",
    "P2",
    "P1",
)


class QueryRequest(BaseModel):
    """Request payload for legal query retrieval and analysis."""

    query: str = Field(..., min_length=1, max_length=4000, description="Legal query text")
    as_of_date: str = Field(..., description="Reference point date in YYYY-MM-DD format")
    pipeline: str = Field(default="P3_FullSystem", description="Retrieval pipeline identifier")
    use_llm: bool = Field(default=False, description="Whether to invoke LLM generation")
    model: str = Field(default="qwen2.5:7b", description="LLM model identifier")
    history: list[dict] = Field(default_factory=list, description="Conversation history turns")

    @field_validator("as_of_date")
    @classmethod
    def validate_as_of_date(cls, v: str) -> str:
        if not isinstance(v, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            raise ValueError("as_of_date must be in YYYY-MM-DD format")
        year = int(v[:4])
        if year < 2000 or year > 2100:
            raise ValueError("as_of_date year must be between 2000 and 2100")
        return v

    @field_validator("pipeline")
    @classmethod
    def validate_pipeline(cls, v: str) -> str:
        if v not in ALLOWED_PIPELINES:
            raise ValueError(f"pipeline must be one of {ALLOWED_PIPELINES}")
        return v


class QueryResponse(BaseModel):
    """Response payload containing query answers, citations, and risk verification."""

    query: str = Field(..., description="Original user query")
    as_of_date: str = Field(..., description="Effective validation date")
    pipeline: str = Field(..., description="Pipeline execution identifier")
    citations: list[str] = Field(default_factory=list, description="Cited legal article IDs")
    active_article_ids: list[str] = Field(default_factory=list, description="Validated active article IDs")
    risk_score: int = Field(..., ge=0, le=100, description="Calculated risk score from 0 to 100")
    risk_level: str = Field(..., description="Categorical risk level (LOW, MEDIUM, HIGH, CRITICAL)")
    warnings: list[str] = Field(default_factory=list, description="List of verification warning messages")
    irac_sections: dict[str, str] = Field(..., description="Structured IRAC response sections")
    llm_used: bool = Field(default=False, description="Indicates whether LLM was used")
    raw_llm_response: str | None = Field(default=None, description="Raw generation output from LLM")
    is_out_of_domain: bool = Field(default=False, description="Whether query was outside VAT legal domain")
    is_greeting: bool = Field(default=False, description="Whether query was a conversational greeting")
    answer_status: str = Field(default="ANSWERED", description="Response status (ANSWERED, OUT_OF_SCOPE, INSUFFICIENT_BASIS)")


class DocumentSummary(BaseModel):
    """Summary metadata for a legal document in the knowledge base."""

    id: str = Field(..., description="Unique legal document identifier")
    code: str = Field(..., description="Official document code or number")
    title: str = Field(..., description="Official document title")
    valid_from: str = Field(..., description="Date document becomes effective")
    valid_to: str = Field(..., description="Date document expires or is superseded")


class HealthResponse(BaseModel):
    """Health check status response."""

    status: str = Field(..., description="API operational status")
    neo4j_connected: bool = Field(..., description="Neo4j database connection status")
    version: str = Field(default="1.0.0", description="API version")
