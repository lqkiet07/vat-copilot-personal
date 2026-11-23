"""LLM client supporting local Ollama, Google Gemini API, and OpenAI API."""
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import json
from io import BytesIO
import os
import time
from json import JSONDecodeError
from urllib.error import HTTPError, URLError
import urllib.request
from src.engine.diagnostics import emit_trace, text_sha256


_RESPONSE_DEADLINE = ContextVar("llm_response_deadline", default=None)
_ASSESSMENT_REQUEST = ContextVar("llm_assessment_request", default=False)


class AssessmentProviderError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


@contextmanager
def assessment_request():
    token = _ASSESSMENT_REQUEST.set(True)
    try:
        yield
    finally:
        _ASSESSMENT_REQUEST.reset(token)


@contextmanager
def response_deadline(deadline):
    token = _RESPONSE_DEADLINE.set(deadline)
    try:
        yield
    finally:
        _RESPONSE_DEADLINE.reset(token)


def _remaining_response_time():
    remaining = _RESPONSE_DEADLINE.get() - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Provider deadline exceeded")
    return remaining


def _read_response(response, limit=4 * 1024 * 1024):
    if _RESPONSE_DEADLINE.get() is None:
        return response.read()
    read = getattr(response, "read1", None)
    sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
    if not callable(read) or not callable(getattr(sock, "settimeout", None)):
        raise OSError("Provider response does not support bounded consumption")
    chunks, size = [], 0
    while True:
        sock.settimeout(_remaining_response_time())
        chunk = read(min(65536, limit + 1 - size))
        _remaining_response_time()
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > limit:
            raise OSError("Provider response exceeds size limit")
        chunks.append(chunk)


def _gemini_config(model, config=None):
    if config:
        return config
    if _ASSESSMENT_REQUEST.get():
        from src.engine.answer_assessment import assessment_generation_config
        return assessment_generation_config(model)
    # Thinking tokens count inside maxOutputTokens on Gemini 2.5 Flash; cap thinking to preserve full IRAC output.
    default: dict = {"temperature": 0.2, "maxOutputTokens": 4096}
    if model in {"gemini-2.5-flash", "gemini-2.5-flash-lite"}:
        default["thinkingConfig"] = {"thinkingBudget": 512}
    return default


def _provider_call(provider, model, prompt, callback, **kwargs):
    event = {
        "event": "provider_generation", "provider": provider, "model": model,
        "prompt_sha256": text_sha256(prompt), "output_sha256": None,
        "has_output": False, "failed": True,
    }
    if provider == "gemini":
        config = _gemini_config(model, kwargs.get("generation_config"))
        event["request_config_sha256"] = text_sha256(json.dumps(config, sort_keys=True, separators=(",", ":")))
    try:
        if _RESPONSE_DEADLINE.get() is not None:
            kwargs["timeout"] = min(kwargs.get("timeout", 120.0), _remaining_response_time())
        response = callback(prompt, model=model, **kwargs)
        if _RESPONSE_DEADLINE.get() is not None:
            _remaining_response_time()
        event.update(output_sha256=text_sha256(response) if isinstance(response, str) else None,
                     has_output=isinstance(response, str) and bool(response.strip()))
        event["failed"] = not event["has_output"]
        return response
    finally:
        emit_trace(event)


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


def _call_gemini_api(prompt: str, api_key: str, model: str = "gemini-2.5-flash", timeout: float = 30.0,
                     generation_config: dict | None = None) -> str | None:
    """Call Google Gemini REST API."""
    deadline = time.monotonic() + timeout
    config = _gemini_config(model, generation_config)
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
            if _ASSESSMENT_REQUEST.get():
                raise AssessmentProviderError("deadline")
            return None
        output = None
        try:
            output = _gemini_attempt(req, remaining, event)
            delay = attempt
            if event["error_type"] == "HTTPError" and event["http_status"] in {502, 503, 504} and attempt < 3:
                if deadline - time.monotonic() > delay:
                    time.sleep(delay)
                    event["will_retry"] = time.monotonic() < deadline
                    event["retry_delay"] = delay if event["will_retry"] else 0
        finally:
            if _ASSESSMENT_REQUEST.get():
                event["operation"] = "assessment"
                event["output_sha256"] = text_sha256(output) if isinstance(locals().get("output"), str) else None
            emit_trace(event)
        if not event["will_retry"]:
            if _ASSESSMENT_REQUEST.get():
                reasons = {"max_tokens": "truncated", "transport_timeout": "timeout",
                           "transport_error": "transport", "invalid_json": "invalid_json",
                           "invalid_schema": "invalid_schema"}
                if event["status"] != "success":
                    raise AssessmentProviderError(reasons.get(event["status"],
                        "transport" if event["error_type"] == "HTTPError" else "no_output"))
            return output
    return None


