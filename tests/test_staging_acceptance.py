import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
import urllib.request
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIVE = pytest.mark.skipif(
    os.getenv("TEST_STAGING_ACCEPTANCE") != "1",
    reason="Read-only live staging acceptance requires explicit opt-in",
)
HOUSEHOLD = "Hộ kinh doanh có doanh thu 300 triệu đồng mỗi năm có phải nộp thuế GTGT không?"
EXPORT = "Doanh nghiệp tại Cần Thơ xuất khẩu phần mềm sang Nhật Bản áp dụng thuế suất thuế GTGT bao nhiêu?"
UNCERTAIN = "Theo Điều 5 Văn bản hợp nhất 25/2026, hộ kinh doanh doanh thu 300 triệu đồng có chắc chắn không chịu thuế GTGT không?"


@pytest.fixture(scope="module")
def corpus():
    assert os.environ.get("VAT_ENV") == "staging"
    assert os.environ.get("LLM_PROVIDER") == "gemini"
    assert os.environ.get("NEO4J_TEST_URI") in {
        "bolt://localhost:17687", "bolt://127.0.0.1:17687"}
    assert os.environ.get("NEO4J_URI") in {
        "bolt://localhost:17687", "bolt://127.0.0.1:17687"}
    expected = {
        "VAT_CORPUS_PATH": "vat_corpus.staging.json",
        "VAT_EMBEDDINGS_PATH": "article_embeddings.staging.json",
        "VAT_EMBEDDING_META_PATH": "embedding_meta.staging.json",
    }
    for name, filename in expected.items():
        assert Path(os.environ[name]).resolve() == ROOT / "data/processed" / filename
    assert Path(os.environ["VAT_AMENDMENT_REVIEW_PATH"]).resolve() == ROOT / "tests/fixtures/amendment_review.staging.json"
    return json.loads((ROOT / os.environ["VAT_CORPUS_PATH"]).read_text(encoding="utf-8"))


def request(path, payload=None, ui=False):
    name = "TEST_STAGING_UI_URL" if ui else "TEST_STAGING_BASE_URL"
    base = os.environ[name]
    parsed = urlsplit(base)
    assert parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
    assert parsed.port == (15173 if ui else 18000)
    assert not parsed.username and not parsed.password
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=240) as response:
            text = response.read().decode("utf-8")
            return text if path.endswith("/stream") or path == "/" else json.loads(text)
    except HTTPError as exc:
        pytest.fail(f"{path}: HTTP {exc.code}")


IMPORTER = "Theo khoản 2 Điều 4 Luật 48/2024, tổ chức nhập khẩu hàng hóa chịu thuế có là người nộp thuế GTGT không? Chỉ trả lời câu hỏi xác định người nộp thuế này bằng IRAC ngắn gọn, dựa trên căn cứ được cung cấp; không mở rộng sang vấn đề khác. Hãy trích dẫn chính xác mã khoản 2 trong căn cứ được cung cấp, không chỉ mã Điều 4."
CLAUSE = "LUAT_48_2024_D4_K2"
WORDING = "Tổ chức, cá nhân nhập khẩu hàng hóa chịu thuế giá trị gia tăng (sau đây gọi là người nhập khẩu)."
CORPUS = ROOT / "data/processed/vat_corpus.staging.json"
REVIEW = ROOT / "tests/fixtures/amendment_review.staging.json"
CASES = [
    {"question": question, "as_of": day, "disposition": "safety_bypass", "reason": "unresolved_evidence",
     "evidence_id": evidence, "candidate_id": candidate, "source_unit": source}
    for question, evidence, candidate, source in [
        (HOUSEHOLD, "LUAT_48_2024_D5", "LUAT_149_2025:13:38", "LUAT_149_2025_D1_K1_b"),
        (EXPORT, "LUAT_48_2024_D9", "LUAT_90_2025:552:40", "LUAT_90_2025_D4"),
    ] for day in ("2026-03-20", "2026-06-20")
]


