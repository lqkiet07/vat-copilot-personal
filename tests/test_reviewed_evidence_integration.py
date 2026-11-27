import hashlib
import json
from types import SimpleNamespace

import pytest

from src.api import answer_service, chat_service, routes
from src.api.models import QueryRequest
from src.engine.models import ArticleNode
from src.engine.prompt import build_irac_prompt
from src.engine.provision_evidence import resolve_provision_evidence
from src.engine.retrieval_service import retrieve_and_gate
from src.verifier import citation_verifier
from src.verifier.citation_verifier import extract_citations, verify_citations


A = "LUAT_1_2024_D1"
BLOCKED = A + "_K1"
SAFE = A + "_K2"
DAY = "2025-06-01"
TEXT = "Hồ sơ thuế GTGT được lưu trữ theo quy định về chứng từ."


@pytest.fixture
def reviewed(tmp_path, monkeypatch):
    raw = tmp_path / "source.txt"
    raw.write_text("Change clause one. Effective 2025-02-01.", encoding="utf-8")
    corpus = tmp_path / "corpus.json"
    docs = [{"id": "LUAT_1_2024", "code": "1/2024", "issue_date": "2024-01-01",
             "valid_from": "2024-02-01", "valid_to": "9999-12-31", "articles": [
                 {"id": A, "code": "Điều 1", "content": "RAW CONTAINER", "clauses": [
                     {"id": BLOCKED, "content": "UNSAFE OLD TEXT"},
                     {"id": SAFE, "content": TEXT}]}]}]
    corpus.write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    ref = {"path": "source.txt", "sha256": sha(raw), "excerpt": "Change clause one."}
    review = {"schema_version": 1, "oracle": "independent_raw_source_review", "review_complete": True,
              "pins": {"corpus.json": sha(corpus), "source.txt": sha(raw)},
              "documents": [{"doc_id": "LUAT_1_2024", "review_status": "reviewed",
                             "source_paths": ["source.txt"], "issued_on": "2024-01-01",
                             "effective_on": "2024-02-01"}],
              "candidates": [{"id": "change", "source_doc": "LUAT_2_2025",
                              "source_unit": "LUAT_2_2025_D1", "classification": "resolved",
                              "evidence": ref, "source_effective_intervals": [
                                  {"from": "2025-02-01", "to": None, "issued_on": "2025-01-01",
                                   "date_evidence_refs": [ref]}],
                              "expected": {"scope": "clause", "targets": [
                                  {"doc_id": "LUAT_1_2024", "article_id": A, "clause_id": BLOCKED}]}}]}
    ledger = tmp_path / "review.json"
    ledger.write_text(json.dumps(review), encoding="utf-8")
    node = ArticleNode(id=A, doc_id="LUAT_1_2024", code="Điều 1", title="Chứng từ thuế GTGT",
                       content="RAW CONTAINER UNSAFE OLD TEXT", valid_from="2024-02-01", valid_to="9999-12-31")
    fetch = lambda q, d: SimpleNamespace(articles=[node])
    missing = set()

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def run(self, query, **kwargs):
            assert "UNWIND $ids" in query
            rows = [{"cid": cid, "exists": cid not in missing, "valid_from": "2024-02-01",
                     "valid_to": "9999-12-31", "amended_by": ["coarse"], "amendments": []}
                    for cid in kwargs["ids"]]
            return SimpleNamespace(data=lambda: rows)

    monkeypatch.setattr(citation_verifier, "get_neo4j_driver", lambda: SimpleNamespace(session=Session))
    monkeypatch.setattr("src.config.selected_review_path", lambda p: None)
    return SimpleNamespace(corpus_path=str(corpus), reviewed_amendment_path=str(ledger),
                           fetch=fetch, missing=missing, raw=raw)


def semantic_reply(outcome="supported", coverage=True, status="supported"):
    return json.dumps({"outcome": outcome, "coverage": coverage,
                       "claims": [{"text": TEXT, "status": status, "evidence_ids": [SAFE]}],
                       "missing_facts": []})


def options(case):
    return {"corpus_path": case.corpus_path, "reviewed_amendment_path": case.reviewed_amendment_path}


def answer(case, question="Hồ sơ thuế GTGT lưu trữ thế nào?", **kwargs):
    return answer_service.answer_query(question, DAY, retriever_fn=case.fetch, **options(case), **kwargs)


@pytest.mark.parametrize("reference", ["Khoản 1 Điều 1 Luật 1/2024", "Điều 99 Luật 1/2024",
                                       "Khoản 2 Điều 1 Nghị định 1/2024"])
def test_reference_hint_retains_other_evidence_and_all_blockers(reviewed, reference):
    question = f"{reference} quy định gì và hồ sơ thuế GTGT lưu trữ thế nào?"
    result = retrieve_and_gate(question, DAY, retriever_fn=reviewed.fetch, **options(reviewed))
    assert result.active_ids == [SAFE]
    baseline = resolve_provision_evidence([A], DAY, **options(reviewed))
    assert baseline.blocked.items() <= result.resolved_evidence.blocked.items()
    assert BLOCKED not in result.active_ids
    from src.engine.retrieval_service import explicit_evidence_ids
    assert set(explicit_evidence_ids(question, reviewed.corpus_path)) <= result.resolved_evidence.blocked.keys()


@pytest.mark.parametrize("reference", ["Khoản 1 Điều 1 Luật 1/2024", "Điều 99 Luật 1/2024",
                                       "Khoản 2 Điều 1 Nghị định 1/2024", "[LUAT_9_2024_D99]"])
def test_unavailable_requested_unit_cannot_be_answered_by_other_evidence(reviewed, reference):
    def forbidden(*args, **kwargs):
        pytest.fail("Unavailable requested reference must not be answered by another unit")
    result = answer(reviewed, f"{reference} quy định gì?", use_llm=True, llm_fn=forbidden)
    assert result.active_article_ids == [SAFE]
    assert result.answer_status == "INSUFFICIENT_BASIS"
    assert not result.llm_used
    assert "Hệ thống chưa xác minh" in result.irac_sections["conclusion"]
    assert result.resolved_evidence.blocked


