import math
from pathlib import Path
import hashlib
import json
from unittest.mock import Mock

import scripts.generate_embeddings as generation
from src.engine.embedding_meta import validate_embedding_meta
import scripts.seed_neo4j as seeding
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
        "article_embeddings.staging.json", "embedding_meta.staging.json", "vat_corpus.staging.json",
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
    for name in ("reset_neo4j", "init_schema", "import_corpus_to_neo4j", "import_amends_relations",
                 "import_cites_relations", "import_guides_relations", "import_article_embeddings"):
        monkeypatch.setattr(seeding, name, Mock(side_effect=AssertionError("DB forbidden")))
    monkeypatch.setattr("src.graph.connection.get_neo4j_driver", Mock(side_effect=AssertionError("Driver forbidden")))


@pytest.fixture
def seed_artifacts(tmp_path, monkeypatch):
    corpus = tmp_path / "vat_corpus.staging.json"
    cache = tmp_path / "article_embeddings.staging.json"
    meta = tmp_path / "embedding_meta.staging.json"
    articles = [{"id": f"A{i}", "code": str(i), "content": "text", "valid_from": "2025-01-01"}
                for i in range(494)]
    corpus.write_text(json.dumps([{"id": "D", "valid_from": "2025-01-01", "articles": articles}]), encoding="utf-8")
    vectors = {art["id"]: [0.0] * i + [0.6, 0.8] + [0.0] * (1022 - i)
               for i, art in enumerate(articles)}
    cache.write_text(json.dumps(vectors), encoding="utf-8")
    meta.write_text(json.dumps({"provider": "ollama", "model": "bge-m3", "dimension": 1024,
                                "count": 494, "corpus_sha256": hashlib.sha256(corpus.read_bytes()).hexdigest(),
                                "cache_sha256": hashlib.sha256(cache.read_bytes()).hexdigest()}), encoding="utf-8")
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:17687")
    return corpus, cache, meta


def test_seed_dry_run_validates_and_plans_all_phases_without_driver(seed_artifacts):
    corpus, cache, meta = seed_artifacts
    stats = seeding.seed(True, True, corpus, cache, meta_path=meta)
    assert stats["articles"] == stats["embeddings"] == 494
    assert stats["guides"] == stats["amends"] == stats["cites"] == 0


@pytest.mark.parametrize("fault", ["missing", "malformed", "provider", "model", "dimension", "count",
                                  "corpus_sha256", "cache_sha256", "ids", "duplicate", "invalid_id",
                                  "short", "fallback", "nan", "wrong_dimension", "float_count", "float_dimension"])
def test_seed_rejects_bad_staging_before_any_mutation(seed_artifacts, fault):
    corpus, cache, meta = seed_artifacts
    metadata = json.loads(meta.read_text(encoding="utf-8"))
    if fault == "missing":
        meta.unlink()
    elif fault == "malformed":
        meta.write_text("[]", encoding="utf-8")
    elif fault.startswith("float_"):
        field = fault.removeprefix("float_")
        metadata[field] = float(metadata[field])
        meta.write_text(json.dumps(metadata), encoding="utf-8")
    elif fault in metadata:
        metadata[fault] = "bad"
        meta.write_text(json.dumps(metadata), encoding="utf-8")
    else:
        data = json.loads(corpus.read_text(encoding="utf-8"))
        vectors = json.loads(cache.read_text(encoding="utf-8"))
        if fault == "ids":
            vectors["OTHER"] = vectors.pop("A0")
        elif fault in {"duplicate", "invalid_id"}:
            data[0]["articles"][0]["id"] = "A1" if fault == "duplicate" else ""
        elif fault == "short":
            data[0]["articles"].pop()
            vectors.pop("A493")
            metadata["count"] = 493
        else:
            vectors["A0"] = {"fallback": [1.0] + [0.0] * 1023,
                             "nan": [float("nan")] * 1024, "wrong_dimension": [0.0]}[fault]
        corpus.write_text(json.dumps(data), encoding="utf-8")
        cache.write_text(json.dumps(vectors), encoding="utf-8")
        metadata.update(corpus_sha256=hashlib.sha256(corpus.read_bytes()).hexdigest(),
                        cache_sha256=hashlib.sha256(cache.read_bytes()).hexdigest())
        meta.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError), match="[Vv]alid|[Mm]etadata|494|ID|embedding"):
        seeding.seed(True, False, corpus, cache, meta_path=meta)


