import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from io import BytesIO
from types import SimpleNamespace
import pytest

from src.engine.llm_client import _call_gemini_api
from src.engine.diagnostics import text_sha256

from src.engine.llm_client import (
    check_ollama_available,
    generate_llm_response,
    generate_llm_response_stream,
)


@pytest.fixture(autouse=True)
def gemini_clock(monkeypatch):
    clock = SimpleNamespace(now=100.0, sleeps=[])
    def sleep(delay):
        clock.sleeps.append(delay)
        clock.now += delay
    clock.sleep = sleep
    monkeypatch.setattr("src.engine.llm_client.time", SimpleNamespace(
        monotonic=lambda: clock.now, sleep=sleep))
    return clock


@pytest.fixture(autouse=True)
def clear_provider_env(monkeypatch):
    for name in ("LLM_PROVIDER", "GEMINI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _generate(prompt, streaming):
    if streaming:
        return list(generate_llm_response_stream(prompt, timeout=7))
    return generate_llm_response(prompt, timeout=7)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("provider", ["gemini", "auto"])
def test_gemini_returns_all_visible_parts_with_key_only_in_header(monkeypatch, streaming, provider):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-key")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-openai-key")
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps({"candidates": [{"content": {"parts": [
        {"text": "private reasoning", "thought": True},
        {"text": "Thuế "}, {"inlineData": {}}, None, "invalid", {"text": 42},
        {"text": "0%"},
    ]}}]}).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=response) as request:
        assert _generate("prompt", streaming) == (["Thuế 0%"] if streaming else "Thuế 0%")

    request.assert_called_once()
    req = request.call_args.args[0]
    assert req.full_url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    assert req.get_header("X-goog-api-key") == "test-secret-key"
    assert "test-secret-key" not in req.full_url
    assert "test-secret-key" not in req.data.decode("utf-8")
    assert json.loads(req.data)["contents"] == [{"parts": [{"text": "prompt"}]}]
    assert request.call_args.kwargs["timeout"] == 7


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("key", [None, "", "   "])
def test_strict_gemini_missing_key_fails_without_network(monkeypatch, streaming, key):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-secret")
    if key is not None:
        monkeypatch.setenv("GEMINI_API_KEY", key)

    with patch("urllib.request.urlopen") as request:
        with pytest.raises(ValueError, match="GEMINI_API_KEY") as error:
            _generate("prompt", streaming)
    request.assert_not_called()
    assert "unused-secret" not in str(error.value)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("failure", ["transport", "json", "empty", "malformed", "thinking"])
