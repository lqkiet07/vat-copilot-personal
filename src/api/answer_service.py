"""Canonical answer service for VAT legal QA and IRAC orchestration."""
from dataclasses import asdict, dataclass, field
import re
from typing import Any

from src.api.chat_domain import normalize_query, rewrite_query, understand_query
from src.engine.diagnostics import EmptyReason, QueryTrace, classify_empty, emit_trace
from src.engine.llm_client import generate_llm_response
from src.engine.models import ArticleNode
from src.engine.prompt import build_irac_prompt
from src.engine.retrieval_service import retrieve_and_gate
from src.engine.temporal_retriever import query_temporal_rag
from src.ui.corpus_viewer import load_corpus_index, load_corpus_tree
from src.verifier.citation_verifier import extract_citations
from src.verifier.evidence_numbers import extract_revenue_thresholds
from src.verifier.guardrails import check_query_domain
from src.verifier.relevance import build_relevance_map, score_article_relevance
from src.verifier.risk_score import verify_and_score

_STOPWORDS = {
    "mức", "đối", "với", "là", "bao", "nhiêu", "của", "và", "các", "cho",
    "tại", "năm", "tháng", "ngày", "được", "theo", "quy", "định", "thuế",
    "gtgt", "tôi", "thì", "có", "phải", "không", "tiền", "ở", "dưới", "quê",
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
) -> list[tuple[str, str, str]]:
    """Extract matching legal provisions from cited articles based on query terms and entities."""
    corpus = load_corpus_index() if articles is None else {
        art.id: {"text": art.content, "code": art.code, "title": art.title}
        for art in articles
    }
    q_norm = normalize_query(query).lower()
    entities = understand_query(query)
    raw_words = re.findall(r"\w+", q_norm)
    words = [w for w in raw_words if w not in _STOPWORDS and len(w) > 1]
    is_hh = entities.business_type == "HỘ_KINH_DOANH"
    is_domestic_hh = is_hh and not any(m in q_norm for m in ("xuất khẩu", "xuat khau"))

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
            if is_hh:
                if any(k in line_l for k in ("hộ, cá nhân", "hộ kinh doanh", "cá nhân kinh doanh")):
                    score += 25
                if "200 triệu" in line_l or "doanh thu hằng năm" in line_l or "doanh thu hàng năm" in line_l:
                    score += 35
                if "trực tiếp" in line_l or "khoán" in line_l:
                    score += 20
                if is_domestic_hh and any(e in line_l for e in ("xuất khẩu", "0%", "phi thuế quan")):
                    score -= 50
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
) -> dict[str, str]:
    """Build structured IRAC sections dynamically from verified citation evidence."""
    rule_str = ", ".join(f"[{c}]" for c in citations) if citations else "Không có căn cứ hiệu lực"
    issue = f"Xác định quy định pháp luật thuế GTGT áp dụng cho: '{query}' tại mốc {as_of_date}."
    rule = rule_str

    evidence_items = _extract_citation_evidence(query, citations, articles=articles)
    entities = understand_query(query)
    q_check = (normalize_query(query) + " " + query).lower()
    is_domestic_hh = (
        entities.business_type == "HỘ_KINH_DOANH"
        and not any(m in q_check for m in ("xuất khẩu", "xuat khau"))
    )

    if evidence_items:
        app_lines = [f"Theo các quy định pháp luật thuế GTGT có hiệu lực tại mốc {as_of_date}:"]
        for cid, label, text in evidence_items:
            app_lines.append(f"• Căn cứ [{cid}] {label}: {text}")
        if warnings:
            app_lines.append("\nCảnh báo hiệu lực: " + "; ".join(warnings))
        app = "\n".join(app_lines)

        all_evidence_text = " ".join(text.lower() for _, _, text in evidence_items)
        if articles:
            all_evidence_text += " " + " ".join(art.content.lower() for art in articles)

        is_hh = is_domestic_hh
        threshold_sources = [art.content for art in articles if art.id in citations] if articles is not None else [text for _, _, text in evidence_items]
        thresholds = list(dict.fromkeys(
            threshold for text in threshold_sources for threshold in extract_revenue_thresholds(text)
        ))

        if is_hh and thresholds:
            t = thresholds[0]
            amount_note = ""
            if any(w in query.lower() for w in ("bao nhiêu", "bnh", "mấy %", "may %", "nhiêu")):
                amount_note = (
                    "\n- Về số tiền thuế phải nộp: Do chưa có dữ liệu doanh thu thực tế và bảng tỷ lệ % cụ thể theo ngành nghề, "
                    "hệ thống chưa thể tính ra số tiền phát sinh chính xác. Bạn có thể ước tính mức doanh thu để hệ thống tư vấn sát hơn."
                )
            conc = (
                "Nghĩa vụ thuế GTGT được xác định theo mức doanh thu hằng năm:\n"
                f"- Doanh thu từ {t} đồng/năm trở xuống: Thuộc đối tượng không chịu thuế GTGT (không phát sinh số thuế GTGT phải nộp).\n"
                f"- Doanh thu trên {t} đồng/năm: Không thuộc trường hợp không chịu thuế theo ngưỡng doanh thu nêu trên; cần đối chiếu thêm điều kiện áp dụng, phương pháp và tỷ lệ tính thuế có căn cứ.\n"
                f"Người nộp thuế cần căn cứ mức doanh thu thực tế ước tính để xác định chính xác số thuế phát sinh.{amount_note}"
            )
        elif is_hh:
            conc = (
                f"Nghĩa vụ thuế GTGT đối với hộ, cá nhân kinh doanh được áp dụng căn cứ theo quy định tại {rule_str}. "
                "Người nộp thuế thực hiện nghĩa vụ thuế theo phương pháp và tỷ lệ quy định đối với ngành nghề kinh doanh theo các điều khoản nêu trên."
            )
        elif "thuế suất 0%" in all_evidence_text or ("0%" in all_evidence_text and "xuất khẩu" in all_evidence_text):
            conc = (
                f"Áp dụng mức thuế suất thuế GTGT 0% đối với hàng hóa, dịch vụ xuất khẩu theo quy định hiện hành ({rule_str}) "
                "khi đáp ứng đầy đủ điều kiện về hợp đồng và chứng từ thanh toán không dùng tiền mặt."
            )
        else:
            conc = f"Các tài liệu {rule_str} đã được truy xuất tại mốc {as_of_date}, nhưng chưa đủ căn cứ để xác định nghĩa vụ thuế cụ thể cho tình huống này."
    elif warnings:
        warn_text = "; ".join(warnings)
        app = f"Tại mốc {as_of_date}, các quy định viện dẫn có cảnh báo sau: {warn_text}"
        conc = f"Cần thận trọng: Phát hiện rủi ro thời gian hoặc hiệu lực pháp lý đối với câu hỏi tại ngày {as_of_date}."
    else:
        app = f"Tại thời điểm tra cứu {as_of_date}, các quy định trên đang có hiệu lực thi hành đầy đủ."
        conc = f"Các căn cứ pháp lý {rule_str} có hiệu lực áp dụng tại mốc thời gian tra cứu."

    return {
        "issue": issue,
        "rule": rule,
        "application": app,
        "conclusion": conc,
    }


