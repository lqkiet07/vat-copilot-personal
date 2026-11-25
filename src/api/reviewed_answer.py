from src.engine.prompt import build_irac_prompt
from src.engine.retrieval_service import _invoke_callback, explicit_evidence_ids, project_reviewed_articles, retrieve_and_gate
from src.verifier.citation_verifier import extract_citations
import time


GROUNDING_LIMITATION = "Đánh giá ngữ nghĩa và độ bao phủ bằng mô hình có thể sai, không phải bằng chứng chứng minh; điểm rủi ro chỉ phản ánh trích dẫn."
PROVIDER_UNAVAILABLE_WARNING = (
    "LLM_PROVIDER_UNAVAILABLE: Mô hình AI tạm thời không phản hồi hoặc hết hạn mức; "
    "hệ thống đang hiển thị trích đoạn điều khoản pháp luật gốc đã được đối chiếu hiệu lực."
)


def _extract_amendment_links(provenance) -> list[dict]:
    """Collect one unique amending statute reference per source_unit from provision provenance."""
    merged: dict[str, dict] = {}

    def walk(node) -> None:
        if isinstance(node, dict):
            source_unit = node.get("source_unit")
            if isinstance(source_unit, str) and source_unit.strip():
                unit = source_unit.strip()
                effective_on = str(node.get("effective_on") or "").strip()
                op = node.get("operation")
                op_action = op.get("action") if isinstance(op, dict) else node.get("action")
                action = str(op_action or "").strip()
                cand = {"source_unit": unit, "effective_on": effective_on, "action": action}
                prev = merged.get(unit)
                cand_score = (bool(effective_on), bool(action))
                prev_score = (bool(prev["effective_on"]), bool(prev["action"])) if prev else (False, False)
                if prev is None or cand_score > prev_score:
                    merged[unit] = cand
            for child in node.values():
                walk(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(provenance)
    return list(merged.values())


def _build_resolved_provisions(resolution, cited_ids: set[str]) -> dict[str, dict]:
    """Map cited provision IDs to operative text, status, and amendment provenance."""
    if resolution is None or not cited_ids:
        return {}
    resolved: dict[str, dict] = {}
    for item in getattr(resolution, "items", ()) or ():
        for provision in getattr(item, "provisions", ()) or ():
            if provision.id in cited_ids:
                resolved[provision.id] = {
                    "text": provision.text,
                    "status": provision.status,
                    "amended_by": _extract_amendment_links(getattr(provision, "provenance", None)),
                }
    return resolved


def grounded_delivery(text, articles, report):
    ids = extract_citations(text)
    statuses = getattr(getattr(report, "report", report), "citations", [])
    sources = {a.id: a.content for a in articles}
    if not ids or not set(ids) <= sources.keys() or {c.id for c in statuses} != set(ids):
        return False
    if any(c.status not in {"VALID", "AMENDED"} for c in statuses):
        return False
    from src.api.answer_service import _parse_irac_sections
    sections = _parse_irac_sections(text)
    for key in ("rule", "application", "conclusion"):
        section = sections[key]
        cited = extract_citations(section)
        if not cited or not set(cited) <= sources.keys():
            return False
        for cid in cited:
            section = section.replace(f"[{cid}]", "")
        if not any(char.isalnum() for char in section):
            return False
    return True


def evidence_sections(question, day, articles, resolution=None, metadata=None):
    blockers = "\n".join(f"Hệ thống chưa xác minh được chứng cứ {unit}: {reason}".replace("_", " ")
                         for unit, reason in (resolution.blocked.items() if resolution else []))
    temporal = []
    for unit in resolution.blocked if resolution else []:
        meta = (metadata or {}).get(unit, {})
        start, end = meta.get("valid_from", ""), meta.get("valid_to", "")
        if start and day < start:
            temporal.append(f"{unit.replace('_', ' ')}: ngày bắt đầu hiệu lực theo dữ liệu là {start}; ngày tra cứu {day} nằm trước mốc này.")
        elif end and day > end:
            temporal.append(f"{unit.replace('_', ' ')}: ngày kết thúc hiệu lực theo dữ liệu là {end}; ngày tra cứu {day} nằm sau mốc này.")
    blockers = "\n".join(filter(None, [blockers, *temporal]))
    return {
        "issue": f"Tra cứu chứng cứ pháp lý tại ngày {day}.",
        "rule": " ".join(f"[{a.id}]" for a in articles),
        "application": "\n".join(f"[{a.id}] {a.content}" for a in articles),
        "conclusion": "Chưa đủ căn cứ để kết luận pháp lý cho tình huống; trên đây chỉ là trích đoạn chứng cứ đã xác minh."
                      + ("\n" + blockers if blockers else ""),
    }


def _verify(text, day, articles, resolution, scorer, corpus_path, review_path, query):
    from src.engine.provision_evidence import resolve_provision_evidence
    cited = extract_citations(text)
    target_ids = list(dict.fromkeys([a.id for a in articles] + cited))
    current = resolve_provision_evidence(target_ids, day, corpus_path=corpus_path,
                                         reviewed_amendment_path=review_path)
    exact = {p.id: p for item in current.items for p in item.provisions}
    supplied = {p.id: p for item in resolution.items for p in item.provisions}
    for a in articles:
        if a.id not in supplied or a.content != supplied[a.id].text or exact.get(a.id) != supplied.get(a.id):
            raise ValueError("Evidence projection differs from pinned source")
    for cid in cited:
        if exact.get(cid) != supplied.get(cid):
            raise ValueError("Evidence projection differs from pinned source")
    return _invoke_callback(
        scorer, text, day, context_ids={a.id for a in articles},
        resolved_evidence=resolution, corpus_path=corpus_path,
        reviewed_amendment_path=review_path,
    )


def _generate(question, day, articles, resolution, llm, scorer, model, history, paths, trace):
    from src.api.answer_service import _parse_irac_sections, _needs_irac_fallback
    prompt = build_irac_prompt(question, day, articles, history, resolved_evidence=resolution, corpus_path=paths[0])
    from src.engine.diagnostics import text_sha256
    trace.update(generation_attempted=False, prompt_article_count=len(articles), prompt_sha256=text_sha256(prompt))
    from src.engine.evidence_pack import measure_prompt
    from src.engine.llm_client import _llm_provider, response_deadline
    from src.config import local_llm_limits, provider_context_limit
    provider = _llm_provider()
    local = provider == "ollama"
    context = provider_context_limit(provider)
    total, generation, assessment, _ = local_llm_limits() if local else (120.0, 120.0, 120.0, context)
    measurement = measure_prompt(prompt, context, 1024)
    trace.update(prompt_budget_method=measurement.method, prompt_utf8_bytes=measurement.utf8_bytes,
                 prompt_input_limit=measurement.input_limit, context_limit=context)
    if local:
        trace["local_num_ctx"] = context
    if not measurement.fits:
        trace.update(generation_disposition="safety_bypass", generation_reason="context_budget_exceeded",
                     generated_delivered=False)
        return None, None, None
    trace["generation_attempted"] = True
    deadline = time.monotonic() + total
    generation_deadline = min(deadline, time.monotonic() + generation)
    if local:
        trace.update(llm_total_budget_seconds=total, generation_budget_seconds=generation,
                     assessment_budget_seconds=assessment)
    try:
        with response_deadline(generation_deadline):
            raw = llm(prompt, model=model, timeout=generation_deadline - time.monotonic(),
                      options={"num_ctx": context if local else 4096, "num_predict": 1024})
    except Exception:
        trace.update(generation_disposition="provider_no_output", generation_reason="provider_error")
        return None, None, None
    if raw is not None and not isinstance(raw, str):
        trace.update(generation_disposition="provider_no_output", generation_reason="invalid_provider_output")
        return None, None, None
    trace["output_sha256"] = text_sha256(raw) if raw is not None else None
    if time.monotonic() >= generation_deadline:
        trace.update(generation_disposition="draft_replaced", generation_reason="provider_deadline")
        return None, None, raw
    if not raw or not raw.strip():
        trace.update(generation_disposition="provider_no_output", generation_reason="empty_provider_output")
        return None, None, raw
    report = _verify(raw, day, articles, resolution, scorer, *paths, question)
    sections = _parse_irac_sections(raw)
    accepted = False
    trace.setdefault("assessment_call_count", 0)
    if grounded_delivery(raw, articles, report) and not _needs_irac_fallback(raw, sections):
        assessment_deadline = min(deadline, time.monotonic() + assessment)
        accepted = _assess(question, day, history, articles, resolution, raw, llm, model, trace, assessment_deadline)
        if accepted:
            report = _verify(raw, day, articles, resolution, scorer, *paths, question)
            accepted = grounded_delivery(raw, articles, report) and time.monotonic() < deadline
    trace.update(generation_disposition="generated_delivered" if accepted else "draft_replaced",
                 generation_reason="verified_draft" if accepted else "unsupported_draft", generated_delivered=accepted)
    return (sections if accepted else None), (report if accepted else None), raw


def _assess(question, day, history, articles, resolution, raw, llm, model, trace, deadline):
    from src.engine.answer_assessment import (
        AssessmentJSONError, assessment_generation_config, build_assessment_prompt, delivery_allowed, parse_assessment,
    )
    from src.engine.llm_client import AssessmentProviderError, assessment_request, response_deadline
    from src.engine.diagnostics import emit_trace, text_sha256
    prompt = build_assessment_prompt(question, day, history, articles, resolution, raw)
    trace.update(assessment_prompt_sha256=text_sha256(prompt), assessment_outcome="unknown",
                  assessment_call_count=0, assessment_claim_count=0, assessment_output_sha256=None,
                 assessment_reason="deadline", assessment_coverage=False)
    budget_ctx = trace.get("local_num_ctx") or trace.get("context_limit")
    if budget_ctx:
        from src.engine.evidence_pack import measure_prompt
        measurement = measure_prompt(prompt, budget_ctx, 2048)
        trace.update(assessment_budget_method=measurement.method, assessment_prompt_utf8_bytes=measurement.utf8_bytes,
                     assessment_input_limit=measurement.input_limit)
        if not measurement.fits:
            trace["assessment_reason"] = "context_budget_exceeded"
            return False
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    trace["assessment_call_count"] = 1
    try:
        from inspect import Parameter, signature
        if "local_num_ctx" in trace:
            trace["assessment_timeout_seconds"] = remaining
        kwargs = {"model": model, "timeout": remaining,
                  "options": {"num_ctx": trace.get("local_num_ctx", 4096), "num_predict": 2048}}
        parameters = signature(llm).parameters
        if "generation_config" in parameters or any(p.kind == Parameter.VAR_KEYWORD for p in parameters.values()):
            assessment_model = model if model.startswith("gemini-") else "gemini-2.5-flash"
            kwargs["generation_config"] = assessment_generation_config(assessment_model)
        signature(llm).bind(prompt, **kwargs)
        with response_deadline(deadline), assessment_request():
            output = llm(prompt, **kwargs)
        trace["assessment_output_sha256"] = text_sha256(output) if isinstance(output, str) else None
        if time.monotonic() >= deadline:
            return False
        if output is None or output == "":
            trace["assessment_reason"] = "no_output"
            return False
        assessment = parse_assessment(output, set(extract_citations(raw)))
        if time.monotonic() >= deadline:
            return False
        trace.update(
            assessment_outcome=assessment.outcome,
            assessment_claim_count=len(assessment.claims),
            assessment_coverage=assessment.coverage,
            assessment_missing_facts=list(assessment.missing_facts),
        )
        allowed = delivery_allowed(assessment, resolution)
        trace["assessment_reason"] = "accepted" if allowed else "unsupported"
        return allowed
    except AssessmentProviderError as error:
        trace["assessment_reason"] = error.reason
        return False
    except AssessmentJSONError:
        trace["assessment_reason"] = "invalid_json"
        return False
    except ValueError:
        trace["assessment_reason"] = "invalid_schema"
        return False
    except TimeoutError:
        trace["assessment_reason"] = "timeout"
        return False
    except Exception:
        trace["assessment_reason"] = "transport"
        return False
    finally:
        emit_trace({"event": "answer_assessment", **{k: v for k, v in trace.items() if k.startswith("assessment_")}})


def _articles_match_resolution(raw_articles, resolution):
    if not raw_articles:
        return False
    provisions = {p.id: p for item in resolution.items for p in item.provisions}
    return all(a.id in provisions and a.content == provisions[a.id].text for a in raw_articles)


def reviewed_answer(question, query, day, pipeline, use_llm, model, history, trace,
                    fetch_fn, retriever, verifier, scorer, llm, corpus_path, review_path,
                    progress_cb=None):
    from src.api.answer_service import AnswerResult, _emit_progress
    if fetch_fn is None:
        result = retrieve_and_gate(query, day, pipeline=pipeline, retriever_fn=retriever,
                                   verifier_fn=verifier, corpus_path=corpus_path, reviewed_amendment_path=review_path)
        articles, resolution = result.articles, result.resolved_evidence
        trace.update(raw_article_count=result.raw_article_count, gated_article_count=len(articles))
    else:
        trace["raw_article_count"] = None
        _, _, raw = _invoke_callback(fetch_fn, pipeline, query, day, trace=trace, corpus_path=corpus_path,
                                     reviewed_amendment_path=review_path, verifier_fn=verifier)
        previous = trace.pop("_resolved_evidence", None)
        explicit_req = explicit_evidence_ids(query, corpus_path)
        accounted = ({item.id for item in previous.items} | set(previous.blocked)) if previous is not None else set()
        if (previous is not None and verifier is None and _articles_match_resolution(raw, previous)
                and all(eid in accounted for eid in explicit_req)):
            articles, resolution = raw, previous
        else:
            articles, resolution, _ = project_reviewed_articles(raw, query, day, corpus_path, review_path, verifier)
            if previous is not None:
                from dataclasses import replace
                from src.engine.answer_assessment import merge_blockers
                resolution = replace(resolution, blocked=merge_blockers(previous.blocked, resolution.blocked))
        trace.update(gated_article_count=len(articles))
    _emit_progress(
        progress_cb,
        1,
        "retrieve",
        f"Đang tra cứu các điều khoản còn hiệu lực tại ngày {day} (tìm thấy {len(articles)} căn cứ)...",
    )
    trace.update(all_blockers=resolution.blocked)
    from src.ui.corpus_viewer import load_corpus_index
    metadata = load_corpus_index(corpus_path)
    from src.config import provider_context_limit
    from src.engine.llm_client import _llm_provider
    packing_failure = None
    explicit_ids = explicit_evidence_ids(query, corpus_path)
    if articles:
        from src.engine.reranker import rerank_provisions
        from src.engine.evidence_pack import pack_evidence
        from src.engine.answer_assessment import build_assessment_prompt

        articles = rerank_provisions(
            query, articles, explicit_ids=explicit_ids,
            top_n=max(5, len(explicit_ids) + 4),
        )
        context = provider_context_limit(_llm_provider())
        # Separate unavailable explicit refs so partial verified evidence still displays instead of wiping to [].
        available_requested = [
            cid for cid in explicit_ids
            if any(a.id == cid or a.id.startswith(cid + "_") for a in articles)
        ]
        pack = pack_evidence(
            articles, available_requested, context,
            lambda chosen, omitted: build_irac_prompt(query, day, chosen, history,
                corpus_path=corpus_path, resolved_evidence=resolution),
            lambda chosen, omitted: build_assessment_prompt(query, day, history, chosen, resolution, ""),
            max_extra=5,
        )
        trace.update(selected_evidence_count=len(pack.articles), omitted_evidence_ids=pack.omitted_ids,
                     evidence_pack_reason=pack.reason, context_limit=context)
        articles = list(pack.articles)
        if not pack.ready:
            packing_failure = pack.reason
            trace.update(generation_reason=pack.reason, generation_disposition="safety_bypass",
                         generation_attempted=False, generated_delivered=False)
    _emit_progress(
        progress_cb,
        2,
        "rerank",
        f"Đang chọn lọc và đối chiếu hiệu lực {len(articles)} căn cứ trọng tâm...",
    )
    sections = evidence_sections(question, day, articles, resolution, metadata)
    if packing_failure:
        sections["conclusion"] += " Không thể đóng gói đầy đủ căn cứ bắt buộc trong giới hạn context; chưa gọi mô hình."
    report, raw_response = None, None
    missing_requested = [cid for cid in explicit_ids
                         if not any(a.id == cid or a.id.startswith(cid + "_") for a in articles)]
    if missing_requested and not packing_failure:
        trace.update(generation_reason="unresolved_evidence")
    if use_llm and articles and not missing_requested:
        _emit_progress(
            progress_cb,
            3,
            "generate",
            "Đang tổng hợp câu trả lời và kiểm tra trích dẫn...",
        )
        generated, report, raw_response = _generate(query, day, articles, resolution, llm, scorer,
                                                    model, history, (corpus_path, review_path), trace)
        sections = generated or sections
    if report is None:
        report = _verify("\n".join(sections.values()), day, articles, resolution, scorer, corpus_path, review_path, query)
    statuses = getattr(getattr(report, "report", report), "citations", [])
    allowed = {c.id for c in statuses if c.status in {"VALID", "AMENDED"}}
    cited = set(extract_citations("\n".join(sections.values())))
    if not cited <= allowed:
        articles = [a for a in articles if a.id in allowed]
        sections = evidence_sections(question, day, articles, resolution, metadata)
        trace.update(generated_delivered=False, generation_disposition="draft_replaced" if raw_response else trace["generation_disposition"])
    blockers = [f"Hệ thống chưa xác minh được chứng cứ {unit}: {reason}"
                for unit, reasons in resolution.blocked.items()
                for reason in ((reasons,) if isinstance(reasons, str) else reasons)]
    if not articles and use_llm and not packing_failure:
        trace.update(generation_reason="unresolved_evidence" if blockers else "no_evidence")
    omissions = trace.get("omitted_evidence_ids", ())
    context_warnings = (["Các căn cứ chưa được đưa vào context do giới hạn ngân sách; không có nghĩa là không liên quan: "
                         + ", ".join(omissions)] if omissions else [])
    if packing_failure:
        context_warnings.append("CONTEXT_BUDGET_EXCEEDED: chưa gọi mô hình vì context không chứa đủ căn cứ bắt buộc.")
    if use_llm and trace.get("generation_disposition") == "provider_no_output":
        context_warnings.append(PROVIDER_UNAVAILABLE_WARNING)
    warnings = list(dict.fromkeys(list(getattr(report, "warnings", [])) + blockers + context_warnings + [GROUNDING_LIMITATION]))
    delivered_ok = bool(
        trace.get("generated_delivered")
        and trace.get("assessment_outcome") in {"supported", "needs_facts"}
    )
    missing_facts = list(
        trace.get("assessment_missing_facts")
        or trace.get("understanding_missing_facts")
        or []
    )
    final_citations = extract_citations("\n".join(sections.values()))
    final_cite_set = set(final_citations)
    amended_citation_ids = sorted({
        a.id for a in articles
        if getattr(a, "status", "") == "AMENDED" and a.id in final_cite_set
    })
    resolved_provisions = _build_resolved_provisions(resolution, final_cite_set)
    return AnswerResult(
        query=question, as_of_date=day, pipeline=pipeline, citations=final_citations,
        active_article_ids=[a.id for a in articles], risk_score=getattr(report, "risk_score", 50),
        risk_level=getattr(report, "risk_level", "MEDIUM"), warnings=warnings, irac_sections=sections,
        llm_used=bool(raw_response and raw_response.strip()), raw_llm_response=raw_response,
        answer_status="ANSWERED" if delivered_ok else "INSUFFICIENT_BASIS",
        articles=articles, resolved_evidence=resolution,
        missing_facts=missing_facts, rewritten_query=trace.get("rewritten_query"),
        amended_citation_ids=amended_citation_ids,
        resolved_provisions=resolved_provisions,
    )
