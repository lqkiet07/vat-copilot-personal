"""Metadata tracking for vector embeddings to ensure query/index space consistency."""
import hashlib
import json
import os
import tempfile
import math
from pathlib import Path
from typing import Any

from . import embedder
from .embedder import BaseEmbedder, DeterministicEmbedder, get_embedder

DEFAULT_META_PATH = Path("data/processed/embedding_meta.json")
DEFAULT_CACHE_PATH = Path("data/processed/article_embeddings.json")


def validate_embeddings(embeddings: dict[str, list[float]], dimension: int = 1024) -> list[str]:
    """Validate dimensionality, unit norm, non-fallback, and non-trivial distribution."""
    errors = []
    if not isinstance(embeddings, dict) or not embeddings:
        return ["Embeddings dictionary is empty or invalid"]

    items = list(embeddings.items())
    for art_id, vec in items:
        if not isinstance(art_id, str) or not art_id.strip():
            errors.append("Invalid article ID")
            break
        if not isinstance(vec, list) or len(vec) != dimension:
            errors.append(f"Vector for '{art_id}' has invalid dimension, expected {dimension}")
            break
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in vec):
            errors.append(f"Vector for '{art_id}' must contain finite numeric values")
            break
        norm = math.sqrt(sum(x * x for x in vec))
        if not (0.95 <= norm <= 1.05):
            errors.append(f"Vector for '{art_id}' is not normalized: L2 norm = {norm:.4f}")
            break
        if vec[0] == 1.0 and sum(abs(x) for x in vec[1:]) == 0.0:
            errors.append(f"Vector for '{art_id}' is a constant fallback [1.0, 0.0, ...]")
            break

    if len(items) >= 2 and not errors:
        v1, v2 = items[0][1], items[1][1]
        dot = sum(a * b for a, b in zip(v1, v2))
        if math.isclose(dot, 1.0, rel_tol=1e-5):
            errors.append("Vectors across distinct articles are identical (variance is zero)")

    return errors


def validate_embedding_meta(meta_dict: dict[str, Any], embeddings: dict[str, list[float]]) -> list[str]:
    """Validate consistency between metadata and embedding dictionary."""
    errors = []
    count = meta_dict.get("count")
    if count is not None and count != len(embeddings):
        errors.append(f"Metadata count ({count}) does not match embeddings length ({len(embeddings)})")
    dim = meta_dict.get("dimension")
    if dim is not None:
        for vector in embeddings.values():
            if not isinstance(vector, list) or len(vector) != dim:
                errors.append(f"Metadata dimension ({dim}) != vector dimension")
                break
    return errors


def load_embedding_meta(path: str | Path = DEFAULT_META_PATH) -> dict[str, Any] | None:
    """Load metadata dictionary describing the active Neo4j vector index."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=p.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, p)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_embedding_meta(meta_dict: dict[str, Any], path: str | Path = DEFAULT_META_PATH) -> None:
    """Save metadata describing the active Neo4j vector index."""
    atomic_write_bytes(path, json.dumps(meta_dict, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"))


def _is_cache_fallback(cache_path: str | Path) -> bool:
    """Check if first cached vector is a fallback [1.0, 0.0, ...]."""
    p = Path(cache_path)
    if not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data:
            v = next(iter(data.values()))
            if isinstance(v, list) and len(v) > 1 and v[0] == 1.0 and sum(abs(x) for x in v[1:]) == 0.0:
                return True
    except Exception:
        pass
    return False


def _valid_artifact_pair(meta, cache_path, corpus_path, strict):
    try:
        cache_bytes = Path(cache_path).read_bytes()
        cache = json.loads(cache_bytes)
        if not isinstance(cache, dict):
            return False
        if validate_embedding_meta(meta, cache) or validate_embeddings(cache):
            return False
        if strict and (meta.get("provider"), meta.get("model"), meta.get("dimension")) != ("ollama", "bge-m3", 1024):
            return False
        if (strict or "cache_sha256" in meta) and meta.get("cache_sha256") != hashlib.sha256(cache_bytes).hexdigest():
            return False
        if strict and (type(meta.get("count")) is not int or not isinstance(meta.get("corpus_sha256"), str)
                       or len(meta["corpus_sha256"]) != 64):
            return False
        if corpus_path is not None:
            corpus_bytes = Path(corpus_path).read_bytes()
            if meta.get("corpus_sha256") != hashlib.sha256(corpus_bytes).hexdigest():
                return False
            ids = [article["id"] for doc in json.loads(corpus_bytes) for article in doc["articles"]]
            if len(ids) != len(set(ids)) or set(ids) != set(cache):
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError):
        return False


def resolve_embedder_for_index(
    meta_path: str | Path = DEFAULT_META_PATH,
    cache_path: str | Path = DEFAULT_CACHE_PATH,
    *,
    strict: bool = False,
    corpus_path: str | Path | None = None,
) -> BaseEmbedder | None:
    """Resolve embedder matching the stored index vector space.

    Returns None if the index requires an embedder (e.g. Ollama bge-m3) that is
    currently unreachable or if cached vectors are corrupted fallbacks, preventing
    corrupting similarity results with mismatched vectors.
    """
    if _is_cache_fallback(cache_path):
        return None

    meta = load_embedding_meta(meta_path)
    if strict or (meta and "cache_sha256" in meta):
        if meta is None or not _valid_artifact_pair(meta, cache_path, corpus_path, strict):
            return None
    if meta is None or meta.get("provider") == "deterministic":
        return get_embedder(provider="deterministic")

    provider = meta.get("provider", "ollama")
    model = meta.get("model", "bge-m3")

    if provider == "ollama":
        if embedder.check_ollama_embed_available(model=model):
            return get_embedder(provider="ollama")
        return None

    return get_embedder(provider="deterministic")
