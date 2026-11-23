"""Behaviour tests for the VAT Copilot chat service."""
from types import SimpleNamespace

import pytest

from src.api import chat_service
from src.api.chat_models import ChatMessage
from src.engine.llm_client import generate_llm_response_stream
from src.engine.models import ArticleNode
from src.verifier.models import VerificationReport, VerificationResult

@pytest.mark.parametrize("mixed", [False, True])
def test_step86_chat_canonical_uncertainty_skips_generation(monkeypatch, mixed):
    from src.verifier.models import CitationStatus
    article = _fake_article()
    uncertain = article.model_copy(update={"id": "VBHN_25_2026_D1"})
    articles = [article, uncertain] if mixed else [uncertain]
    monkeypatch.setattr(chat_service, "query_temporal_rag", lambda *a: SimpleNamespace(articles=articles))
    monkeypatch.setattr(chat_service, "verify_citations", lambda *a, **k: SimpleNamespace(citations=[
        CitationStatus(id=a.id, status="UNRESOLVED" if a.id == uncertain.id else "VALID",
                       exists=True, is_active=None if a.id == uncertain.id else True,
                       message="Exact chat uncertainty" if a.id == uncertain.id else "Valid")
        for a in articles
    ]))

    def forbidden(*a, **k):
        pytest.fail("Canonical uncertainty must not enter chat generation")

    monkeypatch.setattr(chat_service, "generate_llm_response_stream", forbidden)
    response = chat_service.build_response([{"role": "user", "content": VAT_QUESTION}])
    assert response.answer_status == "INSUFFICIENT_BASIS"
    assert uncertain.id in response.citations
    assert "Exact chat uncertainty" in response.warnings
    assert "chưa đủ căn cứ" in response.message.lower()
    assert "không có điều khoản nào còn hiệu lực" not in response.message.lower()
    assert response.llm_used is False


def test_step86_chat_verifies_generated_text_before_tokens_and_metadata(monkeypatch, stub_retrieval):
    from src.verifier.models import CitationStatus
    checked = []
    article = _fake_article()
    monkeypatch.setattr(chat_service, "verify_citations", lambda *a, **k: SimpleNamespace(citations=[
        CitationStatus(id=article.id, status="VALID", exists=True, is_active=True, message=""),
    ]))

    def scorer(text, date, **kwargs):
        checked.append(text)
        uncertain = "UNVERIFIED ASSERTION" in text
        result = _fake_verification(date, 50 if uncertain else 0, ["Generated citation unresolved"] if uncertain else [])
        result.report.citations = [CitationStatus(
            id=article.id, status="UNRESOLVED" if uncertain else "VALID", exists=True,
            is_active=None if uncertain else True, message="Generated citation unresolved" if uncertain else "",
        )]
        return result

    monkeypatch.setattr(chat_service, "verify_and_score", scorer)
    monkeypatch.setattr(chat_service, "generate_llm_response_stream", lambda *a, **k: iter([
        f"UNVERIFIED ASSERTION [{article.id}]",
    ]))
    events = list(chat_service.stream_chat([{"role": "user", "content": VAT_QUESTION}]))
    response = events[-1][1]
    assert checked and all("UNVERIFIED ASSERTION" not in text for text in checked)
    assert response["answer_status"] == "INSUFFICIENT_BASIS"
    assert not response["llm_used"]
    assert any("thẩm định" in warning for warning in response["warnings"])
    assert "UNVERIFIED ASSERTION" not in response["message"]
    assert all("UNVERIFIED ASSERTION" not in payload.get("delta", "") for name, payload in events if name == "token")


