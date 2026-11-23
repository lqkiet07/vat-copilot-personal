import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]


def runner():
    path = ROOT / "scripts/live_qa_validation.py"
    assert path.is_file(), "bounded live runner is missing"
    spec = importlib.util.spec_from_file_location("live_qa_validation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def case():
    return {"id": "one", "question": "question", "history": [], "as_of_date": "2026-11-19",
            "expected_status": "ANSWERED", "required_propositions": ["required"],
            "prohibited_propositions": ["forbidden"], "source_refs": [], "expected_evidence_ids": ["A"]}


def test_manifest_freezes_oracle_and_selected_code():
    module = runner()
    manifest, oracle = module.fingerprint(ROOT)
    assert manifest["experiment_revision"] is None
    assert manifest["baseline_code_pins"]["src/config.py"] == module.file_hash(ROOT / "src/config.py")
    assert manifest["experiment_code_pins"] == {}
    assert len(manifest["head"]) == 40
    assert manifest["oracle_freeze_sha256"] == module.ORACLE_FREEZE
    assert manifest["profile"]["documents"] == 33
    assert manifest["profile"]["vectors"] == 494
    assert "src/api/main.py" in manifest["code_sha256"]
    assert "scripts/live_qa_validation.py" in manifest["code_sha256"]
    assert len(oracle["cases"]) == 12


@pytest.mark.parametrize("revision", ["local-ollama-20261120", "unknown"])
def test_historical_or_unknown_revision_rejects_current_code(revision):
    with pytest.raises(ValueError):
        runner().fingerprint(ROOT, experiment_revision=revision)


def test_local_revision_rejects_unapproved_config_changes(monkeypatch):
    module = runner()
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda path: original(path) + b" " if path == ROOT / "src/config.py" else original(path))
    with pytest.raises(ValueError, match="pinned_bytes_drift"):
        module.fingerprint(ROOT)


def test_local_revision_rejects_source_drift(monkeypatch):
    module = runner()
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda path: original(path) + b" " if path.name == "01_luat_48_2024.html" else original(path))
    with pytest.raises(ValueError, match="pinned_bytes_drift"):
        module.fingerprint(ROOT)


def test_local_onecase_harness_defaults_to_no_calls(monkeypatch, capsys):
    import urllib.request
    path = ROOT / ".scratch/local_ollama_one_qa.py"
    spec = importlib.util.spec_from_file_location("local_onecase", path)
    module = importlib.util.module_from_spec(spec)
    boundary = Mock(side_effect=AssertionError("No live calls allowed"))
    monkeypatch.setattr(urllib.request, "urlopen", boundary)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "execute", boundary)
    assert module.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["provider_called"] is False
    assert report["cpu_caps_forced"] is False
    assert report["graph_access"] is False
    boundary.assert_not_called()


def test_local_onecase_harness_rejects_remote_and_redirects():
    import urllib.request
    path = ROOT / ".scratch/local_ollama_one_qa.py"
    spec = importlib.util.spec_from_file_location("local_onecase", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    guard = module.LocalOnly()
    for url in ("https://localhost:11434/api/generate", "http://example.invalid:11434/api/generate",
                "http://localhost:11434/api/pull", "http://localhost:11434/api/generate?x=1"):
        with pytest.raises(ValueError):
            guard.http_request(urllib.request.Request(url))
    with pytest.raises(ValueError, match="redirect_blocked"):
        guard.redirect_request(None, None, 302, None, None, "http://localhost:11434/api/generate")


def test_fingerprint_detects_changed_bytes():
    module = runner()
    assert module.digest({"a": 1}) != module.digest({"a": 2})


@pytest.mark.parametrize("mutation", ["oracle", "pin", "vectors"])
def test_fingerprint_rejects_drift(tmp_path, monkeypatch, mutation):
    module = runner()
    original = Path.read_bytes
    def changed(path):
        data = original(path)
        if mutation == "oracle" and path.name == "live_qa_oracle.json":
            value = json.loads(data)
            value["cases"][0]["question"] = "changed"
            return json.dumps(value).encode()
        if mutation == "pin" and path.name == "vat_corpus.staging.json":
            return data + b" "
        if mutation == "vectors" and path.name == "article_embeddings.staging.json":
            return b"{}"
        return data
    monkeypatch.setattr(Path, "read_bytes", changed)
    with pytest.raises(ValueError):
        module.fingerprint(ROOT)


def test_environment_is_pinned_and_does_not_inherit_provider_secrets(monkeypatch):
    module = runner()
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-inherit")
    monkeypatch.setenv("NEO4J_URI", "bolt://production:7687")
    env = module.environment(ROOT, ("test", "private-password", "private-key"))
    assert env["NEO4J_URI"] == "bolt://127.0.0.1:17687"
    assert env["PYTHON_DOTENV_DISABLED"] == "1"
    assert env["LLM_PROVIDER"] == "gemini"
    assert "OPENAI_API_KEY" not in env
    assert "envFile:false" in module.commands(ROOT)[1][1][-1]
    assert "--env-file" not in str(module.commands(ROOT))


@pytest.mark.parametrize("port", [18000, 15173])
def test_preflight_blocks_occupied_ports(monkeypatch, port):
    module = runner()
    monkeypatch.setattr(module, "fingerprint", Mock(return_value=({"head": "h"}, {"cases": []})))
    monkeypatch.setattr(module, "credentials", Mock(return_value=("u", "secret", "key")))
    monkeypatch.setattr(module.socket, "create_connection", Mock(return_value=SimpleNamespace(
        __enter__=lambda self: self, __exit__=lambda *args: None)))
    monkeypatch.setattr(module, "listening", lambda value: value == port)
    monkeypatch.setattr(module, "local_json", Mock(return_value={"models": [{"name": "bge-m3:latest"}]}))
    monkeypatch.setattr(module, "snapshot", Mock(return_value={"graph": "g"}))
    report, env = module.preflight(ROOT)
    assert not report["ready"]
    assert f"port_{port}_occupied" in report["blockers"]
    assert "secret" not in json.dumps(report)


def test_preflight_missing_credentials_never_starts_process(monkeypatch):
    module = runner()
    monkeypatch.setattr(module, "credentials", Mock(side_effect=ValueError("secret-bearing-error")))
    monkeypatch.setattr(module, "fingerprint", Mock(return_value=({}, {})))
    monkeypatch.setattr(module, "listening", lambda port: False)
    monkeypatch.setattr(module, "local_json", Mock(return_value={"models": []}))
    spawn = Mock(side_effect=AssertionError("must not start"))
    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    report, env = module.preflight(ROOT)
    assert not report["ready"]
    assert "secret-bearing-error" not in json.dumps(report)
    spawn.assert_not_called()


def test_generation_sends_one_request_per_case_and_saves_actual(tmp_path, monkeypatch):
    module = runner()
    boundary = Mock(return_value={"answer_status": "ANSWERED", "raw_llm_response": "actual", "llm_used": True})
    monkeypatch.setattr(module, "local_json", boundary)
    trace = tmp_path / "trace.jsonl"
    trace.touch()
    rows = module.generate_cases({"cases": [case()]}, tmp_path, trace, {})
    assert boundary.call_count == 1
    assert boundary.call_args.args[0] == "http://127.0.0.1:18000/api/query"
    assert boundary.call_args.kwargs["payload"]["use_llm"] is True
    assert rows[0]["actual"]["raw_llm_response"] == "actual"
    assert json.loads((tmp_path / "actual.json").read_text())[0]["actual"] == rows[0]["actual"]


@pytest.mark.parametrize("events", [[], [{"event": "query"}]])
def test_missing_generation_trace_is_failure(events):
    module = runner()
    rows = [{"id": "one", "actual": {"answer_status": "ANSWERED", "llm_used": True,
             "raw_llm_response": "actual", "citations": ["A"]}, "trace_start": 0, "trace_end": len(events)}]
    assessment = module.assess({"cases": [case()], "source_refs": {}}, rows, events)
    assert assessment["exit_code"] == 2
    assert assessment["cases"][0]["manual_verdict"] == "UNDECIDED"


def test_status_mismatch_fails_even_with_provider_success():
    module = runner()
    events = [{"event": "query"}, {"event": "provider_generation", "provider": "gemini", "has_output": True}]
    rows = [{"id": "one", "actual": {"answer_status": "INSUFFICIENT_BASIS", "llm_used": True,
             "raw_llm_response": "actual", "citations": ["A"]}, "trace_start": 0, "trace_end": 2}]
    result = module.assess({"cases": [case()], "source_refs": {}}, rows, events)
    assert result["exit_code"] == 2
    assert "status_mismatch" in result["cases"][0]["failures"]


def valid_evidence(module):
    raw = "Synthetic provider draft."
    sections = {"issue": "Question", "rule": "[A]", "application": "Grounded reasoning", "conclusion": "Result"}
    import hashlib
    sha = lambda text: hashlib.sha256(text.encode()).hexdigest()
    assessment = {"assessment_call_count": 1, "assessment_claim_count": 1,
                  "assessment_outcome": "supported", "assessment_reason": "accepted", "assessment_coverage": True,
                  "assessment_prompt_sha256": sha("assessment prompt"), "assessment_output_sha256": sha("assessment output")}
    query = {"event": "query", "generation_attempted": True, "generated_delivered": True,
             "generation_disposition": "generated_delivered", "answer_status": "ANSWERED", "llm_used": True,
             "prompt_sha256": sha("generation prompt"), "output_sha256": sha(raw),
             "delivered_sha256": sha("\n".join(sections.values())), **assessment}
    provider = {"event": "provider_generation", "provider": "gemini", "model": "gemini-2.5-flash", "has_output": True}
    events = [{**provider, "prompt_sha256": query["prompt_sha256"], "output_sha256": query["output_sha256"]},
              {**provider, "prompt_sha256": assessment["assessment_prompt_sha256"], "output_sha256": assessment["assessment_output_sha256"]},
              {"event": "answer_assessment", **assessment}, query]
    rows = [{"id": "one", "actual": {"answer_status": "ANSWERED", "llm_used": True,
             "raw_llm_response": raw, "irac_sections": sections, "citations": ["A"]}, "trace_start": 0, "trace_end": len(events)}]
    return rows, events


def test_successful_mechanics_remain_undecided_not_llm_pass():
    module = runner()
    rows, events = valid_evidence(module)
    result = module.assess({"cases": [case()], "source_refs": {}}, rows, events)
    assert result["exit_code"] == 3
    assert result["status"] == "MANUAL_REVIEW_REQUIRED"
    assert result["cases"][0]["required_propositions"][0]["verdict"] == "UNDECIDED"


@pytest.mark.parametrize("failure", ["second_start", "health", "generation", "snapshot_drift", "manifest_drift", "cleanup"])
def test_run_cleans_all_owned_children_on_every_failure(tmp_path, monkeypatch, failure):
    module = runner()
    env = {"GEMINI_API_KEY": "private", "NEO4J_PASSWORD": "hidden"}
    manifest = {"head": "h"}
    monkeypatch.setattr(module, "preflight", Mock(return_value=({"ready": True, "manifest": manifest}, env)))
    monkeypatch.setattr(module, "fingerprint", Mock(side_effect=[(manifest, {"cases": [case()]}),
                        ({"head": "changed"} if failure == "manifest_drift" else manifest, {})]))
    monkeypatch.setattr(module, "snapshot", Mock(side_effect=[{"graph": "a"},
                        {"graph": "b" if failure == "snapshot_drift" else "a"}]))
    children = [Mock(pid=1001, stderr=io.BytesIO()), Mock(pid=1002, stderr=io.BytesIO())]
    spawn = Mock(side_effect=[children[0], OSError("private") if failure == "second_start" else children[1]])
    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    monkeypatch.setattr(module, "wait_healthy", Mock(side_effect=RuntimeError("hidden") if failure == "health" else None))
    monkeypatch.setattr(module, "generate_cases", Mock(side_effect=RuntimeError("private") if failure == "generation" else None,
                                                      return_value=[]))
    stop = Mock(side_effect=RuntimeError("hidden") if failure == "cleanup" else None)
    monkeypatch.setattr(module, "stop_owned", stop)
    code, report = module.run(ROOT, tmp_path)
    assert spawn.call_args.kwargs["stdout"] == module.subprocess.DEVNULL
    assert spawn.call_args.kwargs["stderr"] == module.subprocess.PIPE
    if failure in {"second_start", "health", "generation"}:
        assert report["error_code"] == {"second_start": "vite_start_failed", "health": "health_check_failed",
                                         "generation": "generation_failed"}[failure]
    assert "service_diagnostics" in report
    assert report["stage"] in {"vite_start", "health_check", "generation", "assessment"}
    assert code != 0
    assert stop.call_count == (1 if failure == "second_start" else 2)
    assert "private" not in json.dumps(report)
    assert "hidden" not in json.dumps(report)
    assert module.snapshot.call_count == 2
    assert report["services_started"] is True
    assert report["provider_called"] is False


def test_run_collision_never_starts_children(tmp_path, monkeypatch):
    module = runner()
    monkeypatch.setattr(module, "preflight", Mock(return_value=({"ready": False, "blockers": ["occupied"]}, {})))
    spawn = Mock()
    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    assert module.run(ROOT, tmp_path)[0] == 2
    spawn.assert_not_called()


def test_cli_requires_explicit_mode_and_preflight_never_runs(monkeypatch, capsys):
    module = runner()
    with pytest.raises(SystemExit) as error:
        module.main([])
    assert error.value.code == 2
    monkeypatch.setattr(module, "preflight", Mock(return_value=({"ready": False, "blockers": ["blocked"]}, {})))
    launch = Mock(side_effect=AssertionError("must not run"))
    monkeypatch.setattr(module, "run", launch)
    assert module.main(["--preflight"]) == 2
    launch.assert_not_called()
    assert "blocked" in capsys.readouterr().out


def test_generation_failure_does_not_retry_or_skip_following_case(tmp_path, monkeypatch):
    module = runner()
    boundary = Mock(side_effect=[TimeoutError("private"), {"raw_llm_response": "actual"}])
    monkeypatch.setattr(module, "local_json", boundary)
    trace = tmp_path / "trace.jsonl"
    trace.touch()
    second = {**case(), "id": "two"}
    rows = module.generate_cases({"cases": [case(), second]}, tmp_path, trace, {})
    assert boundary.call_count == 2
    assert rows[0]["error"] == "request_failed_no_retry"
    assert rows[1]["actual"]["raw_llm_response"] == "actual"
    assert "private" not in json.dumps(rows)


@pytest.mark.parametrize("change", ["model", "assessment", "prompt_hash"])
def test_invalid_real_generation_provenance_fails(change):
    module = runner()
    query = {"event": "query", "generation_attempted": True, "assessment_call_count": 1,
             "prompt_sha256": "a" * 64, "output_sha256": "b" * 64,
             "assessment_prompt_sha256": "c" * 64, "assessment_output_sha256": "d" * 64}
    provider = {"event": "provider_generation", "provider": "gemini", "model": "gemini-2.5-flash", "has_output": True}
    events = [provider, {"event": "answer_assessment", "assessment_call_count": 1}, query]
    if change == "model":
        provider["model"] = "qwen2.5:7b"
    elif change == "assessment":
        events.pop(1)
    else:
        query.pop("prompt_sha256")
    rows = [{"id": "one", "actual": {"answer_status": "ANSWERED", "llm_used": True,
             "raw_llm_response": "actual", "citations": ["A"]}, "trace_start": 0, "trace_end": len(events)}]
    assert module.assess({"cases": [case()]}, rows, events)["exit_code"] == 2


@pytest.mark.parametrize("mutation", [
    "llm_string", "provider_string", "attempt_false", "delivered_false", "zero_assessments", "count_bool",
    "draft_replaced", "unknown_outcome", "coverage_false", "assessment_mismatch", "provider_hash",
    "raw_hash", "delivered_hash", "nonhex", "duplicate_generation", "missing_assessment_provider", "same_prompts",
])
def test_contradictory_evidence_cannot_pass(mutation):
    module = runner()
    rows, events = valid_evidence(module)
    query, assessed = events[-1], events[-2]
    if mutation == "llm_string":
        rows[0]["actual"]["llm_used"] = "false"
    elif mutation == "provider_string":
        events[0]["has_output"] = "false"
    elif mutation == "attempt_false":
        query["generation_attempted"] = False
    elif mutation == "delivered_false":
        query["generated_delivered"] = False
    elif mutation == "zero_assessments":
        query["assessment_call_count"] = 0
        events.remove(assessed)
    elif mutation == "count_bool":
        query["assessment_call_count"] = True
    elif mutation == "draft_replaced":
        query["generation_disposition"] = "draft_replaced"
    elif mutation == "unknown_outcome":
        query["assessment_outcome"] = assessed["assessment_outcome"] = "unknown"
    elif mutation == "coverage_false":
        query["assessment_coverage"] = assessed["assessment_coverage"] = False
    elif mutation == "assessment_mismatch":
        assessed["assessment_output_sha256"] = "e" * 64
    elif mutation == "provider_hash":
        events[1]["output_sha256"] = "e" * 64
    elif mutation == "raw_hash":
        rows[0]["actual"]["raw_llm_response"] += " changed"
    elif mutation == "delivered_hash":
        rows[0]["actual"]["irac_sections"]["conclusion"] = "changed"
    elif mutation == "nonhex":
        query["prompt_sha256"] = events[0]["prompt_sha256"] = "z" * 64
    elif mutation == "duplicate_generation":
        events.insert(0, events[0].copy())
    elif mutation == "missing_assessment_provider":
        events.pop(1)
    else:
        query["assessment_prompt_sha256"] = assessed["assessment_prompt_sha256"] = events[1]["prompt_sha256"] = query["prompt_sha256"]
    rows[0]["trace_end"] = len(events)
    assert module.assess({"cases": [case()]}, rows, events)["exit_code"] == 2


@pytest.mark.parametrize("mutation", [None, "hash", "attempt", "assessment", "raw", "llm_string"])
def test_bypass_requires_zero_generation_provenance(mutation):
    module = runner()
    query = {"event": "query", "generation_disposition": "safety_bypass", "generation_attempted": False,
             "generated_delivered": False, "assessment_call_count": 0}
    actual = {"answer_status": "INSUFFICIENT_BASIS", "llm_used": False, "raw_llm_response": None}
    if mutation == "hash":
        query["prompt_sha256"] = "a" * 64
    elif mutation == "attempt":
        query["generation_attempted"] = "false"
    elif mutation == "assessment":
        query["assessment_prompt_sha256"] = "b" * 64
    elif mutation == "raw":
        actual["raw_llm_response"] = "draft"
    elif mutation == "llm_string":
        actual["llm_used"] = "false"
    expected = {**case(), "expected_status": "INSUFFICIENT_BASIS", "expected_evidence_ids": []}
    rows = [{"id": "one", "actual": actual, "trace_start": 0, "trace_end": 1}]
    assert module.assess({"cases": [expected]}, rows, [query])["exit_code"] == (3 if mutation is None else 2)


def test_replaced_draft_keeps_raw_and_delivered_hashes_separate():
    module = runner()
    rows, events = valid_evidence(module)
    query = events[-1]
    rows[0]["actual"]["answer_status"] = query["answer_status"] = "INSUFFICIENT_BASIS"
    query.update(generation_disposition="draft_replaced", generated_delivered=False,
                 assessment_outcome="unsupported", assessment_reason="unsupported", assessment_coverage=False)
    for key in ("assessment_outcome", "assessment_reason", "assessment_coverage"):
        events[-2][key] = query[key]
    expected = {**case(), "expected_status": "INSUFFICIENT_BASIS"}
    assert module.assess({"cases": [expected]}, rows, events)["exit_code"] == 3
    query["output_sha256"] = query["delivered_sha256"]
    assert module.assess({"cases": [expected]}, rows, events)["exit_code"] == 2


def test_missing_coverage_is_not_inferred_from_supported_outcome():
    module = runner()
    rows, events = valid_evidence(module)
    for event in (events[-1], events[-2]):
        event.pop("assessment_coverage")
    assert module.assess({"cases": [case()]}, rows, events)["exit_code"] == 2


@pytest.mark.parametrize("assessment", [False, True])
@pytest.mark.parametrize("http_status,status", [(200, "no_candidates"), (400, "http_error"),
    (429, "http_rate_limited"), (None, "transport_timeout")])
def test_failed_attempts_are_correlated_not_byte_corruption(assessment, http_status, status):
    module = runner()
    rows, events = valid_evidence(module)
    query = events[-1]
    provider = events[1 if assessment else 0]
    provider.update(failed=True, has_output=False, output_sha256=None, request_config_sha256="f" * 64)
    diagnostic = {"event": "gemini_response", "prompt_sha256": provider["prompt_sha256"],
                  "request_config_sha256": "f" * 64, "http_status": http_status,
                  "status": status, "will_retry": False}
    query.update(generated_delivered=False, answer_status="INSUFFICIENT_BASIS")
    rows[0]["actual"]["answer_status"] = "INSUFFICIENT_BASIS"
    if assessment:
        query.update(generation_disposition="draft_replaced", assessment_output_sha256=None,
                     assessment_outcome="unknown", assessment_reason="transport", assessment_coverage=False,
                     assessment_claim_count=0)
        events[-2] = {"event": "answer_assessment", **{k: v for k, v in query.items() if k.startswith("assessment_")}}
    else:
        query.update(generation_disposition="provider_no_output", output_sha256=None,
                     assessment_call_count=0, llm_used=False)
        for key in list(query):
            if key.startswith("assessment_") and key != "assessment_call_count":
                del query[key]
        rows[0]["actual"].update(llm_used=False, raw_llm_response=None)
        events = [provider, query]
    events.insert(0, diagnostic)
    rows[0]["trace_end"] = len(events)
    assert module.provenance_failures(events) == []
    assert module.delivery_failures(query, rows[0]["actual"]) == []
    report = module.assess({"cases": [case()]}, rows, events)
    assert report["exit_code"] == 2
    failures = report["cases"][0]["failures"]
    assert ("assessment_failed" if assessment else "provider_no_output") in failures
    assert not {"raw_output_hash_mismatch", "provider_hash_mismatch", "generation_hash_missing",
                "assessment_hash_missing", "delivery_disposition_mismatch"} & set(failures)
    diagnostic["request_config_sha256"] = "e" * 64
    assert module.provenance_failures(events)


def test_empty_evaluation_is_not_a_report():
    module = runner()
    assert module.assess({"cases": []}, [], [])["exit_code"] == 2


def test_credentials_reject_wrong_docker_binding_before_using_auth(monkeypatch):
    module = runner()
    inspected = [{"State": {"Running": True}, "NetworkSettings": {"Ports": {
        "7687/tcp": [{"HostIp": "0.0.0.0", "HostPort": "17687"}]}}}]
    monkeypatch.setattr(module.subprocess, "check_output", Mock(return_value=json.dumps(inspected)))
    with pytest.raises(ValueError, match="not_isolated"):
        module.credentials()


def test_artifact_identity_includes_exact_review_hash():
    module = runner()
    identity = module.artifact_identity(ROOT)
    assert identity["review_sha256"] == module.file_hash(ROOT / module.REVIEW)


@pytest.mark.parametrize("mutation", [None, "review_sha256", "corpus_sha256", "extra", "missing", "status", "environment", "neo4j_connected"])
def test_health_keeps_exact_identity_and_staging_assertions(monkeypatch, mutation):
    module = runner()
    identity = module.artifact_identity(ROOT)
    health = {"status": "healthy", "neo4j_connected": True, "environment": "staging",
              "artifacts": {**identity, "review_sha256": module.file_hash(ROOT / module.REVIEW)}}
    if mutation in {"review_sha256", "corpus_sha256", "extra"}:
        health["artifacts"][mutation] = "changed"
    elif mutation == "missing":
        health["artifacts"].pop("review_sha256")
    elif mutation:
        health[mutation] = False
    monkeypatch.setattr(module, "local_json", Mock(return_value=health))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    child = Mock()
    child.poll.return_value = None
    if mutation is None:
        module.wait_healthy({}, {"artifacts": identity}, [child])
    else:
        with pytest.raises(RuntimeError, match="health_artifact_mismatch" if mutation in {
                "review_sha256", "corpus_sha256", "extra", "missing"} else "health_timeout"):
            module.wait_healthy({}, {"artifacts": identity}, [child])


def test_service_diagnostics_allowlist_never_retains_raw_errors():
    module = runner()
    child = SimpleNamespace(stderr=io.BytesIO(
        b"private-password private-key arbitrary secret\nModuleNotFoundError: private-key\n"
        b"Application startup complete.\n"))
    records = []
    module.capture_service_errors(child, "api", records)
    assert records == [{"service": "api", "code": "module_missing"},
                       {"service": "api", "code": "application_startup_complete"}]
    assert "private" not in json.dumps(records)


def test_local_http_rejects_external_and_production_urls():
    module = runner()
    for url in ("https://example.com", "http://127.0.0.1:8000/api/query", "http://localhost:18000/api/query"):
        with pytest.raises(ValueError):
            module.local_json(url)
