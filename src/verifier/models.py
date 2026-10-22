from typing import Literal
from pydantic import BaseModel, Field

CitationState = Literal["VALID", "AMENDED", "NOT_YET_EFFECTIVE", "EXPIRED", "NOT_FOUND"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class CitationStatus(BaseModel):
    """Status details for an individual legal citation."""
    id: str
    status: CitationState
    exists: bool
    is_active: bool
    message: str
    amended_by: list[str] = Field(default_factory=list)


class VerificationReport(BaseModel):
    """Aggregated verification report for all citations extracted from response text."""
    as_of_date: str
    total_citations: int
    valid_citations: int
    citations: list[CitationStatus]
    is_clean: bool


class VerificationResult(BaseModel):
    """Complete verification outcome including calculated risk score and warnings."""
    as_of_date: str
    risk_score: int = Field(..., ge=0, le=100)
    risk_level: RiskLevel
    warnings: list[str]
    report: VerificationReport

