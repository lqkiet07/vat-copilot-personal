from pydantic import BaseModel, Field


class BenchmarkItem(BaseModel):
    question_id: str
    question: str
    as_of_date: str
    category: str
    gold_citations: list[str]
    gold_atomic_facts: list[str]
    is_temporal_trap: bool = False


class BenchmarkMetrics(BaseModel):
    precision: float = Field(default=0.0, ge=0.0, le=1.0)
    recall: float = Field(default=0.0, ge=0.0, le=1.0)
    temporal_accuracy: float = Field(default=0.0, ge=0.0, le=1.0)
    hallucination_rate: float = Field(default=0.0, ge=0.0, le=1.0)


class PipelineRunResult(BaseModel):
    question_id: str
    pipeline: str
    predicted_citations: list[str] = Field(default_factory=list)
    metrics: BenchmarkMetrics | None = None
    as_of_date: str = ""


class AblationReport(BaseModel):
    total_questions: int
    pipelines: list[str]
    metrics_summary: dict[str, BenchmarkMetrics] = Field(default_factory=dict)
    runs: list[PipelineRunResult] = Field(default_factory=list)
