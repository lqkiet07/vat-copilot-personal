import math
from pathlib import Path
import hashlib
import json
from unittest.mock import Mock

import scripts.generate_embeddings as generation
from src.engine.embedding_meta import validate_embedding_meta
import pytest

from src.engine.embedder import OllamaEmbedder
from src.engine.embedding_meta import validate_embeddings, resolve_embedder_for_index


@pytest.fixture(autouse=True)
def isolated_embedding_files(tmp_path, monkeypatch):
    import builtins
    import io
    import os

    root = Path(__file__).resolve().parents[1]
    protected = {(root / "data/processed" / name).resolve() for name in (
        "article_embeddings.json", "embedding_meta.json", "vat_corpus.json",
    )}
    monkeypatch.chdir(tmp_path)

    def guarded_open(original):
        def open_file(file, mode="r", *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)) and any(flag in mode for flag in "wax+"):
                assert Path(file).resolve() not in protected, "Canonical write forbidden"
            return original(file, mode, *args, **kwargs)
        return open_file

    replace = os.replace

    def guarded_replace(source, destination):
        assert Path(destination).resolve() not in protected, "Canonical replacement forbidden"
        return replace(source, destination)

    monkeypatch.setattr(builtins, "open", guarded_open(builtins.open))
    monkeypatch.setattr(io, "open", guarded_open(io.open))
    monkeypatch.setattr(os, "replace", guarded_replace)
    monkeypatch.setattr(generation, "init_schema", Mock(side_effect=AssertionError("DB forbidden")))
    monkeypatch.setattr(generation, "import_article_embeddings", Mock(side_effect=AssertionError("DB forbidden")))


def test_canonical_write_guard_blocks_absolute_paths():
    root = Path(__file__).resolve().parents[1]
    for name in ("article_embeddings.json", "embedding_meta.json", "vat_corpus.json"):
        with pytest.raises(AssertionError, match="Canonical write forbidden"):
            (root / "data/processed" / name).write_text("forbidden", encoding="utf-8")