@pytest.mark.parametrize("output,disposition", [(None, "provider_no_output"), ("  ", "provider_no_output"), ("safe", "generated_delivered"), ("unsafe", "draft_replaced")])
def test_r5_chat_outer_generation_trace(monkeypatch, stub_retrieval, tmp_path, output, disposition):
    import json
    from src.verifier.models import CitationStatus
    path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("VAT_DIAGNOSTICS", str(path))
    article = _fake_article()
    monkeypatch.setattr(chat_service, "verify_citations", lambda *a, **k: SimpleNamespace(citations=[
        CitationStatus(id=article.id, status="VALID", exists=True, is_active=True, message=""),
    ]))

    def scorer(text, date, **kwargs):
        unsafe = "unsafe" in text
        result = _fake_verification(date, 50 if unsafe else 0)
        result.report.citations = [CitationStatus(
            id=article.id, status="UNRESOLVED" if unsafe else "VALID", exists=True,
            is_active=None if unsafe else True, message="review required" if unsafe else "",
        )]
        return result

    monkeypatch.setattr(chat_service, "verify_and_score", scorer)
    text = f"{output} [{article.id}]" if output in {"safe", "unsafe"} else output
    monkeypatch.setattr(chat_service, "generate_llm_response_stream", lambda *a, **k: iter([text] if text else []))
    events = list(chat_service.stream_chat([{"role": "user", "content": VAT_QUESTION}]))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    outer, = [row for row in rows if row["event"] == "chat_generation"]
    assert outer["generation_disposition"] == "safety_bypass"
    assert outer["generation_attempted"] is False
    assert outer["generated_delivered"] is False
    assert outer["generation_reason"] == "review_required_for_generation"
    inner, = [row for row in rows if row["event"] == "query"]
    assert inner["generation_disposition"] == outer["generation_disposition"]
    assert "generation_disposition" not in events[-1][1]
    assert "".join(payload["delta"] for name, payload in events if name == "token") == events[-1][1]["message"]


VAT_QUESTION = "Áp dụng thuế suất xuất khẩu ra sao?"
OUT_OF_SCOPE_QUESTION = "Người bị cáo cướp tài sản thì xử lý thế nào?"


def _fake_article() -> ArticleNode:
    return ArticleNode(
        id="LUAT_48_2024_D10",
        doc_id="LUAT_48_2024",
        code="D10",
        title="Thuế suất 0%",
        content="Thuế suất 0%.",
        valid_from="2025-07-01",
        valid_to="9999-12-31",
        status="ACTIVE",
    )


def _fake_verification(as_of_date: str, score: int = 0, warnings: list[str] | None = None) -> VerificationResult:
    return VerificationResult(
        as_of_date=as_of_date,
        risk_score=score,
        risk_level="LOW" if score == 0 else "MEDIUM",
        warnings=warnings or [],
        report=VerificationReport(
            as_of_date=as_of_date,
            total_citations=1,
            valid_citations=1 if score == 0 else 0,
            citations=[],
            is_clean=score == 0,
        ),
    )


@pytest.fixture
def stub_verification(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        chat_service,
        "verify_and_score",
        lambda text, as_of_date: _fake_verification(as_of_date),
    )


@pytest.fixture
def stub_retrieval(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        chat_service,
        "query_temporal_rag",
        lambda query, as_of_date: SimpleNamespace(articles=[_fake_article()]),
    )


@pytest.fixture
def stub_llm(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        chat_service,
        "generate_llm_response_stream",
        lambda prompt, model="qwen2.5:7b": iter(["Thuế suất 0% [LUAT_48_2024_D10]."]),
    )


def test_out_of_scope_question_skips_retrieval(monkeypatch: pytest.MonkeyPatch):
    def _explode(query, as_of_date):
        raise AssertionError("retrieval must not run for out-of-scope questions")

    monkeypatch.setattr(chat_service, "query_temporal_rag", _explode)

    response = chat_service.build_response([{"role": "user", "content": OUT_OF_SCOPE_QUESTION}])

    assert response.in_scope is False
    assert "thuế GTGT" in response.message
    assert response.risk_score == 0
    assert response.risk_level == "LOW"


def test_vat_question_returns_citations_and_low_risk(stub_retrieval, stub_llm, stub_verification):
    response = chat_service.build_response([{"role": "user", "content": VAT_QUESTION}])

    assert response.in_scope is True
    assert response.citations == ["LUAT_48_2024_D10"]
    assert response.risk_score == 0
    assert response.risk_level == "LOW"
    assert response.llm_used is False
    assert response.answer_status == "INSUFFICIENT_BASIS"
    assert response.message
    assert response.sources[0].id == "LUAT_48_2024_D10"
    assert response.as_of_date == "2026-09-30"


def test_vat_question_accepts_chat_message_objects(stub_retrieval, stub_llm, stub_verification):
    response = chat_service.build_response(
        [ChatMessage(role="user", content=VAT_QUESTION)],
        as_of_date="2024-05-15",
    )

    assert response.in_scope is True
    assert response.as_of_date == "2024-05-15"
    assert response.citations == ["LUAT_48_2024_D10"]


