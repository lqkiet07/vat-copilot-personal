"""Canonical answer service for VAT legal QA and IRAC orchestration."""
from dataclasses import asdict, dataclass, field
import re
from typing import Any

from src.api.chat_domain import normalize_query, rewrite_query, understand_query
from src.engine.diagnostics import EmptyReason, QueryTrace, classify_empty, emit_trace, text_sha256
from src.engine.llm_client import generate_llm_response
from src.engine.models import ArticleNode
from src.engine.prompt import build_irac_prompt
from src.engine.retrieval_service import _DEFAULT_CORPUS_PATH, _invoke_callback, retrieve_and_gate
from src.engine.temporal_retriever import query_temporal_rag
from src.ui.corpus_viewer import load_corpus_index, load_corpus_tree
from src.verifier.citation_verifier import extract_citations
from src.verifier.guardrails import check_query_domain
from src.verifier.relevance import build_relevance_map, score_article_relevance
from src.verifier.risk_score import verify_and_score

_STOPWORDS = {
    "mức", "đối", "với", "là", "bao", "nhiêu", "của", "và", "các", "cho",
    "tại", "năm", "tháng", "ngày", "được", "theo", "quy", "định", "thuế",
    "gtgt", "tôi", "thì", "có", "phải", "không", "tiền",
}


@dataclass
class AnswerResult:
    query: str
    as_of_date: str
    pipeline: str
    citations: list[str]
    active_article_ids: list[str]
    risk_score: int
    risk_level: str
    warnings: list[str]
    irac_sections: dict[str, str]
    llm_used: bool
    raw_llm_response: str | None
    answer_status: str
    is_out_of_domain: bool = False
    is_greeting: bool = False
    articles: list[ArticleNode] = field(default_factory=list)
    resolved_evidence: Any = None

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "as_of_date": self.as_of_date,
            "pipeline": self.pipeline,
            "citations": self.citations,
            "active_article_ids": self.active_article_ids,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "warnings": self.warnings,
            "irac_sections": self.irac_sections,
            "llm_used": self.llm_used,
            "raw_llm_response": self.raw_llm_response,
            "answer_status": self.answer_status,
            "is_out_of_domain": self.is_out_of_domain,
            "is_greeting": self.is_greeting,
        }


def _extract_citation_evidence(
    query: str, citations: list[str], articles: list[ArticleNode] | None = None,
    *, corpus_path: str = _DEFAULT_CORPUS_PATH,
) -> list[tuple[str, str, str]]:
    """Extract matching legal provisions from cited articles based on query terms and entities."""
    corpus = load_corpus_index(corpus_path) if not articles else {
        art.id: {"text": art.content, "code": art.code, "title": art.title}
        for art in articles
    }
    q_norm = normalize_query(query).lower()
    entities = understand_query(query)
    raw_words = re.findall(r"\w+", q_norm)
    words = [w for w in raw_words if w not in _STOPWORDS and len(w) > 1]

    evidence_items = []
    for cid in citations:
        meta = corpus.get(cid)
        if not meta or not meta.get("text"):
            continue
        lines = meta["text"].split("\n")
        best_line = ""
        best_score = -1

        for line in lines:
            line_clean = line.strip()
            if not line_clean:
                continue
            line_l = line_clean.lower()
            score = sum(3 for w in words if w in line_l)
            if any(s in line_l for s in entities.industry_synonyms):
                score += 15
            for n in (4, 3, 2):
                for i in range(len(raw_words) - n + 1):
                    phrase = " ".join(raw_words[i : i + n])
                    if phrase in line_l and any(w not in _STOPWORDS for w in raw_words[i : i + n]):
                        score += 8 * n
            if score > best_score:
                best_score = score
                best_line = line_clean

        if not best_line and lines:
            best_line = lines[0].strip()

        if best_line:
            evidence_items.append((cid, f"{meta.get('code', '')} ({meta.get('title', '')})", best_line))

    return evidence_items


def _extract_best_clause(query: str, citations: list[str]) -> str | None:
    """Extract the most relevant clause text from cited articles matching query terms."""
    items = _extract_citation_evidence(query, citations)
    if items:
        cid, label, line = items[0]
        return f"Theo {label}: {line}"
    return None