@pytest.mark.parametrize("history", [[], [{"role": "user", "content": "Điều 1 Luật 1/2024"}]])
def test_multiple_authority_hints_keep_other_rules_and_history(reviewed, history):
    from pathlib import Path
    from src.engine.answer_assessment import evidence_data
    corpus = Path(reviewed.corpus_path)
    docs = json.loads(corpus.read_text(encoding="utf-8"))
    other = "ND_3_2024_D2"
    extra = A + "_K3"
    docs[0]["articles"][0]["clauses"].append({"id": extra, "content": "Quy định chứng từ khác."})
    docs.append({"id": "ND_3_2024", "code": "3/2024", "issue_date": "2024-01-01",
                 "valid_from": "2024-02-01", "valid_to": "9999-12-31",
                 "articles": [{"id": other, "content": "Quy định bổ sung về lưu trữ."}]})
    corpus.write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
    ledger = Path(reviewed.reviewed_amendment_path)
    review = json.loads(ledger.read_text(encoding="utf-8"))
    review["pins"]["corpus.json"] = hashlib.sha256(corpus.read_bytes()).hexdigest()
    review["documents"].append({**review["documents"][0], "doc_id": "ND_3_2024"})
    ledger.write_text(json.dumps(review), encoding="utf-8")
    question = "Khoản 2 Điều 1 Luật 1/2024 và Điều 2 Nghị định 3/2024 quy định gì; các quy định chứng từ khác?"
    result = retrieve_and_gate(question, DAY, retriever_fn=reviewed.fetch, **options(reviewed))
    assert set(result.active_ids) == {SAFE, extra, other}
    assert set(result.resolved_evidence.blocked) == {A, BLOCKED}
    data = evidence_data(question, DAY, history, result.articles, result.resolved_evidence)
    assert data["history"] == history
    assert {p["id"] for p in data["evidence"]} == {SAFE, extra, other}
    assert set(data["blocked_units"]) == {A, BLOCKED}


def test_reviewed_retrieval_keeps_exact_safe_sibling(reviewed):
    result = retrieve_and_gate("hồ sơ thuế GTGT chứng từ", DAY, retriever_fn=reviewed.fetch, **options(reviewed))
    assert result.active_ids == [SAFE]
    assert [a.content for a in result.articles] == [TEXT]
    assert set(result.resolved_evidence.blocked) == {A, BLOCKED}
    assert result.unresolved_citations == []


@pytest.mark.parametrize("question", ["Hồ sơ thuế GTGT lưu trữ thế nào?", "Chứng từ thuế GTGT có nội dung gì?",
                                      "Hộ kinh doanh xuất khẩu lưu hồ sơ thuế GTGT thế nào?"])
def test_reviewed_no_llm_is_extractive_not_affirmative(reviewed, question):
    result = answer(reviewed, question)
    text = "\n".join(result.irac_sections.values())
    assert result.citations == [SAFE]
    assert TEXT in text
    assert "UNSAFE" not in text and "RAW CONTAINER" not in text
    assert "Áp dụng mức thuế suất" not in text
    assert "nghĩa vụ thuế theo phương pháp" not in text
    assert result.answer_status == "INSUFFICIENT_BASIS"
    assert result.active_article_ids == [SAFE]


def test_reviewed_prompt_never_reloads_raw_corpus(reviewed, monkeypatch):
    resolution = resolve_provision_evidence([A], DAY, **options(reviewed))
    node = reviewed.fetch("", DAY).articles[0].model_copy(update={
        "id": SAFE, "content": TEXT, "resolved_evidence": True})
    def forbidden(*args, **kwargs):
        pytest.fail("raw prompt corpus reload")
    monkeypatch.setattr("src.engine.prompt.load_corpus_index", forbidden)
    monkeypatch.setattr(chat_service, "_load_corpus_index", forbidden)
    prompt = build_irac_prompt("thuế GTGT", DAY, [node], resolved_evidence=resolution)
    chat_prompt = chat_service._build_prompt("thuế GTGT", DAY, [node], (A,), resolved_evidence=resolution)
    for text in (prompt, chat_prompt):
        assert TEXT in text and f"[{SAFE}]" in text
        assert f"[{A}]" not in text
        assert "ngưỡng trở xuống" not in text


@pytest.mark.parametrize("cid,status", [(SAFE, "VALID"), (BLOCKED, "UNRESOLVED"), (A, "UNRESOLVED")])
def test_reviewed_verifier_overrides_coarse_graph_only_with_exact_source(reviewed, cid, status):
    report = verify_citations(f"[{cid}]", DAY, **options(reviewed))
    assert report.citations[0].status == status


def test_reviewed_context_parent_does_not_authorize_descendants(reviewed):
    resolution = resolve_provision_evidence([A], DAY, **options(reviewed))
    report = verify_citations(f"[{SAFE}]", DAY, context_ids={A}, resolved_evidence=resolution, **options(reviewed))
    assert report.citations[0].status == "IRRELEVANT"
    reviewed.missing.add(SAFE)
    assert verify_citations(f"[{SAFE}]", DAY, **options(reviewed)).citations[0].status == "NOT_FOUND"
    reviewed.missing.clear()
    assert verify_citations(f"[{SAFE}]", DAY, relevance_map={SAFE: False}, **options(reviewed)).citations[0].status == "IRRELEVANT"


def test_reviewed_source_tampering_fails_closed(reviewed):
    reviewed.raw.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA mismatch"):
        verify_citations(f"[{SAFE}]", DAY, **options(reviewed))


@pytest.mark.parametrize("draft_id", [BLOCKED, A, "LUAT_9_2024_D9"])
def test_reviewed_llm_blocked_cite_is_not_delivered(reviewed, draft_id):
    draft = f"Issue: thuế GTGT\nRule: [{draft_id}]\nApplication: unsupported\nConclusion: unsafe conclusion"
    result = answer(reviewed, use_llm=True, llm_fn=lambda *a, **k: draft)
    assert "unsafe conclusion" not in "\n".join(result.irac_sections.values())
    assert result.citations == [SAFE]


@pytest.mark.parametrize("claim", ["Thuế GTGT áp dụng 73%.", "Mọi hồ sơ đều được chấp thuận vô điều kiện.", f"{TEXT} [Thuế GTGT áp dụng 73%]"])
def test_valid_citation_cannot_certify_fabricated_assertions_query_chat(reviewed, monkeypatch, claim):
    draft = f"Issue: {TEXT}\nRule: {TEXT} [{SAFE}]\nApplication: {claim} [{SAFE}]\nConclusion: {claim} [{SAFE}]"
    trace = {}
    outputs = iter([draft, semantic_reply("unsupported", False, "contradicted")])
    result = answer(reviewed, use_llm=True, llm_fn=lambda *a, **k: next(outputs), trace=trace)
    assert trace["assessment_call_count"] == 1
    assert result.answer_status == "INSUFFICIENT_BASIS"
    assert result.raw_llm_response == draft
    assert claim not in result.irac_sections["application"]
    assert trace["generation_disposition"] == "draft_replaced"
    monkeypatch.setattr(chat_service, "selected_corpus_kwargs", lambda: {"corpus_path": reviewed.corpus_path})
    monkeypatch.setattr(chat_service, "selected_review_path", lambda p: reviewed.reviewed_amendment_path)
    monkeypatch.setattr(chat_service, "query_temporal_rag", reviewed.fetch)
    outputs = iter([draft, semantic_reply("unsupported", False, "contradicted")])
    monkeypatch.setattr(chat_service, "generate_llm_response", lambda *a, **k: next(outputs))
    done = list(chat_service.stream_chat([{"role": "user", "content": "Hồ sơ thuế GTGT?"}], DAY))[-1][1]
    assert done["answer_status"] == "INSUFFICIENT_BASIS"
    assert claim not in done["message"]


@pytest.mark.parametrize("reference,expected", [("Khoản 2 Điều 1 Luật 1/2024", SAFE),
                                                 ("Điều 1 Luật 1/2024", SAFE)])
def test_natural_exact_and_parent_references_keep_safe_coverage(reviewed, reference, expected):
    result = answer(reviewed, f"{reference} quy định hồ sơ thuế GTGT thế nào?")
    assert expected in result.citations
    assert BLOCKED not in result.citations and A not in result.citations
    assert any("Hệ thống" in w for w in result.warnings)


@pytest.mark.parametrize("reference", ["Luật 999/2099", "Nghị định 1/2024", "Luật 1/2099"])
def test_unknown_authority_without_article_never_uses_other_authority(reviewed, reference):
    result = answer(reviewed, f"Hồ sơ thuế GTGT theo {reference}?")
    assert result.citations == [SAFE]
    assert result.answer_status == "INSUFFICIENT_BASIS"
    assert "Hệ thống chưa xác minh" in result.irac_sections["conclusion"]


def test_reference_parser_preserves_family_year_clause_point(tmp_path):
    from src.engine.retrieval_service import explicit_evidence_ids
    corpus = tmp_path / "refs.json"
    corpus.write_text(json.dumps([{"id": family + "_181_2025", "articles": [
        {"id": family + "_181_2025_D5", "clauses": [{"id": family + "_181_2025_D5_K25",
        "points": [{"id": family + "_181_2025_D5_K25_b"}]}]}]} for family in ("LUAT", "ND")]), encoding="utf-8")
    assert explicit_evidence_ids("điểm b khoản 25 Điều 5 Luật 181/2025", str(corpus)) == ["LUAT_181_2025_D5_K25_b"]
    assert explicit_evidence_ids("Khoản 25 Điều 5 Nghị định 181/2025", str(corpus)) == ["ND_181_2025_D5_K25"]
    assert explicit_evidence_ids("Luật 181/2025", str(corpus)) == ["LUAT_181_2025_D5"]


@pytest.mark.parametrize("channel", ["query", "chat"])
def test_source_excerpt_does_not_certify_missing_question_coverage(reviewed, monkeypatch, channel):
    question = "Hồ sơ thuế GTGT thiếu chứng từ thì được chấp thuận không?"
    draft = f"Issue: {TEXT}\nRule: {TEXT} [{SAFE}]\nApplication: {TEXT} [{SAFE}]\nConclusion: {TEXT} [{SAFE}]"
    if channel == "query":
        outputs = iter([draft, semantic_reply("unknown", False)])
        result = answer(reviewed, question, use_llm=True, llm_fn=lambda *a, **k: next(outputs))
        assert result.answer_status == "INSUFFICIENT_BASIS"
        assert any("độ bao phủ" in w for w in result.warnings)
    else:
        monkeypatch.setattr(chat_service, "selected_corpus_kwargs", lambda: {"corpus_path": reviewed.corpus_path})
        monkeypatch.setattr(chat_service, "selected_review_path", lambda p: reviewed.reviewed_amendment_path)
        monkeypatch.setattr(chat_service, "query_temporal_rag", reviewed.fetch)
        outputs = iter([draft, semantic_reply("unknown", False)])
        monkeypatch.setattr(chat_service, "generate_llm_response", lambda *a, **k: next(outputs))
        done = list(chat_service.stream_chat([{"role": "user", "content": question}], DAY))[-1][1]
        assert done["answer_status"] == "INSUFFICIENT_BASIS"
        assert any("độ bao phủ" in w for w in done["warnings"])


def test_compact_natural_reference_keeps_exact_clause(tmp_path):
    from src.engine.retrieval_service import explicit_evidence_ids
    path = tmp_path / "compact.json"
    path.write_text(json.dumps([{"id": "LUAT_48_2024", "articles": [{"id": "LUAT_48_2024_D5"}]}]), encoding="utf-8")
    assert explicit_evidence_ids("Khoan25 Dieu5 Luat48", str(path)) == ["LUAT_48_2024_D5_K25"]


def test_verified_generated_subset_keeps_report_risk(reviewed):
    draft = f"Issue: {TEXT}\nRule: {TEXT} [{SAFE}]\nApplication: {TEXT} [{SAFE}]\nConclusion: {TEXT} [{SAFE}]"
    trace = {}
    outputs = iter([draft, semantic_reply()])
    result = answer(reviewed, use_llm=True, llm_fn=lambda *a, **k: next(outputs), trace=trace)
    assert result.answer_status == "ANSWERED"
    assert trace["generation_disposition"] == "generated_delivered"
    assert trace["assessment_call_count"] == 1
    assert any("không phải" in w for w in result.warnings)
    assert (result.risk_score, result.risk_level) == (0, "LOW")
    assert any("Hệ thống" in w and BLOCKED in w for w in result.warnings)
    assert not any("bổ sung" in w.lower() for w in result.warnings)


@pytest.mark.parametrize("output", [None, "", "   ", "invalid actual output"])
def test_reviewed_output_provenance_and_blocker_warnings(reviewed, output):
    from src.engine.diagnostics import text_sha256
    trace = {}
    result = answer(reviewed, use_llm=True, llm_fn=lambda *a, **k: output, trace=trace)
    actual = bool(output and output.strip())
    assert result.llm_used is actual
    assert result.raw_llm_response == output
    assert trace["generation_disposition"] == ("draft_replaced" if actual else "provider_no_output")
    assert trace["output_sha256"] == (text_sha256(output) if output is not None else None)
    assert any("Hệ thống" in w and BLOCKED in w for w in result.warnings)
    assert trace["delivered_sha256"] == text_sha256("\n".join(result.irac_sections.values()))


def test_scorer_cannot_certify_source_changed_during_generation(reviewed):
    draft = f"Issue: thuế GTGT\nRule: {TEXT} [{SAFE}]\nApplication: {TEXT} [{SAFE}]\nConclusion: {TEXT} [{SAFE}]"
    def generator(*args, **kwargs):
        reviewed.raw.write_text("tampered", encoding="utf-8")
        return draft
    def scorer(text, day, **kwargs):
        return SimpleNamespace(risk_score=0, risk_level="LOW", warnings=[], report=SimpleNamespace(
            citations=[SimpleNamespace(id=cid, status="VALID") for cid in extract_citations(text)]))
    with pytest.raises(ValueError, match="SHA mismatch"):
        answer(reviewed, use_llm=True, llm_fn=generator, score_fn=scorer)


def test_safe_scorer_cannot_certify_missing_resolver_provenance(reviewed):
    def scorer(text, day, **kwargs):
        return SimpleNamespace(risk_score=0, risk_level="LOW", warnings=[], report=SimpleNamespace(
            citations=[SimpleNamespace(id=cid, status="VALID") for cid in extract_citations(text)]))
    draft = f"Issue: thuế GTGT\nRule: [{BLOCKED}]\nApplication: unsafe\nConclusion: unsafe"
    result = answer(reviewed, use_llm=True, score_fn=scorer, llm_fn=lambda *a, **k: draft)
    assert result.citations == [SAFE]
    assert result.answer_status == "INSUFFICIENT_BASIS"
    assert "unsafe" not in result.irac_sections["application"]


def test_missing_literal_reference_never_becomes_answer_citation(reviewed):
    result = answer(reviewed, "Hồ sơ thuế GTGT theo [LUAT_9_2024_D99]?")
    assert result.citations == [SAFE]
    assert result.answer_status == "INSUFFICIENT_BASIS"
    assert "Hệ thống chưa xác minh" in result.irac_sections["conclusion"]


def test_explicit_missing_reference_fails_closed(reviewed):
    result = answer(reviewed, "Điều 99 Luật 1/2024 quy định hồ sơ thuế GTGT thế nào?")
    assert result.answer_status == "INSUFFICIENT_BASIS"
    assert result.citations == [SAFE]
    assert "missing explicit unit" in result.irac_sections["conclusion"]


def test_query_chat_sse_consume_same_projection_and_buffer_draft(reviewed, monkeypatch):
    monkeypatch.setattr(chat_service, "selected_corpus_kwargs", lambda: {"corpus_path": reviewed.corpus_path})
    monkeypatch.setattr(chat_service, "selected_review_path", lambda p: reviewed.reviewed_amendment_path)
    monkeypatch.setattr("src.config.selected_review_path", lambda p: reviewed.reviewed_amendment_path)
    monkeypatch.setattr(chat_service, "query_temporal_rag", reviewed.fetch)
    monkeypatch.setattr(answer_service, "query_temporal_rag", reviewed.fetch)
    def forbidden(*a, **k):
        pytest.fail("chat raw corpus reload")
    monkeypatch.setattr(chat_service, "_load_corpus_index", forbidden)
    prompts = []
    def stream(prompt, **kwargs):
        prompts.append(prompt)
        return f"Unsafe [{BLOCKED}]"
    monkeypatch.setattr(chat_service, "generate_llm_response", stream)
    events = list(chat_service.stream_chat([{"role": "user", "content": "Hồ sơ thuế GTGT lưu trữ thế nào?"}], DAY))
    done = events[-1][1]
    assert done["citations"] == [SAFE]
    assert TEXT in done["message"] and "Unsafe" not in done["message"]
    assert "".join(p["delta"] for n, p in events if n == "token") == done["message"]
    assert prompts and TEXT in prompts[0] and "UNSAFE OLD TEXT" not in prompts[0]
    monkeypatch.setattr(routes, "execute_demo_query", lambda **kw: answer(reviewed, kw["query"]).to_dict())
    result = routes.post_query(QueryRequest(query="Hồ sơ thuế GTGT lưu trữ thế nào?", as_of_date=DAY))
    assert result.citations == done["citations"]
    assert [s["id"] for s in done["sources"]] == [SAFE]


