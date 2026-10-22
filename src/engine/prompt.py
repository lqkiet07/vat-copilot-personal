"""IRAC prompt generation module."""
from src.engine.models import ArticleNode


def build_irac_prompt(
    query: str, as_of_date: str, articles: list[ArticleNode]
) -> str:
    """Build IRAC-formatted prompt containing active legal rules."""
    rules = []
    for art in articles:
        related = f" (Sửa đổi/liên quan: {', '.join(art.related_ids)})" if art.related_ids else ""
        rules.append(f"- [{art.id}] {art.code} - {art.title}{related}\n  {art.content}")

    rule_text = (
        "\n\n".join(rules)
        if rules
        else "Không tìm thấy điều khoản pháp luật nào còn hiệu lực phù hợp tại thời điểm tra cứu."
    )

    return (
        "Bạn là trợ lý pháp lý chuyên nghiệp. Hãy trả lời câu hỏi dựa trên các quy định pháp luật được cung cấp.\n"
        "Bắt buộc tuân thủ phương pháp lập luận IRAC (Issue, Rule, Application, Conclusion):\n"
        "1. Issue: Xác định rõ vấn đề pháp lý cần giải quyết.\n"
        "2. Rule: Trích dẫn chính xác quy phạm pháp luật áp dụng, bắt buộc dùng mã trích dẫn chuẩn [DOC_ID_D...].\n"
        "3. Application: Áp dụng và phân tích quy định vào bối cảnh câu hỏi.\n"
        "4. Conclusion: Đưa ra câu trả lời kết luận súc tích, trực tiếp.\n\n"
        f"=== BỐI CẢNH & VẤN ĐỀ (ISSUE) ===\n"
        f"- Câu hỏi: {query}\n"
        f"- Thời điểm tra cứu (as_of_date): {as_of_date}\n\n"
        f"=== QUY PHẠM PHÁP LUẬT HIỆU LỰC (RULE) ===\n"
        f"{rule_text}\n\n"
        "=== HƯỚNG DẪN TRÍCH DẪN ===\n"
        "Chỉ viện dẫn thông tin từ các căn cứ trên. Mọi viện dẫn căn cứ phải sử dụng tag định dạng [DOC_ID_D...] (ví dụ: [13/2008/QH12_D8])."
    )