def test_offline_llm_returns_deterministic_fallback(monkeypatch: pytest.MonkeyPatch, stub_retrieval, stub_verification):
    monkeypatch.setattr(
        chat_service,
        "generate_llm_response_stream",
        lambda prompt, model="qwen2.5:7b": iter([]),
    )

    response = chat_service.build_response([{"role": "user", "content": VAT_QUESTION}])

    assert response.llm_used is False
    assert response.message
    assert response.citations == ["LUAT_48_2024_D10"]


def test_empty_retrieval_returns_no_evidence_reply(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        chat_service,
        "query_temporal_rag",
        lambda query, as_of_date: SimpleNamespace(articles=[]),
    )

    response = chat_service.build_response([{"role": "user", "content": VAT_QUESTION}])

    assert response.in_scope is True
    assert response.risk_score == 50
    assert response.risk_level == "MEDIUM"
    assert response.llm_used is False
    assert response.citations == []
    assert response.answer_status == "INSUFFICIENT_BASIS"
    assert "chưa đủ căn cứ" in response.message.lower()


def test_prompt_contains_clause_text_from_corpus(monkeypatch: pytest.MonkeyPatch, stub_retrieval, stub_verification):
    corpus = [
        {
            "id": "LUAT_48_2024",
            "articles": [
                {
                    "id": "LUAT_48_2024_D10",
                    "code": "Dieu 10",
                    "title": "Thuế suất 0%",
                    "clauses": [
                        {"code": "Khoan 1", "content": "Hàng hóa xuất khẩu không chịu thuế GTGT."},
                        {"code": "Khoan 2", "content": "Dịch vụ xuất khẩu theo hợp đồng."},
                    ],
                }
            ],
        }
    ]
    monkeypatch.setattr(chat_service, "load_corpus_tree", lambda *a, **k: corpus)
    captured: dict[str, str] = {}

    def _capture(prompt: str, model: str = "qwen2.5:7b"):
        captured["prompt"] = prompt
        return iter(["Thuế suất 0% [LUAT_48_2024_D10]."])

    monkeypatch.setattr(chat_service, "generate_llm_response_stream", _capture)

    chat_service.build_response([{"role": "user", "content": VAT_QUESTION}])

    assert captured == {}
    prompt = chat_service._build_prompt(VAT_QUESTION, "2026-03-20", [_fake_article()])
    assert "Hàng hóa xuất khẩu không chịu thuế GTGT." in prompt
    assert "Dịch vụ xuất khẩu theo hợp đồng." in prompt


def test_prompt_preserves_long_article_clauses_without_truncation(monkeypatch: pytest.MonkeyPatch, stub_verification):
    clauses = [{"code": f"Khoan {i}", "content": f"Quy định chi tiết điều khoản thứ {i} về đối tượng miễn giảm thuế GTGT..." * 5} for i in range(1, 26)]
    clauses[-1]["content"] = "Hàng hóa dịch vụ của hộ kinh doanh mức doanh thu từ 200 triệu đồng trở xuống."
    corpus = [
        {
            "id": "LUAT_48_2024",
            "articles": [
                {
                    "id": "LUAT_48_2024_D5",
                    "code": "Dieu 5",
                    "title": "Doi tuong khong chiu thue",
                    "clauses": clauses,
                }
            ],
        }
    ]
    monkeypatch.setattr(chat_service, "load_corpus_tree", lambda *a, **k: corpus)
    monkeypatch.setattr(chat_service, "query_temporal_rag", lambda q, d: SimpleNamespace(articles=[ArticleNode(
        id="LUAT_48_2024_D5", doc_id="LUAT_48_2024", code="D5", title="Doi tuong khong chiu thue", content="Khoan 25: hộ kinh doanh 200 triệu", valid_from="2026-01-01", valid_to="9999-12-31"
    )]))
    captured: dict[str, str] = {}
    def _capture(prompt: str, model: str = "qwen2.5:7b"):
        captured["prompt"] = prompt
        return iter(["[LUAT_48_2024_D5]"])
    monkeypatch.setattr(chat_service, "generate_llm_response_stream", _capture)
    chat_service.build_response([{"role": "user", "content": "tiệm net 200 triệu đóng thuế không?"}])
    assert captured == {}
    prompt = chat_service._build_prompt("thuế GTGT", "2026-03-20", [], ("LUAT_48_2024_D5",))
    assert clauses[-1]["content"] in prompt
    assert clauses[0]["content"] in prompt


def test_prompt_falls_back_to_article_content_without_corpus_clause(
    monkeypatch: pytest.MonkeyPatch, stub_retrieval, stub_verification
):
    monkeypatch.setattr(chat_service, "load_corpus_tree", lambda *a, **k: [])
    captured: dict[str, str] = {}

    def _capture(prompt: str, model: str = "qwen2.5:7b"):
        captured["prompt"] = prompt
        return iter(["Thuế suất 0% [LUAT_48_2024_D10]."])

    monkeypatch.setattr(chat_service, "generate_llm_response_stream", _capture)

    chat_service.build_response([{"role": "user", "content": VAT_QUESTION}])

    assert captured == {}
    assert "Thuế suất 0%." in chat_service._build_prompt(VAT_QUESTION, "2026-03-20", [_fake_article()])


def test_explicit_article_reference_enters_the_legal_basis(monkeypatch: pytest.MonkeyPatch, stub_retrieval, stub_verification):
    corpus = [
        {
            "id": "ND_181_2025",
            "code": "181/2025/ND-CP",
            "title": "Nghi dinh 181/2025",
            "valid_from": "2025-07-01",
            "valid_to": "9999-12-31",
            "articles": [
                {
                    "id": "ND_181_2025_D9",
                    "code": "Dieu 9",
                    "title": "Hoa don, chung tu",
                    "clauses": [{"code": "Khoan 1", "content": "Ho don duoc lap khi ban hang."}],
                }
            ],
        },
        {
            "id": "LUAT_48_2024",
            "code": "48/2024/QH15",
            "title": "Luat Thuê GTGT",
            "valid_from": "2025-07-01",
            "valid_to": "9999-12-31",
            "articles": [
                {
                    "id": "LUAT_48_2024_D9",
                    "code": "Dieu 9",
                    "title": "Thue suat",
                    "clauses": [{"code": "Khoan 1", "content": "Thuê suât 0%."}],
                }
            ],
        },
    ]
    monkeypatch.setattr(chat_service, "load_corpus_tree", lambda *a, **k: corpus)
    captured: dict[str, str] = {}

    def _capture(prompt: str, model: str = "qwen2.5:7b"):
        captured["prompt"] = prompt
        return iter(["Cau tra loi [ND_181_2025_D9]."])

    monkeypatch.setattr(chat_service, "generate_llm_response_stream", _capture)

    response = chat_service.build_response(
        [{"role": "user", "content": "Hoa don chung tu theo Dieu 9 Nghi dinh 181/2025?"}]
    )

    assert captured == {}
    explicit = tuple(chat_service._explicit_article_ids("Dieu 9 Nghi dinh 181/2025"))
    prompt = chat_service._build_prompt("thuế GTGT", "2026-03-20", [], explicit)
    assert "ND_181_2025_D9" in prompt
    assert "Ho don duoc lap khi ban hang." in prompt
    assert "LUAT_48_2024_D9" not in prompt
    assert response.answer_status == "INSUFFICIENT_BASIS"


def test_article_reference_without_document_hint_keeps_every_match(
    monkeypatch: pytest.MonkeyPatch, stub_retrieval, stub_verification
):
    corpus = [
        {
            "id": "ND_181_2025",
            "code": "181/2025/ND-CP",
            "title": "Nghi dinh 181/2025",
            "valid_from": "2025-07-01",
            "valid_to": "9999-12-31",
            "articles": [
                {"id": "ND_181_2025_D9", "code": "Dieu 9", "title": "A", "clauses": []},
            ],
        },
        {
            "id": "LUAT_48_2024",
            "code": "48/2024/QH15",
            "title": "Luat 48/2024",
            "valid_from": "2025-07-01",
            "valid_to": "9999-12-31",
            "articles": [
                {"id": "LUAT_48_2024_D9", "code": "Dieu 9", "title": "B", "clauses": []},
            ],
        },
    ]
    monkeypatch.setattr(chat_service, "load_corpus_tree", lambda *a, **k: corpus)

    matched = chat_service._explicit_article_ids("Theo Dieu 9 thi ap dung the nao?")

    assert set(matched) == {"ND_181_2025_D9", "LUAT_48_2024_D9"}