def sha(path):
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def importer_oracle():
    from html.parser import HTMLParser
    ledger = json.loads(REVIEW.read_text(encoding="utf-8"))
    assert ledger["review_complete"] is True
    for path in (CORPUS, ROOT / "data/raw/01_luat_48_2024.html"):
        assert sha(path) == ledger["pins"][path.relative_to(ROOT).as_posix()]
    review = next(doc for doc in ledger["documents"] if doc["doc_id"] == "LUAT_48_2024")
    assert review["review_status"] == "reviewed" and review["gaps"] == []
    assert review["effective_on"] == "2025-07-01"
    parts = []
    parser = HTMLParser()
    parser.handle_data = parts.append
    parser.feed((ROOT / review["body_scan"]["path"]).read_text(encoding="utf-8"))
    raw = " ".join(" ".join(parts).split())
    assert WORDING in raw and review["date_evidence"]["effective_excerpt"] in raw
    docs = json.loads(CORPUS.read_text(encoding="utf-8"))
    doc = next(doc for doc in docs if doc["id"] == "LUAT_48_2024")
    article = next(a for a in doc["articles"] if a["id"] == "LUAT_48_2024_D4")
    clause = next(c for c in article["clauses"] if c["id"] == CLAUSE)
    assert clause["content"] == WORDING
    assert clause["valid_from"] == "2025-07-01" and clause["valid_to"] == "9999-12-31"
    assert not clause.get("provision_spans") and not doc.get("provision_spans")
    from src.verifier.citation_verifier import _load_verification_metadata, _load_reviewed_amendments, _reviewed_uncertainty
    index = _load_verification_metadata(str(CORPUS))
    _load_reviewed_amendments(str(REVIEW), str(CORPUS), index)
    assert _reviewed_uncertainty(CLAUSE, "2026-03-20", index) is None
    assert not any(CLAUSE in rule.get("target_ids", []) or rule.get("container_id") == CLAUSE
                   for rule in review["provision_effective_dates"])
    from src.graph.importer import plan_corpus_import
    plan = plan_corpus_import(str(CORPUS))
    assert not any(relation.get("source_unit") == CLAUSE
                   for relation in plan["resolved_amends"] + plan["unresolved_amends"])
    return {"category": "taxable_goods_importer", "citation": CLAUSE, "wording": WORDING,
            "article": article, "doc": doc, "review_sha256": sha(REVIEW), "corpus_sha256": sha(CORPUS)}


def contract_example():
    from src.engine.diagnostics import text_sha256
    sections = {"issue": "Người nộp thuế", "rule": f"[{CLAUSE}] {WORDING}",
                "application": "Tổ chức nhập khẩu hàng hóa chịu thuế là người nộp thuế GTGT.",
                "conclusion": f"Tổ chức nhập khẩu là người nộp thuế [{CLAUSE}]."}
    raw = "\n".join(sections.values())
    sections["raw_text"] = raw
    body = {"answer_status": "ANSWERED", "llm_used": True, "raw_llm_response": raw,
            "irac_sections": sections, "citations": [CLAUSE], "warnings": []}
    provider = {"event": "provider_generation", "provider": "gemini", "model": "gemini-2.5-flash",
                "has_output": True, "prompt_sha256": text_sha256("controlled prompt"), "output_sha256": text_sha256(raw)}
    trace = {"event": "query", "generation_disposition": "generated_delivered", "generation_reason": "verified_draft",
             "generation_attempted": True, "generated_delivered": True,
             "prompt_sha256": provider["prompt_sha256"], "output_sha256": provider["output_sha256"],
             "delivered_sha256": text_sha256("\n".join(sections.values()))}
    return body, trace, provider


def assert_generated_importer(body, trace, provider):
    from src.engine.diagnostics import text_sha256
    assert body["answer_status"] == "ANSWERED", "BLOCKED: evidence is not generation-ready"
    assert body["llm_used"] is True and body["raw_llm_response"]
    assert trace["generation_disposition"] == "generated_delivered", "BLOCKED: generation not delivered"
    assert trace["generation_reason"] == "verified_draft"
    assert trace["generation_attempted"] is True and trace["generated_delivered"] is True
    assert provider["event"] == "provider_generation" and provider["provider"] == "gemini"
    assert provider["model"] == "gemini-2.5-flash" and provider["has_output"] is True
    assert provider["prompt_sha256"] == trace["prompt_sha256"]
    assert provider["output_sha256"] == trace["output_sha256"] == text_sha256(body["raw_llm_response"])
    sections = body["irac_sections"]
    assert trace["delivered_sha256"] == text_sha256("\n".join(sections.values()))
    assert sections["raw_text"] == body["raw_llm_response"] and CLAUSE in body["citations"]
    assert not any("Bản nháp LLM đã bị thay" in warning for warning in body["warnings"])
    application = sections["application"].lower()
    conclusion = sections["conclusion"].lower()
    for field, text in (("application", application), ("conclusion", conclusion)):
        assert "nhập khẩu" in text and "nộp thuế" in text
        assert re.search(r"(?:là|thuộc đối tượng) người nộp thuế", text)
        match = re.search(r"\d\s*%|phần trăm|triệu|tỷ đồng|doanh thu|ngưỡng|miễn thuế|được miễn|không phải nộp|không là người nộp|không thuộc|không chịu thuế", text)
        assert not match, f"BLOCKED: field={field} matched={match.group(0)!r} text={sections[field][:12000]}"
    assert "hàng hóa chịu thuế" in application


def controlled_importer_provider(events, trace):
    providers = [event for event in events if event.get("event") == "provider_generation"]
    assert len(providers) == 1, "BLOCKED: strict Gemini provider evidence missing or ambiguous"
    provider = providers[0]
    diagnostics = [event for event in events if event.get("event") == "gemini_response"]
    assert 1 <= len(diagnostics) <= 3, "BLOCKED: Gemini response attempts missing or unbounded"
    diagnostic = diagnostics[-1]
    config_hash = diagnostic.get("request_config_sha256")
    assert isinstance(config_hash, str) and re.fullmatch(r"[0-9a-f]{64}", config_hash), "BLOCKED: config hash missing"
    for index, attempt in enumerate(diagnostics, 1):
        assert events.index(attempt) < events.index(provider), "BLOCKED: attempt after logical generation"
        assert attempt.get("attempt") == index and type(attempt.get("attempt")) is int, "BLOCKED: attempt index mismatch"
        assert attempt.get("max_attempts") == 3, "BLOCKED: attempt limit mismatch"
        assert attempt.get("provider") == provider["provider"] == "gemini", "BLOCKED: provider mismatch"
        assert attempt.get("model") == provider["model"] == "gemini-2.5-flash", "BLOCKED: model mismatch"
        assert attempt.get("prompt_sha256") == provider["prompt_sha256"] == trace["prompt_sha256"], "BLOCKED: prompt hash mismatch"
        assert attempt.get("request_config_sha256") == config_hash, "BLOCKED: config hash mismatch"
        retry = index < len(diagnostics)
        assert attempt.get("will_retry") is retry, "BLOCKED: retry disposition mismatch"
        assert attempt.get("retry_delay") == (index if retry else 0), "BLOCKED: retry delay mismatch"
        if retry:
            assert attempt.get("http_status") in {502, 503, 504}, "BLOCKED: non-retryable HTTP status"
            assert attempt.get("status") == "http_server_error", "BLOCKED: non-retryable response status"
    assert provider["output_sha256"] == trace["output_sha256"], "BLOCKED: output hash mismatch"
    assert diagnostic.get("http_status") == 200, "BLOCKED: final attempt did not succeed"
    assert diagnostic["status"] in {"success", "max_tokens"}, "BLOCKED: unexpected Gemini response status"
    return provider


@pytest.mark.parametrize("status", ["success", "max_tokens"])
def test_offline_controlled_importer_event_correlation(status):
    body, trace, provider = contract_example()
    diagnostic = {"event": "gemini_response", "provider": provider["provider"],
                  "model": provider["model"], "prompt_sha256": provider["prompt_sha256"], "status": status,
                  "attempt": 1, "max_attempts": 3, "will_retry": False, "retry_delay": 0,
                  "request_config_sha256": "a" * 64, "http_status": 200}
    events = [diagnostic, {"event": "future_diagnostic"}, provider]
    actual = controlled_importer_provider(events, trace)
    assert actual is provider
    assert_generated_importer(body, trace, actual)


@pytest.mark.parametrize("mutation", [
    "missing_generation", "duplicate_generation", "missing_diagnostic", "duplicate_diagnostic",
    "provider", "model", "prompt", "output", "status",
])
def test_offline_controlled_importer_event_correlation_rejects_invalid_evidence(mutation):
    _, trace, provider = contract_example()
    diagnostic = {"event": "gemini_response", "provider": provider["provider"],
                  "model": provider["model"], "prompt_sha256": provider["prompt_sha256"], "status": "success",
                  "attempt": 1, "max_attempts": 3, "will_retry": False, "retry_delay": 0,
                  "request_config_sha256": "a" * 64, "http_status": 200}
    events = [diagnostic, provider]
    if mutation.startswith("missing_"):
        events.remove(provider if mutation == "missing_generation" else diagnostic)
    elif mutation.startswith("duplicate_"):
        events.append(provider if mutation == "duplicate_generation" else diagnostic)
    elif mutation == "output":
        provider["output_sha256"] = "0" * 64
    else:
        field = "prompt_sha256" if mutation == "prompt" else mutation
        diagnostic[field] = "invalid"
    with pytest.raises(AssertionError, match="BLOCKED"):
        controlled_importer_provider(events, trace)