def test_strict_gemini_failure_never_falls_back_or_logs_secrets(monkeypatch, capsys, streaming, failure):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-key")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-secret")
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = {
        "json": b"not json",
        "empty": b'{"candidates": []}',
        "malformed": b'{"candidates": [null]}',
        "thinking": b'{"candidates": [{"content": {"parts": [{"thought": true, "text": "hidden"}]}}]}',
    }.get(failure, b"{}")
    error = URLError("https://example.invalid/?key=test-secret-key") if failure == "transport" else None

    with patch("urllib.request.urlopen", return_value=response, side_effect=error) as request:
        assert _generate("prompt", streaming) == ([] if streaming else None)
    request.assert_called_once()
    assert "generativelanguage.googleapis.com" in request.call_args.args[0].full_url
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_auto_preserves_openai_then_ollama_fallback(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b'{"response": "local"}'
    with patch("urllib.request.urlopen", side_effect=[URLError("offline"), URLError("offline"), response]) as request:
        assert generate_llm_response("prompt") == "local"
    assert [call.args[0].full_url.split("/")[2] for call in request.call_args_list] == [
        "generativelanguage.googleapis.com", "api.openai.com", "localhost:11434",
    ]


@pytest.mark.parametrize("streaming", [False, True])
def test_unknown_provider_is_rejected_without_network(monkeypatch, streaming):
    monkeypatch.setenv("LLM_PROVIDER", "invalid-provider")
    with patch("urllib.request.urlopen") as request:
        with pytest.raises(ValueError, match="LLM_PROVIDER"):
            _generate("prompt", streaming)
    request.assert_not_called()


@pytest.mark.parametrize("streaming", [False, True])
def test_auto_gemini_failure_preserves_fallback(monkeypatch, streaming):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b'{"response": "local"}'
    with patch("urllib.request.urlopen", side_effect=[URLError("offline"), response]) as request:
        assert _generate("prompt", streaming) == (["local"] if streaming else "local")
    assert request.call_count == 2
    assert request.call_args.args[0].full_url == "http://localhost:11434/api/generate"


@pytest.mark.parametrize("streaming", [False, True])
def test_auto_openai_success_uses_cloud(monkeypatch, streaming):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = b'{"choices": [{"message": {"content": "cloud"}}]}'
    with patch("urllib.request.urlopen", return_value=response) as request:
        assert _generate("prompt", streaming) == (["cloud"] if streaming else "cloud")
    request.assert_called_once()
    assert request.call_args.args[0].full_url == "https://api.openai.com/v1/chat/completions"


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("output", ["visible", "", None])
def test_r5_provider_boundary_trace_has_actual_model_and_hashes(monkeypatch, tmp_path, streaming, output):
    import hashlib
    path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("VAT_DIAGNOSTICS", str(path))
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "never-log-this-key")
    body = {"candidates": [{"content": {"parts": [{"text": output}]}}]}
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(body).encode()
    with patch("urllib.request.urlopen", return_value=response) as request:
        result = generate_llm_response_stream("private prompt", model="gemini-2.5-flash") if streaming else generate_llm_response("private prompt", model="gemini-2.5-flash")
        if streaming:
            list(result)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["event"] for row in rows] == ["gemini_response", "provider_generation"]
    row = rows[1]
    assert rows[0]["prompt_sha256"] == row["prompt_sha256"]
    assert rows[0]["model"] == row["model"]
    assert row["event"] == "provider_generation"
    assert row["provider"] == "gemini" and row["model"] == "gemini-2.5-flash"
    assert row["prompt_sha256"] == hashlib.sha256(b"private prompt").hexdigest()
    assert row["output_sha256"] == (hashlib.sha256(output.encode()).hexdigest() if output else None)
    assert row["has_output"] is bool(output)
    assert "gemini-2.5-flash:generateContent" in request.call_args.args[0].full_url
    assert all(secret not in path.read_text() for secret in ("never-log-this-key", "private prompt", "visible", "https://"))


def test_r5_native_stream_records_actual_provider_and_untrimmed_output(monkeypatch, tmp_path):
    import hashlib
    path = tmp_path / "trace.jsonl"
    monkeypatch.setenv("VAT_DIAGNOSTICS", str(path))
    response = MagicMock()
    response.__enter__.return_value = response
    response.__iter__.return_value = iter([b'{"response":"  local", "done":false}\n', b'{"response":" output  ", "done":true}\n'])
    with patch("urllib.request.urlopen", return_value=response):
        assert "".join(generate_llm_response_stream("private prompt", model="local-model")) == "  local output  "
    row, = [json.loads(line) for line in path.read_text().splitlines()]
    assert row == {
        "event": "provider_generation", "provider": "ollama", "model": "local-model",
        "prompt_sha256": hashlib.sha256(b"private prompt").hexdigest(),
        "output_sha256": hashlib.sha256(b"  local output  ").hexdigest(), "has_output": True,
    }