def test_question_without_article_reference_has_no_explicit_articles(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(chat_service, "load_corpus_tree", lambda *a, **k: [])
    assert chat_service._explicit_article_ids("Thuê suât xuât khau la bao nhieu?") == []


def test_no_evidence_reply_explains_the_temporal_gap(monkeypatch: pytest.MonkeyPatch):
    corpus = [
        {
            "id": "LUAT_48_2024",
            "code": "48/2024/QH15",
            "title": "Luật Thuế giá trị gia tăng số 48/2024/QH15",
            "valid_from": "2025-07-01",
            "articles": [],
        }
    ]
    monkeypatch.setattr(chat_service, "load_corpus_tree", lambda *a, **k: corpus)
    monkeypatch.setattr(
        chat_service,
        "query_temporal_rag",
        lambda query, as_of_date: SimpleNamespace(articles=[]),
    )

    response = chat_service.build_response(
        [{"role": "user", "content": "thuế suất xuất khẩu dịch vụ tháng 5/2024?"}],
        as_of_date="2024-05-15",
    )

    assert response.risk_score == 50
    assert "2024-05-31" in response.message
    assert response.answer_status == "INSUFFICIENT_BASIS"
    assert "chưa đủ căn cứ" in response.message.lower()
    assert not response.llm_used


def test_stream_chat_emits_meta_first_and_done_last(stub_retrieval, stub_llm, stub_verification):
    events = list(chat_service.stream_chat([{"role": "user", "content": VAT_QUESTION}]))

    assert events[0][0] == "meta"
    assert events[-1][0] == "done"
    assert events[0][1]["as_of_date"] == "2026-09-30"
    assert events[0][1]["sources"][0]["id"] == "LUAT_48_2024_D10"
    assert any(name == "token" for name, _ in events)


class _FakeStreamResponse:
    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def __iter__(self):
        return iter(self._lines)


def test_generate_llm_response_stream_yields_response_chunks(monkeypatch: pytest.MonkeyPatch):
    for name in ("LLM_PROVIDER", "GEMINI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    body = [
        b'{"response": "Thu\xe1\xba\xbf ", "done": false}\n',
        b'{"response": "0%", "done": true}\n',
    ]
    captured = {}

    def _fake_urlopen(req, timeout=None):
        captured["payload"] = req.data.decode("utf-8")
        captured["url"] = req.full_url
        return _FakeStreamResponse(body)

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    chunks = list(generate_llm_response_stream("prompt", model="qwen2.5:7b"))

    assert chunks == ["Thuế ", "0%"]
    assert captured["url"] == "http://localhost:11434/api/generate"
    assert '"stream": true' in captured["payload"]


def test_generate_llm_response_stream_stops_silently_on_error(monkeypatch: pytest.MonkeyPatch):
    for name in ("LLM_PROVIDER", "GEMINI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    def _fake_urlopen(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    assert list(generate_llm_response_stream("prompt")) == []


@pytest.mark.parametrize("mode", ["good", "none", "blank", "bad", "unresolved"])
def test_r5_chat_trace_is_independent_of_inner_deterministic_answer(monkeypatch, tmp_path, mode):
    import json
    from pathlib import Path
    from unittest.mock import MagicMock, Mock
    from src.engine.diagnostics import text_sha256
    from src.ui.corpus_viewer import load_corpus_index
    import src.verifier.citation_verifier as verifier

    root = Path(__file__).resolve().parents[1]
    corpus = str(root / "data/processed/vat_corpus.staging.json")
    ledger = str(root / "tests/fixtures/amendment_review.staging.json")
    monkeypatch.setattr(chat_service, "selected_corpus_kwargs", lambda: {"corpus_path": corpus})
    monkeypatch.setenv("VAT_ENV", "staging")
    monkeypatch.setenv("VAT_CORPUS_PATH", corpus)
    monkeypatch.setenv("VAT_EMBEDDINGS_PATH", str(root / "data/processed/article_embeddings.staging.json"))
    monkeypatch.setenv("VAT_EMBEDDING_META_PATH", str(root / "data/processed/embedding_meta.staging.json"))
    monkeypatch.setenv("VAT_AMENDMENT_REVIEW_PATH", ledger)
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:17687")
    cid = "VBHN_25_2026_D5" if mode == "unresolved" else "LUAT_48_2024_D4"
    meta = load_corpus_index(corpus)[cid]
    article = ArticleNode(id=cid, doc_id=meta["doc_id"], code=meta["code"], title=meta["title"],
                          content=meta["text"], valid_from=meta["valid_from"], valid_to=meta["valid_to"])
    monkeypatch.setattr(chat_service, "query_temporal_rag", lambda *a, **k: SimpleNamespace(articles=[article]))
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.side_effect = lambda query, **kw: MagicMock(data=lambda: [
        {"cid": value, "exists": True, "valid_from": "2025-07-01", "valid_to": "9999-12-31", "amendments": []}
        for value in kw["ids"]])
    monkeypatch.setattr(verifier, "get_neo4j_driver", lambda: driver)
    from src.engine.provision_evidence import resolve_provision_evidence
    resolution = resolve_provision_evidence([cid], "2026-03-20", corpus_path=corpus, reviewed_amendment_path=ledger)
    exact = next((p for item in resolution.items for p in item.provisions if p.id == cid), None)
    draft = f"Issue: Người nộp thuế GTGT\nRule: {exact.text} [{cid}]\nApplication: {exact.text} [{cid}]\nConclusion: {exact.text} [{cid}]" if mode == "good" else "unverified draft"
    assessment = json.dumps({"outcome": "supported", "coverage": True, "claims": [
        {"text": draft, "status": "supported", "evidence_ids": [cid]}], "missing_facts": []})
    generator = Mock(side_effect=[{"none": None, "blank": "  "}.get(mode, draft), assessment])
    monkeypatch.setattr(chat_service, "generate_llm_response", generator)
    log = tmp_path / "trace.jsonl"
    monkeypatch.setenv("VAT_DIAGNOSTICS", str(log))
    question = "Hộ kinh doanh có phải nộp thuế GTGT không?" if mode == "unresolved" else "Người nộp thuế GTGT khi nhập khẩu là ai?"
    events = list(chat_service.stream_chat([{"role": "user", "content": question}], "2026-03-20"))
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    inner, outer = rows[-2:]
    assert inner["generation_disposition"] == outer["generation_disposition"]
    assert inner["output_sha256"] == outer.get("output_sha256") if mode != "unresolved" else True
    if mode == "good":
        assert rows[0]["event"] == "answer_assessment"
        assert outer["assessment_call_count"] == 1
        assert outer["output_sha256"] == text_sha256(draft)
        assert outer["assessment_output_sha256"] == text_sha256(assessment)
    assert outer["event"] == "chat_generation"
    assert outer["generation_disposition"] == {
        "good": "generated_delivered", "none": "provider_no_output", "blank": "provider_no_output",
        "bad": "draft_replaced", "unresolved": "safety_bypass",
    }[mode]
    assert outer["generation_reason"] == {
        "good": "verified_draft", "none": "empty_provider_output", "blank": "empty_provider_output",
        "bad": "unsupported_draft", "unresolved": "unresolved_evidence",
    }[mode]
    assert outer["generation_attempted"] is (mode != "unresolved")
    assert outer["generated_delivered"] is (mode == "good")
    assert generator.call_count == (0 if mode == "unresolved" else 2 if mode == "good" else 1)
    done = events[-1][1]
    assert done["llm_used"] is (mode in {"good", "bad"})
    assert "".join(payload["delta"] for name, payload in events if name == "token") == done["message"]
    assert outer["delivered_sha256"] == text_sha256(done["message"])
    if mode == "good":
        assert exact.text in done["message"]
        assert done["answer_status"] == "ANSWERED"
        from src.engine.provision_evidence import resolve_provision_evidence
        resolution = resolve_provision_evidence([cid], "2026-03-20", corpus_path=corpus,
                                                reviewed_amendment_path=ledger)
        exact = next(p for item in resolution.items for p in item.provisions if p.id == cid)
        assert exact.text in generator.call_args.args[0]
    if mode == "unresolved":
        assert done["answer_status"] == "INSUFFICIENT_BASIS"
        assert done["warnings"]
