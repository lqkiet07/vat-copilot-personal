"""Request and response models for the VAT Copilot chat endpoints."""
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ChatRole = Literal["user", "assistant"]


class ChatMessage(BaseModel):
    """A single conversation turn exchanged with the VAT Copilot."""

    role: ChatRole
    content: str = Field(..., min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    """Payload accepted by the chat endpoints."""

    messages: list[ChatMessage] = Field(..., min_length=1)
    as_of_date: str | None = None
    model: str = "qwen2.5:7b"

    @field_validator("as_of_date")
    @classmethod
    def validate_as_of_date(cls, v: str | None) -> str | None:
        if v is not None:
            if not isinstance(v, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
                raise ValueError("as_of_date must be in YYYY-MM-DD format")
            year = int(v[:4])
            if year < 2000 or year > 2100:
                raise ValueError("as_of_date year must be between 2000 and 2100")
        return v


class ChatSource(BaseModel):
    """Legal article backing a chat answer."""

    id: str
    code: str
    title: str
    doc_id: str
    valid_from: str
    valid_to: str


class ChatResponse(BaseModel):
    """Final answer of the VAT Copilot including citation and risk metadata."""

    message: str
    citations: list[str] = Field(default_factory=list)
    sources: list[ChatSource] = Field(default_factory=list)
    risk_score: int = Field(..., ge=0, le=100)
    risk_level: str
    warnings: list[str] = Field(default_factory=list)
    llm_used: bool = False
    in_scope: bool = True
    answer_status: str = "ANSWERED"
    as_of_date: str
