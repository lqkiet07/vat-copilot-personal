from collections.abc import Iterator
import json
from json import JSONDecodeError
from urllib.error import URLError
import urllib.request


def check_ollama_available(
    host: str = "http://localhost:11434",
    timeout: float = 2.0,
) -> bool:
    """Check if the Ollama service is reachable."""
    req = urllib.request.Request(host, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status == 200
    except (URLError, OSError, TimeoutError):
        return False


def generate_llm_response(
    prompt: str,
    model: str = "qwen2.5:3b",
    host: str = "http://localhost:11434",
    timeout: float = 30.0,
) -> str | None:
    """Generate a response from Ollama API using the specified prompt and model."""
    url = f"{host}/api/generate"
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }
    ).encode("utf-8")
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


def generate_llm_response_stream(
    prompt: str,
    model: str = "qwen2.5:7b",
    host: str = "http://localhost:11434",
    timeout: float = 120.0,
) -> Iterator[str]:
    """Stream response chunks from Ollama, yielding text fragments as they arrive.

    Ollama answers a streaming request with NDJSON: one JSON object per line. Any
    transport or decoding failure ends the stream silently so callers can fall back
    to a deterministic answer instead of surfacing a 500.
    """
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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            for raw_line in response:
                chunk = _parse_stream_chunk(raw_line)
                if chunk is None:
                    continue
                token = chunk.get("response")
                if token:
                    yield str(token)
                if chunk.get("done"):
                    return
    except (URLError, OSError, TimeoutError, JSONDecodeError):
        return


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
