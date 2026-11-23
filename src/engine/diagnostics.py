"""Diagnostics and query trace instrumentation module for root-cause classification."""
from dataclasses import asdict, dataclass
from enum import Enum
import json
import os
from pathlib import Path
import threading

_TRACE_LOCK = threading.Lock()


class EmptyReason(str, Enum):
    TEMPORAL_TRAP = "TEMPORAL_TRAP"
    RETRIEVAL_MISS = "RETRIEVAL_MISS"
    GATING_OVERREJECT = "GATING_OVERREJECT"
    LLM_REFUSED_WITH_EVIDENCE = "LLM_REFUSED_WITH_EVIDENCE"
    IRAC_PARSE_FAILED = "IRAC_PARSE_FAILED"
    DRAFT_REPLACED = "DRAFT_REPLACED"
    INSUFFICIENT_BASIS = "INSUFFICIENT_BASIS"
    ANSWERED = "ANSWERED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


@dataclass(frozen=True)
class QueryTrace:
    query: str
    as_of_date: str
    pipeline: str
    raw_article_count: int | None = 0
    gated_article_count: int = 0
    prompt_article_count: int = 0
    context_article_count: int = 0
    citation_in_context_count: int = 0
    llm_used: bool = False
    llm_citation_count: int = 0
    irac_rule_empty: bool = False
    irac_conclusion_empty: bool = False
    answer_status: str = "ANSWERED"
    empty_reason: EmptyReason = EmptyReason.ANSWERED
    pipeline_note: str | None = None
    generation_disposition: str = "deterministic_off"
    generation_reason: str = "llm_disabled"
    generation_attempted: bool = False
    generated_delivered: bool = False
    event: str = "query"
    prompt_sha256: str | None = None
    output_sha256: str | None = None
    delivered_sha256: str | None = None
    assessment_prompt_sha256: str | None = None
    assessment_output_sha256: str | None = None
    assessment_call_count: int = 0
    assessment_claim_count: int = 0
    assessment_outcome: str = "unknown"
    assessment_coverage: bool = False
    assessment_reason: str = "not_attempted"
    llm_total_budget_seconds: float | None = None
    generation_budget_seconds: float | None = None
    assessment_budget_seconds: float | None = None
    assessment_timeout_seconds: float | None = None
    local_num_ctx: int | None = None
    all_blockers: dict[str, str | tuple[str, ...]] | None = None
    selected_evidence_count: int = 0
    omitted_evidence_ids: tuple[str, ...] = ()
    evidence_pack_reason: str | None = None
    prompt_budget_method: str | None = None
    prompt_utf8_bytes: int | None = None
    prompt_input_limit: int | None = None
    assessment_budget_method: str | None = None
    assessment_prompt_utf8_bytes: int | None = None
    assessment_input_limit: int | None = None


def classify_empty(trace: QueryTrace) -> EmptyReason:
    if trace.answer_status == "OUT_OF_SCOPE":
        return EmptyReason.OUT_OF_SCOPE
    if trace.raw_article_count == 0 and trace.gated_article_count == 0:
        return EmptyReason.RETRIEVAL_MISS
    if trace.raw_article_count is not None and trace.raw_article_count > 0 and trace.gated_article_count == 0:
        return EmptyReason.GATING_OVERREJECT
    if trace.generation_disposition == "draft_replaced":
        return EmptyReason.DRAFT_REPLACED
    if trace.answer_status == "INSUFFICIENT_BASIS":
        return EmptyReason.INSUFFICIENT_BASIS
    if trace.generated_delivered and trace.answer_status == "ANSWERED":
        return EmptyReason.ANSWERED
    if trace.llm_used and trace.prompt_article_count > 0 and trace.llm_citation_count == 0:
        return EmptyReason.LLM_REFUSED_WITH_EVIDENCE
    if trace.llm_used and (trace.irac_rule_empty or trace.irac_conclusion_empty):
        return EmptyReason.IRAC_PARSE_FAILED
    return EmptyReason.ANSWERED if trace.answer_status == "ANSWERED" else EmptyReason.INSUFFICIENT_BASIS


def text_sha256(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def emit_trace(trace: QueryTrace | dict) -> None:
    path_str = os.environ.get("VAT_DIAGNOSTICS")
    if not path_str:
        return
    try:
        path = Path(path_str)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(trace if isinstance(trace, dict) else asdict(trace), default=str) + "\n"
        with _TRACE_LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass
