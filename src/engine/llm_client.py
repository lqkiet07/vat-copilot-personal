"""LLM client supporting local Ollama, Google Gemini API, and OpenAI API."""
from collections.abc import Iterator
import json
import os
import time
from json import JSONDecodeError
from urllib.error import HTTPError, URLError
import urllib.request
from src.engine.diagnostics import emit_trace, text_sha256


def _provider_call(provider, model, prompt, callback, **kwargs):
    response = callback(prompt, model=model, **kwargs)
    emit_trace({
        "event": "provider_generation", "provider": provider, "model": model,
        "prompt_sha256": text_sha256(prompt),
        "output_sha256": text_sha256(response) if response else None,
        "has_output": bool(response and response.strip()),
    })
    return response


def check_ollama_available(
    host: str = "http://localhost:11434",
    timeout: float = 2.0,
) -> bool:
    """Check if the local Ollama service is reachable."""
    req = urllib.request.Request(host, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status == 200
    except (URLError, OSError, TimeoutError):
        return False


_GEMINI_FINISH_REASONS = frozenset({
    "FINISH_REASON_UNSPECIFIED", "STOP", "MAX_TOKENS", "SAFETY", "RECITATION",
    "LANGUAGE", "OTHER", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII",
    "MALFORMED_FUNCTION_CALL", "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT",
    "IMAGE_OTHER", "NO_IMAGE", "IMAGE_RECITATION", "UNEXPECTED_TOOL_CALL",
    "TOO_MANY_TOOL_CALLS", "MISSING_THOUGHT_SIGNATURE",
})
_GEMINI_BLOCK_REASONS = frozenset({
    "BLOCK_REASON_UNSPECIFIED", "SAFETY", "OTHER", "BLOCKLIST",
    "PROHIBITED_CONTENT", "IMAGE_SAFETY",
})


def _gemini_enum(value, allowed):
    if value is None:
        return None
    return value if isinstance(value, str) and value in allowed else "UNKNOWN"


def _gemini_metadata(data, event):
    usage = data.get("usageMetadata", {})
    if isinstance(usage, dict):
        for name, key in (("prompt", "promptTokenCount"), ("candidates", "candidatesTokenCount"),
                          ("thought", "thoughtsTokenCount"), ("total", "totalTokenCount")):
            count = usage.get(key)
            event["usage"][name] = count if type(count) is int and 0 <= count <= 2 ** 31 - 1 else None
    feedback = data.get("promptFeedback", {})
    if isinstance(feedback, dict):
        event["prompt_block_reason"] = _gemini_enum(feedback.get("blockReason"), _GEMINI_BLOCK_REASONS)
    candidates = data.get("candidates", [])
    if not isinstance(candidates, list):
        return None
    event["candidate_count"] = len(candidates)
    event["finish_reasons"] = [
        _gemini_enum(candidate.get("finishReason"), _GEMINI_FINISH_REASONS)
        if isinstance(candidate, dict) else None for candidate in candidates[:8]
    ]
    return candidates


def _gemini_output(data, event):
    event["status"] = "invalid_schema"
    if not isinstance(data, dict):
        return None
    candidates = _gemini_metadata(data, event)
    if candidates is None:
        return None
    if not candidates:
        event["status"] = "prompt_blocked" if event["prompt_block_reason"] not in (
            None, "UNKNOWN", "BLOCK_REASON_UNSPECIFIED") else "no_candidates"
        return None
    first = candidates[0]
    if not isinstance(first, dict) or not isinstance(first.get("content", {}), dict):
        return None
    parts = first.get("content", {}).get("parts", [])
    if not isinstance(parts, list):
        return None
    text_parts = [part["text"] for part in parts if isinstance(part, dict)
                  and not part.get("thought") and isinstance(part.get("text"), str)]
    event["text_part_count"] = len(text_parts)
    event["thought_part_count"] = sum(bool(part.get("thought")) for part in parts if isinstance(part, dict))
    output = "".join(text_parts) or None
    if first.get("finishReason") == "MAX_TOKENS":
        event["status"] = "max_tokens"
    elif output:
        event["status"] = "success"
    else:
        event["status"] = "thought_only" if event["thought_part_count"] else "no_text"
    return output


def _call_gemini_api(prompt: str, api_key: str, model: str = "gemini-2.5-flash", timeout: float = 30.0) -> str | None:
    """Call Google Gemini REST API."""
    deadline = time.monotonic() + timeout
    config = {"temperature": 0.2, "maxOutputTokens": 2048}
    template = {
        "event": "gemini_response", "provider": "gemini", "model": model,
        "prompt_sha256": text_sha256(prompt),
        "request_config_sha256": text_sha256(json.dumps(config, sort_keys=True, separators=(",", ":"))),
        "generation_config": config, "status": "unexpected_error", "http_status": None,
        "error_type": None, "candidate_count": None, "text_part_count": 0,
        "thought_part_count": 0, "finish_reasons": [], "prompt_block_reason": None,
        "usage": dict.fromkeys(("prompt", "candidates", "thought", "total")),
    }
    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config}).encode("utf-8")
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=payload, headers={"Content-Type": "application/json", "x-goog-api-key": api_key}, method="POST",
    )
    for attempt in range(1, 4):
        event = dict(template, usage=template["usage"].copy(), attempt=attempt,
                     max_attempts=3, will_retry=False, retry_delay=0)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            output = _gemini_attempt(req, remaining, event)
            delay = attempt
            if event["error_type"] == "HTTPError" and event["http_status"] in {502, 503, 504} and attempt < 3:
                if deadline - time.monotonic() > delay:
                    time.sleep(delay)
                    event["will_retry"] = time.monotonic() < deadline
                    event["retry_delay"] = delay if event["will_retry"] else 0
        finally:
            emit_trace(event)
        if not event["will_retry"]:
            return output
    return None