def _check_temporal_trap(query: str, as_of_date: str, corpus_tree_fn: Any = None) -> list[str]:
    """Check if query references a legal document not yet effective at as_of_date."""
    docs = corpus_tree_fn() if corpus_tree_fn else load_corpus_tree()
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
    current_key: str | None = None
    for line in raw_text.splitlines():
        stripped = line.strip().lstrip("#* -0123456789.:").strip()
        lower_header = stripped.lower()
        if lower_header.startswith("issue"):
            current_key = "issue"
            val = line.split(":", 1)[-1].strip() if ":" in line else ""
            if val and val.lower() != "issue":
                sections[current_key] = val
        elif lower_header.startswith("rule"):
            current_key = "rule"
            val = line.split(":", 1)[-1].strip() if ":" in line else ""
            if val and val.lower() != "rule":
                sections[current_key] = val
        elif lower_header.startswith("application"):
            current_key = "application"
            val = line.split(":", 1)[-1].strip() if ":" in line else ""
            if val and val.lower() != "application":
                sections[current_key] = val
        elif lower_header.startswith("conclusion"):
            current_key = "conclusion"
            val = line.split(":", 1)[-1].strip() if ":" in line else ""
            if val and val.lower() != "conclusion":
                sections[current_key] = val
        elif current_key and line.strip():
            sections[current_key] = (sections[current_key] + " " + line.strip()).strip()
    if not any([sections["issue"], sections["rule"], sections["application"], sections["conclusion"]]):
        sections["application"] = raw_text
    return sections


