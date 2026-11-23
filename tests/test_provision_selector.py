import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.models import ArticleNode
from src.ui.corpus_viewer import load_corpus_index


def _luat48_d5_clauses() -> list[ArticleNode]:
    corpus = Path(__file__).resolve().parents[1] / "data/processed/vat_corpus.staging.json"
    if not corpus.exists():
        pytest.skip("staging corpus unavailable")
    index = load_corpus_index(str(corpus))
    d5 = index["LUAT_48_2024_D5"]
    title = d5.get("title", "")
    nodes = []
    for pid, meta in d5["provisions"].items():
        if pid == "LUAT_48_2024_D5":
            continue
        nodes.append(ArticleNode(
            id=pid,
            doc_id="LUAT_48_2024",
            code=meta.get("code", pid),
            title=meta.get("title") or title,
            content=meta["text"],
            valid_from=meta["valid_from"],
            valid_to=meta["valid_to"] or "9999-12-31",
            status=meta.get("status", "ACTIVE"),
        ))
    return nodes


def test_explicit_ids_ranked_tier_zero_ahead_of_siblings():
    from src.engine.provision_selector import rank_provisions

    nodes = _luat48_d5_clauses()
    ranked = rank_provisions(
        "Theo Điều 5 khoản 22 Luật 48/2024 về thuế giá trị gia tăng",
        nodes,
        explicit_ids=["LUAT_48_2024_D5_K22"],
    )
    assert ranked[0].id == "LUAT_48_2024_D5_K22"
    assert len(ranked) == len(nodes)


@pytest.mark.parametrize("query,expected_id", [
    ("Dịch vụ tang lễ có phải nộp thuế GTGT không?", "LUAT_48_2024_D5_K11"),
    ("Chuyển nhượng quyền sử dụng đất ở có thuộc đối tượng chịu thuế GTGT không?", "LUAT_48_2024_D5_K7"),
    ("Vàng dạng thỏi, miếng chưa được chế tác ở khâu nhập khẩu có chịu thuế giá trị gia tăng không?", "LUAT_48_2024_D5_K22"),
    ("Hộ, cá nhân sản xuất kinh doanh có mức doanh thu hằng năm dưới 200 triệu đồng có phải nộp thuế GTGT không?", "LUAT_48_2024_D5_K25"),
])
def test_natural_language_queries_rank_exact_clause_first(query, expected_id):
    from src.engine.provision_selector import rank_provisions

    nodes = _luat48_d5_clauses()
    assert len(nodes) >= 40
    ranked = rank_provisions(query, nodes)
    assert ranked[0].id == expected_id


def test_neutral_synonyms_and_parent_rrf_prior_break_ties():
    from src.engine.provision_selector import rank_provisions

    a1 = ArticleNode(id="DOC_A_D1_K1", doc_id="DOC_A", code="Khoản 1", title="",
                     content="Áp dụng thuế suất quy định chung.", valid_from="2025-01-01", valid_to="9999-12-31")
    a2 = ArticleNode(id="DOC_B_D2_K1", doc_id="DOC_B", code="Khoản 1", title="",
                     content="Áp dụng thuế suất quy định chung.", valid_from="2025-01-01", valid_to="9999-12-31")
    a3 = ArticleNode(id="DOC_C_D3_K1", doc_id="DOC_C", code="Khoản 1", title="",
                     content="Thuế suất hàng hóa xuất khẩu ra nước ngoài.", valid_from="2025-01-01", valid_to="9999-12-31")

    tied = rank_provisions("thuế suất quy định chung", [a1, a2], parent_order=["DOC_B_D2", "DOC_A_D1"])
    assert [a.id for a in tied] == ["DOC_B_D2_K1", "DOC_A_D1_K1"]

    with_syn = rank_provisions("bán hàng ra nước ngoài", [a1, a3], synonyms=("xuất khẩu",))
    assert with_syn[0].id == "DOC_C_D3_K1"