_ERROR_STATUSES = frozenset({
    "OK", "CANCELLED", "UNKNOWN", "INVALID_ARGUMENT", "DEADLINE_EXCEEDED", "NOT_FOUND",
    "ALREADY_EXISTS", "PERMISSION_DENIED", "UNAUTHENTICATED", "RESOURCE_EXHAUSTED",
    "FAILED_PRECONDITION", "ABORTED", "OUT_OF_RANGE", "UNIMPLEMENTED", "INTERNAL",
    "UNAVAILABLE", "DATA_LOSS",
})
_ERROR_FIELDS = frozenset({
    "contents", "contents[0].parts", "contents[0].parts[0].text",
    "generationConfig", "generation_config",
    *[f"{root}.{field}" for root in ("generationConfig", "generation_config") for field in (
        "temperature", "maxOutputTokens", "max_output_tokens", "responseMimeType", "response_mime_type",
        "responseJsonSchema", "response_json_schema", "thinkingConfig", "thinking_config",
        "thinkingConfig.thinkingBudget", "thinking_config.thinking_budget")],
})


def _gemini_error_body(error, timeout, event):
    deadline = time.monotonic() + min(timeout, 1.0)
    if _RESPONSE_DEADLINE.get() is not None:
        deadline = min(deadline, _RESPONSE_DEADLINE.get())
    try:
        with response_deadline(deadline):
            if isinstance(error.fp, BytesIO):
                _remaining_response_time()
                raw = error.fp.read(16385)
                _remaining_response_time()
                if len(raw) > 16384:
                    return
            else:
                raw = _read_response(error.fp, limit=16384)
            data = json.loads(raw)
            _remaining_response_time()
        detail = data.get("error") if isinstance(data, dict) else None
        if not isinstance(detail, dict):
            return
        code, status = detail.get("code"), detail.get("status")
        event["error_code"] = code if type(code) is int and 0 <= code <= 2 ** 31 - 1 else None
        event["error_status"] = status if isinstance(status, str) and status in _ERROR_STATUSES else None
        details = detail.get("details", [])
        fields = set()
        for item in details if isinstance(details, list) else []:
            if not isinstance(item, dict) or item.get("@type") != "type.googleapis.com/google.rpc.BadRequest":
                continue
            violations = item.get("fieldViolations", [])
            for violation in violations if isinstance(violations, list) else []:
                field = violation.get("field") if isinstance(violation, dict) else None
                if isinstance(field, str) and field in _ERROR_FIELDS:
                    fields.add(field)
        event["error_fields"] = sorted(fields)
    except (OSError, ValueError, RecursionError, TypeError):
        return
    finally:
        error.close()