@pytest.mark.parametrize("codes", [(503,), (502, 504)])
def test_offline_controlled_importer_accepts_bounded_retry_evidence(codes):
    _, trace, provider = contract_example()
    diagnostics = [{"event": "gemini_response", "provider": "gemini", "model": provider["model"],
                    "prompt_sha256": provider["prompt_sha256"], "request_config_sha256": "a" * 64,
                    "attempt": index, "max_attempts": 3, "will_retry": True, "retry_delay": index,
                    "status": "http_server_error", "http_status": code}
                   for index, code in enumerate(codes, 1)]
    diagnostics.append(dict(diagnostics[0], attempt=len(codes) + 1, will_retry=False,
                            retry_delay=0, status="success", http_status=200))
    assert controlled_importer_provider(diagnostics + [provider], trace) is provider


@pytest.mark.parametrize("mutation", ["code", "index", "config", "prompt", "model", "provider",
    "retry", "delay", "max_attempts", "final_retry", "final_http", "final_delay",
    "extra_attempt", "late_attempt", "extra_provider", "status", "order", "missing_config"])
def test_offline_controlled_importer_rejects_fake_retry_evidence(mutation):
    _, trace, provider = contract_example()
    first = {"event": "gemini_response", "provider": "gemini", "model": provider["model"],
             "prompt_sha256": provider["prompt_sha256"], "request_config_sha256": "a" * 64,
             "attempt": 1, "max_attempts": 3, "will_retry": True, "retry_delay": 1,
             "status": "http_server_error", "http_status": 503}
    final = dict(first, attempt=2, will_retry=False, retry_delay=0, status="success", http_status=200)
    events = [first, final, provider]
    fields = {"code": ("http_status", 429), "index": ("attempt", 2),
              "config": ("request_config_sha256", "b" * 64), "prompt": ("prompt_sha256", "b" * 64),
              "model": ("model", "other"), "provider": ("provider", "openai"),
              "retry": ("will_retry", False), "delay": ("retry_delay", 2),
              "max_attempts": ("max_attempts", 4), "status": ("status", "success")}
    if mutation in fields:
        key, value = fields[mutation]
        first[key] = value
    elif mutation.startswith("final_"):
        key, value = {"final_retry": ("will_retry", True), "final_http": ("http_status", 503),
                      "final_delay": ("retry_delay", 1)}[mutation]
        final[key] = value
    elif mutation == "extra_attempt":
        events = [first, dict(first, attempt=2, retry_delay=2), dict(first, attempt=3, retry_delay=3),
                  dict(final, attempt=4), provider]
    elif mutation == "late_attempt":
        events = [first, provider, final]
    elif mutation == "extra_provider":
        events.append(dict(provider, provider="openai"))
    elif mutation == "order":
        events = [final, first, provider]
    else:
        del first["request_config_sha256"]
    with pytest.raises(AssertionError, match="BLOCKED"):
        controlled_importer_provider(events, trace)


def read_new_traces(path, offset):
    with path.open("rb") as stream:
        stream.seek(offset)
        return [json.loads(line) for line in stream.read().splitlines() if line.strip()]


def correlated_query(rows, question, as_of):
    queries = [row for row in rows if row.get("event") == "query" and row.get("query") == question
               and row.get("as_of_date") == as_of and row.get("pipeline") == "P3_FullSystem"]
    assert len(queries) == 1, "BLOCKED: missing or ambiguous fresh server query trace"
    trace = queries[0]
    providers = [row for row in rows if row.get("event") == "provider_generation"
                 and row.get("prompt_sha256") == trace.get("prompt_sha256")
                 and row.get("output_sha256") == trace.get("output_sha256")]
    return trace, providers


def server_query(question, as_of, use_llm):
    health = request("/api/health")
    assert health["artifacts"]["review_sha256"] == sha(REVIEW)
    assert health["artifacts"]["corpus_sha256"] == sha(CORPUS)
    path = Path(os.environ["TEST_STAGING_SERVER_TRACE"])
    assert path.is_file(), "BLOCKED: expose the server VAT_DIAGNOSTICS file read-only to this test process"
    offset = path.stat().st_size
    body = request("/api/query", {"query": question, "as_of_date": as_of,
                                  "pipeline": "P3", "use_llm": use_llm, "model": "gemini-2.5-flash"})
    trace, providers = correlated_query(read_new_traces(path, offset), question, as_of)
    return body, trace, providers


def test_offline_r5_oracle_and_case_contract():
    oracle = importer_oracle()
    assert oracle["category"] == "taxable_goods_importer"
    assert oracle["citation"] == "LUAT_48_2024_D4_K2"
    assert len(CASES) == 4
    assert {case["reason"] for case in CASES} == {"unresolved_evidence"}
    ledger = json.loads(REVIEW.read_text(encoding="utf-8"))
    for case in CASES:
        candidate = next(c for c in ledger["candidates"] if c["id"] == case["candidate_id"])
        assert candidate["classification"] == "unresolved"
        assert candidate["source_unit"] == case["source_unit"]
        assert any(t["article_id"] == case["evidence_id"] for t in candidate["expected"]["targets"])
        assert any(interval["from"] <= case["as_of"] for interval in candidate["source_effective_intervals"])
        source = ROOT / candidate["evidence"]["path"]
        assert sha(source) == candidate["evidence"]["sha256"]
        snapshot = json.loads(source.read_text(encoding="utf-8"))
        from html.parser import HTMLParser
        parts = []
        parser = HTMLParser()
        parser.handle_data = parts.append
        parser.feed(snapshot["html"])
        text = " ".join(" ".join(parts).split())
        evidence = candidate["evidence"]
        start = evidence["locator"]["normalized_text_start"]
        assert text[start:start + len(evidence["excerpt"])] == evidence["excerpt"]
        assert evidence["parent_context"] in text
        assert case["disposition"] == "safety_bypass"


@pytest.mark.parametrize("mutation", ["bypass", "replacement", "provider", "output", "delivered", "rate", "threshold", "category"])
def test_offline_r5_generation_contract_rejects_false_success(mutation):
    body, trace, provider = contract_example()
    if mutation == "bypass":
        trace["generation_disposition"] = "safety_bypass"
    elif mutation == "replacement":
        trace["generated_delivered"] = False
    elif mutation == "provider":
        provider["provider"] = "ollama"
    elif mutation == "output":
        provider["output_sha256"] = "0" * 64
    elif mutation == "delivered":
        trace["delivered_sha256"] = "0" * 64
    else:
        body["irac_sections"]["application"] = {
            "rate": "Người nhập khẩu nộp thuế 10%.",
            "threshold": "Người nhập khẩu nộp thuế khi doanh thu trên 200 triệu đồng.",
            "category": "Hộ kinh doanh không phải nộp thuế.",
        }[mutation]
        from src.engine.diagnostics import text_sha256
        sections = body["irac_sections"]
        raw = "\n".join(value for key, value in sections.items() if key != "raw_text")
        sections["raw_text"] = body["raw_llm_response"] = raw
        provider["output_sha256"] = trace["output_sha256"] = text_sha256(raw)
        trace["delivered_sha256"] = text_sha256("\n".join(sections.values()))
    with pytest.raises(AssertionError):
        assert_generated_importer(body, trace, provider)


def test_offline_r5_generation_contract_requires_real_delivery():
    assert_generated_importer(*contract_example())


@pytest.mark.parametrize("field", ["application", "conclusion"])
@pytest.mark.parametrize("claim", [
    "Tổ chức nhập khẩu hàng hóa chịu thuế là người nộp thuế GTGT và được miễn thuế.",
    "Tổ chức nhập khẩu hàng hóa chịu thuế là người nộp thuế GTGT nhưng được miễn nghĩa vụ nộp thuế.",
    "Tổ chức nhập khẩu hàng hóa chịu thuế là người nộp thuế GTGT với thuế suất 10%.",
    "Tổ chức nhập khẩu hàng hóa chịu thuế có thể phải nộp thuế GTGT.",
    "Tổ chức nhập khẩu hàng hóa chịu thuế không là người nộp thuế GTGT.",
])
def test_offline_importer_oracle_rejects_unsupported_claims(field, claim):
    from src.engine.diagnostics import text_sha256
    body, trace, provider = contract_example()
    sections = body["irac_sections"]
    sections[field] = claim
    raw = "\n".join(value for key, value in sections.items() if key != "raw_text")
    sections["raw_text"] = body["raw_llm_response"] = raw
    provider["output_sha256"] = trace["output_sha256"] = text_sha256(raw)
    trace["delivered_sha256"] = text_sha256("\n".join(sections.values()))
    with pytest.raises(AssertionError):
        assert_generated_importer(body, trace, provider)


@pytest.mark.parametrize("count", [0, 2])
def test_offline_r5_trace_correlation_rejects_missing_or_ambiguous_queries(count):
    row = {"event": "query", "query": IMPORTER, "as_of_date": "2026-03-20", "pipeline": "P3_FullSystem"}
    with pytest.raises(AssertionError, match="BLOCKED"):
        correlated_query([row] * count, IMPORTER, "2026-03-20")


@pytest.mark.parametrize("field", ["application", "conclusion"])
def test_offline_importer_rejection_identifies_field_token_and_bounded_text(field):
    from src.engine.diagnostics import text_sha256
    body, trace, provider = contract_example()
    sections = body["irac_sections"]
    offending = "Tổ chức nhập khẩu hàng hóa chịu thuế là người nộp thuế GTGT và được miễn thuế."
    sections[field] = offending + " " + "x" * 13000 + "END_NOT_CAPTURED"
    raw = "\n".join(value for key, value in sections.items() if key != "raw_text")
    sections["raw_text"] = body["raw_llm_response"] = raw
    provider["output_sha256"] = trace["output_sha256"] = text_sha256(raw)
    trace["delivered_sha256"] = text_sha256("\n".join(sections.values()))
    with pytest.raises(AssertionError) as error:
        assert_generated_importer(body, trace, provider)
    message = str(error.value)
    assert f"field={field}" in message
    assert "matched='được miễn'" in message
    assert offending in message
    assert "END_NOT_CAPTURED" not in message
    assert len(message) < 12500


def test_offline_r5_trace_reader_excludes_prior_requests(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text(json.dumps({"event": "provider_generation", "prompt_sha256": "stale"}) + "\n", encoding="utf-8")
    offset = path.stat().st_size
    body, trace, provider = contract_example()
    trace.update(query=IMPORTER, as_of_date="2026-03-20", pipeline="P3_FullSystem")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(provider) + "\n" + json.dumps(trace) + "\n")
    actual, providers = correlated_query(read_new_traces(path, offset), IMPORTER, "2026-03-20")
    assert providers == [provider]
    assert_generated_importer(body, actual, providers[0])


def test_offline_r7_prompt_exposes_only_grounded_clause_ids():
    from src.engine.models import ArticleNode
    from src.engine.prompt import build_irac_prompt
    from src.ui.corpus_viewer import load_corpus_index
    oracle = importer_oracle()
    meta = load_corpus_index(str(CORPUS))["LUAT_48_2024_D4"]
    article = ArticleNode(id="LUAT_48_2024_D4", doc_id="LUAT_48_2024", code=meta["code"], title=meta["title"],
                          content=meta["text"], valid_from=meta["valid_from"], valid_to=meta["valid_to"])
    prompt = build_irac_prompt(IMPORTER, "2026-03-20", [article], corpus_path=str(CORPUS))
    assert f"[{CLAUSE}] {oracle['wording']}" in prompt
    assert article.id == "LUAT_48_2024_D4"
    missing = article.model_copy(update={"content": "Không có nguyên văn khoản trong ngữ cảnh."})
    assert f"[{CLAUSE}]" not in build_irac_prompt(IMPORTER, "2026-03-20", [missing], corpus_path=str(CORPUS))
    assert f"[{CLAUSE}]" not in build_irac_prompt(IMPORTER, "2024-01-01", [article], corpus_path=str(CORPUS))


@pytest.mark.parametrize("retries", [0, 1, 2])
def test_offline_r5_real_prompt_provider_and_verifier_contract(monkeypatch, retries):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from src.api.answer_service import answer_query
    from src.engine.models import ArticleNode
    from src.ui.corpus_viewer import load_corpus_index
    from src.engine.diagnostics import text_sha256
    oracle = importer_oracle()
    meta = load_corpus_index(str(CORPUS))["LUAT_48_2024_D4"]
    article = ArticleNode(id="LUAT_48_2024_D4", doc_id="LUAT_48_2024", code=meta["code"], title=meta["title"],
                          content=meta["text"], valid_from=meta["valid_from"], valid_to=meta["valid_to"])
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.side_effect = lambda query, **kw: MagicMock(data=lambda: [
        {"cid": cid, "exists": True, "valid_from": "2025-07-01", "valid_to": "9999-12-31", "amendments": []}
        for cid in kw["ids"]])
    monkeypatch.setattr("src.verifier.citation_verifier.get_neo4j_driver", lambda: driver)
    example, _, _ = contract_example()
    draft = "\n".join(key.title() + ": " + value for key, value in example["irac_sections"].items() if key != "raw_text")
    response = MagicMock(status=200)
    response.__enter__.return_value = response
    response.read.return_value = json.dumps({"candidates": [{"content": {"parts": [{"text": draft}]}}]}).encode()
    prompts = []
    clock = SimpleNamespace(now=100.0)
    def sleep(delay):
        clock.now += delay
    monkeypatch.setattr("src.engine.llm_client.time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep))
    def transport(req, timeout):
        prompt = json.loads(req.data)["contents"][0]["parts"][0]["text"]
        assert oracle["wording"] in prompt
        assert f"[{CLAUSE}]" in prompt
        assert f"[{CLAUSE}] {WORDING}" in prompt
        prompts.append(prompt)
        if len(prompts) <= retries:
            raise HTTPError("https://offline.invalid", 503, "offline", {}, None)
        return response
    monkeypatch.setattr("urllib.request.urlopen", transport)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "offline-test-key")
    monkeypatch.delenv("VAT_DIAGNOSTICS", raising=False)
    events = []
    monkeypatch.setattr("src.engine.llm_client.emit_trace", events.append)
    trace = {}
    result = answer_query(IMPORTER, "2026-03-20", use_llm=True, model="gemini-2.5-flash", trace=trace,
                          corpus_path=str(CORPUS), reviewed_amendment_path=str(REVIEW),
                          retriever_fn=lambda *a: SimpleNamespace(articles=[article]))
    assert len(prompts) == retries + 1 and len(set(prompts)) == 1
    provider = controlled_importer_provider(events, trace)
    diagnostic = [event for event in events if event["event"] == "gemini_response"][-1]
    assert diagnostic["provider"] == "gemini" and diagnostic["model"] == "gemini-2.5-flash"
    assert diagnostic["status"] == "success"
    assert diagnostic["prompt_sha256"] == trace["prompt_sha256"] == text_sha256(prompts[0])
    assert_generated_importer(result.to_dict(), trace, provider)