def _needs_irac_fallback(raw_response: str | None, irac: dict[str, str]) -> bool:
    """Return True if raw response is empty or missing essential IRAC sections."""
    if not (raw_response or "").strip():
        return True
    return not bool(irac.get("rule", "").strip()) or not bool(irac.get("conclusion", "").strip())


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

    # 3. Household business guardrails
    if is_household:
        lower_conc = conc_text.lower()
        # Household businesses cannot be assigned deduction method (ND_181_2025_D21 explicitly excludes them)
        if "khấu trừ" in lower_conc and "trừ hộ" not in lower_conc and "không áp dụng" not in lower_conc:
            return True
        # Unconditional exemption claims without threshold
        unconditional_free = any(p in lower_conc for p in (
            "được miễn thuế hoàn toàn",
            "không cần phải chịu thuế",
            "không phải chịu thuế giá trị gia tăng",
            "không phải nộp thuế gtgt",
            "không phải đóng thuế",
            "không chịu thuế giá trị gia tăng theo quy định của",
        ))
        has_threshold = any(t in lower_conc for t in ("200 triệu", "doanh thu", "ngưỡng", "mức doanh thu"))
        if unconditional_free and not has_threshold:
            return True

    return False


def _fetch_pipeline_articles(
    pipeline: str,
    query: str,
    as_of_date: str,
    trace: dict | None = None,
    retriever_fn: Any = None,
    verifier_fn: Any = None,
) -> tuple[list[str], list[str], list[ArticleNode]]:
    """Retrieve citations, active IDs, and articles for the selected pipeline."""
    res = retrieve_and_gate(
        query,
        as_of_date,
        top_k=5,
        pipeline=pipeline,
        retriever_fn=retriever_fn or query_temporal_rag,
        verifier_fn=verifier_fn,
    )
    if trace is not None:
        trace["raw_article_count"] = res.raw_article_count
        trace["gated_article_count"] = res.gated_article_count
        if pipeline == "P1_FlatRAG":
            trace["pipeline_note"] = "flat_rag_no_article_objects"
    return res.active_ids, res.active_ids, res.articles


