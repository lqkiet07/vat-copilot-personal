import json
from pathlib import Path

from scripts.seed_clause_embeddings import (
    build_clause_embed_text,
    collect_clause_texts,
    seed_clause_embeddings,
)


def test_build_clause_embed_text_combines_context_and_points():
    doc = {"code": "48/2024/QH15", "title": "Luật Thuế GTGT"}
    art = {"code": "Dieu 5", "title": "Đối tượng không chịu thuế"}
    cl = {
        "code": "Khoan 25",
        "content": "Hàng hóa, dịch vụ của hộ, cá nhân kinh doanh.",
        "points": [{"code": "Diem a", "content": "Doanh thu từ 200 triệu đồng trở xuống."}],
    }
    text = build_clause_embed_text(doc, art, cl)
    assert "48/2024/QH15" in text
    assert "Đối tượng không chịu thuế" in text
    assert "Khoan 25" in text
    assert "200 triệu" in text


def test_collect_clause_texts_reads_staging_corpus():
    staging = Path(__file__).resolve().parents[1] / "data/processed/vat_corpus.staging.json"
    if not staging.exists():
        return
    texts = collect_clause_texts(str(staging))
    assert len(texts) > 1000
    assert "LUAT_48_2024_D5_K25" in texts
    assert "200" in texts["LUAT_48_2024_D5_K25"]


def test_seed_clause_embeddings_reuses_cache_and_imports_changed(tmp_path):
    corpus_file = tmp_path / "corpus.json"
    corpus_file.write_text(
        json.dumps(
            [
                {
                    "id": "DOC_1",
                    "code": "01/2026",
                    "title": "Luật mẫu",
                    "articles": [
                        {
                            "id": "DOC_1_D1",
                            "code": "Dieu 1",
                            "title": "Phạm vi",
                            "clauses": [
                                {"id": "DOC_1_D1_K1", "code": "Khoan 1", "content": "Nội dung 1"},
                                {"id": "DOC_1_D1_K2", "code": "Khoan 2", "content": "Nội dung 2"},
                            ],
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cache_file = tmp_path / "clause_cache.json"
    embedded_batches = []
    imported = []

    class FakeEmbedder:
        def embed_texts(self, texts):
            embedded_batches.append(list(texts))
            return [[0.5] * 4 for _ in texts]

    def fake_importer(vectors):
        imported.append(dict(vectors))
        return len(vectors)

    stats1 = seed_clause_embeddings(
        corpus_path=str(corpus_file),
        cache_path=str(cache_file),
        batch_size=1,
        embedder=FakeEmbedder(),
        schema_fn=lambda: None,
        importer_fn=fake_importer,
    )
    assert stats1["total_clauses"] == 2
    assert stats1["newly_embedded"] == 2
    assert stats1["imported"] == 2
    assert len(embedded_batches) == 2

    # Second run with identical corpus must reuse cache and embed 0 new clauses.
    embedded_batches.clear()
    stats2 = seed_clause_embeddings(
        corpus_path=str(corpus_file),
        cache_path=str(cache_file),
        batch_size=2,
        embedder=FakeEmbedder(),
        schema_fn=lambda: None,
        importer_fn=fake_importer,
    )
    assert stats2["newly_embedded"] == 0
    assert embedded_batches == []