def summary(body):
    return json.dumps(body, ensure_ascii=False)[:6000]


def assert_uncertain(body):
    assert body["answer_status"] == "INSUFFICIENT_BASIS", summary(body)
    text = body.get("irac_sections", {}).get("conclusion", body.get("message", ""))
    assert re.search(r"chưa đủ căn cứ|không đủ căn cứ", text, re.I), summary(body)
    assert not re.search(r"(?<!\d)(?:0\s*%|500\s*triệu|200\s*triệu)", text, re.I), summary(body)
    assert body["warnings"], summary(body)


@LIVE
def test_health_identity_and_ui_proxy(corpus):
    from src.engine.embedding_meta import validate_artifact_identity

    expected = validate_artifact_identity(*(ROOT / os.environ[name] for name in (
        "VAT_EMBEDDING_META_PATH", "VAT_EMBEDDINGS_PATH", "VAT_CORPUS_PATH")))
    expected["review_sha256"] = sha(REVIEW)
    for ui in (False, True):
        health = request("/api/health", ui=ui)
        assert health["status"] == "healthy" and health["neo4j_connected"], summary(health)
        assert health["environment"] == "staging" and health["artifacts"] == expected
    assert expected["count"] == 494
    assert len(corpus) == 33 and sum(len(doc["articles"]) for doc in corpus) == 494
    assert '<div id="root">' in request("/", ui=True)


@LIVE
def test_all_document_summaries_and_new_details(corpus):
    docs = request("/api/documents")
    assert {doc["id"] for doc in docs} == {doc["id"] for doc in corpus}
    assert len(docs) == 33
    for doc_id in ("ND_144_2026", "TT_89_2026", "LUAT_09_2026", "VBHN_25_2026"):
        actual = request("/api/documents/" + doc_id)
        expected = next(doc for doc in corpus if doc["id"] == doc_id)
        assert actual == expected, doc_id


@pytest.mark.parametrize("article_id,boundary", [
    ("ND_144_2026_D1", "2026-06-20"),
    ("LUAT_09_2026_D1", "2026-01-01"),
    ("LUAT_48_2024_D17", "2026-01-01"),
])
@pytest.mark.parametrize("offset", [-1, 0, 1], ids=["before", "on", "after"])
@LIVE
def test_corpus_temporal_boundaries_read_only(corpus, article_id, boundary, offset):
    from datetime import date, timedelta
    from src.verifier.citation_verifier import verify_citations

    article = next(article for doc in corpus for article in doc["articles"] if article["id"] == article_id)
    assert article["valid_from"] == boundary
    as_of = (date.fromisoformat(boundary) + timedelta(days=offset)).isoformat()
    report = verify_citations(f"[{article_id}]", as_of, corpus_path=os.environ["VAT_CORPUS_PATH"])
    citation, = report.citations
    assert citation.status in ({"NOT_YET_EFFECTIVE"} if offset < 0 else {"VALID", "AMENDED"}), str(citation)


@pytest.mark.parametrize("use_llm", [False, True], ids=["llm_off", "llm_on"])
@pytest.mark.parametrize("case", CASES, ids=[("household" if c["question"] == HOUSEHOLD else "export") + c["as_of"] for c in CASES])
@LIVE
def test_p3_household_export(corpus, case, use_llm):
    body, trace, providers = server_query(case["question"], case["as_of"], use_llm)
    assert body["as_of_date"] == case["as_of"] and body["query"] == case["question"]
    assert_uncertain(body)
    assert body["llm_used"] is False and body["raw_llm_response"] is None
    assert case["evidence_id"] in body["citations"], "BLOCKED: expected relevant uncertain evidence missing"
    assert any(case["source_unit"] in warning and case["candidate_id"] in warning for warning in body["warnings"])
    assert trace["generation_disposition"] == (case["disposition"] if use_llm else "deterministic_off")
    assert trace["generation_reason"] == (case["reason"] if use_llm else "llm_disabled")
    assert trace["generation_attempted"] is False and trace["generated_delivered"] is False
    assert providers == []


