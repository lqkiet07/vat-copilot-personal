import pytest
from src.engine.models import ArticleNode
from src.engine.prompt import build_irac_prompt


def test_build_irac_prompt_contains_sections():
    query = "Thuế suất GTGT cho phần mềm xuất khẩu là bao nhiêu?"
    as_of_date = "2024-01-01"
    articles = [
        ArticleNode(
            id="13/2008/QH12_D8",
            doc_id="13/2008/QH12",
            code="13/2008/QH12",
            title="Điều 8. Thuế suất",
            content="Thuế suất 0% áp dụng đối với hàng hóa, dịch vụ xuất khẩu...",
            valid_from="2009-01-01",
            valid_to="9999-12-31",
            status="ACTIVE",
            related_ids=["31/2013/QH13_D1_K2"],
        ),
        ArticleNode(
            id="219/2013/TT-BTC_D9",
            doc_id="219/2013/TT-BTC",
            code="219/2013/TT-BTC",
            title="Điều 9. Thuế suất 0%",
            content="Dịch vụ phần mềm cung cấp cho tổ chức, cá nhân ở nước ngoài...",
            valid_from="2014-01-01",
            valid_to="9999-12-31",
            status="ACTIVE",
            related_ids=[],
        ),
    ]

    prompt = build_irac_prompt(query=query, as_of_date=as_of_date, articles=articles)

    assert query in prompt
    assert as_of_date in prompt

    for art in articles:
        assert f"[{art.id}]" in prompt
        assert art.code in prompt
        assert art.title in prompt
        assert art.content in prompt

    assert "31/2013/QH13_D1_K2" in prompt
    assert "IRAC" in prompt
    assert "Issue" in prompt
    assert "Rule" in prompt
    assert "Application" in prompt
    assert "Conclusion" in prompt
    assert "[DOC_ID_D" in prompt


def test_build_irac_prompt_empty_articles():
    query = "Quy định về thuế GTGT áp dụng từ năm 1990"
    as_of_date = "1990-01-01"
    articles = []

    prompt = build_irac_prompt(query=query, as_of_date=as_of_date, articles=articles)

    assert query in prompt
    assert as_of_date in prompt
    assert (
        "không có điều khoản" in prompt.lower()
        or "no active legal provisions" in prompt.lower()
        or "không tìm thấy" in prompt.lower()
    )
