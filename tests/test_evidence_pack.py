import json
import time
from unittest.mock import Mock

import pytest
from types import SimpleNamespace

from src.engine.answer_assessment import build_assessment_prompt, evidence_data
from src.engine.prompt import build_irac_prompt
from src.engine.provision_evidence import EvidenceItem, EvidenceResolution, ProvisionEvidence
from tests.test_reviewed_evidence_integration import reviewed


def sample():
    text = "A complete rule with conditions and exceptions."
    provenance = ({"path": "raw/source.html", "sha256": "a" * 64,
                   "operation": {"replacement": text * 1000}},)
    hierarchy = ({"id": "LUAT_17_2024_D2", "title": "Parent context"},)
    unit = ProvisionEvidence("LUAT_17_2024_D2_K3", text, "VALID", "2025-01-01",
                             "9999-12-31", "2024-01-01", provenance, hierarchy)
    article = SimpleNamespace(id=unit.id, doc_id="LUAT_17_2024", content=text)
    resolution = EvidenceResolution((EvidenceItem(unit.id, (unit,), True),), {"unknown": "unresolved effect"})
    return article, unit, resolution


def test_model_data_excludes_full_operation_but_keeps_source_reference():
    article, unit, resolution = sample()
    data = evidence_data("question", "2026-01-01", [], [article], resolution)
    encoded = json.dumps(data)
    assert len(encoded) < 3000
    assert data["evidence"][0]["text"] == unit.text
    assert "a" * 64 in encoded
    assert "Parent context" in encoded
    assert "operation" not in encoded
    assert resolution.items[0].provisions[0].provenance[0]["operation"]
    assert data["blocked_units"] == {"unknown": [0]}


def test_rendered_prompts_include_each_legal_text_once():
    article, unit, resolution = sample()
    prompts = [build_irac_prompt("question", "2026-01-01", [article], resolved_evidence=resolution),
               build_assessment_prompt("question", "2026-01-01", [], [article], resolution, "draft")]
    for prompt in prompts:
        assert prompt.count(unit.text) == 1
        assert "[LUAT_17_2024_D2_K3]" in prompt or '\"LUAT_17_2024_D2_K3\"' in prompt


@pytest.mark.parametrize("stage", ["generation", "assessment"])
def test_oversized_rendered_prompt_never_calls_provider(monkeypatch, stage):
    from src.api.reviewed_answer import _generate, _assess
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "8192")
    article, _, resolution = sample()
    llm, scorer, trace = Mock(), Mock(), {}
    question = "x" * 312814
    if stage == "generation":
        result = _generate(question, "2026-01-01", [article], resolution, llm, scorer,
                           "qwen2.5:7b", [], ("unused", "unused"), trace)
        assert result == (None, None, None)
        assert trace["generation_reason"] == "context_budget_exceeded"
        assert trace["generation_attempted"] is False
    else:
        trace["local_num_ctx"] = 8192
        assert not _assess(question, "2026-01-01", [], [article], resolution, "draft",
                           llm, "qwen2.5:7b", trace, time.monotonic() + 180)
        assert trace["assessment_reason"] == "context_budget_exceeded"
    llm.assert_not_called()
    scorer.assert_not_called()


@pytest.mark.parametrize("text,context,reserve,expected", [
    ("abcdefgh", 520, 4, True), ("abcdefghi", 520, 4, False),
    ("ếế", 519, 4, True), ("ếếa", 519, 4, False),
])
def test_rendered_budget_boundary_and_unicode(text, context, reserve, expected):
    from src.engine.evidence_pack import measure_prompt
    measured = measure_prompt(text, context, reserve)
    assert measured.fits is expected
    assert measured.utf8_bytes == len(text.encode("utf-8"))
    assert measured.estimated_tokens == (measured.utf8_bytes + 1) // 2
    assert "not_tokenizer_exact" in measured.method


@pytest.mark.parametrize("context,reserve", [(True, 0), (0, 0), (8192, -1), (8192.0, 1)])
def test_invalid_budgets_rejected(context, reserve):
    from src.engine.evidence_pack import measure_prompt
    with pytest.raises(ValueError):
        measure_prompt("prompt", context, reserve)