def _gemini_attempt(req, timeout, event):
    deadline = time.monotonic() + timeout
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.status
            event["http_status"] = code if type(code) is int and 100 <= code <= 599 else None
            data = json.loads(_read_response(resp).decode("utf-8"))
        return _gemini_output(data, event)
    except HTTPError as error:
        code = error.code
        event["http_status"] = code if type(code) is int and 100 <= code <= 599 else None
        event["error_type"] = "HTTPError"
        event["status"] = {401: "http_unauthorized", 403: "http_forbidden", 429: "http_rate_limited"}.get(
            event["http_status"], "http_server_error" if event["http_status"] is not None and code >= 500 else "http_error")
        _gemini_error_body(error, max(0, deadline - time.monotonic()), event)
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
            data = json.loads(_read_response(resp).decode("utf-8"))
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
    generation_config: dict | None = None,
) -> str | None:
    """Generate a response from Ollama API using the specified prompt and model."""
    url = f"{host}/api/generate"
    payload_dict: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    if _ASSESSMENT_REQUEST.get() or generation_config:
        config = _gemini_config(model, generation_config)
        payload_dict["format"] = config["responseJsonSchema"]
        options = {"temperature": config["temperature"], "num_predict": config["maxOutputTokens"], **(options or {})}
    if options:
        payload_dict["options"] = options
    payload = json.dumps(payload_dict).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    event = {"event": "ollama_response", "provider": "ollama", "model": model,
             "operation": "assessment" if _ASSESSMENT_REQUEST.get() else "generation",
             "prompt_sha256": text_sha256(prompt), "prompt_chars": len(prompt),
             "num_ctx": (options or {}).get("num_ctx"), "num_predict": (options or {}).get("num_predict", 0),
             "prompt_eval_count": None,
             "prompt_coverage": "unknown", "output_sha256": None, "status": "transport"}
    deadline = time.monotonic() + timeout
    enclosing = _RESPONSE_DEADLINE.get()
    if enclosing is not None:
        deadline = min(deadline, enclosing)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if enclosing is None:
                data = json.loads(_read_response(response).decode("utf-8"))
            else:
                with response_deadline(deadline):
                    data = json.loads(_read_response(response).decode("utf-8"))
        output = _ollama_output(data, event)
        if _ASSESSMENT_REQUEST.get() and event["status"] != "success":
            raise AssessmentProviderError(event["status"])
        return output
    except (JSONDecodeError, UnicodeDecodeError) as error:
        event["status"] = "invalid_json"
        if _ASSESSMENT_REQUEST.get():
            raise AssessmentProviderError("invalid_json") from error
        return None
    except (URLError, OSError) as error:
        event["status"] = "timeout" if isinstance(error, TimeoutError) or isinstance(
            getattr(error, "reason", None), TimeoutError) else "transport"
        if _ASSESSMENT_REQUEST.get():
            raise AssessmentProviderError(event["status"]) from error
        return None
    finally:
        emit_trace(event)


def _ollama_output(data, event):
    event["status"] = "invalid_schema"
    if not isinstance(data, dict):
        return None
    count = data.get("prompt_eval_count")
    if type(count) is int and 0 <= count <= 2 ** 31 - 1:
        event["prompt_eval_count"] = count
    output = data.get("response")
    if not isinstance(output, str):
        return None
    event["output_sha256"] = text_sha256(output)
    event["status"] = "success" if output.strip() else "no_output"
    if data.get("done_reason") == "length":
        event["status"] = "truncated"
    context = event["num_ctx"]
    if context and event["prompt_eval_count"] is not None and count >= context - event["num_predict"]:
        event.update(status="context_capacity", prompt_coverage="capacity_reached")
    return output if event["status"] == "success" else None


def _llm_provider() -> str:
    provider = os.getenv("LLM_PROVIDER", "auto").strip().lower()
    if provider not in {"auto", "gemini", "ollama"}:
        raise ValueError("LLM_PROVIDER must be auto, gemini or ollama")
    return provider


def generate_llm_response(
    prompt: str,
    model: str = "qwen2.5:7b",
    host: str = "http://localhost:11434",
    timeout: float = 120.0,
    options: dict | None = None,
    generation_config: dict | None = None,
) -> str | None:
    """Generate a response using Ollama API, with optional Gemini/OpenAI cloud provider."""
    provider = _llm_provider()
    if provider == "ollama":
        return _provider_call("ollama", model, prompt, _call_ollama, host=host, timeout=timeout,
                              options=options, generation_config=generation_config)
    gemini_model = model if model.startswith("gemini-") else "gemini-2.5-flash"
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    config_kwargs = {"generation_config": generation_config} if generation_config is not None else {}
    if provider == "gemini":
        if not gemini_key:
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
        return _provider_call("gemini", gemini_model, prompt, _call_gemini_api, api_key=gemini_key, timeout=timeout, **config_kwargs)
    if gemini_key:
        res = _provider_call("gemini", gemini_model, prompt, _call_gemini_api, api_key=gemini_key, timeout=timeout, **config_kwargs)
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
    if provider != "ollama" and (provider == "gemini" or os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")):
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

