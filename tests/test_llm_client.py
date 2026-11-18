import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from io import BytesIO

from src.engine.llm_client import _call_gemini_api
from src.engine.diagnostics import text_sha256
import pytest

from src.engine.llm_client import (
    check_ollama_available,
    generate_llm_response,
    generate_llm_response_stream,
)


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
    }
    serialized = json.dumps(event)
    assert all(secret not in serialized for secret in (
        "private-prompt-secret", "private-key-secret", "private-output-secret",
        "private-error-secret", "https://", "private-reason-secret",
    ))
    return result, event


@pytest.mark.parametrize("code,status", [(400, "http_error"), (401, "http_unauthorized"),
    (403, "http_forbidden"), (429, "http_rate_limited"), (500, "http_server_error"), (503, "http_server_error")])
def test_gemini_diagnostic_http_errors_are_safe_and_not_retried(code, status):
    error = HTTPError("https://private-error-secret", code, "private-error-secret", {}, BytesIO(b"private-error-secret"))
    result, event = _gemini_diagnostic(error=error)
    assert result is None
    assert (event["status"], event["http_status"], event["error_type"]) == (status, code, "HTTPError")
    assert error.fp.tell() == 0


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
