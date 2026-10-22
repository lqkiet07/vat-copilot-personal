"""Text embedding generation and caching utilities."""
import hashlib
import json
import math
from pathlib import Path
from typing import Protocol, runtime_checkable


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


def get_embedder(
    cache_path: str | Path | None = None, dimension: int = 1024
) -> BaseEmbedder:
    """Return precomputed or deterministic embedder instance."""
    if cache_path:
        return PrecomputedEmbedder(cache_path=cache_path, dimension=dimension)
    return DeterministicEmbedder(dimension=dimension)
