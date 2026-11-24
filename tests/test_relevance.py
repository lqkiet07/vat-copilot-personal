from src.engine.models import ArticleNode
from src.verifier.relevance import build_relevance_map, cosine_similarity, score_article_relevance


def test_cosine_similarity():
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    v3 = [0.0, 1.0, 0.0]
    assert cosine_similarity(v1, v2) == 1.0
    assert cosine_similarity(v1, v3) == 0.0


def test_score_article_relevance_uses_semantic_cosine_without_hardcoded_rules():
    art = ArticleNode(
        id="LUAT_48_2024_D5_K25",
        doc_id="LUAT_48_2024",
        code="Khoan 25 Dieu 5",
        title="Doi tuong khong chiu thue",
        content="Hàng hóa, dịch vụ của hộ, cá nhân kinh doanh có mức doanh thu thấp.",
        embedding=[1.0, 0.0],
        valid_from="2026-01-01",
        valid_to="9999-12-31",
    )
    assert score_article_relevance("bất kỳ câu hỏi nào", art, query_vec=[0.95, 0.05]) is True
    assert score_article_relevance("bất kỳ câu hỏi nào", art, query_vec=[0.1, 0.99]) is False


def test_build_relevance_map_lexical_overlap_without_hardcoded_blacklists():
    art_match = ArticleNode(
        id="LUAT_48_2024_D5_K25",
        doc_id="LUAT_48_2024",
        code="D5",
        title="Doanh thu ho kinh doanh",
        content="Hộ kinh doanh cá thể doanh thu hằng năm.",
        valid_from="2026-01-01",
        valid_to="9999-12-31",
    )
    art_unrelated = ArticleNode(
        id="ND_144_2026_D2",
        doc_id="ND_144_2026",
        code="D2",
        title="Hoa hong bao hiem",
        content="Hoa hồng đại lý bảo hiểm nhân thọ.",
        valid_from="2026-01-01",
        valid_to="9999-12-31",
    )
    rel_map = build_relevance_map("hộ kinh doanh nộp thuế", [art_match, art_unrelated])
    assert rel_map["LUAT_48_2024_D5_K25"] is True
    assert rel_map["ND_144_2026_D2"] is False