def test_pack_prioritizes_requested_whole_units_and_records_omissions():
    import src.engine.evidence_pack as module
    pack = getattr(module, "pack_evidence", None)
    assert callable(pack), "Budgeted evidence selection is missing"
    articles = [SimpleNamespace(id=f"DOC_D{i}", content="x" * 200) for i in range(8)]
    render = lambda selected, omitted: "".join(a.content for a in selected) + ",".join(omitted)
    result = pack(articles, ["DOC_D7"], 3000, render, render, draft_reserve=0)
    assert result.ready
    assert result.articles[0].id == "DOC_D7"
    assert len(result.articles) < len(articles)
    assert set(a.id for a in result.articles).isdisjoint(result.omitted_ids)
    assert {a.id for a in result.articles} | set(result.omitted_ids) == {a.id for a in articles}
    assert all(a.content == "x" * 200 for a in result.articles)


def test_pack_does_not_skip_required_evidence_or_oversized_blockers():
    import src.engine.evidence_pack as module
    pack = getattr(module, "pack_evidence", None)
    assert callable(pack), "Budgeted evidence selection is missing"
    articles = [SimpleNamespace(id="DOC_D1", content="x" * 10000)]
    render = lambda selected, omitted: "".join(a.content for a in selected)
    assert not pack(articles, ["DOC_D1"], 8192, render, render).ready
    oversized = lambda selected, omitted: "blocker" * 10000
    assert not pack(articles, [], 8192, oversized, oversized).ready