def _build_candidate_relevance_map(
    query: str, raw_response: str, candidate_citations: list[str], articles: list[ArticleNode]
) -> dict[str, bool]:
    """Build and enrich relevance map for citations extracted from LLM response."""
    rel_map = build_relevance_map(query, articles) if articles else {}
    export_markers = ("xuất khẩu", "xuat khau", "0%", "nước ngoài", "nuoc ngoai", "phi thuế quan")
    has_export_query = any(m in query.lower() for m in export_markers)

    for cid in candidate_citations:
        base_id = cid.split("_K")[0]
        if base_id in rel_map or cid in rel_map:
            continue
        if base_id == "LUAT_48_2024_D9":
            is_domestic_shop = any(k in query.lower() for k in ("hộ", "quán", "tiệm", "cá nhân", "dưới quê"))
            if not has_export_query and (is_domestic_shop or any(p in raw_response.lower() for p in ("0%", "xuất khẩu", "xuat khau"))):
                is_rel = False
            else:
                is_rel = has_export_query
            rel_map[cid] = rel_map[base_id] = is_rel
            continue
        corpus_meta = load_corpus_index().get(base_id)
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
) -> AnswerResult:
    """Canonical orchestration for VAT QA with retrieval gating, IRAC synthesis and verification."""
    trace_data: dict = {
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

    def _finalize(res: AnswerResult, reason_override: EmptyReason | None = None) -> AnswerResult:
        trace_data["answer_status"] = res.answer_status
        trace_data["llm_used"] = res.llm_used
        irac = res.irac_sections
        trace_data["irac_rule_empty"] = not bool((irac.get("rule") or "").strip())
        trace_data["irac_conclusion_empty"] = not bool((irac.get("conclusion") or "").strip())
        if res.llm_used:
            trace_data["llm_citation_count"] = len(res.citations)

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
                    "rule": "Phạm vi điều chỉnh: Các văn bản pháp luật về Thuế Giá trị gia tăng (Luật 48/2024, NĐ 181/2025, TT 69/2025, NĐ 359/2025, NĐ 144/2026).",
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

    # 2. Retrieval and gating
    if fetch_fn is not None:
        citations, active_ids, articles = fetch_fn(pipeline, effective_query, as_of_date, trace=trace_data)
    else:
        citations, active_ids, articles = _fetch_pipeline_articles(
            pipeline,
            effective_query,
            as_of_date,
            trace=trace_data,
            retriever_fn=retriever_fn,
            verifier_fn=verifier_fn,
        )

    # 3. Temporal trap check on empty retrieval
    if not citations and not articles:
        trap_warnings = _check_temporal_trap(effective_query, as_of_date, corpus_tree_fn=corpus_tree_fn)
        if trap_warnings:
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
                risk_score=0,
                risk_level="LOW",
                warnings=["Không tìm thấy điều khoản pháp luật thuế GTGT nào điều chỉnh nội dung này tại mốc thời gian tra cứu."],
                irac_sections={
                    "issue": f"Xác định quy định pháp luật cho: '{question}' tại mốc {as_of_date}.",
                    "rule": "Không tìm thấy điều khoản pháp luật thuế GTGT phù hợp.",
                    "application": f"Tại mốc {as_of_date}, hệ thống đã đối chiếu qua cơ sở dữ liệu pháp luật thuế GTGT nhưng không tìm thấy điều khoản nào quy định nội dung này.",
                    "conclusion": "Câu hỏi không có căn cứ pháp luật thuế GTGT điều chỉnh tại mốc thời gian đã chọn.",
                },
                llm_used=False,
                raw_llm_response=None,
                answer_status="INSUFFICIENT_BASIS",
                is_out_of_domain=False,
                is_greeting=False,
            )
        )

    # 4. LLM Generation or Deterministic IRAC
    scorer = score_fn or verify_and_score
    llm_gen = llm_fn or generate_llm_response

    if use_llm:
        prompt_articles = articles if pipeline != "P1_FlatRAG" else []
        prompt = build_irac_prompt(effective_query, as_of_date, prompt_articles, history=history)
        raw_response = llm_gen(
            prompt,
            model=model,
            options={"num_ctx": 4096, "num_predict": 1024},
        )
        if raw_response is not None:
            extracted = extract_citations(raw_response)
            candidate_citations = list(dict.fromkeys(extracted))
            rel_map = _build_candidate_relevance_map(effective_query, raw_response, candidate_citations, articles)

            context_ids = {a.id for a in articles}
            scorer_kwargs = {"context_ids": context_ids}
            if rel_map is not None:
                scorer_kwargs["relevance_map"] = rel_map
            try:
                v_res = scorer(raw_response, as_of_date, **scorer_kwargs)
            except TypeError:
                scorer_kwargs.pop("context_ids", None)
                v_res = scorer(raw_response, as_of_date, **scorer_kwargs)
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
            q_check = (normalize_query(effective_query) + " " + effective_query).lower()
            is_household = (
                entities.business_type == "HỘ_KINH_DOANH"
                or any(k in q_check for k in ("hộ kinh doanh", "hộ cá thể", "tiệm", "quán", "cá nhân kinh doanh", "dưới quê"))
            )
            fallback_used = _needs_irac_fallback(raw_response, irac) or _is_llm_answer_unsupported(
                raw_response, irac, prompt_articles, extracted, llm_citations, invalid_or_irrelevant, is_household=is_household
            )
            v_warnings = getattr(v_res, "warnings", [])
            v_score = getattr(v_res, "risk_score", 0)
            v_level = getattr(v_res, "risk_level", "LOW")
            if fallback_used:
                irac = _build_irac_sections(effective_query, as_of_date, citations, [], articles=articles)
                final_text = "\n".join(irac.values())
                final_relevance = _build_candidate_relevance_map(
                    effective_query, final_text, extract_citations(final_text), articles,
                )
                try:
                    final_verification = scorer(
                        final_text, as_of_date, context_ids=context_ids, relevance_map=final_relevance,
                    )
                except TypeError:
                    final_verification = scorer(final_text, as_of_date, relevance_map=final_relevance)
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
    try:
        v_res = scorer(cite_text, as_of_date, context_ids=context_ids)
    except TypeError:
        v_res = scorer(cite_text, as_of_date)
    v_warnings = getattr(v_res, "warnings", [])
    v_score = getattr(v_res, "risk_score", 0)
    v_level = getattr(v_res, "risk_level", "LOW")
    irac = _build_irac_sections(effective_query, as_of_date, citations, v_warnings, articles=articles)

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
