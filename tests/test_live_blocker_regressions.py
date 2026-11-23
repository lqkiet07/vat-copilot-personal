import json
from types import SimpleNamespace

import pytest

from tests.test_answer_assessment import assessment
from tests.test_reviewed_evidence_integration import SAFE, TEXT, answer, reviewed


@pytest.mark.parametrize("labels", [
    ["I. Vấn đề (Issue)", "II. Quy định pháp luật (Rule)", "III. Áp dụng (Application)", "IV. Kết luận (Conclusion)"],
    ["1) Vấn đề", "2) Cơ sở pháp lý", "3) Phân tích", "4) Kết luận"],
    ["Issue (Vấn đề)", "Rule (Quy định)", "Application (Áp dụng)", "Conclusion (Kết luận)"],
])
def test_numbered_bilingual_irac_preserves_bodies(labels):
    from src.api.answer_service import _parse_irac_sections, _needs_irac_fallback
    bodies = ["Question about records.", f"Retain records. [{SAFE}]", "These records meet the condition.", "Retain these records."]
    raw = "\n".join(f"## **{label}:**\n{body}" for label, body in zip(labels, bodies))
    sections = _parse_irac_sections(raw)
    assert [sections[k] for k in ("issue", "rule", "application", "conclusion")] == bodies
    assert not _needs_irac_fallback(raw, sections)


@pytest.mark.parametrize("label", ["Rule", "**Rule**", "## **II. Quy định pháp luật (Rule)**"])
def test_inline_irac_citation_ids_preserved(label):
    from src.api.answer_service import _parse_irac_sections
    body = f"Giữ nguyên nội dung và mã [{SAFE}]"
    assert _parse_irac_sections(f"{label}: {body}")["rule"] == body


@pytest.mark.parametrize("raw", ["Rule: law\nConclusion: yes", "Issue: ?\nRule: law\nApplication:\nConclusion: yes", "Issues are complicated\nRules may vary\nConclusionary prose"])
def test_incomplete_or_arbitrary_draft_rejected(raw):
    from src.api.answer_service import _parse_irac_sections, _needs_irac_fallback
    assert _needs_irac_fallback(raw, _parse_irac_sections(raw))


@pytest.mark.parametrize("query, expected", [
    ("khoản 3 Điều 7 Luật 71/2030 được sửa đổi bởi Luật 82/2031", ["LUAT_71_2030_D7_K3"]),
    ("Theo khoản 3 Điều 7 Luật 71/2030. Hãy dẫn Điều 7 chính xác.", ["LUAT_71_2030_D7_K3", "LUAT_71_2030_D7"]),
    ("khoản 3 Điều 7 Luật 71/2030 và khoản 2 Điều 4 Luật 82/2031", ["LUAT_71_2030_D7_K3", "LUAT_82_2031_D4_K2"]),
    ("Luật 71/2030 Điều 7 khoản 3; Luật 82/2031 Điều 4 khoản 2", ["LUAT_71_2030_D7_K3", "LUAT_82_2031_D4_K2"]),
    ("khoản 3 Điều 7 Luật 71/2030; không áp dụng Luật 99/2032", ["LUAT_71_2030_D7_K3", "missing_explicit_authority_LUAT_99_2032"]),
])
def test_authority_units_attach_locally(monkeypatch, query, expected):
    from src.engine.retrieval_service import explicit_evidence_ids
    docs = [{"id": f"LUAT_{n}_{y}", "articles": [{"id": f"LUAT_{n}_{y}_D{a}"}]} for n, y, a in [("71", "2030", "7"), ("82", "2031", "4")]]
    monkeypatch.setattr("src.engine.retrieval_service.load_corpus_tree", lambda p: docs)
    assert set(explicit_evidence_ids(query, "synthetic")) == set(expected)


@pytest.mark.parametrize("payload, reason", [("not json", "invalid_json"), ("{}", "invalid_schema"), (None, "no_output")])
def test_assessment_failure_reason_preserved(reviewed, payload, reason):
    draft = f"Issue: Record question\nRule: {TEXT} [{SAFE}]\nApplication: {TEXT} [{SAFE}]\nConclusion: Retain records. [{SAFE}]"
    outputs = iter([draft, payload])
    trace = {}
    answer(reviewed, use_llm=True, llm_fn=lambda *a, **k: next(outputs), trace=trace)
    assert trace["assessment_reason"] == reason