def test_assessment_uses_same_selected_evidence_and_discloses_omissions():
    from dataclasses import replace
    article, unit, resolution = sample()
    omitted = replace(unit, id="LUAT_17_2024_D2_K4", text="An omitted exception.")
    resolution = EvidenceResolution((EvidenceItem(unit.id, (unit, omitted), True),), resolution.blocked)
    generation = evidence_data("question", "2026-01-01", [], [article], resolution)
    prompt = build_assessment_prompt("question", "2026-01-01", [], [article], resolution, "draft")
    assessed = json.loads(prompt.split("BEGIN_UNTRUSTED_DATA_JSON\n")[1].split("\nEND_UNTRUSTED_DATA_JSON")[0])
    assert json.loads(json.dumps(generation["evidence"])) == assessed["evidence"]
    import hashlib
    assert assessed["selection"]["omitted_count"] == 1
    assert assessed["selection"]["omitted_ids_sha256"] == hashlib.sha256(
        json.dumps([omitted.id], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    assert "omitted_ids" not in assessed["selection"]
    assert assessed["selection"]["coverage_certified"] is False
    assert omitted.text not in prompt
    assert "unresolved effect" in prompt


def test_missing_required_unit_fails_before_rendering():
    from src.engine.evidence_pack import pack_evidence
    render = Mock(side_effect=AssertionError("Unavailable required evidence must not render"))
    result = pack_evidence([], ["LUAT_17_2024_D99"], 8192, render, render)
    assert not result.ready
    assert result.reason == "requested_evidence_unavailable"
    render.assert_not_called()


def test_saved_167_units_are_never_sent_as_unbounded_prompt():
    from pathlib import Path
    from src.engine.models import ArticleNode
    from src.engine.evidence_pack import pack_evidence, measure_prompt
    from src.engine.provision_evidence import resolve_provision_evidence
    root = Path(__file__).resolve().parents[1]
    source = root / ".scratch/local-e2e-20261122T142013378805Z/response.json"
    if not source.exists():
        pytest.skip("Historical diagnostic replay unavailable")
    saved = json.loads(source.read_text(encoding="utf-8"))
    corpus = str(root / "data/processed/vat_corpus.staging.json")
    captured = root / ".scratch/local-e2e-20261122T153335951921Z/response.json"
    if captured.exists():
        saved = json.loads(captured.read_text(encoding="utf-8"))
        raw_resolution = saved["resolved_evidence"]
        resolution = EvidenceResolution(tuple(EvidenceItem(item["id"], tuple(
            ProvisionEvidence(**{**p, "provenance": tuple(p["provenance"]), "hierarchy": tuple(p.get("hierarchy", []))})
            for p in item["provisions"]), item["citable"]) for item in raw_resolution["items"]),
            raw_resolution["blocked"])
    else:
        resolution = resolve_provision_evidence(saved["active_article_ids"], saved["as_of_date"],
            corpus_path=corpus, reviewed_amendment_path=str(root / "tests/fixtures/amendment_review.staging.json"))
    units = {p.id: p for item in resolution.items for p in item.provisions}
    articles = [ArticleNode(id=p.id, doc_id=p.id.split("_D")[0], code=p.id, title="", content=p.text,
        valid_from=p.valid_from, valid_to=p.valid_to) for p in units.values()]
    generation = lambda chosen, omitted: build_irac_prompt(saved["query"], saved["as_of_date"], chosen,
        resolved_evidence=resolution)
    assessment = lambda chosen, omitted: build_assessment_prompt(saved["query"], saved["as_of_date"], [],
        chosen, resolution, "")
    result = pack_evidence(articles, ["LUAT_48_2024_D5_K7"], 8192, generation, assessment)
    assert len(articles) >= 100
    if captured.exists():
        assert result.ready is True
    if result.ready:
        assert measure_prompt(generation(result.articles, result.omitted_ids), 8192, 1024).fits
        assert measure_prompt(assessment(result.articles, result.omitted_ids), 8192, 3072).fits
        assert "LUAT_48_2024_D5_K7" in {a.id for a in result.articles}
    else:
        assert result.reason == "context_budget_exceeded"
        assert not result.articles
    print(json.dumps({"candidates": len(articles), "selected": len(result.articles),
        "omitted": len(result.omitted_ids), "ready": result.ready,
        "generation_bytes": len(generation(result.articles, result.omitted_ids).encode()),
        "assessment_bytes": len(assessment(result.articles, result.omitted_ids).encode())}))


def test_omitted_evidence_warning_is_visible(reviewed, monkeypatch):
    from src.api.answer_service import answer_query
    from src.engine.evidence_pack import EvidencePack
    from tests.test_reviewed_evidence_integration import DAY, options
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    omitted = "LUAT_17_2024_D2_K4"
    def limited(articles, *args, **kwargs):
        return EvidencePack(tuple(articles), (omitted,), True, "packed")
    monkeypatch.setattr("src.engine.evidence_pack.pack_evidence", limited)
    result = answer_query("Hồ sơ thuế GTGT?", DAY, retriever_fn=reviewed.fetch, **options(reviewed))
    assert any("context" in warning and omitted in warning for warning in result.warnings)


def test_cloud_provider_also_packs_evidence_within_provider_budget(reviewed, monkeypatch):
    from src.api.answer_service import answer_query
    from src.config import provider_context_limit
    from tests.test_reviewed_evidence_integration import DAY, SAFE, TEXT, options, semantic_reply
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("CLOUD_LLM_CONTEXT_LIMIT", "16384")
    assert provider_context_limit("gemini") == 16384
    monkeypatch.setenv("CLOUD_LLM_CONTEXT_LIMIT", "bad")
    with pytest.raises(ValueError):
        provider_context_limit("gemini")
    monkeypatch.setenv("CLOUD_LLM_CONTEXT_LIMIT", "16384")
    trace = {}
    draft = f"Issue: Q\nRule: {TEXT} [{SAFE}]\nApplication: {TEXT} [{SAFE}]\nConclusion: OK [{SAFE}]"
    outputs = iter([draft, semantic_reply()])
    result = answer_query("Hồ sơ thuế GTGT?", DAY, retriever_fn=reviewed.fetch,
                          use_llm=True, llm_fn=lambda *a, **k: next(outputs),
                          trace=trace, **options(reviewed))
    assert trace["selected_evidence_count"] >= 1
    assert trace["evidence_pack_reason"] == "packed"
    assert trace["context_limit"] == 16384
    assert result.answer_status == "ANSWERED"


