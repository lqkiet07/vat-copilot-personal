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


def test_rerank_provisions_demotes_vbhn_and_boilerplate_for_tax_rate_queries():
    provisions = [
        _art("VBHN_25_2026_D3", "Hợp nhất", "Văn bản hợp nhất hộ kinh doanh"),
        _art("ND_181_2025_D39", "Hiệu lực thi hành", "Nghị định này có hiệu lực từ ngày 01 tháng 7 năm 2025."),
        _art("LUAT_48_2024_D9_K2", "Thuế suất", "Thuế suất 5% áp dụng đối với phân bón."),
    ]
    scores = {
        "Hợp nhất: Văn bản hợp nhất hộ kinh doanh": 0.90,
        "Hiệu lực thi hành: Nghị định này có hiệu lực từ ngày 01 tháng 7 năm 2025.": 0.85,
        "Thuế suất: Thuế suất 5% áp dụng đối với phân bón.": 0.72,
    }
    ranked = rerank_provisions(
        "Từ ngày 01/07/2025, mặt hàng phân bón áp dụng mức thuế suất thuế GTGT bao nhiêu?",
        provisions,
        top_n=2,
        min_k=1,
        scorer=lambda pairs: [scores[d] for _, d in pairs],
    )
    assert "LUAT_48_2024_D9_K2" in [p.id for p in ranked]
    assert ranked[0].id == "LUAT_48_2024_D9_K2"


def test_rerank_provisions_windows_long_articles_and_enriches_point_lead_in_without_mutating_content():
    parent = _art("LUAT_48_2024_D9_K2", "Thuế suất", "Mức thuế suất 5% áp dụng đối với hàng hóa sau:")
    point = _art("LUAT_48_2024_D9_K2_b", "Thuế suất", "Phân bón, quặng để sản xuất phân bón.")
    long_art = _art(
        "LUAT_48_2024_D9",
        "Thuế suất",
        ("Điều khoản xuất khẩu và dịch vụ chung. " * 45) + "Diem b: Mặt hàng phân bón chịu thuế 5%.",
    )
    captured: list[tuple[str, str]] = []

    def inspecting_scorer(pairs):
        captured.extend(pairs)
        return [0.85 if "phân bón" in doc.lower() and "5%" in doc.lower() else 0.4 for _, doc in pairs]

    ranked = rerank_provisions(
        "Mặt hàng phân bón áp dụng thuế suất GTGT bao nhiêu?",
        [parent, point, long_art],
        top_n=3,
        min_k=1,
        scorer=inspecting_scorer,
    )
    point_pair = next(doc for _, doc in captured if "quặng để sản xuất phân bón" in doc)
    long_pair = next(doc for _, doc in captured if "Điều khoản xuất khẩu" in doc or "phân bón chịu thuế" in doc)
    assert "Mức thuế suất 5%" in point_pair
    assert "phân bón" in long_pair.lower()
    assert len(long_pair) <= 1000
    assert point.content == "Phân bón, quặng để sản xuất phân bón."
    assert ranked[0].id in {"LUAT_48_2024_D9_K2_b", "LUAT_48_2024_D9"}


def test_expand_companion_provisions_attaches_point_parent_and_compact_threshold_counterpart():
    from src.engine.reranker import expand_companion_provisions

    k1 = _art("ND_68_2026_D3_K1", "Thuế GTGT", "Doanh thu năm từ 01 tỷ đồng trở xuống không chịu thuế.")
    k2 = _art("ND_68_2026_D3_K2", "Thuế GTGT", "Doanh thu năm trên 01 tỷ đồng chịu thuế trực tiếp.")
    lead = _art("LUAT_48_2024_D9_K2", "Thuế suất", "Mức thuế suất 5% áp dụng đối với:")
    pt = _art("LUAT_48_2024_D9_K2_b", "Thuế suất", "Phân bón, thuốc bảo vệ thực vật.")
    expanded = expand_companion_provisions([pt, k2], [lead, pt, k1, k2], max_total=10)
    ids = [p.id for p in expanded]
    assert ids[0] == "LUAT_48_2024_D9_K2_b"
    assert "LUAT_48_2024_D9_K2" in ids
    assert "ND_68_2026_D3_K1" in ids