def test_gemini_assessment_config_stop_and_truncation(monkeypatch):
    from src.engine import llm_client as client
    requests, events = [], []
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return json.dumps({"candidates": [{"finishReason": finish, "content": {"role": "model", "parts": [{"thought": True, "text": "hidden"}, {"text": assessment()}]}}]}).encode()
    def transport(req, timeout):
        requests.append(json.loads(req.data))
        return Response()
    monkeypatch.setattr(client.urllib.request, "urlopen", transport)
    monkeypatch.setattr(client, "emit_trace", events.append)
    finish = "STOP"
    with client.assessment_request():
        assert client._call_gemini_api("synthetic", "fake") == assessment()
    config = requests[-1]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"]["required"] == ["outcome", "coverage", "claims", "missing_facts"]
    assert config["thinkingConfig"] == {"thinkingBudget": 0}
    assert events[-1]["thought_part_count"] == 1
    assert len(events[-1]["output_sha256"]) == 64
    assert "hidden" not in json.dumps(events)
    finish = "MAX_TOKENS"
    with client.assessment_request():
        with pytest.raises(client.AssessmentProviderError) as error:
            client._call_gemini_api("synthetic", "fake")
    assert error.value.reason == "truncated"
    assert len(requests) == 2
    finish = "STOP"
    client._call_gemini_api("generation", "fake")
    assert requests[-1]["generationConfig"] == {"temperature": 0.2, "maxOutputTokens": 4096, "thinkingConfig": {"thinkingBudget": 512}}


def test_safety_bypass_still_requires_independent_review():
    from scripts.live_qa_validation import assess
    case = {"id": "synthetic", "expected_status": "INSUFFICIENT_BASIS", "expected_evidence_ids": [], "required_propositions": ["Explain the exact blocker."], "prohibited_propositions": ["Invent an answer."], "source_refs": []}
    trace = {"event": "query", "generation_disposition": "safety_bypass", "generation_attempted": False, "generated_delivered": False, "assessment_call_count": 0}
    row = {"id": "synthetic", "trace_start": 0, "trace_end": 1, "actual": {"answer_status": "INSUFFICIENT_BASIS", "llm_used": False, "raw_llm_response": None}}
    report = assess({"cases": [case]}, [row], [trace])
    assert report["cases"][0]["failures"] == []
    assert report["status"] == "MANUAL_REVIEW_REQUIRED"
    assert report["cases"][0]["required_propositions"][0]["verdict"] == "UNDECIDED"


def test_fallback_exposes_exact_blocker_metadata():
    from src.api.reviewed_answer import evidence_sections
    resolution = SimpleNamespace(blocked={"UNIT": "Not effective until 2030-03-04"})
    sections = evidence_sections("question", "2029-01-01", [], resolution=resolution)
    assert "Not effective until 2030-03-04" in sections["conclusion"]
    sections = evidence_sections("question", "2029-01-01", [], resolution=resolution,
                                 metadata={"UNIT": {"valid_from": "2030-03-04"}})
    assert "2030-03-04" in sections["conclusion"]
    assert "2029-01-01" in sections["conclusion"]


@pytest.mark.parametrize("payload", ["```json\n" + assessment() + "\n```", assessment()[:-1],
                                      assessment().replace('"coverage": true', '"coverage": false'),
                                      assessment(status="contradicted")])
def test_fenced_truncated_or_false_claims_never_delivered(reviewed, payload):
    draft = f"Issue: Records\nRule: {TEXT} [{SAFE}]\nApplication: {TEXT} [{SAFE}]\nConclusion: Retain records. [{SAFE}]"
    outputs = iter([draft, payload])
    result = answer(reviewed, use_llm=True, llm_fn=lambda *a, **k: next(outputs))
    assert result.answer_status == "INSUFFICIENT_BASIS"
    assert result.irac_sections["conclusion"] != "Retain records."


@pytest.mark.parametrize("kind", ["timeout", "transport"])
def test_assessment_transport_reason_safe_and_single_call(reviewed, kind):
    calls = []
    def llm(prompt, model, timeout, options):
        calls.append(prompt)
        if len(calls) == 2:
            raise (TimeoutError("private") if kind == "timeout" else OSError("private"))
        return f"Issue: Records\nRule: {TEXT} [{SAFE}]\nApplication: {TEXT} [{SAFE}]\nConclusion: Retain records. [{SAFE}]"
    trace = {}
    result = answer(reviewed, use_llm=True, llm_fn=llm, trace=trace)
    assert trace["assessment_reason"] == kind
    assert len(calls) == 2
    assert "private" not in str(trace) + str(result.to_dict())


def test_assessment_optional_config_bound_without_generation_change(reviewed):
    calls = []
    def llm(prompt, model, timeout, options, generation_config=None):
        calls.append(generation_config)
        if len(calls) == 1:
            return f"Issue: Records\nRule: {TEXT} [{SAFE}]\nApplication: {TEXT} [{SAFE}]\nConclusion: Retain records. [{SAFE}]"
        return assessment()
    answer(reviewed, use_llm=True, llm_fn=llm)
    assert calls[0] is None
    assert calls[1]["responseMimeType"] == "application/json"
    assert calls[1]["thinkingConfig"] == {"thinkingBudget": 0}