def _gemini_attempt(req, timeout, event):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.status
            event["http_status"] = code if type(code) is int and 100 <= code <= 599 else None
            data = json.loads(resp.read().decode("utf-8"))
        return _gemini_output(data, event)
    except HTTPError as error:
        code = error.code
        event["http_status"] = code if type(code) is int and 100 <= code <= 599 else None
        event["error_type"] = "HTTPError"
        event["status"] = {401: "http_unauthorized", 403: "http_forbidden", 429: "http_rate_limited"}.get(
            event["http_status"], "http_server_error" if event["http_status"] is not None and code >= 500 else "http_error")
        error.close()
    except (URLError, OSError) as error:
        timed_out = isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError)
        event["status"] = "transport_timeout" if timed_out else "transport_error"
        event["error_type"] = "TimeoutError" if timed_out else "URLError" if isinstance(error, URLError) else "OSError"
    except (JSONDecodeError, UnicodeDecodeError) as error:
        event["status"] = "invalid_json"
        event["error_type"] = "JSONDecodeError" if isinstance(error, JSONDecodeError) else "UnicodeDecodeError"
    return None


def _call_openai_api(prompt: str, api_key: str, model: str = "gpt-4o-mini", timeout: float = 30.0) -> str | None:
    """Call OpenAI Chat Completions API."""
    url = "https://api.openai.com/v1/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "Bạn là Trợ lý Pháp lý Thuế GTGT Việt Nam chuyên nghiệp."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
    }).encode("utf-8")
    
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content")
    except Exception:
        return None
    return None


def _call_ollama(
    prompt: str,
    model: str = "qwen2.5:7b",
    host: str = "http://localhost:11434",
    timeout: float = 120.0,
    options: dict | None = None,
) -> str | None:
    """Generate a response from Ollama API using the specified prompt and model."""
    url = f"{host}/api/generate"
    payload_dict: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    if options:
        payload_dict["options"] = options
    payload = json.dumps(payload_dict).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
            res = data.get("response")
            return str(res) if res is not None else None
    except (URLError, OSError, TimeoutError, JSONDecodeError):
        return None


def _llm_provider() -> str:
    provider = os.getenv("LLM_PROVIDER", "auto").strip().lower()
    if provider not in {"auto", "gemini"}:
        raise ValueError("LLM_PROVIDER must be auto or gemini")
    return provider


def generate_llm_response(
    prompt: str,
    model: str = "qwen2.5:7b",
    host: str = "http://localhost:11434",
    timeout: float = 120.0,
    options: dict | None = None,
) -> str | None:
    """Generate a response using Ollama API, with optional Gemini/OpenAI cloud provider."""
    provider = _llm_provider()
    gemini_model = model if model.startswith("gemini-") else "gemini-2.5-flash"
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if provider == "gemini":
        if not gemini_key:
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
        return _provider_call("gemini", gemini_model, prompt, _call_gemini_api, api_key=gemini_key, timeout=timeout)
    if gemini_key:
        res = _provider_call("gemini", gemini_model, prompt, _call_gemini_api, api_key=gemini_key, timeout=timeout)
        if res:
            return res

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        res = _provider_call("openai", "gpt-4o-mini", prompt, _call_openai_api, api_key=openai_key, timeout=timeout)
        if res:
            return res

    return _provider_call("ollama", model, prompt, _call_ollama, host=host, timeout=timeout, options=options)


def generate_llm_response_stream(
    prompt: str,
    model: str = "qwen2.5:7b",
    host: str = "http://localhost:11434",
    timeout: float = 120.0,
) -> Iterator[str]:
    """Yield buffered cloud responses or native Ollama NDJSON fragments."""
    provider = _llm_provider()
    if provider == "gemini" or os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY"):
        response = generate_llm_response(prompt, model=model, host=host, timeout=timeout)
        if response:
            yield response
        return
    url = f"{host}/api/generate"
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": True,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    parts: list[str] = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            for raw_line in response:
                chunk = _parse_stream_chunk(raw_line)
                if chunk is None:
                    continue
                token = chunk.get("response")
                if token:
                    parts.append(str(token))
                    yield str(token)
                if chunk.get("done"):
                    return
    except (URLError, OSError, TimeoutError, JSONDecodeError):
        return
    finally:
        output = "".join(parts)
        emit_trace({
            "event": "provider_generation", "provider": "ollama", "model": model,
            "prompt_sha256": text_sha256(prompt),
            "output_sha256": text_sha256(output) if output else None,
            "has_output": bool(output.strip()),
        })


def _parse_stream_chunk(raw_line: bytes) -> dict | None:
    """Decode a single NDJSON line of an Ollama stream, skipping malformed lines."""
    line = raw_line.decode("utf-8", errors="replace").strip()
    if not line:
        return None
    try:
        chunk = json.loads(line)
    except JSONDecodeError:
        return None
    return chunk if isinstance(chunk, dict) else None