def _gemini_diagnostic(body=None, error=None):
    response = MagicMock()
    response.status = 200
    response.__enter__.return_value = response
    response.read.return_value = body if isinstance(body, bytes) else json.dumps(body).encode()
    with patch("urllib.request.urlopen", return_value=response, side_effect=error) as request:
        with patch("src.engine.llm_client.emit_trace") as trace:
            result = _call_gemini_api("private-prompt-secret", "private-key-secret", timeout=7)
    request.assert_called_once()
    trace.assert_called_once()
    event = trace.call_args.args[0]
    assert event["event"] == "gemini_response"
    assert event["provider"] == "gemini"
    assert event["model"] == "gemini-2.5-flash"
    assert event["prompt_sha256"] == text_sha256("private-prompt-secret")
    config = {"temperature": 0.2, "maxOutputTokens": 2048}
    assert json.loads(request.call_args.args[0].data)["generationConfig"] == config
    assert event["generation_config"] == config
    assert event["request_config_sha256"] == text_sha256(json.dumps(config, sort_keys=True, separators=(",", ":")))
    assert set(event) == {
        "event", "provider", "model", "prompt_sha256", "request_config_sha256",
        "generation_config", "status", "http_status", "error_type", "candidate_count",
        "text_part_count", "thought_part_count", "finish_reasons", "prompt_block_reason", "usage",
        "attempt", "max_attempts", "will_retry", "retry_delay",
    }
    serialized = json.dumps(event)
    assert all(secret not in serialized for secret in (
        "private-prompt-secret", "private-key-secret", "private-output-secret",
        "private-error-secret", "https://", "private-reason-secret",
    ))
    return result, event


@pytest.mark.parametrize("code,status", [(400, "http_error"), (401, "http_unauthorized"),
    (403, "http_forbidden"), (429, "http_rate_limited"), (500, "http_server_error"), (501, "http_server_error")])
def test_gemini_diagnostic_http_errors_are_safe_and_not_retried(code, status):
    stream = BytesIO(b"private-error-secret")
    stream.read = MagicMock(side_effect=AssertionError("Error body must not be read"))
    error = HTTPError("https://private-error-secret", code, "private-error-secret", {}, stream)
    result, event = _gemini_diagnostic(error=error)
    assert result is None
    assert (event["status"], event["http_status"], event["error_type"]) == (status, code, "HTTPError")
    stream.read.assert_not_called()
    assert stream.closed


@pytest.mark.parametrize("error,status,error_type", [
    (TimeoutError("private-error-secret"), "transport_timeout", "TimeoutError"),
    (URLError(TimeoutError("private-error-secret")), "transport_timeout", "TimeoutError"),
    (URLError("https://private-error-secret"), "transport_error", "URLError"),
    (OSError("private-error-secret"), "transport_error", "OSError"),
])
def test_gemini_diagnostic_transport_errors_are_safe(error, status, error_type):
    result, event = _gemini_diagnostic(error=error)
    assert result is None
    assert (event["status"], event["error_type"], event["http_status"]) == (status, error_type, None)


@pytest.mark.parametrize("body,status", [
    (b"private-error-secret", "invalid_json"), (b"\xff", "invalid_json"),
    (None, "invalid_schema"), ([], "invalid_schema"),
    ({"candidates": None}, "invalid_schema"), ({"candidates": [None]}, "invalid_schema"),
    ({"candidates": [{"content": None}]}, "invalid_schema"),
    ({"candidates": [{"content": {"parts": {}}}]}, "invalid_schema"),
    ({}, "no_candidates"), ({"candidates": []}, "no_candidates"),
    ({"candidates": [{"content": {"parts": []}}]}, "no_text"),
    ({"candidates": [{"content": {"parts": [{"thought": True, "text": "private-output-secret"}]}}]}, "thought_only"),
    ({"candidates": [{"finishReason": "MAX_TOKENS"}]}, "max_tokens"),
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "prompt_blocked"),
])
def test_gemini_diagnostic_no_output_causes(body, status):
    result, event = _gemini_diagnostic(body)
    assert result is None
    assert event["status"] == status
    assert event["http_status"] == 200


def test_gemini_diagnostic_counts_usage_and_first_candidate_semantics():
    body = {"candidates": [
        {"finishReason": "MAX_TOKENS", "content": {"parts": [
            {"thought": True, "text": "private-output-secret"}, {"text": " one"}, {"text": "two "}, None]}},
        {"finishReason": "STOP", "content": {"parts": [{"text": "ignored"}]}},
    ], "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 3,
        "thoughtsTokenCount": 2048, "totalTokenCount": 2062}}
    result, event = _gemini_diagnostic(body)
    assert result == " onetwo "
    assert event["status"] == "max_tokens"
    assert (event["candidate_count"], event["text_part_count"], event["thought_part_count"]) == (2, 2, 1)
    assert event["finish_reasons"] == ["MAX_TOKENS", "STOP"]
    assert event["usage"] == {"prompt": 11, "candidates": 3, "thought": 2048, "total": 2062}


def test_gemini_diagnostic_sanitizes_and_bounds_untrusted_metadata():
    body = {"candidates": [{"finishReason": "private-reason-secret", "content": {"parts": [{"text": "ok"}]}}] * 30,
        "promptFeedback": {"blockReason": "private-reason-secret"},
        "usageMetadata": {"promptTokenCount": True, "candidatesTokenCount": -1,
            "thoughtsTokenCount": "private-error-secret", "totalTokenCount": 10 ** 100}}
    result, event = _gemini_diagnostic(body)
    assert result == "ok"
    assert event["status"] == "success"
    assert event["candidate_count"] == 30
    assert event["finish_reasons"] == ["UNKNOWN"] * 8
    assert event["prompt_block_reason"] == "UNKNOWN"
    assert event["usage"] == dict.fromkeys(("prompt", "candidates", "thought", "total"))


def test_gemini_diagnostic_does_not_swallow_programmer_errors():
    with patch("urllib.request.urlopen", side_effect=RuntimeError("programmer failure")):
        with pytest.raises(RuntimeError, match="programmer failure"):
            _call_gemini_api("prompt", "key")


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("codes", [(503,), (502, 504), (503, 503, 503)])
def test_gemini_transient_retries_preserve_request_and_logical_event(monkeypatch, gemini_clock, capsys, streaming, codes):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "private-key-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-secret")
    streams = [BytesIO(b"private-error-secret") for code in codes]
    for stream in streams:
        stream.read = MagicMock(side_effect=AssertionError("Error body must not be read"))
    errors = [HTTPError("https://private-error-secret", code, "private-error-secret",
                        {"secret": "private-key-secret"}, stream) for code, stream in zip(codes, streams)]
    def sleep(delay):
        assert all(stream.closed for stream in streams[:len(gemini_clock.sleeps) + 1])
        gemini_clock.sleep(delay)
    response = MagicMock(status=200)
    response.__enter__.return_value = response
    response.read.return_value = b'{"candidates":[{"content":{"parts":[{"text":"private-output-secret"}]}}]}'
    effects = errors + ([response] if len(codes) < 3 else [])
    events = []
    with patch("src.engine.llm_client.time.sleep", side_effect=sleep):
        with patch("urllib.request.urlopen", side_effect=effects) as request:
            with patch("src.engine.llm_client.emit_trace", side_effect=events.append):
                result = _generate("private-prompt-secret", streaming)
    expected = "private-output-secret" if len(codes) < 3 else None
    assert result == ([expected] if expected else []) if streaming else result == expected
    count = len(effects)
    assert request.call_count == count
    assert gemini_clock.sleeps == [1, 2][:count - 1]
    assert [call.kwargs["timeout"] for call in request.call_args_list] == [7, 6, 4][:count]
    assert all(call.args[0] is request.call_args_list[0].args[0] for call in request.call_args_list)
    diagnostics = events[:-1]
    assert [event["event"] for event in events] == ["gemini_response"] * count + ["provider_generation"]
    assert [event["attempt"] for event in diagnostics] == list(range(1, count + 1))
    assert [event["will_retry"] for event in diagnostics] == [True] * (count - 1) + [False]
    assert [event["retry_delay"] for event in diagnostics] == [1, 2][:count - 1] + [0]
    assert [event["http_status"] for event in diagnostics] == list(codes) + ([200] if expected else [])
    for field in ("model", "prompt_sha256", "request_config_sha256"):
        assert len({event[field] for event in diagnostics}) == 1
    assert all(event["max_attempts"] == 3 for event in diagnostics)
    assert events[-1]["has_output"] is bool(expected)
    for stream in streams:
        stream.read.assert_not_called()
        assert stream.closed
    assert all(secret not in json.dumps(events) for secret in (
        "private-key-secret", "private-error-secret", "private-prompt-secret", "private-output-secret", "https://"))
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("timeout,elapsed,oversleep,count,sleeps", [
    (1, 0, 0, 1, []), (7, 7, 0, 1, []), (3, 1, 0, 2, [1]),
    (7, 0, 7, 1, [1]), (0, 0, 0, 0, []),
])
def test_gemini_retry_deadline_includes_requests_and_sleep(gemini_clock, timeout, elapsed, oversleep, count, sleeps):
    events, remaining = [], []
    def transport(req, timeout):
        remaining.append(timeout)
        gemini_clock.now += elapsed
        raise HTTPError("https://secret", 503, "secret", {}, BytesIO(b"secret"))
    def sleep(delay):
        gemini_clock.sleep(delay)
        gemini_clock.now += oversleep
    with patch("src.engine.llm_client.time.sleep", side_effect=sleep):
        with patch("urllib.request.urlopen", side_effect=transport) as request:
            with patch("src.engine.llm_client.emit_trace", side_effect=events.append):
                assert _call_gemini_api("prompt", "key", timeout=timeout) is None
    assert request.call_count == count
    assert gemini_clock.sleeps == sleeps
    assert remaining == ([3, 1] if count == 2 else [timeout] if count else [])
    if count:
        assert not events[-1]["will_retry"]
        assert events[-1]["retry_delay"] == 0
    else:
        assert events == []


def test_gemini_semantically_wrong_visible_output_is_not_retried(gemini_clock):
    result, event = _gemini_diagnostic({"candidates": [{"content": {"parts": [{"text": "wrong legal answer"}]}}]})
    assert result == "wrong legal answer"
    assert event["attempt"] == 1 and event["max_attempts"] == 3
    assert event["will_retry"] is False and event["retry_delay"] == 0
    assert gemini_clock.sleeps == []


def test_check_ollama_available_success():
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.getcode.return_value = 200
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        assert check_ollama_available() is True


def test_check_ollama_available_failure():
    with patch("urllib.request.urlopen", side_effect=URLError("Connection refused")):
        assert check_ollama_available() is False


def test_generate_llm_response_success():
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"response": "Issue: test\\nRule: test", "done": true}'
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = generate_llm_response("Test prompt")
        assert result == "Issue: test\nRule: test"


@pytest.mark.parametrize("error", [TimeoutError("timed out"), URLError("Connection refused")])
def test_generate_llm_response_timeout_or_error(error):
    with patch("urllib.request.urlopen", side_effect=error):
        result = generate_llm_response("Test prompt")
        assert result is None


def test_generate_llm_response_sends_correct_payload():
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"response": "OK", "done": true}'
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        generate_llm_response(prompt="Hello", model="qwen2.5:7b", host="http://localhost:11434")

        assert mock_urlopen.called
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://localhost:11434/api/generate"
        assert req.get_header("Content-type") == "application/json"

        body = json.loads(req.data.decode("utf-8"))
        assert body == {
            "model": "qwen2.5:7b",
            "prompt": "Hello",
            "stream": False,
        }