def test_real_query_adapter_preserves_safe_sibling(reviewed, monkeypatch):
    from src.ui import demo_service
    monkeypatch.setattr(demo_service, "query_temporal_rag", reviewed.fetch)
    monkeypatch.setattr(demo_service, "selected_corpus_kwargs", lambda: {"corpus_path": reviewed.corpus_path})
    monkeypatch.setattr("src.config.selected_review_path", lambda p: reviewed.reviewed_amendment_path)
    result = demo_service.execute_demo_query("Hồ sơ thuế GTGT lưu trữ thế nào?", DAY)
    assert result["citations"] == [SAFE]
    assert TEXT in result["irac_sections"]["application"]


def test_reviewed_chat_accepts_verified_plain_text_and_separates_trace(reviewed, monkeypatch, tmp_path):
    monkeypatch.setattr(chat_service, "selected_corpus_kwargs", lambda: {"corpus_path": reviewed.corpus_path})
    monkeypatch.setattr(chat_service, "selected_review_path", lambda p: reviewed.reviewed_amendment_path)
    monkeypatch.setattr("src.config.selected_review_path", lambda p: reviewed.reviewed_amendment_path)
    monkeypatch.setattr(chat_service, "query_temporal_rag", reviewed.fetch)
    draft = f"Issue: Hồ sơ thuế GTGT\nRule: {TEXT} [{SAFE}]\nApplication: {TEXT} [{SAFE}]\nConclusion: {TEXT} [{SAFE}]"
    outputs = iter([draft, semantic_reply()])
    monkeypatch.setattr(chat_service, "generate_llm_response", lambda *a, **k: next(outputs))
    log = tmp_path / "trace.jsonl"
    monkeypatch.setenv("VAT_DIAGNOSTICS", str(log))
    events = list(chat_service.stream_chat([{"role": "user", "content": "Hồ sơ thuế GTGT?"}], DAY))
    assessment_event, inner, outer = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert assessment_event["event"] == "answer_assessment"
    assert inner["generation_disposition"] == outer["generation_disposition"] == "generated_delivered"
    assert inner["output_sha256"] == outer["output_sha256"]
    assert inner["assessment_output_sha256"] != inner["output_sha256"]
    assert TEXT in events[-1][1]["message"]
    assert events[-1][1]["answer_status"] == "ANSWERED"
    assert events[-1][1]["citations"] == [SAFE]
    assert any("Hệ thống" in w and BLOCKED in w for w in events[-1][1]["warnings"])


def test_provider_no_output_emits_unavailable_warning(reviewed):
    trace = {}
    result = answer(reviewed, use_llm=True, llm_fn=lambda *a, **k: None, trace=trace)
    assert trace["generation_disposition"] == "provider_no_output"
    assert any("LLM_PROVIDER_UNAVAILABLE" in w for w in result.warnings)