def capture_importer_evidence(body, trace, providers):
    evidence_path = os.environ.get("TEST_IMPORTER_EVIDENCE_PATH")
    if evidence_path:
        path = Path(evidence_path).resolve()
        assert path.parent == (ROOT / ".scratch").resolve()
        sections = body.get("irac_sections", {})
        evidence = {"query": IMPORTER, "answer_status": body.get("answer_status"),
                    "application": sections.get("application", "")[:12000],
                    "conclusion": sections.get("conclusion", "")[:12000],
                    "citations": body.get("citations"),
                    "generation_disposition": trace.get("generation_disposition"),
                    "prompt_sha256": trace.get("prompt_sha256"),
                    "output_sha256": trace.get("output_sha256"),
                    "provider_count": len(providers)}
        with path.open("x", encoding="utf-8") as stream:
            json.dump(evidence, stream, ensure_ascii=False, indent=2)


@LIVE
def test_generated_importer_application_is_mandatory(corpus):
    importer_oracle()
    body, trace, providers = server_query(IMPORTER, "2026-03-20", True)
    capture_importer_evidence(body, trace, providers)
    assert len(providers) == 1, "BLOCKED: no unique correlated Gemini generation"
    assert_generated_importer(body, trace, providers[0])


@LIVE
def test_controlled_importer_real_gemini_application_is_mandatory(corpus, monkeypatch):
    from types import SimpleNamespace
    from src.api.answer_service import answer_query
    from src.engine.models import ArticleNode
    from src.engine.llm_client import generate_llm_response
    from src.verifier.citation_verifier import verify_citations
    from src.ui.corpus_viewer import load_corpus_index
    oracle = importer_oracle()
    meta = load_corpus_index(str(CORPUS))["LUAT_48_2024_D4"]
    article = ArticleNode(id="LUAT_48_2024_D4", doc_id="LUAT_48_2024", code=meta["code"], title=meta["title"],
                          content=meta["text"], valid_from=meta["valid_from"], valid_to=meta["valid_to"])
    report = verify_citations(f"[{CLAUSE}]", "2026-03-20", corpus_path=str(CORPUS),
                              reviewed_amendment_path=str(REVIEW))
    assert report.citations[0].status == "VALID", "BLOCKED: source-reviewed importer clause ineligible in actual graph"
    events = []
    monkeypatch.setattr("src.engine.llm_client.emit_trace", events.append)
    trace = {}
    result = answer_query(
        IMPORTER, "2026-03-20", use_llm=True, model="gemini-2.5-flash", trace=trace,
        corpus_path=str(CORPUS), reviewed_amendment_path=str(REVIEW), llm_fn=generate_llm_response,
        retriever_fn=lambda *a: SimpleNamespace(articles=[article]),
    )
    capture_importer_evidence(result.to_dict(), trace, events)
    assert oracle["review_sha256"] == sha(REVIEW)
    provider = controlled_importer_provider(events, trace)
    assert_generated_importer(result.to_dict(), trace, provider)


@pytest.mark.parametrize("stream", [False, True], ids=["chat", "sse"])
@LIVE
def test_multiturn_uncertainty_stays_fail_closed(corpus, stream):
    payload = {"messages": [{"role": "user", "content": HOUSEHOLD},
                            {"role": "assistant", "content": "Cần kiểm tra căn cứ và thời điểm áp dụng."},
                            {"role": "user", "content": UNCERTAIN}],
               "as_of_date": "2026-06-20", "model": "gemini-2.5-flash"}
    body = request("/api/chat/stream" if stream else "/api/chat", payload)
    if stream:
        events = []
        for frame in body.replace("\r\n", "\n").split("\n\n"):
            if not frame.strip():
                continue
            lines = frame.splitlines()
            name = next(line[7:] for line in lines if line.startswith("event: "))
            data = json.loads("\n".join(line[6:] for line in lines if line.startswith("data: ")))
            assert name != "error", summary(data)
            events.append((name, data))
        assert events[0][0] == "meta" and events[-1][0] == "done", summary(events)
        body = events[-1][1]
        tokens = "".join(data["delta"] for name, data in events if name == "token")
        assert tokens == body["message"], summary(events)
    assert body["as_of_date"] == payload["as_of_date"]
    assert_uncertain(body)
