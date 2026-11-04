import math
from pathlib import Path
import pytest

from src.engine.embedder import OllamaEmbedder
from src.engine.embedding_meta import validate_embeddings, resolve_embedder_for_index


def test_validate_embeddings_empty():
    errors = validate_embeddings({})
    assert any("empty" in e.lower() for e in errors)


def test_validate_embeddings_wrong_dimension():
    bad_dim = {"art_1": [1.0, 0.0, 0.0]}
    errors = validate_embeddings(bad_dim, dimension=1024)
    assert any("dimension" in e.lower() for e in errors)


def test_validate_embeddings_rejects_fallback_constant():
    fallback_vec = [1.0] + [0.0] * 1023
    data = {
        "art_1": fallback_vec,
        "art_2": fallback_vec,
    }
    errors = validate_embeddings(data, dimension=1024)
    assert any("fallback" in e.lower() or "constant" in e.lower() for e in errors)


def test_validate_embeddings_rejects_identical_vectors():
    vec = [1.0 / math.sqrt(1024)] * 1024
    data = {
        "art_1": list(vec),
        "art_2": list(vec),
    }
    errors = validate_embeddings(data, dimension=1024)
    assert any("identical" in e.lower() for e in errors)


def test_validate_embeddings_accepts_valid_distinct_vectors():
    vec1 = [0.5 / math.sqrt(512)] * 512 + [0.5 / math.sqrt(512)] * 512
    vec2 = [0.5 / math.sqrt(512)] * 512 + [-0.5 / math.sqrt(512)] * 512
    # Normalize to unit length
    n1 = math.sqrt(sum(x*x for x in vec1))
    n2 = math.sqrt(sum(x*x for x in vec2))
    data = {
        "art_1": [x / n1 for x in vec1],
        "art_2": [x / n2 for x in vec2],
    }
    errors = validate_embeddings(data, dimension=1024)
    assert errors == []


def test_ollama_embedder_fails_loud_on_error(monkeypatch):
    embedder = OllamaEmbedder(model="bge-m3", host="http://localhost:99999")
    with pytest.raises(RuntimeError):
        embedder.embed_text("văn bản kiểm tra")


def test_resolve_embedder_for_index_rejects_fallback_cache(tmp_path: Path):
    import json
    meta_path = tmp_path / "meta.json"
    cache_path = tmp_path / "cache.json"

    meta_path.write_text(json.dumps({"provider": "ollama", "model": "bge-m3", "dimension": 1024}))
    # Cache filled with fallback vector
    cache_path.write_text(json.dumps({"art_1": [1.0] + [0.0] * 1023}))

    embedder = resolve_embedder_for_index(meta_path=meta_path, cache_path=cache_path)
    # Must degrade to None (fulltext only) when cache has fallback vectors
    assert embedder is None


def test_generate_refuses_deterministic_overwrite_of_semantic_index(tmp_path):
    import json
    from scripts.generate_embeddings import generate_and_import_embeddings

    meta = tmp_path / "embedding_meta.json"
    meta.write_text(json.dumps({"provider": "ollama", "model": "bge-m3", "dimension": 1024, "count": 1}), encoding="utf-8")
    cache = tmp_path / "article_embeddings.json"
    cache.write_text(json.dumps({"X_D1": [0.0] * 1023 + [1.0]}), encoding="utf-8")
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"id": "X", "articles": [{"id": "X_D1", "title": "t", "lead": "", "clauses": []}]}]), encoding="utf-8")

    with pytest.raises(RuntimeError, match="downgrade"):
        generate_and_import_embeddings(
            corpus_path=corpus, output_path=cache, provider="deterministic",
            meta_path=meta, import_fn=lambda emb: len(emb),
        )


def test_generate_allows_downgrade_when_explicitly_permitted(tmp_path):
    import json
    from scripts.generate_embeddings import generate_and_import_embeddings
    meta = tmp_path / "embedding_meta.json"
    meta.write_text(json.dumps({"provider": "ollama", "dimension": 1024}), encoding="utf-8")
    cache = tmp_path / "article_embeddings.json"
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps([{"id": "X", "articles": [{"id": "X_D1", "title": "t", "lead": "", "clauses": []}]}]), encoding="utf-8")

    written, imported = generate_and_import_embeddings(
        corpus_path=corpus, output_path=cache, provider="deterministic",
        meta_path=meta, allow_downgrade=True, import_fn=lambda emb: len(emb),
    )
    assert written == 1 and imported == 1

