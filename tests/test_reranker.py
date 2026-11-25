from src.engine.models import ArticleNode
from src.engine.reranker import rerank_provisions, reranker_device


def _art(pid: str, title: str = "Tieu de", content: str = "Noi dung") -> ArticleNode:
    return ArticleNode(
        id=pid,
        doc_id=pid.split("_D")[0],
        code=pid,
        title=title,
        content=content,
        valid_from="2025-07-01",
        valid_to="9999-12-31",
    )


def test_rerank_provisions_prunes_low_score_noise_and_enforces_article_quota():
    provisions = [
        _art("ND_181_2025_D10_K1", "Casino", "Trò chơi điện tử có thưởng"),
        _art("ND_181_2025_D10_K2", "Casino", "Đặt cược"),
        _art("ND_181_2025_D10_K3", "Casino", "Khấu trừ casino"),
        _art("LUAT_48_2024_D5_K25", "Không chịu thuế", "Hộ kinh doanh doanh thu 200 triệu"),
        _art("LUAT_48_2024_D5_K1", "Không chịu thuế", "Sản phẩm trồng trọt"),
        _art("LUAT_48_2024_D5_K2", "Không chịu thuế", "Sản phẩm giống vật nuôi"),
    ]
    score_map = {
        "Casino: Trò chơi điện tử có thưởng": 0.02,
        "Casino: Đặt cược": 0.01,
        "Casino: Khấu trừ casino": 0.01,
        "Không chịu thuế: Hộ kinh doanh doanh thu 200 triệu": 0.91,
        "Không chịu thuế: Sản phẩm trồng trọt": 0.72,
        "Không chịu thuế: Sản phẩm giống vật nuôi": 0.65,
    }

    def fake_scorer(pairs):
        return [score_map[doc_text] for _, doc_text in pairs]

    ranked = rerank_provisions(
        "tiệm net hộ kinh doanh đóng thuế GTGT thế nào",
        provisions,
        min_score=0.15,
        article_quota=2,
        top_n=5,
        scorer=fake_scorer,
    )
    assert [p.id for p in ranked] == ["LUAT_48_2024_D5_K25", "LUAT_48_2024_D5_K1"]


def test_rerank_provisions_never_drops_explicit_ids():
    provisions = [
        _art("LUAT_48_2024_D5_K25", "Hộ kinh doanh", "Doanh thu 200 triệu"),
        _art("LUAT_48_2024_D5_K7", "Đất đai", "Chuyển quyền sử dụng đất"),
    ]

    def fake_scorer(pairs):
        return [0.95 if "200" in doc_text else 0.01 for _, doc_text in pairs]

    ranked = rerank_provisions(
        "hỏi về khoản 7 Điều 5 Luật 48/2024",
        provisions,
        explicit_ids=["LUAT_48_2024_D5_K7"],
        min_score=0.15,
        article_quota=1,
        top_n=2,
        scorer=fake_scorer,
    )
    assert ranked[0].id == "LUAT_48_2024_D5_K7"


def test_rerank_provisions_keeps_min_k_when_all_below_threshold():
    provisions = [
        _art("LUAT_48_2024_D1_K1", "A", "Item A"),
        _art("LUAT_48_2024_D2_K1", "B", "Item B"),
    ]

    def low_scorer(pairs):
        return [0.03, 0.08]

    ranked = rerank_provisions(
        "câu hỏi mơ hồ",
        provisions,
        min_score=0.15,
        top_n=5,
        min_k=1,
        scorer=low_scorer,
    )
    assert [p.id for p in ranked] == ["LUAT_48_2024_D2_K1"]


def test_rerank_provisions_pads_up_to_min_k_when_only_one_clears_threshold():
    provisions = [
        _art("LUAT_48_2024_D18_K2", "Hiệu lực", "Từ 01/07/2025"),
        _art("LUAT_48_2024_D5_K25", "Không chịu thuế", "Hộ kinh doanh doanh thu 200 triệu"),
        _art("LUAT_48_2024_D9_K2", "Thuế suất", "Phân bón áp dụng thuế suất 5%"),
        _art("ND_181_2025_D10_K1", "Casino", "Trò chơi điện tử có thưởng"),
    ]
    scores = {
        "Hiệu lực: Từ 01/07/2025": 0.82,
        "Không chịu thuế: Hộ kinh doanh doanh thu 200 triệu": 0.12,
        "Thuế suất: Phân bón áp dụng thuế suất 5%": 0.09,
        "Casino: Trò chơi điện tử có thưởng": 0.01,
    }

    ranked = rerank_provisions(
        "doanh thu hộ kinh doanh từ 01/07/2025",
        provisions,
        min_score=0.15,
        min_k=3,
        top_n=5,
        scorer=lambda pairs: [scores[doc_text] for _, doc_text in pairs],
    )
    assert [p.id for p in ranked] == [
        "LUAT_48_2024_D18_K2",
        "LUAT_48_2024_D5_K25",
        "LUAT_48_2024_D9_K2",
    ]


def test_rerank_provisions_fails_open_on_scorer_exception():
    provisions = [
        _art("LUAT_48_2024_D4_K1", "A", "Item 1"),
        _art("LUAT_48_2024_D5_K25", "B", "Item 2"),
        _art("LUAT_48_2024_D5_K1", "C", "Item 3"),
    ]

    def broken_scorer(pairs):
        raise RuntimeError("VRAM OOM simulation")

    ranked = rerank_provisions(
        "thuế GTGT",
        provisions,
        top_n=2,
        scorer=broken_scorer,
    )
    assert [p.id for p in ranked] == ["LUAT_48_2024_D4_K1", "LUAT_48_2024_D5_K25"]


def test_resolve_reranker_device_prefers_cpu_for_ollama_and_cuda_for_gemini(monkeypatch):
    monkeypatch.delenv("RERANKER_DEVICE", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert reranker_device() == "cpu"

    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    assert reranker_device() == "cuda"

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert reranker_device() == "cpu"


def test_prefilter_for_cpu_preserves_top_vector_and_rescues_lexical_matches():
    from src.engine.reranker import _prefilter_for_cpu

    candidates = [
        _art(f"DOC_D1_K{i}", "Mục chung", f"Nội dung pháp lý thông thường số {i}")
        for i in range(60)
    ]
    candidates[55] = _art(
        "LUAT_48_2024_D5_K25",
        "Đối tượng không chịu thuế",
        "Hộ kinh doanh cá nhân kinh doanh tiệm net doanh thu dưới ngưỡng",
    )

    kept = _prefilter_for_cpu("mở tiệm net hộ kinh doanh doanh thu", candidates)
    kept_ids = [item.id for item in kept]
    assert len(kept) <= 35
    assert len(set(kept_ids)) == len(kept_ids)
    assert kept_ids[:20] == [f"DOC_D1_K{i}" for i in range(20)]
    assert "LUAT_48_2024_D5_K25" in kept_ids