def test_reviewed_answer_exposes_resolved_provisions_and_clean_progress(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus.json"
    docs = [
        {
            "id": "ND_68_2026",
            "code": "68/2026/NĐ-CP",
            "issue_date": "2026-01-01",
            "valid_from": "2026-01-01",
            "valid_to": "9999-12-31",
            "articles": [
                {
                    "id": "ND_68_2026_D3",
                    "code": "Điều 3",
                    "content": "Điều 3",
                    "clauses": [
                        {
                            "id": "ND_68_2026_D3_K1",
                            "content": "1. Hộ kinh doanh có mức doanh thu năm từ 500 triệu đồng trở xuống thuộc đối tượng không chịu thuế giá trị gia tăng.",
                        }
                    ],
                }
            ],
        },
        {
            "id": "ND_141_2026",
            "code": "141/2026/NĐ-CP",
            "issue_date": "2026-04-29",
            "valid_from": "2026-01-01",
            "valid_to": "9999-12-31",
            "articles": [
                {
                    "id": "ND_141_2026_D1",
                    "code": "Điều 1",
                    "content": "Điều 1",
                    "clauses": [{"id": "ND_141_2026_D1_K1", "content": "Sửa đổi ngưỡng doanh thu."}],
                }
            ],
        },
    ]
    corpus.write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
    raw_text = (
        "Ban hành ngày 29 tháng 4 năm 2026. "
        "Hiệu lực từ ngày 01 tháng 01 năm 2026. "
        "Sửa đổi cụm từ 500 triệu đồng thành 01 tỷ đồng."
    )
    raw = tmp_path / "nd141.txt"
    raw.write_text(raw_text, encoding="utf-8")
    raw_bytes = raw.read_bytes()
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

    def make_span_ref(sub: str) -> dict:
        bsub = sub.encode("utf-8")
        start = raw_bytes.index(bsub)
        return {"path": "nd141.txt", "sha256": sha(raw), "byte_start": start, "byte_end": start + len(bsub), "text": sub}

    ref_main = {"path": "nd141.txt", "sha256": sha(raw), "excerpt": raw_text}
    ref_issue = {"path": "nd141.txt", "sha256": sha(raw), "excerpt": "Ban hành ngày 29 tháng 4 năm 2026."}
    ref_eff = {"path": "nd141.txt", "sha256": sha(raw), "excerpt": "Hiệu lực từ ngày 01 tháng 01 năm 2026."}
    review = {
        "schema_version": 1,
        "oracle": "independent_raw_source_review",
        "review_complete": True,
        "pins": {"corpus.json": sha(corpus), "nd141.txt": sha(raw)},
        "documents": [
            {
                "doc_id": "ND_68_2026",
                "review_status": "reviewed",
                "source_paths": ["nd141.txt"],
                "issued_on": "2026-01-01",
                "effective_on": "2026-01-01",
            },
            {
                "doc_id": "ND_141_2026",
                "review_status": "reviewed",
                "source_paths": ["nd141.txt"],
                "issued_on": "2026-04-29",
                "effective_on": "2026-01-01",
                "date_evidence": {
                    "path": "nd141.txt",
                    "issued_excerpt": "Ban hành ngày 29 tháng 4 năm 2026.",
                    "effective_excerpt": "Hiệu lực từ ngày 01 tháng 01 năm 2026.",
                },
            },
        ],
        "candidates": [
            {
                "id": "nd141_threshold",
                "source_doc": "ND_141_2026",
                "source_unit": "ND_141_2026_D1_K1",
                "classification": "unresolved",
                "evidence": ref_main,
                "source_effective_intervals": [
                    {
                        "from": "2026-01-01",
                        "to": None,
                        "issued_on": "2026-04-29",
                        "date_evidence_refs": [ref_eff],
                        "issuance_evidence": ref_issue,
                        "commencement_evidence": ref_eff,
                        "commencement_basis": "retroactive",
                    }
                ],
                "operative_scope": {
                    "status": "approved",
                    "scope": "phrase_replacement",
                    "targets": [
                        {"doc_id": "ND_68_2026", "article_id": "ND_68_2026_D3", "clause_id": "ND_68_2026_D3_K1"}
                    ],
                    "evidence_refs": [ref_main],
                },
                "operations": [
                    {
                        "candidate_id": "nd141_threshold",
                        "source_unit": "ND_141_2026_D1_K1",
                        "target_id": "ND_68_2026_D3_K1",
                        "action": "phrase_replace",
                        "issued_on": "2026-04-29",
                        "effective_on": "2026-01-01",
                        "issuance_evidence": ref_issue,
                        "commencement_evidence": ref_eff,
                        "old": make_span_ref("500 triệu đồng"),
                        "new": make_span_ref("01 tỷ đồng"),
                        "expected_count": 1,
                    }
                ],
                "expected": {
                    "scope": "phrase_replacement",
                    "targets": [
                        {"doc_id": "ND_68_2026", "article_id": "ND_68_2026_D3"}
                    ],
                },
            }
        ],
    }
    ledger = tmp_path / "review.json"
    ledger.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def run(self, query, **kwargs):
            rows = [
                {
                    "cid": cid,
                    "exists": True,
                    "valid_from": "2026-01-01",
                    "valid_to": "9999-12-31",
                    "amended_by": [],
                    "amendments": [],
                }
                for cid in kwargs["ids"]
            ]
            return SimpleNamespace(data=lambda: rows)

    monkeypatch.setattr(citation_verifier, "get_neo4j_driver", lambda: SimpleNamespace(session=Session))
    monkeypatch.setattr("src.config.selected_review_path", lambda p: None)

    node = ArticleNode(
        id="ND_68_2026_D3_K1",
        doc_id="ND_68_2026",
        code="Khoản 1 Điều 3",
        title="Thuế giá trị gia tăng",
        content="1. Hộ kinh doanh có mức doanh thu năm từ 500 triệu đồng trở xuống thuộc đối tượng không chịu thuế giá trị gia tăng.",
        valid_from="2026-01-01",
        valid_to="9999-12-31",
    )
    events = []
    res = answer_service.answer_query(
        "Hộ kinh doanh doanh thu bao nhiêu không chịu thuế GTGT?",
        "2026-03-20",
        retriever_fn=lambda q, d: SimpleNamespace(articles=[node]),
        corpus_path=str(corpus),
        reviewed_amendment_path=str(ledger),
        progress_cb=events.append,
    )
    assert "ND_68_2026_D3_K1" in res.resolved_provisions
    meta = res.resolved_provisions["ND_68_2026_D3_K1"]
    assert meta["status"] == "AMENDED"
    assert "01 tỷ đồng" in meta["text"]
    assert meta["amended_by"] == [
        {"source_unit": "ND_141_2026_D1_K1", "effective_on": "2026-01-01", "action": "phrase_replace"}
    ]
    joined = " ".join(ev["message"] for ev in events)
    assert not any(tok in joined for tok in ("Neo4j", "Reranker", "IRAC"))


def test_whole_document_mention_does_not_force_context_budget_exceeded(reviewed):
    from src.api.reviewed_answer import _partition_explicit_ids, evidence_sections

    articles = reviewed.fetch("Hồ sơ theo Luật 1/2024?", DAY).articles
    strict, doc_scope = _partition_explicit_ids("Theo Luật 1/2024 thì lưu trữ thế nào?", ["LUAT_1_2024_D1", "LUAT_1_2024_D2"], articles)
    assert strict == []
    assert doc_scope == ["LUAT_1_2024_D1", "LUAT_1_2024_D2"]

    inactive_strict, inactive_scope = _partition_explicit_ids("Theo Luật 99/2030 thì sao?", ["LUAT_99_2030_D1", "LUAT_99_2030_D2"], articles)
    assert inactive_strict == ["LUAT_99_2030_D1"]
    assert inactive_scope == []

    fallback = evidence_sections("quán net?", DAY, articles, missing_facts=["Doanh thu hằng năm?"])
    assert "vui lòng cho biết thêm" in fallback["conclusion"].lower()


def test_reviewed_answer_retains_point_lead_in_and_counterpart_threshold_clause_via_expansion(
    tmp_path, monkeypatch
):
    from src.engine import reranker as reranker_mod

    raw = tmp_path / "source.txt"
    raw.write_text("Mức thuế suất 5% và ngưỡng doanh thu 01 tỷ đồng.", encoding="utf-8")
    corpus = tmp_path / "corpus.json"
    docs = [
        {
            "id": "LUAT_48_2024",
            "code": "48/2024",
            "issue_date": "2024-11-26",
            "valid_from": "2025-07-01",
            "valid_to": "9999-12-31",
            "articles": [
                {
                    "id": "LUAT_48_2024_D9",
                    "code": "Điều 9",
                    "title": "Thuế suất",
                    "content": "Điều 9. Thuế suất",
                    "clauses": [
                        {
                            "id": "LUAT_48_2024_D9_K2",
                            "code": "Khoản 2",
                            "content": "2. Mức thuế suất 5% áp dụng đối với hàng hóa sau:",
                            "points": [
                                {"id": "LUAT_48_2024_D9_K2_b", "code": "Điểm b", "content": "Phân bón."}
                            ],
                        }
                    ],
                }
            ],
        },
        {
            "id": "ND_68_2026",
            "code": "68/2026",
            "issue_date": "2026-03-05",
            "valid_from": "2026-01-01",
            "valid_to": "9999-12-31",
            "articles": [
                {
                    "id": "ND_68_2026_D3",
                    "code": "Điều 3",
                    "title": "Hộ kinh doanh",
                    "content": "Điều 3",
                    "clauses": [
                        {"id": "ND_68_2026_D3_K1", "code": "Khoản 1", "content": "Từ 01 tỷ đồng trở xuống không chịu thuế."},
                        {"id": "ND_68_2026_D3_K2", "code": "Khoản 2", "content": "Trên 01 tỷ đồng nộp trực tiếp."},
                    ],
                }
            ],
        },
    ]
    corpus.write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    review = {
        "schema_version": 1,
        "oracle": "independent_raw_source_review",
        "review_complete": True,
        "pins": {"corpus.json": sha(corpus), "source.txt": sha(raw)},
        "documents": [
            {"doc_id": "LUAT_48_2024", "review_status": "reviewed", "source_paths": ["source.txt"], "issued_on": "2024-11-26", "effective_on": "2025-07-01"},
            {"doc_id": "ND_68_2026", "review_status": "reviewed", "source_paths": ["source.txt"], "issued_on": "2026-03-05", "effective_on": "2026-01-01"},
        ],
        "candidates": [],
    }
    ledger = tmp_path / "review.json"
    ledger.write_text(json.dumps(review), encoding="utf-8")

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def run(self, query, **kwargs):
            rows = [{"cid": cid, "exists": True, "valid_from": "2025-07-01", "valid_to": "9999-12-31", "amended_by": [], "amendments": []} for cid in kwargs["ids"]]
            return SimpleNamespace(data=lambda: rows)

    monkeypatch.setattr(citation_verifier, "get_neo4j_driver", lambda: SimpleNamespace(session=_Session))
    monkeypatch.setattr("src.config.selected_review_path", lambda p: None)
    # Simulate rerank keeping only D9_K2_b and D3_K2 while omitting their companion parent/sibling.
    monkeypatch.setattr(
        reranker_mod,
        "rerank_provisions",
        lambda q, arts, **kw: [a for a in arts if a.id in {"LUAT_48_2024_D9_K2_b", "ND_68_2026_D3_K2"}],
    )

    pool_nodes = [
        ArticleNode(id="LUAT_48_2024_D9", doc_id="LUAT_48_2024", code="Điều 9", title="Thuế suất", content="Điều 9. Thuế suất", valid_from="2025-07-01", valid_to="9999-12-31"),
        ArticleNode(id="ND_68_2026_D3", doc_id="ND_68_2026", code="Điều 3", title="Hộ kinh doanh", content="Điều 3", valid_from="2026-01-01", valid_to="9999-12-31"),
    ]
    res = answer_service.answer_query(
        "Phân bón và hộ kinh doanh doanh thu 650 triệu thế nào?",
        "2026-03-20",
        retriever_fn=lambda q, d: SimpleNamespace(articles=pool_nodes),
        corpus_path=str(corpus),
        reviewed_amendment_path=str(ledger),
    )
    assert "LUAT_48_2024_D9_K2" in res.active_article_ids
    assert "ND_68_2026_D3_K1" in res.active_article_ids


def test_reviewed_answer_keeps_interleaved_companion_and_multi_doc_coverage_under_9_item_pack_cap(
    tmp_path, monkeypatch
):
    from src.engine import reranker as reranker_mod

    raw = tmp_path / "source.txt"
    raw.write_text("Nội dung tổng hợp 181, 359, 144 và ngưỡng 01 tỷ đồng.", encoding="utf-8")
    corpus = tmp_path / "corpus.json"
    docs = [
        {
            "id": "ND_68_2026",
            "code": "68/2026",
            "issue_date": "2026-03-05",
            "valid_from": "2026-01-01",
            "valid_to": "9999-12-31",
            "articles": [
                {
                    "id": "ND_68_2026_D3",
                    "code": "Điều 3",
                    "title": "Hộ kinh doanh",
                    "content": "Điều 3",
                    "clauses": [
                        {"id": "ND_68_2026_D3_K1", "code": "Khoản 1", "content": "Từ 01 tỷ đồng trở xuống không chịu thuế."},
                        {"id": "ND_68_2026_D3_K2", "code": "Khoản 2", "content": "Trên 01 tỷ đồng nộp trực tiếp."},
                    ],
                }
            ],
        },
        {
            "id": "ND_181_2025",
            "code": "181/2025",
            "issue_date": "2025-07-01",
            "valid_from": "2025-07-01",
            "valid_to": "9999-12-31",
            "articles": [
                {
                    "id": "ND_181_2025_D2",
                    "code": "Điều 2",
                    "title": "Quy định 181",
                    "content": "Điều 2",
                    "clauses": [
                        {"id": f"ND_181_2025_D2_K{i}", "code": f"Khoản {i}", "content": f"Khoản 181 số {i}."}
                        for i in range(1, 5)
                    ],
                }
            ],
        },
        {
            "id": "ND_359_2025",
            "code": "359/2025",
            "issue_date": "2025-12-31",
            "valid_from": "2026-01-01",
            "valid_to": "9999-12-31",
            "articles": [
                {
                    "id": "ND_359_2025_D1",
                    "code": "Điều 1",
                    "title": "Quy định 359",
                    "content": "Điều 1",
                    "clauses": [
                        {"id": f"ND_359_2025_D1_K{i}", "code": f"Khoản {i}", "content": f"Khoản 359 số {i}."}
                        for i in range(1, 10)
                    ],
                }
            ],
        },
        {
            "id": "ND_144_2026",
            "code": "144/2026",
            "issue_date": "2026-04-01",
            "valid_from": "2026-04-01",
            "valid_to": "9999-12-31",
            "articles": [
                {
                    "id": "ND_144_2026_D1",
                    "code": "Điều 1",
                    "title": "Quy định 144",
                    "content": "Điều 1",
                    "clauses": [
                        {"id": f"ND_144_2026_D1_K{i}", "code": f"Khoản {i}", "content": f"Khoản 144 số {i}."}
                        for i in range(1, 3)
                    ],
                }
            ],
        },
    ]
    corpus.write_text(json.dumps(docs, ensure_ascii=False), encoding="utf-8")
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    review = {
        "schema_version": 1,
        "oracle": "independent_raw_source_review",
        "review_complete": True,
        "pins": {"corpus.json": sha(corpus), "source.txt": sha(raw)},
        "documents": [
            {"doc_id": "ND_68_2026", "review_status": "reviewed", "source_paths": ["source.txt"], "issued_on": "2026-03-05", "effective_on": "2026-01-01"},
            {"doc_id": "ND_181_2025", "review_status": "reviewed", "source_paths": ["source.txt"], "issued_on": "2025-07-01", "effective_on": "2025-07-01"},
            {"doc_id": "ND_359_2025", "review_status": "reviewed", "source_paths": ["source.txt"], "issued_on": "2025-12-31", "effective_on": "2026-01-01"},
            {"doc_id": "ND_144_2026", "review_status": "reviewed", "source_paths": ["source.txt"], "issued_on": "2026-04-01", "effective_on": "2026-04-01"},
        ],
        "candidates": [],
    }
    ledger = tmp_path / "review.json"
    ledger.write_text(json.dumps(review), encoding="utf-8")

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def run(self, query, **kwargs):
            rows = [{"cid": cid, "exists": True, "valid_from": "2025-07-01", "valid_to": "9999-12-31", "amended_by": [], "amendments": []} for cid in kwargs["ids"]]
            return SimpleNamespace(data=lambda: rows)

    monkeypatch.setattr(citation_verifier, "get_neo4j_driver", lambda: SimpleNamespace(session=_Session))
    monkeypatch.setattr("src.config.selected_review_path", lambda p: None)

    # Case A: 9-item cap must still keep D3_K1 right after D3_K2 despite 8 extra clauses.
    keep_a = {"ND_68_2026_D3_K2"} | {f"ND_359_2025_D1_K{i}" for i in range(1, 9)}
    monkeypatch.setattr(
        reranker_mod,
        "rerank_provisions",
        lambda q, arts, **kw: [a for a in arts if a.id == "ND_68_2026_D3_K2"] + [a for a in arts if a.id in keep_a and a.id != "ND_68_2026_D3_K2"],
    )
    pool_a = [
        ArticleNode(id="ND_68_2026_D3", doc_id="ND_68_2026", code="Điều 3", title="Hộ kinh doanh", content="Điều 3", valid_from="2026-01-01", valid_to="9999-12-31"),
        ArticleNode(id="ND_359_2025_D1", doc_id="ND_359_2025", code="Điều 1", title="Quy định 359", content="Điều 1", valid_from="2026-01-01", valid_to="9999-12-31"),
    ]
    res_a = answer_service.answer_query(
        "Hộ kinh doanh doanh thu 650 triệu thế nào?",
        "2026-04-15",
        retriever_fn=lambda q, d: SimpleNamespace(articles=pool_a),
        corpus_path=str(corpus),
        reviewed_amendment_path=str(ledger),
    )
    assert "ND_68_2026_D3_K1" in res_a.active_article_ids

    # Case B: multi-doc scope must preserve coverage for 181, 359, and 144 under 9-item cap.
    monkeypatch.setattr(
        reranker_mod,
        "rerank_provisions",
        lambda q, arts, **kw: [a for a in arts if a.id.startswith("ND_359_2025_")][:9],
    )
    pool_b = [
        ArticleNode(id="ND_359_2025_D1", doc_id="ND_359_2025", code="Điều 1", title="Quy định 359", content="Điều 1", valid_from="2026-01-01", valid_to="9999-12-31"),
        ArticleNode(id="ND_181_2025_D2", doc_id="ND_181_2025", code="Điều 2", title="Quy định 181", content="Điều 2", valid_from="2025-07-01", valid_to="9999-12-31"),
        ArticleNode(id="ND_144_2026_D1", doc_id="ND_144_2026", code="Điều 1", title="Quy định 144", content="Điều 1", valid_from="2026-04-01", valid_to="9999-12-31"),
    ]
    res_b = answer_service.answer_query(
        "So sánh Nghị định 181/2025/NĐ-CP, Nghị định 359/2025/NĐ-CP và Nghị định 144/2026/NĐ-CP?",
        "2026-04-15",
        retriever_fn=lambda q, d: SimpleNamespace(articles=pool_b),
        corpus_path=str(corpus),
        reviewed_amendment_path=str(ledger),
    )
    active_docs = {aid.split("_D", 1)[0] for aid in res_b.active_article_ids}
    assert {"ND_181_2025", "ND_359_2025", "ND_144_2026"}.issubset(active_docs)





