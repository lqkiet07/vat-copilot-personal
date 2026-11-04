"""Text embedding generation and caching utilities."""
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.error import URLError
import urllib.request


@runtime_checkable
class BaseEmbedder(Protocol):
    """Protocol for text embedding models."""

    def embed_text(self, text: str) -> list[float]:
        ...


class DeterministicEmbedder:
    """Deterministic hash-based vector generator for local testing."""

    def __init__(self, dimension: int = 1024) -> None:
        self.dimension = dimension

    def embed_text(self, text: str) -> list[float]:
        """Generate normalized pseudo-embedding vector for text."""
        raw = hashlib.shake_256(text.encode("utf-8")).digest(self.dimension * 2)
        values = [
            int.from_bytes(raw[i * 2 : (i + 1) * 2], byteorder="little", signed=True)
            for i in range(self.dimension)
        ]
        norm = math.sqrt(sum(v * v for v in values))
        if norm == 0.0:
            return [1.0] + [0.0] * (self.dimension - 1)
        return [v / norm for v in values]


class PrecomputedEmbedder:
    """Lookup precomputed embeddings from JSON cache with fallback."""

    def __init__(
        self,
        cache_path: str | Path,
        dimension: int = 1024,
        fallback: BaseEmbedder | None = None,
    ) -> None:
        self.cache_path = Path(cache_path)
        self.dimension = dimension
        self.fallback = fallback or DeterministicEmbedder(dimension=dimension)
        self._cache: dict[str, list[float]] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        if not self.cache_path.exists():
            return
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    self._cache = data
        except (json.JSONDecodeError, OSError):
            self._cache = {}

    def embed_text(self, text: str) -> list[float]:
        """Return cached embedding or fall back to embedder."""
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        return self.fallback.embed_text(text)


def _normalize_vec(vec: list[float], target_dim: int) -> list[float]:
    """Ensure vector has target_dim and L2 norm is 1.0."""
    if len(vec) < target_dim:
        vec = vec + [0.0] * (target_dim - len(vec))
    elif len(vec) > target_dim:
        vec = vec[:target_dim]
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return [1.0] + [0.0] * (target_dim - 1)
    return [x / norm for x in vec]


class OllamaEmbedder:
    """Semantic vector generator using local Ollama embedding models."""

    def __init__(
        self,
        model: str = "bge-m3",
        host: str = "http://localhost:11434",
        dimension: int = 1024,
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.dimension = dimension
        self.timeout = timeout

    def embed_text(self, text: str) -> list[float]:
        """Generate normalized semantic embedding vector for a single string."""
        vectors = self.embed_texts([text])
        if not vectors or len(vectors) == 0:
            raise RuntimeError(f"Ollama failed to produce embedding for text using model {self.model}")
        return vectors[0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate normalized semantic embeddings for a batch of strings."""
        if not texts:
            return []

        url = f"{self.host}/api/embed"
        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                embeddings = data.get("embeddings", [])
                if not embeddings:
                    raise RuntimeError(f"Ollama returned empty embeddings for model {self.model}")
                return [_normalize_vec(v, self.dimension) for v in embeddings]
        except Exception as e:
            # Fallback to legacy /api/embeddings single-prompt endpoint
            try:
                results: list[list[float]] = []
                for t in texts:
                    single_url = f"{self.host}/api/embeddings"
                    single_payload = json.dumps({"model": self.model, "prompt": t}).encode("utf-8")
                    single_req = urllib.request.Request(
                        single_url,
                        data=single_payload,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(single_req, timeout=self.timeout) as s_resp:
                        s_data = json.loads(s_resp.read().decode("utf-8"))
                        raw = s_data.get("embedding", [])
                        if not raw:
                            raise RuntimeError(f"Ollama legacy embedding empty for model {self.model}")
                        results.append(_normalize_vec(raw, self.dimension))
                return results
            except Exception as legacy_err:
                raise RuntimeError(
                    f"Ollama embedding failed for model {self.model}: {e}; legacy fallback failed: {legacy_err}"
                ) from e


def check_ollama_embed_available(
    host: str = "http://localhost:11434",
    model: str = "bge-m3",
    timeout: float = 2.0,
) -> bool:
    """Check if local Ollama daemon is reachable and has the requested embedding model."""
    url = f"{host.rstrip('/')}/api/tags"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False
            data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name", "") for m in data.get("models", [])]
            return any(model in m for m in models)
    except (URLError, OSError, TimeoutError, json.JSONDecodeError):
        return False


def get_embedder(
    cache_path: str | Path | None = None,
    dimension: int = 1024,
    provider: str | None = None,
) -> BaseEmbedder:
    """Return an embedder instance according to active provider configuration."""
    active_provider = provider or os.getenv("EMBEDDING_PROVIDER", "deterministic")

    if active_provider == "ollama":
        return OllamaEmbedder(dimension=dimension)
    if active_provider == "auto":
        if check_ollama_embed_available():
            return OllamaEmbedder(dimension=dimension)
        return DeterministicEmbedder(dimension=dimension)
    if cache_path:
        return PrecomputedEmbedder(cache_path=cache_path, dimension=dimension)
    return DeterministicEmbedder(dimension=dimension)