def test_expand_companion_provisions_interleaves_adjacent_within_top9_and_skips_boilerplate():
    from src.engine.reranker import expand_companion_provisions

    k1 = _art("ND_68_2026_D3_K1", "Thuế GTGT", "Doanh thu năm từ 01 tỷ đồng trở xuống không chịu thuế.")
    k2 = _art("ND_68_2026_D3_K2", "Thuế GTGT", "Doanh thu năm trên 01 tỷ đồng chịu thuế trực tiếp.")
    boiler = _art("LUAT_48_2024_D17_K1", "Hiệu lực", "Luật này có hiệu lực thi hành từ ngày 01 tháng 7 năm 2025.")
    sub = _art("LUAT_48_2024_D17_K2", "Hiệu lực", "Quy định chi tiết thi hành Luật này.")
    fillers = [_art(f"ND_181_2025_D2_K{i}", "Thuế GTGT", f"Khoản đệm {i}.") for i in range(1, 9)]
    selected = [k2, sub, *fillers[:7]]
    pool = [k1, k2, boiler, sub, *fillers]

    expanded = expand_companion_provisions(selected, pool, max_total=12)
    top9_ids = [p.id for p in expanded[:9]]
    assert top9_ids[:2] == ["ND_68_2026_D3_K2", "ND_68_2026_D3_K1"]
    assert "LUAT_48_2024_D17_K1" not in [p.id for p in expanded]


def test_ensure_scope_doc_coverage_guarantees_slots_for_every_scoped_doc():
    from src.engine.reranker import ensure_scope_doc_coverage

    d359 = [_art(f"ND_359_2025_D1_K{i}", "359", f"Điều khoản 359.{i}") for i in range(1, 10)]
    d181 = [_art(f"ND_181_2025_D2_K{i}", "181", f"Điều khoản 181.{i}") for i in range(1, 4)]
    d144 = [_art(f"ND_144_2026_D3_K{i}", "144", f"Điều khoản 144.{i}") for i in range(1, 4)]

    covered = ensure_scope_doc_coverage(
        d359,
        d359 + d181 + d144,
        ["ND_181_2025", "ND_359_2025", "ND_144_2026"],
        per_doc_min=2,
        insert_at=4,
        max_total=12,
    )
    top9_docs = [p.id.split("_D", 1)[0] for p in covered[:9]]
    assert top9_docs.count("ND_181_2025") >= 2
    assert top9_docs.count("ND_144_2026") >= 2
    assert top9_docs.count("ND_359_2025") >= 2


def test_preserve_top_retrieved_anchor_keeps_parent_article_clauses_in_top_pack():
    from src.engine.reranker import preserve_top_retrieved_anchor

    anchor_clauses = [
        _art("ND_68_2026_D3_K1", "Hộ kinh doanh", "Doanh thu từ 01 tỷ đồng trở xuống không chịu thuế."),
        _art("ND_68_2026_D3_K2", "Hộ kinh doanh", "Doanh thu trên 01 tỷ đồng nộp trực tiếp."),
    ]
    others = [_art(f"ND_181_2025_D7_K{i}", "Cho thuê", f"Quy định cho thuê tài sản {i}.") for i in range(1, 10)]

    preserved = preserve_top_retrieved_anchor(
        others,
        anchor_clauses + others,
        "ND_68_2026_D3",
        insert_at=5,
        max_total=12,
    )
    top9_ids = [p.id for p in preserved[:9]]
    assert "ND_68_2026_D3_K1" in top9_ids
    assert "ND_68_2026_D3_K2" in top9_ids