def test_project_reviewed_articles_orders_clauses_and_falls_back_to_hierarchy_title(monkeypatch):
    from src.engine.provision_evidence import EvidenceItem, EvidenceResolution, ProvisionEvidence
    from src.engine.retrieval_service import project_reviewed_articles

    p1 = ProvisionEvidence(
        id="LUAT_17_2024_D5_K1", text="Sản phẩm nông nghiệp trồng trọt chưa chế biến.",
        status="VALID", valid_from="2025-01-01", valid_to="9999-12-31", issued_on="2024-01-01",
        provenance=(), hierarchy=({"id": "LUAT_17_2024", "title": "Luật"}, {"id": "LUAT_17_2024_D5", "title": "Đối tượng không chịu thuế"}),
    )
    p2 = ProvisionEvidence(
        id="LUAT_17_2024_D5_K11", text="Dịch vụ tang lễ.",
        status="VALID", valid_from="2025-01-01", valid_to="9999-12-31", issued_on="2024-01-01",
        provenance=(), hierarchy=({"id": "LUAT_17_2024", "title": "Luật"}, {"id": "LUAT_17_2024_D5", "title": "Đối tượng không chịu thuế"}),
    )
    resolution = EvidenceResolution((EvidenceItem("LUAT_17_2024_D5", (p1, p2), True),), {})
    monkeypatch.setattr("src.engine.retrieval_service.explicit_evidence_ids", lambda q, c: [])
    monkeypatch.setattr("src.engine.retrieval_service.resolve_provision_evidence", lambda *a, **k: resolution)

    fake_report = SimpleNamespace(citations=[
        SimpleNamespace(id="LUAT_17_2024_D5_K1", status="VALID"),
        SimpleNamespace(id="LUAT_17_2024_D5_K11", status="VALID"),
    ])
    selected, _, _ = project_reviewed_articles(
        [], "Dịch vụ tang lễ có chịu thuế không?", "2026-01-01",
        "unused.json", "unused_review.json", lambda *a, **k: fake_report,
    )
    assert [a.id for a in selected] == ["LUAT_17_2024_D5_K11", "LUAT_17_2024_D5_K1"]
    assert selected[0].title == "Đối tượng không chịu thuế"


def test_concise_exact_clause_ranks_top5_across_130_clause_pool():
    from src.engine.provision_selector import rank_provisions

    corpus = Path(__file__).resolve().parents[1] / "data/processed/vat_corpus.staging.json"
    if not corpus.exists():
        pytest.skip("staging corpus unavailable")
    index = load_corpus_index(str(corpus))
    nodes = []
    for aid in ("LUAT_48_2024_D5", "LUAT_48_2024_D7", "LUAT_48_2024_D8", "LUAT_48_2024_D9", "ND_181_2025_D4", "ND_181_2025_D8", "ND_181_2025_D9"):
        if aid not in index:
            continue
        title = index[aid].get("title", "")
        for pid, meta in index[aid]["provisions"].items():
            if pid == aid:
                continue
            nodes.append(ArticleNode(
                id=pid,
                doc_id=aid.rsplit("_D", 1)[0],
                code=meta.get("code", pid),
                title=meta.get("title") or title,
                content=meta["text"],
                valid_from=meta["valid_from"],
                valid_to=meta["valid_to"] or "9999-12-31",
                status=meta.get("status", "ACTIVE"),
            ))
    assert len(nodes) >= 130
    ranked = rank_provisions(
        "Chuyển nhượng quyền sử dụng đất có chịu thuế giá trị gia tăng không?",
        nodes,
        parent_order=["LUAT_48_2024_D7", "ND_181_2025_D8", "LUAT_48_2024_D5"],
    )
    top5_ids = [node.id for node in ranked[:5]]
    assert "LUAT_48_2024_D5_K7" in top5_ids, f"Expected D5_K7 in top 5, got {top5_ids}"