@pytest.mark.parametrize("uri", ["bolt://localhost:7687", "bolt://remote:17687", "neo4j://localhost:17687", "bad"])
@pytest.mark.parametrize("staging", [True, False])
def test_seed_rejects_unsafe_target_before_reset(seed_artifacts, monkeypatch, uri, staging):
    corpus, cache, meta = seed_artifacts
    if not staging:
        corpus, cache, meta = seeding.CORPUS_PATH, seeding.EMBEDDINGS_PATH, None
    monkeypatch.setenv("NEO4J_URI", uri)
    with pytest.raises(ValueError, match="17687"):
        seeding.seed(True, False, corpus, cache, meta_path=meta)


@pytest.mark.parametrize("target", ["cache", "meta", "implicit_meta", "same"])
def test_seed_rejects_canonical_or_implicit_staging_pair(seed_artifacts, target):
    corpus, cache, meta = seed_artifacts
    if target == "cache":
        cache = seeding.EMBEDDINGS_PATH
    elif target == "meta":
        meta = seeding.ROOT / "data/processed/embedding_meta.json"
    elif target == "implicit_meta":
        meta = None
    else:
        meta = cache
    with pytest.raises(ValueError, match="path|canonical|explicit"):
        seeding.seed(True, False, corpus, cache, meta_path=meta)


def test_seed_imports_selected_artifacts_in_order(seed_artifacts, monkeypatch):
    corpus, cache, meta = seed_artifacts
    calls = []
    results = {"reset_neo4j": None, "init_schema": None, "import_corpus_to_neo4j": {"articles": 494},
               "import_amends_relations": 2, "import_cites_relations": 3,
               "import_guides_relations": 4, "import_article_embeddings": 494}
    for name, result in results.items():
        def record(*args, _name=name, _result=result, **kwargs):
            calls.append((_name, args, kwargs))
            return _result
        monkeypatch.setattr(seeding, name, record)
    stats = seeding.seed(True, False, corpus, cache, meta_path=meta)
    assert [name for name, _, _ in calls] == ["reset_neo4j", "init_schema", "import_corpus_to_neo4j",
                                             "import_amends_relations", "import_guides_relations",
                                             "import_cites_relations", "import_article_embeddings"]
    assert calls[0][2] == {"confirm": True}
    assert all(args == (str(corpus),) for _, args, _ in calls[2:4] + calls[5:6])
    assert calls[-1][1] == (json.loads(cache.read_text(encoding="utf-8")),)
    assert stats == {"articles": 494, "amends": 2, "cites": 3, "guides": 4, "embeddings": 494}


def test_seed_cli_explicit_dry_run_from_tmp(seed_artifacts, tmp_path):
    import subprocess
    import sys

    corpus, cache, meta = seed_artifacts
    result = subprocess.run([sys.executable, str(Path(seeding.__file__).resolve()), "--dry-run", "--reset",
                             "--corpus", str(corpus), "--embeddings", str(cache), "--meta", str(meta)],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "'embeddings': 494" in result.stdout
    assert "'guides': 0" in result.stdout


def test_seed_explicit_metadata_never_falls_back_outside_staging(seed_artifacts):
    corpus, cache, meta = seed_artifacts
    ordinary = [path.with_name(path.name.replace(".staging", "")) for path in (corpus, cache, meta)]
    for source, destination in zip((corpus, cache), ordinary):
        destination.write_bytes(source.read_bytes())
    with pytest.raises(ValueError, match="metadata"):
        seeding.seed(False, True, ordinary[0], ordinary[1], meta_path=ordinary[2])


def test_seed_legacy_dry_run_keeps_canonical_defaults_without_metadata(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    planner = Mock(return_value={"stats": {"articles": 76}, "resolved_amends": [],
                                "unresolved_amends": [], "cites": []})
    monkeypatch.setattr(seeding, "plan_corpus_import", planner)
    assert seeding.seed(dry_run=True) == {"articles": 76, "amends": 0, "unresolved_amends": 0, "cites": 0}
    planner.assert_called_once_with(str(seeding.CORPUS_PATH))


def test_seed_reports_partial_vector_import(seed_artifacts, monkeypatch):
    corpus, cache, meta = seed_artifacts
    for name in ("init_schema", "import_amends_relations", "import_cites_relations", "import_guides_relations"):
        monkeypatch.setattr(seeding, name, Mock(return_value=0))
    monkeypatch.setattr(seeding, "import_corpus_to_neo4j", Mock(return_value={"articles": 494}))
    monkeypatch.setattr(seeding, "import_article_embeddings", Mock(return_value=493))
    with pytest.raises(RuntimeError, match="imported 493 articles, expected 494"):
        seeding.seed(False, False, corpus, cache, meta_path=meta)


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

