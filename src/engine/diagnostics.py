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
    ANSWERED = "ANSWERED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


@dataclass(frozen=True)
class QueryTrace:
    query: str
    as_of_date: str
    pipeline: str
    raw_article_count: int = 0
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


def classify_empty(trace: QueryTrace) -> EmptyReason:
    if trace.answer_status == "OUT_OF_SCOPE":
        return EmptyReason.OUT_OF_SCOPE
    if trace.raw_article_count == 0:
        return EmptyReason.RETRIEVAL_MISS
    if trace.raw_article_count > 0 and trace.gated_article_count == 0:
        return EmptyReason.GATING_OVERREJECT
    if trace.llm_used and trace.prompt_article_count > 0 and trace.llm_citation_count == 0:
        return EmptyReason.LLM_REFUSED_WITH_EVIDENCE
    if trace.llm_used and (trace.irac_rule_empty or trace.irac_conclusion_empty):
        return EmptyReason.IRAC_PARSE_FAILED
    return EmptyReason.ANSWERED


def emit_trace(trace: QueryTrace) -> None:
    path_str = os.environ.get("VAT_DIAGNOSTICS")
    if not path_str:
        return
    try:
        path = Path(path_str)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(trace), default=str) + "\n"
        with _TRACE_LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass
