import json
import math
from pathlib import Path

import pytest

from src.engine.embedder import (
    BaseEmbedder,
    DeterministicEmbedder,
    PrecomputedEmbedder,
    get_embedder,
)


def test_deterministic_embedder_dimension():
    embedder = DeterministicEmbedder(dimension=1024)
    vec = embedder.embed_text("Luật thuế giá trị gia tăng")
    assert isinstance(vec, list)
    assert len(vec) == 1024
    assert all(isinstance(x, float) for x in vec)

    embedder_custom = DeterministicEmbedder(dimension=256)
    vec_custom = embedder_custom.embed_text("Luật thuế")
    assert len(vec_custom) == 256


def test_deterministic_embedder_unit_norm():
    embedder = DeterministicEmbedder(dimension=1024)
    samples = [
        "Hóa đơn điện tử",
        "Thuế suất 10%",
        "",
        "Nghị định 181/2025/NĐ-CP sửa đổi bổ sung một số điều của Luật Thuế",
    ]
    for text in samples:
        vec = embedder.embed_text(text)
        norm = math.sqrt(sum(x * x for x in vec))
        assert math.isclose(norm, 1.0, rel_tol=1e-5), f"L2 norm {norm} != 1.0 for '{text}'"


def test_deterministic_embedder_reproducibility():
    embedder = DeterministicEmbedder(dimension=1024)
    text1 = "Thuế giá trị gia tăng 2025"
    text2 = "Thuế giá trị gia tăng 2026"

    vec1_a = embedder.embed_text(text1)
    vec1_b = embedder.embed_text(text1)
    vec2 = embedder.embed_text(text2)

    assert vec1_a == vec1_b, "Same text must produce identical vector"
    assert vec1_a != vec2, "Different text must produce different vector"


def test_precomputed_embedder_cache_hit(tmp_path: Path):
    expected_vec = [float(i) for i in range(1024)]
    cache_file = tmp_path / "embeddings.json"
    cache_file.write_text(
        json.dumps({"cụm từ kiểm tra": expected_vec}, ensure_ascii=False),
        encoding="utf-8",
    )

    embedder = PrecomputedEmbedder(cache_path=str(cache_file))
    result = embedder.embed_text("cụm từ kiểm tra")

    assert result == expected_vec


def test_precomputed_embedder_cache_miss_fallback(tmp_path: Path):
    cache_file = tmp_path / "embeddings.json"
    cache_file.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")

    embedder = PrecomputedEmbedder(cache_path=str(cache_file))
    fallback = DeterministicEmbedder(dimension=1024)

    text = "văn bản không có trong cache"
    result = embedder.embed_text(text)
    expected = fallback.embed_text(text)

    assert result == expected


def test_precomputed_embedder_nonexistent_file_fallback(tmp_path: Path):
    nonexistent = tmp_path / "does_not_exist.json"
    embedder = PrecomputedEmbedder(cache_path=str(nonexistent))
    fallback = DeterministicEmbedder(dimension=1024)

    text = "truy vấn thử nghiệm"
    result = embedder.embed_text(text)
    expected = fallback.embed_text(text)

    assert result == expected


def test_base_embedder_protocol_and_factory(tmp_path: Path):
    det = DeterministicEmbedder()
    assert isinstance(det, BaseEmbedder)

    cache_file = tmp_path / "cache.json"
    cache_file.write_text("{}", encoding="utf-8")
    pre = PrecomputedEmbedder(cache_path=str(cache_file))
    assert isinstance(pre, BaseEmbedder)

    f1 = get_embedder()
    assert isinstance(f1, DeterministicEmbedder)

    f2 = get_embedder(cache_path=str(cache_file))
    assert isinstance(f2, PrecomputedEmbedder)
