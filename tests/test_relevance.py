import pytest

from src.engine.models import ArticleNode
from src.verifier.relevance import build_relevance_map, cosine_similarity, score_article_relevance


def test_cosine_similarity():
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    v3 = [0.0, 1.0, 0.0]
    assert cosine_similarity(v1, v2) == 1.0
    assert cosine_similarity(v1, v3) == 0.0


def test_score_article_relevance_tiem_net():
    art_correct = ArticleNode(
        id="LUAT_48_2024_D5_K25",
        doc_id="LUAT_48_2024",
        code="Khoan 25 Dieu 5",
        title="Doi tuong khong chiu thue",
        content="Hàng hóa, dịch vụ của hộ, cá nhân kinh doanh có mức doanh thu hàng năm từ 200 triệu đồng trở xuống.",
        valid_from="2026-01-01",
        valid_to="9999-12-31",
    )
    art_rate = ArticleNode(
        id="LUAT_48_2024_D9_K3",
        doc_id="LUAT_48_2024",
        code="Khoan 3 Dieu 9",
        title="Thue suat 10%",
        content="Mức thuế suất 10% áp dụng đối với hàng hóa, dịch vụ không quy định tại khoản 1 và khoản 2 Điều này.",
        valid_from="2025-07-01",
        valid_to="9999-12-31",
    )
    art_junk = ArticleNode(
        id="ND_144_2026_D2",
        doc_id="ND_144_2026",
        code="Dieu 2",
        title="Hoa hong dai ly",
        content="Quy định về tiền hoa hồng đại lý bảo hiểm, đại lý xổ số, đại lý bán hàng đa cấp.",
        valid_from="2026-03-01",
        valid_to="9999-12-31",
    )

    query = "tôi mở tiệm net dưới quê thì có phải đóng thuế không và đóng bao nhiêu tiền"
    assert score_article_relevance(query, art_correct) is True
    assert score_article_relevance(query, art_rate) is True
    assert score_article_relevance(query, art_junk) is False


def test_build_relevance_map():
    art_correct = ArticleNode(
        id="LUAT_48_2024_D5_K25",
        doc_id="LUAT_48_2024",
        code="D5",
        title="Doanh thu ho kinh doanh",
        content="Hộ kinh doanh cá thể doanh thu dưới 200 triệu.",
        valid_from="2026-01-01",
        valid_to="9999-12-31",
    )
    art_junk = ArticleNode(
        id="ND_144_2026_D2",
        doc_id="ND_144_2026",
        code="D2",
        title="Hoa hong bao hiem",
        content="Hoa hồng đại lý bảo hiểm nhân thọ.",
        valid_from="2026-01-01",
        valid_to="9999-12-31",
    )
    rel_map = build_relevance_map("tiệm net dưới quê đóng thuế", [art_correct, art_junk])
    assert rel_map["LUAT_48_2024_D5_K25"] is True
    assert rel_map["ND_144_2026_D2"] is False


def test_export_rate_article_rejected_for_domestic_tiem_net():
    art = ArticleNode(
        id="LUAT_48_2024_D9_K1",
        doc_id="LUAT_48_2024",
        code="Khoan 1 Dieu 9",
        title="Mức thuế suất 0%",
        content="Mức thuế suất 0% áp dụng đối với hàng hóa, dịch vụ xuất khẩu...",
        valid_from="2025-07-01",
        valid_to="9999-12-31",
    )
    query = "tôi mở tiệm net dưới quê thì có phải đóng thuế không"
    assert score_article_relevance(query, art) is False


def test_export_rate_article_kept_for_export_query():
    art = ArticleNode(
        id="LUAT_48_2024_D9_K1",
        doc_id="LUAT_48_2024",
        code="Khoan 1 Dieu 9",
        title="Mức thuế suất 0%",
        content="Mức thuế suất 0% áp dụng đối với hàng hóa, dịch vụ xuất khẩu...",
        valid_from="2025-07-01",
        valid_to="9999-12-31",
    )
    query = "doanh nghiệp xuất khẩu phần mềm áp dụng thuế suất bao nhiêu"
    assert score_article_relevance(query, art) is True


def test_export_title_gate_does_not_reject_general_taxpayer_article():
    art = ArticleNode(
        id="LUAT_48_2024_D4",
        doc_id="LUAT_48_2024",
        code="Dieu 4",
        title="Người nộp thuế",
        content="Tổ chức, hộ, cá nhân sản xuất, kinh doanh hàng hóa, dịch vụ chịu thuế GTGT.",
        valid_from="2025-07-01",
        valid_to="9999-12-31",
    )
    query = "tôi mở tiệm net dưới quê thì có phải đóng thuế không"
    assert score_article_relevance(query, art) is True


def test_corporate_deduction_article_excluded_for_domestic_household_query():
    art_d21 = ArticleNode(
        id="ND_181_2025_D21",
        doc_id="ND_181_2025",
        code="Dieu 21",
        title="Đối tượng áp dụng phương pháp khấu trừ thuế",
        content="Cơ sở kinh doanh hoạt động có doanh thu hàng năm từ 1 tỷ đồng trở lên, trừ hộ, cá nhân sản xuất, kinh doanh.",
        valid_from="2025-07-01",
        valid_to="9999-12-31",
    )
    query = "quán net tôi mới mở dưới quê có phải đóng thuế kh và đóng bnh"
    assert score_article_relevance(query, art_d21) is False


@pytest.mark.parametrize("query,expected", [
    ("quán net tôi mới mở dưới quê có phải đóng thuế kh và đóng bnh", False),
    ("cửa hàng internet nộp thuế GTGT thế nào", False),
    ("dịch vụ casino có giá tính thuế GTGT thế nào", True),
    ("kinh doanh trò chơi điện tử có thưởng tính thuế thế nào", True),
])
def test_specialized_gambling_rule_requires_matching_activity(query, expected):
    article = ArticleNode(
        id="ND_181_2025_D10", doc_id="ND_181_2025", code="D10",
        title="Giá tính thuế đối với dịch vụ kinh doanh ca-si-nô, trò chơi điện tử có thưởng và đặt cược",
        content="Giá tính thuế là số tiền thu từ đổi đồng tiền quy ước cho người chơi trừ số tiền trả thưởng.",
        valid_from="2025-07-01", valid_to="9999-12-31",
    )
    assert score_article_relevance(query, article) is expected