def test_cli_subprocess_rejects_implicit_staging_paths(tmp_path):
    import subprocess
    import sys

    script = Path(generation.__file__).resolve()
    result = subprocess.run(
        [sys.executable, str(script), "--corpus", str(tmp_path / "vat_corpus.staging.json"),
         "--provider", "ollama", "--no-import"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "explicit --corpus --output --meta" in result.stderr
    assert not list(tmp_path.iterdir())


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
    monkeypatch.setattr("urllib.request.urlopen", Mock(side_effect=OSError("offline")))
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


@pytest.fixture
def staging(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    corpus = tmp_path / "vat_corpus.staging.json"
    corpus.write_text(json.dumps([{"articles": [
        {"id": "A", "title": "title", "lead": "lead", "clauses": [
            {"content": "clause", "points": [{"content": "point evidence"}]}]},
        {"id": "B", "title": "other", "clauses": []},
    ]}]), encoding="utf-8")
    client = OllamaEmbedder()
    client.embed_texts = Mock(return_value=[
        [1 / 32] * 1024, [1 / 32] * 512 + [-1 / 32] * 512,
    ])
    monkeypatch.setattr(generation, "get_embedder", Mock(return_value=client))
    monkeypatch.setattr(generation, "init_schema", Mock(side_effect=AssertionError("DB forbidden")))
    monkeypatch.setattr(generation, "import_article_embeddings", Mock(side_effect=AssertionError("DB forbidden")))
    return corpus, tmp_path / "vectors.staging.json", tmp_path / "meta.staging.json", client


def test_staging_no_import_records_hashes_ids_and_points(staging):
    corpus, output, meta, client = staging
    importer = Mock(side_effect=AssertionError("import forbidden"))
    assert generation.generate_and_import_embeddings(
        corpus, output, "ollama", meta, import_fn=importer, no_import=True,
    ) == (2, 0)
    importer.assert_not_called()
    assert "point evidence" in client.embed_texts.call_args.args[0][0]
    data = json.loads(output.read_text(encoding="utf-8"))
    metadata = json.loads(meta.read_text(encoding="utf-8"))
    assert set(data) == {"A", "B"}
    assert metadata["corpus_sha256"] == hashlib.sha256(corpus.read_bytes()).hexdigest()
    assert metadata["cache_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert (metadata["provider"], metadata["model"], metadata["dimension"], metadata["count"]) == (
        "ollama", "bge-m3", 1024, 2,
    )


@pytest.mark.parametrize("provider", ["auto", "deterministic"])
def test_staging_rejects_nonsemantic_provider_even_with_downgrade(staging, provider):
    corpus, output, meta, client = staging
    with pytest.raises((ValueError, RuntimeError), match="ollama"):
        generation.generate_and_import_embeddings(corpus, output, provider, meta, True)
    client.embed_texts.assert_not_called()
    assert not output.exists()


@pytest.mark.parametrize("target", ["output", "meta", "missing_meta", "same", "source"])
def test_staging_rejects_unsafe_paths_before_provider(staging, target):
    corpus, output, meta, client = staging
    if target == "output":
        output = generation.OUTPUT_PATH
    elif target == "meta":
        meta = Path("data/processed/embedding_meta.json")
    elif target == "missing_meta":
        meta = None
    elif target == "same":
        meta = output
    else:
        output = corpus
    with pytest.raises((ValueError, RuntimeError), match="path|explicit|canonical"):
        generation.generate_and_import_embeddings(corpus, output, "ollama", meta)
    client.embed_texts.assert_not_called()


@pytest.mark.parametrize("destination", ["output", "meta"])
@pytest.mark.parametrize("location", ["cwd", "source_root"])
def test_cli_rejects_canonical_corpus_destination(staging, tmp_path, monkeypatch, destination, location):
    corpus, output, meta, client = staging
    source_root = tmp_path / "source"
    monkeypatch.setattr(generation, "__file__", str(source_root / "scripts/generate_embeddings.py"))
    root = tmp_path if location == "cwd" else source_root
    canonical = root / "data/processed/vat_corpus.json"
    assert canonical.is_relative_to(tmp_path)
    canonical.parent.mkdir(parents=True)
    original = b'[{"canonical": "preserve"}]'
    canonical.write_bytes(original)
    if destination == "output":
        output = canonical
    else:
        meta = canonical
    monkeypatch.setattr("sys.argv", ["generate_embeddings.py", "--corpus", str(corpus),
                                    "--output", str(output), "--meta", str(meta),
                                    "--provider", "ollama", "--no-import"])
    with pytest.raises(ValueError, match="non-canonical"):
        generation.main()
    client.embed_texts.assert_not_called()
    assert canonical.read_bytes() == original


@pytest.mark.parametrize("ids", [["A", "A"], ["A", ""], ["A", None], ["A", 3]])
def test_staging_rejects_invalid_article_ids(staging, ids):
    corpus, output, meta, client = staging
    corpus.write_text(json.dumps([{"articles": [{"id": item, "title": "text"} for item in ids]}]))
    with pytest.raises((ValueError, RuntimeError), match="ID|id"):
        generation.generate_and_import_embeddings(corpus, output, "ollama", meta)
    client.embed_texts.assert_not_called()
    assert not output.exists()


@pytest.mark.parametrize("count", [0, 1, 3])
def test_staging_rejects_response_count_before_writing(staging, count):
    corpus, output, meta, client = staging
    client.embed_texts.return_value = [[1 / 32] * 1024] * count
    with pytest.raises(RuntimeError, match="count"):
        generation.generate_and_import_embeddings(corpus, output, "ollama", meta)
    assert not output.exists() and not meta.exists()


@pytest.mark.parametrize("model,dimension", [("other", 1024), ("bge-m3", 768)])
def test_staging_rejects_wrong_model_space(staging, model, dimension):
    corpus, output, meta, client = staging
    client.model, client.dimension = model, dimension
    with pytest.raises((ValueError, RuntimeError), match="bge-m3|1024"):
        generation.generate_and_import_embeddings(corpus, output, "ollama", meta)
    assert not output.exists()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "bad", True])
def test_validation_rejects_nonfinite_or_nonnumeric_values(value):
    vector = [1 / 32] * 1024
    vector[8] = value
    assert validate_embeddings({"A": vector})


def test_metadata_validates_every_vector_dimension():
    errors = validate_embedding_meta({"count": 2, "dimension": 1024}, {"A": [0.0] * 1024, "B": [0.0]})
    assert any("dimension" in error for error in errors)


@pytest.mark.parametrize("mode", ["missing", "malformed", "wrong_model", "wrong_dim", "wrong_provider", "count", "hash", "corpus_hash", "valid"])
def test_strict_runtime_resolves_only_matching_staging_pair(staging, monkeypatch, mode):
    corpus, output, meta, client = staging
    output.write_text(json.dumps({"A": [1 / 32] * 1024, "B": [1 / 32] * 512 + [-1 / 32] * 512}))
    metadata = {"provider": "ollama", "model": "bge-m3", "dimension": 1024, "count": 2,
                "cache_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "corpus_sha256": hashlib.sha256(corpus.read_bytes()).hexdigest()}
    changes = {"wrong_model": ("model", "other"), "wrong_dim": ("dimension", 768),
               "wrong_provider": ("provider", "auto"), "count": ("count", 1),
               "hash": ("cache_sha256", "0" * 64), "corpus_hash": ("corpus_sha256", "0" * 64)}
    if mode in changes:
        key, value = changes[mode]
        metadata[key] = value
    if mode != "missing":
        meta.write_text("{" if mode == "malformed" else json.dumps(metadata))
    availability = Mock(return_value=True)
    monkeypatch.setattr("src.engine.embedder.check_ollama_embed_available", availability)
    result = resolve_embedder_for_index(meta, output, strict=True, corpus_path=corpus)
    if mode == "valid":
        assert isinstance(result, OllamaEmbedder)
    else:
        assert result is None
        availability.assert_not_called()


def test_cli_explicit_staging_no_import(staging, monkeypatch):
    corpus, output, meta, client = staging
    monkeypatch.setattr("sys.argv", ["generate_embeddings.py", "--corpus", str(corpus),
                                    "--output", str(output), "--meta", str(meta),
                                    "--provider", "ollama", "--no-import"])
    assert generation.main() == (2, 0)


def test_staging_pair_detects_interrupted_metadata_replace(staging, monkeypatch):
    corpus, output, meta, client = staging
    generation.generate_and_import_embeddings(corpus, output, "ollama", meta, no_import=True)
    previous_meta = meta.read_bytes()
    client.embed_texts.return_value[0] = [-1 / 32] * 1024
    replace = generation.os.replace

    def fail_meta(source, destination):
        if Path(destination) == meta:
            raise OSError("interrupted metadata replacement")
        return replace(source, destination)

    monkeypatch.setattr(generation.os, "replace", fail_meta)
    with pytest.raises(OSError, match="interrupted"):
        generation.generate_and_import_embeddings(corpus, output, "ollama", meta, no_import=True)
    assert meta.read_bytes() == previous_meta
    assert resolve_embedder_for_index(meta, output, strict=True, corpus_path=corpus) is None
    assert sorted(path.name for path in output.parent.iterdir()) == sorted([corpus.name, output.name, meta.name])