def _build_irac_sections(
    query: str,
    as_of_date: str,
    citations: list[str],
    warnings: list[str],
    articles: list[ArticleNode] | None = None,
    *, corpus_path: str = _DEFAULT_CORPUS_PATH,
) -> dict[str, str]:
    """Build structured IRAC sections dynamically from verified citation evidence."""
    rule_str = ", ".join(f"[{c}]" for c in citations) if citations else "Không có căn cứ hiệu lực"
    issue = f"Xác định quy định pháp luật thuế GTGT áp dụng cho: '{query}' tại mốc {as_of_date}."
    rule = rule_str

    evidence_items = _extract_citation_evidence(query, citations, articles=articles, corpus_path=corpus_path)

    if evidence_items:
        app_lines = [f"Theo các quy định pháp luật thuế GTGT có hiệu lực tại mốc {as_of_date}:"]
        for cid, label, text in evidence_items:
            app_lines.append(f"• Căn cứ [{cid}] {label}: {text}")
        if warnings:
            app_lines.append("\nCảnh báo hiệu lực: " + "; ".join(warnings))
        app = "\n".join(app_lines)

        conc = f"Các tài liệu {rule_str} đã được truy xuất tại mốc {as_of_date}, nhưng chưa đủ căn cứ để xác định nghĩa vụ thuế cụ thể cho tình huống này."
    elif warnings:
        warn_text = "; ".join(warnings)
        app = f"Tại mốc {as_of_date}, các quy định viện dẫn có cảnh báo sau: {warn_text}"
        conc = f"Cần thận trọng: Phát hiện rủi ro thời gian hoặc hiệu lực pháp lý đối với câu hỏi tại ngày {as_of_date}."
    else:
        app = f"Tại mốc {as_of_date}, chưa có trích đoạn chứng cứ đủ để áp dụng."
        conc = "Chưa đủ căn cứ pháp lý để kết luận cho tình huống này."

    return {
        "issue": issue,
        "rule": rule,
        "application": app,
        "conclusion": conc,
    }


def _check_temporal_trap(
    query: str, as_of_date: str, corpus_tree_fn: Any = None,
    *, corpus_path: str = _DEFAULT_CORPUS_PATH,
) -> list[str]:
    """Check if query references a legal document not yet effective at as_of_date."""
    docs = _invoke_callback(corpus_tree_fn or load_corpus_tree, corpus_path=corpus_path)
    q_norm = query.lower()
    warnings = []
    for doc in docs:
        doc_code = doc.get("code", "").lower()
        title = doc.get("title", "")
        valid_from = doc.get("valid_from", "")
        num_code = doc_code.split("/")[0] if "/" in doc_code else doc_code
        match = (
            doc_code in q_norm
            or (num_code and f"luật {num_code}" in q_norm)
            or (num_code and f"luat {num_code}" in q_norm)
            or (num_code and f"nghị định {num_code}" in q_norm)
            or (num_code and f"thông tư {num_code}" in q_norm)
            or (num_code and f"{num_code}/20" in q_norm)
        )
        if match and valid_from and as_of_date < valid_from:
            warnings.append(
                f"Văn bản {title} ({doc.get('code')}) chưa có hiệu lực tại ngày {as_of_date} (có hiệu lực từ ngày {valid_from})."
            )
    return warnings


def _parse_irac_sections(raw_text: str) -> dict[str, str]:
    """Parse raw LLM response into IRAC sections."""
    sections: dict[str, str] = {
        "issue": "",
        "rule": "",
        "application": "",
        "conclusion": "",
        "raw_text": raw_text,
    }
    labels = {
        "issue": ("issue", "vấn đề"),
        "rule": ("rule", "quy định", "quy định pháp luật", "cơ sở pháp lý"),
        "application": ("application", "áp dụng", "phân tích"),
        "conclusion": ("conclusion", "kết luận"),
    }
    aliases = {label: key for key, names in labels.items() for label in names}
    current_key: str | None = None
    for line in raw_text.splitlines():
        heading, separator, body = line.strip().lstrip("- ").partition(":")
        heading = re.sub(r"[*_`#]", "", heading).strip()
        heading = re.sub(r"^(?:\d+|[IVXLCDM]+)[.)\-:]\s*", "", heading, flags=re.I)
        body = body.lstrip("*_` ")
        names = [name.strip().casefold() for name in re.split(r"[()]", heading) if name.strip()]
        keys = {aliases.get(name) for name in names}
        if names and None not in keys and len(keys) == 1:
            current_key = keys.pop()
            sections[current_key] = body.strip() if separator else ""
        elif current_key and line.strip():
            sections[current_key] = (sections[current_key] + " " + line.strip()).strip()
    return sections


def _needs_irac_fallback(raw_response: str | None, irac: dict[str, str]) -> bool:
    """Return True if raw response is empty or missing essential IRAC sections."""
    if not (raw_response or "").strip():
        return True
    return any(not re.search(r"\w", irac.get(key, ""))
               for key in ("issue", "rule", "application", "conclusion"))


def _is_llm_answer_unsupported(
    raw_response: str,
    irac: dict[str, str],
    prompt_articles: list[ArticleNode],
    extracted: list[str],
    llm_citations: list[str],
    invalid_or_irrelevant: set[str],
    is_household: bool = False,
) -> bool:
    """Return True if LLM answer lacks valid evidence, relies on invalid citations, or makes unsupported claims."""
    rule_text = irac.get("rule", "")
    conc_text = irac.get("conclusion", "")

    # 1. Any invalid or irrelevant citation is referenced in rule or conclusion
    for bad_id in invalid_or_irrelevant:
        if bad_id in rule_text or bad_id in conc_text:
            return True

    # 2. If evidence was provided in prompt, but LLM cited 0 valid citations:
    if prompt_articles and not llm_citations:
        lower_resp = raw_response.lower()
        if not any(p in lower_resp for p in ("không thuộc phạm vi", "không có quy phạm", "không có căn cứ")):
            return True

    return False


def _fetch_pipeline_articles(
    pipeline: str,
    query: str,
    as_of_date: str,
    trace: dict | None = None,
    retriever_fn: Any = None,
    verifier_fn: Any = None,
    *, corpus_path: str = _DEFAULT_CORPUS_PATH,
) -> tuple[list[str], list[str], list[ArticleNode]]:
    """Retrieve citations, active IDs, and articles for the selected pipeline."""
    res = retrieve_and_gate(
        query,
        as_of_date,
        top_k=5,
        pipeline=pipeline,
        retriever_fn=retriever_fn or query_temporal_rag,
        verifier_fn=verifier_fn,
        corpus_path=corpus_path,
    )
    if trace is not None:
        trace["raw_article_count"] = res.raw_article_count
        trace["gated_article_count"] = res.gated_article_count
        if getattr(res, "unresolved_citations", None):
            trace["unresolved_citations"] = res.unresolved_citations
            trace["unresolved_warnings"] = getattr(res, "unresolved_warnings", [])
        if pipeline == "P1_FlatRAG":
            trace["pipeline_note"] = "flat_rag_no_article_objects"
    return res.active_ids, res.active_ids, res.articles


def _build_candidate_relevance_map(
    query: str, raw_response: str, candidate_citations: list[str], articles: list[ArticleNode],
    *, corpus_path: str = _DEFAULT_CORPUS_PATH,
) -> dict[str, bool]:
    """Build and enrich relevance map for citations extracted from LLM response."""
    rel_map = build_relevance_map(query, articles) if articles else {}
    export_markers = ("xuất khẩu", "xuat khau", "0%", "nước ngoài", "nuoc ngoai", "phi thuế quan")
    has_export_query = any(m in query.lower() for m in export_markers)

    for cid in candidate_citations:
        base_id = cid.split("_K")[0]
        if base_id in rel_map or cid in rel_map:
            continue
        corpus_meta = load_corpus_index(corpus_path).get(base_id)
        if not corpus_meta:
            rel_map[cid] = rel_map[base_id] = False
            continue
        art_node = ArticleNode(
            id=base_id,
            doc_id=corpus_meta.get("doc_id", ""),
            code=corpus_meta.get("code", ""),
            title=corpus_meta.get("title", ""),
            content=corpus_meta.get("text", ""),
            valid_from=str(corpus_meta.get("valid_from", "")),
            valid_to=str(corpus_meta.get("valid_to", "9999-12-31")),
        )
        is_rel = score_article_relevance(query, art_node)
        rel_map[cid] = rel_map[base_id] = is_rel
    return rel_map


def answer_query(
    question: str,
    as_of_date: str,
    history: list[dict] | None = None,
    use_llm: bool = False,
    model: str = "qwen2.5:7b",
    pipeline: str = "P3_FullSystem",
    trace: dict | None = None,
    fetch_fn: Any = None,
    llm_fn: Any = None,
    retriever_fn: Any = None,
    verifier_fn: Any = None,
    score_fn: Any = None,
    corpus_tree_fn: Any = None,
    *,
    corpus_path: str = "data/processed/vat_corpus.json",
    reviewed_amendment_path: str | None = None,
) -> AnswerResult:
    """Canonical orchestration for VAT QA with retrieval gating, IRAC synthesis and verification."""
    if reviewed_amendment_path is None:
        from src.config import selected_review_path
        reviewed_amendment_path = selected_review_path(corpus_path)
    trace_data: dict = {
        "generation_disposition": "safety_bypass" if use_llm else "deterministic_off",
        "generation_reason": "no_evidence" if use_llm else "llm_disabled",
        "generation_attempted": False,
        "generated_delivered": False,
        "query": question,
        "as_of_date": as_of_date,
        "pipeline": pipeline,
        "raw_article_count": 0,
        "gated_article_count": 0,
        "prompt_article_count": 0,
        "llm_used": False,
        "llm_citation_count": 0,
        "irac_rule_empty": False,
        "irac_conclusion_empty": False,
        "answer_status": "ANSWERED",
    }

    def _record_uncertainty(verification):
        report = getattr(verification, "report", verification)
        uncertain = [c for c in getattr(report, "citations", []) if c.status == "UNRESOLVED"]
        if uncertain:
            trace_data["unresolved_citations"] = list(dict.fromkeys(
                trace_data.get("unresolved_citations", []) + [c.id for c in uncertain]
            ))
            trace_data["unresolved_warnings"] = list(dict.fromkeys(
                trace_data.get("unresolved_warnings", []) + [c.message for c in uncertain if c.message]
            ))

    def _finalize(res: AnswerResult, reason_override: EmptyReason | None = None) -> AnswerResult:
        unresolved = trace_data.get("unresolved_citations", [])
        if unresolved:
            if use_llm:
                trace_data["generation_reason"] = "unresolved_evidence"
            if trace_data["generated_delivered"]:
                trace_data["generation_disposition"] = "draft_replaced"
                trace_data["generated_delivered"] = False
            res.citations = list(dict.fromkeys(res.citations + unresolved))
            uncertain_bases = {cid.split("_K")[0] for cid in unresolved}
            res.active_article_ids = [cid for cid in res.active_article_ids if cid.split("_K")[0] not in uncertain_bases]
            res.articles = [a for a in res.articles if a.id not in uncertain_bases]
            res.risk_score = max(res.risk_score, 50)
            if res.risk_level == "LOW":
                res.risk_level = "MEDIUM"
            res.warnings = list(dict.fromkeys(res.warnings + trace_data.get("unresolved_warnings", [])))
            res.answer_status = "INSUFFICIENT_BASIS"
            res.irac_sections = {
                "issue": f"Xác định quy định pháp luật cho: '{question}' tại mốc {as_of_date}.",
                "rule": "Căn cứ chưa xác minh chắc chắn: " + ", ".join(f"[{cid}]" for cid in unresolved),
                "application": "; ".join(res.warnings),
                "conclusion": "Chưa đủ căn cứ pháp lý để đưa ra kết luận chắc chắn. Cần xác minh hiệu lực từng điều khoản trước khi áp dụng.",
            }
        if (reviewed_amendment_path is None
                and pipeline not in {"P1_FlatRAG", "P2", "P2_TemporalGraphRAG"}
                and res.answer_status != "OUT_OF_SCOPE"):
            limitation = "Chưa có hồ sơ thẩm định được cấu hình; chỉ cung cấp trích đoạn qua kiểm tra, không tổng hợp kết luận pháp lý."
            res.answer_status = "INSUFFICIENT_BASIS"
            res.warnings = list(dict.fromkeys(res.warnings + [limitation]))
            if reason_override == EmptyReason.TEMPORAL_TRAP:
                res.irac_sections["conclusion"] = "Chưa đủ căn cứ pháp lý để kết luận tại mốc tra cứu; cần thẩm định hiệu lực và quy định áp dụng."
            trace_data.update(generation_reason="review_required_for_generation", generation_disposition="safety_bypass")
        trace_data["answer_status"] = res.answer_status
        trace_data["llm_used"] = res.llm_used
        irac = res.irac_sections
        trace_data["irac_rule_empty"] = not bool((irac.get("rule") or "").strip())
        trace_data["irac_conclusion_empty"] = not bool((irac.get("conclusion") or "").strip())
        if res.llm_used:
            trace_data["llm_citation_count"] = len(res.citations)

        trace_data["delivered_sha256"] = text_sha256("\n".join(irac.values()))
        temp_trace = QueryTrace(**{k: v for k, v in trace_data.items() if k in QueryTrace.__annotations__})
        final_reason = reason_override if reason_override is not None else classify_empty(temp_trace)
        trace_data["empty_reason"] = final_reason
        emit_trace(QueryTrace(**{k: v for k, v in trace_data.items() if k in QueryTrace.__annotations__}))
        if trace is not None:
            trace.update(trace_data)
        return res

    # 0. Multi-turn context resolution
    history_pairs: list[tuple[str, str]] = []
    if history:
        for item in history:
            if isinstance(item, dict):
                history_pairs.append((str(item.get("role", "user")), str(item.get("content", ""))))
            elif isinstance(item, (tuple, list)) and len(item) >= 2:
                history_pairs.append((str(item[0]), str(item[1])))
    history_pairs.append(("user", question))
    effective_query = normalize_query(rewrite_query(history_pairs) or question)

    # 1. Domain guardrail check
    guard = check_query_domain(question)
    if not guard.is_allowed and history and not guard.is_greeting and guard.reason != "EXPLICIT_OUT_OF_DOMAIN":
        context_guard = check_query_domain(effective_query)
        if context_guard.is_allowed:
            guard = context_guard

    if not guard.is_allowed:
        if use_llm:
            trace_data["generation_reason"] = "out_of_scope"
        return _finalize(
            AnswerResult(
                query=question,
                as_of_date=as_of_date,
                pipeline=pipeline,
                citations=[],
                active_article_ids=[],
                risk_score=0,
                risk_level="LOW",
                warnings=[],
                irac_sections={
                    "issue": f"Phạm vi áp dụng pháp lý: '{question}'",
                    "rule": "Phạm vi điều chỉnh: Các văn bản pháp luật về Thuế Giá trị gia tăng Việt Nam hiện hành trong cơ sở tri thức.",
                    "application": guard.refusal_message,
                    "conclusion": guard.refusal_message,
                },
                llm_used=False,
                raw_llm_response=None,
                answer_status="OUT_OF_SCOPE",
                is_out_of_domain=not guard.is_greeting,
                is_greeting=guard.is_greeting,
            ),
            reason_override=EmptyReason.OUT_OF_SCOPE,
        )

    # Run LLM query understanding only when LLM generation is eligible on the active pipeline.
    if use_llm and (reviewed_amendment_path is not None or pipeline in {"P1_FlatRAG", "P2", "P2_TemporalGraphRAG"}):
        from src.engine.query_understanding import understand_with_llm

        understanding = understand_with_llm(
            question,
            history=history,
            llm_fn=llm_fn or generate_llm_response,
            model=model,
            use_llm=True,
        )
        trace_data["rewritten_query"] = understanding.legal_query
        trace_data["understanding_source"] = understanding.source
        trace_data["understanding_missing_facts"] = list(understanding.missing_facts)
        if understanding.scope in {"greeting", "out_of_scope"}:
            is_greeting = understanding.scope == "greeting"
            msg = (
                "Xin chào! Tôi là Trợ lý Pháp lý ảo về Thuế Giá trị gia tăng (GTGT) Việt Nam."
                if is_greeting
                else "Câu hỏi của bạn không thuộc phạm vi điều chỉnh của Hệ thống Pháp lý Thuế Giá trị gia tăng (GTGT)."
            )
            trace_data["generation_reason"] = "out_of_scope"
            return _finalize(
                AnswerResult(
                    query=question,
                    as_of_date=as_of_date,
                    pipeline=pipeline,
                    citations=[],
                    active_article_ids=[],
                    risk_score=0,
                    risk_level="LOW",
                    warnings=[],
                    irac_sections={
                        "issue": f"Phạm vi áp dụng pháp lý: '{question}'",
                        "rule": "Phạm vi điều chỉnh: Các văn bản pháp luật về Thuế Giá trị gia tăng Việt Nam hiện hành trong cơ sở tri thức.",
                        "application": msg,
                        "conclusion": msg,
                    },
                    llm_used=False,
                    raw_llm_response=None,
                    answer_status="OUT_OF_SCOPE",
                    is_out_of_domain=not is_greeting,
                    is_greeting=is_greeting,
                ),
                reason_override=EmptyReason.OUT_OF_SCOPE,
            )
        effective_query = understanding.legal_query

    if reviewed_amendment_path is not None and pipeline not in {"P1_FlatRAG", "P2", "P2_TemporalGraphRAG"}:
        from src.api.reviewed_answer import reviewed_answer
        return _finalize(reviewed_answer(
            question, effective_query, as_of_date, pipeline, use_llm, model, history, trace_data,
            fetch_fn, retriever_fn or query_temporal_rag, verifier_fn, score_fn or verify_and_score,
            llm_fn or generate_llm_response, corpus_path, reviewed_amendment_path,
        ))

    # 2. Retrieval and gating
    if fetch_fn is not None:
        if corpus_path == _DEFAULT_CORPUS_PATH:
            citations, active_ids, articles = fetch_fn(pipeline, effective_query, as_of_date, trace=trace_data)
        else:
            citations, active_ids, articles = _invoke_callback(
                fetch_fn, pipeline, effective_query, as_of_date, trace=trace_data, corpus_path=corpus_path,
                verifier_fn=verifier_fn,
            )
    else:
        citations, active_ids, articles = _fetch_pipeline_articles(
            pipeline,
            effective_query,
            as_of_date,
            trace=trace_data,
            retriever_fn=retriever_fn,
            verifier_fn=verifier_fn,
            corpus_path=corpus_path,
        )

    if trace_data.get("unresolved_citations"):
        return _finalize(AnswerResult(
            query=question, as_of_date=as_of_date, pipeline=pipeline,
            citations=citations, active_article_ids=active_ids,
            risk_score=50, risk_level="MEDIUM",
            warnings=trace_data.get("unresolved_warnings") or ["Chưa đủ căn cứ xác minh hiệu lực chắc chắn."],
            irac_sections={}, llm_used=False, raw_llm_response=None,
            answer_status="INSUFFICIENT_BASIS", articles=articles,
        ))

    # 3. Temporal trap check / unresolved check on empty retrieval
    if not citations and not articles:
        trap_warnings = _check_temporal_trap(effective_query, as_of_date, corpus_tree_fn=corpus_tree_fn, corpus_path=corpus_path)
        if trap_warnings:
            if use_llm:
                trace_data["generation_reason"] = "temporal_trap"
            return _finalize(
                AnswerResult(
                    query=question,
                    as_of_date=as_of_date,
                    pipeline=pipeline,
                    citations=[],
                    active_article_ids=[],
                    risk_score=100,
                    risk_level="CRITICAL",
                    warnings=trap_warnings,
                    irac_sections={
                        "issue": f"Xác định hiệu lực và khả năng áp dụng: '{question}' tại mốc {as_of_date}.",
                        "rule": "Quy định về thời điểm có hiệu lực thi hành của văn bản quy phạm pháp luật.",
                        "application": "; ".join(trap_warnings),
                        "conclusion": f"Cảnh báo bẫy thời gian: Văn bản được hỏi chưa có hiệu lực thi hành tại mốc {as_of_date}. Doanh nghiệp không được áp dụng các điều kiện hay quy định của văn bản này vào thời điểm trên.",
                    },
                    llm_used=False,
                    raw_llm_response=None,
                    answer_status="ANSWERED",
                    is_out_of_domain=False,
                    is_greeting=False,
                ),
                reason_override=EmptyReason.TEMPORAL_TRAP,
            )
        return _finalize(
            AnswerResult(
                query=question,
                as_of_date=as_of_date,
                pipeline=pipeline,
                citations=[],
                active_article_ids=[],
                risk_score=50,
                risk_level="MEDIUM",
                warnings=["Chưa đủ căn cứ pháp lý trong kết quả truy xuất tại mốc thời gian tra cứu."],
                irac_sections={
                    "issue": f"Xác định quy định pháp luật cho: '{question}' tại mốc {as_of_date}.",
                    "rule": "Không tìm thấy điều khoản pháp luật thuế GTGT phù hợp.",
                    "application": f"Tại mốc {as_of_date}, kết quả truy xuất chưa cung cấp đủ căn cứ để xác định quy định áp dụng.",
                    "conclusion": "Chưa đủ căn cứ pháp lý để đưa ra kết luận chắc chắn tại mốc thời gian đã chọn.",
                },
                llm_used=False,
                raw_llm_response=None,
                answer_status="INSUFFICIENT_BASIS",
                is_out_of_domain=False,
                is_greeting=False,
            )
        )

    if pipeline not in {"P1_FlatRAG", "P2", "P2_TemporalGraphRAG"}:
        cite_text = " ".join(f"[{cid}]" for cid in citations)
        verification = _invoke_callback(
            score_fn or verify_and_score, cite_text, as_of_date,
            context_ids={a.id for a in articles}, corpus_path=corpus_path,
        )
        _record_uncertainty(verification)
        statuses = getattr(getattr(verification, "report", verification), "citations", [])
        rejected = {c.id for c in statuses if c.status not in {"VALID", "AMENDED"}}
        articles = [a for a in articles if a.id not in rejected]
        sections = _invoke_callback(
            _build_irac_sections, effective_query, as_of_date,
            [cid for cid in citations if cid not in rejected], list(getattr(verification, "warnings", [])),
            articles=articles, corpus_path=corpus_path,
        )
        return _finalize(AnswerResult(
            query=question, as_of_date=as_of_date, pipeline=pipeline,
            citations=extract_citations("\n".join(sections.values())),
            active_article_ids=[a.id for a in articles],
            risk_score=getattr(verification, "risk_score", 50),
            risk_level=getattr(verification, "risk_level", "MEDIUM"),
            warnings=list(getattr(verification, "warnings", [])),
            irac_sections=sections, llm_used=False, raw_llm_response=None,
            answer_status="INSUFFICIENT_BASIS", articles=articles,
        ))

    scorer = score_fn or verify_and_score
    llm_gen = llm_fn or generate_llm_response

    if use_llm:
        prompt_articles = articles if pipeline != "P1_FlatRAG" else []
        prompt = build_irac_prompt(effective_query, as_of_date, prompt_articles, history=history, corpus_path=corpus_path)
        trace_data["generation_attempted"] = True
        trace_data["prompt_sha256"] = text_sha256(prompt)
        raw_response = llm_gen(
            prompt,
            model=model,
            options={"num_ctx": 4096, "num_predict": 1024},
        )
        trace_data["generation_disposition"] = "provider_no_output"
        trace_data["generation_reason"] = "empty_provider_output"
        trace_data["output_sha256"] = text_sha256(raw_response) if raw_response else None
        if raw_response is not None:
            extracted = extract_citations(raw_response)
            candidate_citations = list(dict.fromkeys(extracted))
            rel_map = {}

            context_ids = {a.id for a in articles}
            scorer_kwargs = {"context_ids": context_ids, "corpus_path": corpus_path}
            if rel_map is not None:
                scorer_kwargs["relevance_map"] = rel_map
            v_res = _invoke_callback(scorer, raw_response, as_of_date, **scorer_kwargs)
            _record_uncertainty(v_res)
            v_report = getattr(v_res, "report", v_res)
            invalid_or_irrelevant = {
                c.id for c in getattr(v_report, "citations", []) if getattr(c, "status", "") in {"IRRELEVANT", "NOT_FOUND"}
            }
            llm_citations = [
                cid for cid in candidate_citations
                if cid not in invalid_or_irrelevant
                and not (rel_map and rel_map.get(cid.split("_K")[0]) is False)
                and not (rel_map and rel_map.get(cid) is False)
            ]
            llm_citations = list(dict.fromkeys(llm_citations))
            irac = _parse_irac_sections(raw_response)

            entities = understand_query(effective_query)
            is_household = entities.business_type == "HỘ_KINH_DOANH"
            fallback_used = _needs_irac_fallback(raw_response, irac) or _is_llm_answer_unsupported(
                raw_response, irac, prompt_articles, extracted, llm_citations, invalid_or_irrelevant, is_household=is_household
            )
            if raw_response.strip():
                trace_data["generation_disposition"] = "draft_replaced" if fallback_used else "generated_delivered"
                trace_data["generation_reason"] = "unsupported_draft" if fallback_used else "verified_draft"
                trace_data["generated_delivered"] = not fallback_used
            v_warnings = getattr(v_res, "warnings", [])
            v_score = getattr(v_res, "risk_score", 0)
            v_level = getattr(v_res, "risk_level", "LOW")
            if fallback_used and not trace_data.get("unresolved_citations"):
                irac = _invoke_callback(
                    _build_irac_sections, effective_query, as_of_date, citations, [],
                    articles=articles, corpus_path=corpus_path,
                )
                final_text = "\n".join(irac.values())
                final_relevance = {}
                final_verification = _invoke_callback(
                    scorer, final_text, as_of_date, context_ids=context_ids,
                    relevance_map=final_relevance, corpus_path=corpus_path,
                )
                _record_uncertainty(final_verification)
                llm_citations = extract_citations(final_text)
                v_warnings = list(getattr(final_verification, "warnings", []))
                v_warnings.append("Bản nháp LLM đã bị thay bằng câu trả lời tổng hợp từ tài liệu truy xuất; kiểm tra citation không xác nhận tính đúng đắn của kết luận pháp lý.")
                v_score = getattr(final_verification, "risk_score", 0)
                v_level = getattr(final_verification, "risk_level", "LOW")

            llm_status = "ANSWERED"
            if not fallback_used and not extracted and any(p in raw_response.lower() for p in ("không thuộc phạm vi", "không có căn cứ")):
                llm_status = "INSUFFICIENT_BASIS"
            trace_data["prompt_article_count"] = len(prompt_articles)
            return _finalize(
                AnswerResult(
                    query=question,
                    as_of_date=as_of_date,
                    pipeline=pipeline,
                    citations=llm_citations,
                    active_article_ids=list(dict.fromkeys(active_ids)),
                    risk_score=v_score,
                    risk_level=v_level,
                    warnings=v_warnings,
                    irac_sections=irac,
                    llm_used=True,
                    raw_llm_response=raw_response,
                    answer_status=llm_status,
                    articles=articles,
                )
            )

    context_ids = {a.id for a in articles}
    cite_text = " ".join(f"[{cid}]" for cid in citations)
    v_res = _invoke_callback(scorer, cite_text, as_of_date, context_ids=context_ids, corpus_path=corpus_path)
    _record_uncertainty(v_res)
    v_warnings = getattr(v_res, "warnings", [])
    v_score = getattr(v_res, "risk_score", 0)
    v_level = getattr(v_res, "risk_level", "LOW")
    irac = _invoke_callback(
        _build_irac_sections, effective_query, as_of_date, citations, v_warnings,
        articles=articles, corpus_path=corpus_path,
    )

    return _finalize(
        AnswerResult(
            query=question,
            as_of_date=as_of_date,
            pipeline=pipeline,
            citations=list(dict.fromkeys(citations)),
            active_article_ids=list(dict.fromkeys(active_ids)),
            risk_score=v_score,
            risk_level=v_level,
            warnings=v_warnings,
            irac_sections=irac,
            llm_used=False,
            raw_llm_response=None,
            answer_status="ANSWERED",
            articles=articles,
        )
    )
