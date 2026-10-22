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
