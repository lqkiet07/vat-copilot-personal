import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src import config


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def staging(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus.json"
    cache = tmp_path / "vectors.json"
    meta = tmp_path / "meta.json"
    corpus.write_text(json.dumps([{
        "id": "LUAT_999_2026", "code": "99/2026", "title": "Staging only",
        "valid_from": "2026-01-01", "valid_to": "9999-12-31",
        "articles": [{"id": "LUAT_999_2026_D1", "code": "Điều 1", "title": "Staging article",
                      "clauses": [{"id": "LUAT_999_2026_D1_K1", "content": "staging wording"}]}],
    }]), encoding="utf-8")
    cache.write_text(json.dumps({"LUAT_999_2026_D1": [0.03125] * 1024}), encoding="utf-8")
    meta.write_text(json.dumps({
        "provider": "ollama", "model": "bge-m3", "dimension": 1024, "count": 1,
        "corpus_sha256": digest(corpus), "cache_sha256": digest(cache),
    }), encoding="utf-8")
    monkeypatch.setenv("VAT_ENV", "staging")
    monkeypatch.setenv("VAT_CORPUS_PATH", str(corpus))
    monkeypatch.setenv("VAT_EMBEDDINGS_PATH", str(cache))
    monkeypatch.setenv("VAT_EMBEDDING_META_PATH", str(meta))
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:17687")
    return corpus, cache, meta


def test_runtime_defaults_remain_canonical(monkeypatch):
    for name in ("VAT_ENV", "VAT_CORPUS_PATH", "VAT_EMBEDDINGS_PATH", "VAT_EMBEDDING_META_PATH"):
        monkeypatch.delenv(name, raising=False)
    assert hasattr(config, "get_runtime_config"), "Task5 shared configuration is missing"
    selected = config.get_runtime_config()
    assert selected.environment == "canonical"
    assert str(selected.corpus_path).replace("\\", "/") == "data/processed/vat_corpus.json"
    assert str(selected.embeddings_path).replace("\\", "/") == "data/processed/article_embeddings.json"
    assert str(selected.embedding_meta_path).replace("\\", "/") == "data/processed/embedding_meta.json"


def test_startup_validates_and_exposes_safe_identities(staging, monkeypatch):
    from src.api.main import app
    from src.api import routes
    monkeypatch.setattr(routes, "get_neo4j_driver", lambda: SimpleNamespace(verify_connectivity=lambda: None))
    with TestClient(app) as client:
        data = client.get("/api/health").json()
        assert data.get("environment") == "staging"
        assert data["artifacts"] == {
            "corpus_sha256": digest(staging[0]), "cache_sha256": digest(staging[1]),
            "meta_sha256": digest(staging[2]), "provider": "ollama", "model": "bge-m3",
            "dimension": 1024, "count": 1,
        }
        assert str(staging[0].parent) not in json.dumps(data)
        assert client.get("/api/documents").json()[0]["id"] == "LUAT_999_2026"
        assert client.get("/api/documents/LUAT_999_2026").json()["id"] == "LUAT_999_2026"
        assert client.get("/api/documents/LUAT_48_2024").status_code == 404


@pytest.mark.parametrize("broken", ["uri_missing", "uri_canonical", "uri_remote", "corpus_missing", "cache_missing", "meta_missing", "corpus_hash", "cache_hash", "ids", "duplicate_ids", "provider", "model", "count", "dimension", "file_missing", "malformed_meta", "fallback_vector"])
def test_startup_refuses_invalid_staging(staging, monkeypatch, broken):
    from src.api.main import app
    corpus, cache, meta_path = staging
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if broken == "uri_missing":
        monkeypatch.delenv("NEO4J_URI")
    elif broken.startswith("uri_"):
        monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687" if broken == "uri_canonical" else "bolt://example.com:17687")
    elif broken == "file_missing":
        cache.unlink()
    elif broken == "malformed_meta":
        meta = []
    elif broken == "fallback_vector":
        cache.write_text(json.dumps({"LUAT_999_2026_D1": [1.0] + [0.0] * 1023}), encoding="utf-8")
        meta["cache_sha256"] = digest(cache)
    elif broken.endswith("_missing"):
        monkeypatch.delenv({"corpus_missing": "VAT_CORPUS_PATH", "cache_missing": "VAT_EMBEDDINGS_PATH", "meta_missing": "VAT_EMBEDDING_META_PATH"}[broken])
    elif broken in {"corpus_hash", "cache_hash"}:
        meta[{"corpus_hash": "corpus_sha256", "cache_hash": "cache_sha256"}[broken]] = "0" * 64
    elif broken in {"ids", "duplicate_ids"}:
        docs = json.loads(corpus.read_text(encoding="utf-8"))
        if broken == "ids":
            docs[0]["articles"][0]["id"] = "OTHER_D1"
        else:
            docs[0]["articles"].append(docs[0]["articles"][0])
        corpus.write_text(json.dumps(docs), encoding="utf-8")
        meta["corpus_sha256"] = digest(corpus)
    else:
        meta[broken] = {"provider": "deterministic", "model": "other", "count": 2, "dimension": 2}[broken]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="[Ss]taging"):
        with TestClient(app):
            pass


def test_chat_helpers_use_selected_corpus(staging):
    from src.api import chat_service
    assert set(chat_service._load_corpus_index()) == {"LUAT_999_2026_D1"}
    assert chat_service._load_documents()[0]["code"] == "99/2026"
    assert chat_service._explicit_article_ids("Điều 1") == ["LUAT_999_2026_D1"]
    assert "staging wording" in chat_service._build_prompt("Điều 1", "2026-03-20", [], ("LUAT_999_2026_D1",))


def test_demo_query_threads_selected_corpus(staging, monkeypatch):
    from src.ui import demo_service
    captured = {}
    def answer(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(to_dict=lambda: {})
    monkeypatch.setattr(demo_service, "answer_query", answer)
    demo_service.execute_demo_query("thuế", "2026-03-20")
    assert captured.get("corpus_path") == str(staging[0])


def test_query_chat_and_sse_keep_selected_corpus(staging, monkeypatch):
    from src.api.main import app
    from src.api import chat_service
    from src.ui import demo_service
    from src.engine.models import ArticleNode
    from src.verifier.models import CitationStatus, VerificationReport, VerificationResult
    article = ArticleNode(id="LUAT_999_2026_D1", doc_id="LUAT_999_2026", code="Điều 1", title="Thuế GTGT",
                          content="Thuế GTGT staging wording", valid_from="2026-01-01", valid_to="9999-12-31")
    for service in (chat_service, demo_service):
        monkeypatch.setattr(service, "query_temporal_rag", lambda *a, **k: SimpleNamespace(articles=[article]))
    paths = []
    def verify(text, date, *, corpus_path, **kwargs):
        paths.append(corpus_path)
        return VerificationReport(as_of_date=date, total_citations=1, valid_citations=1,
                                  citations=[CitationStatus(id="LUAT_999_2026_D1", status="VALID", exists=True,
                                                            is_active=True, message="")], is_clean=True)
    def score(text, date, *, corpus_path, **kwargs):
        report = verify(text, date, corpus_path=corpus_path)
        if "relevance_map" in kwargs:
            assert kwargs["relevance_map"].get("LUAT_999_2026_D1") is True
        return VerificationResult(as_of_date=date, risk_score=0, risk_level="LOW", warnings=[], report=report)
    monkeypatch.setattr("src.engine.retrieval_service.verify_citations", verify)
    monkeypatch.setattr("src.api.answer_service.verify_and_score", score)
    monkeypatch.setattr(chat_service, "verify_citations", verify)
    monkeypatch.setattr(chat_service, "verify_and_score", score)
    monkeypatch.setattr(chat_service, "generate_llm_response_stream", lambda *a, **k: iter(["Thuế GTGT [LUAT_999_2026_D1]"]))
    with TestClient(app) as client:
        query = client.post("/api/query", json={"query": "Thuế GTGT", "as_of_date": "2026-03-20"})
        assert query.status_code == 200, query.text
        assert query.json()["active_article_ids"] == ["LUAT_999_2026_D1"]
        payload = {"messages": [{"role": "user", "content": "Thuế GTGT"}], "as_of_date": "2026-03-20"}
        response = client.post("/api/chat", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["sources"], response.text
        assert response.json()["sources"][0]["id"] == "LUAT_999_2026_D1"
        streamed = client.post("/api/chat/stream", json=payload)
        assert "event: done" in streamed.text
        assert "LUAT_999_2026_D1" in streamed.text
        assert "LUAT_48_2024" not in streamed.text
        assert client.post("/api/query", json={"query": "thuế", "as_of_date": "2026-03-20", "pipeline": "P1"}).status_code == 400
    assert paths and set(paths) == {str(staging[0])}


def test_r5_review_path_is_explicit_and_validated_at_startup(staging, monkeypatch, tmp_path):
    from src.api.main import app
    monkeypatch.delenv("VAT_AMENDMENT_REVIEW_PATH", raising=False)
    assert config.selected_review_path(str(staging[0])) is None
    ledger = tmp_path / "review.json"
    ledger.write_text(json.dumps({
        "schema_version": 1, "oracle": "independent_raw_source_review", "review_complete": True,
        "pins": {"data/processed/vat_corpus.staging.json": "0" * 64}, "candidates": [],
    }), encoding="utf-8")
    monkeypatch.setenv("VAT_AMENDMENT_REVIEW_PATH", str(ledger))
    assert config.selected_review_path(str(staging[0])) == str(ledger)
    with pytest.raises(ValueError, match="corpus hash mismatch"):
        with TestClient(app):
            pass


def test_r5_startup_reports_only_review_fingerprint(staging, monkeypatch, tmp_path):
    ledger = tmp_path / "private-review.json"
    ledger.write_text(json.dumps({
        "schema_version": 1, "oracle": "independent_raw_source_review", "review_complete": True,
        "pins": {"data/processed/vat_corpus.staging.json": digest(staging[0])}, "candidates": [],
    }), encoding="utf-8")
    monkeypatch.setenv("VAT_AMENDMENT_REVIEW_PATH", str(ledger))
    identity = config.validate_runtime_artifacts(config.get_runtime_config())
    assert identity["review_sha256"] == digest(ledger)
    assert str(tmp_path) not in json.dumps(identity)
    with pytest.raises(ValueError, match="selected staging corpus"):
        config.selected_review_path("other.json")


def test_hybrid_resolver_uses_selected_strict_artifacts(staging, monkeypatch):
    from src.engine import hybrid_retriever
    captured = {}
    def resolve(**kwargs):
        captured.update(kwargs)
        return None
    driver = SimpleNamespace(session=lambda: None)
    monkeypatch.setattr(hybrid_retriever, "resolve_embedder_for_index", resolve)
    monkeypatch.setattr(hybrid_retriever, "get_neo4j_driver", lambda: driver)
    with pytest.raises(TypeError):
        hybrid_retriever.query_hybrid_temporal("thuế", "2026-03-20")
    assert captured == {"meta_path": str(staging[2]), "cache_path": str(staging[1]),
                        "corpus_path": str(staging[0]), "strict": True}
