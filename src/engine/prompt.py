"""IRAC prompt generation module with domain relevance discernment."""
from src.engine.models import ArticleNode


def build_irac_prompt(
    query: str,
    as_of_date: str,
    articles: list[ArticleNode],
    history: list[dict] | None = None,
) -> str:
    """Build IRAC-formatted prompt containing active legal rules and conversation context."""
    rules = []
    for art in articles:
        related = f" (Sửa đổi/liên quan: {', '.join(art.related_ids)})" if art.related_ids else ""
        rules.append(f"- [{art.id}] {art.code} - {art.title}{related}\n  {art.content}")

    rule_text = (
        "\n\n".join(rules)
        if rules
        else "Không tìm thấy điều khoản pháp luật nào còn hiệu lực phù hợp tại thời điểm tra cứu."
    )

    history_lines = []
    if history:
        for turn in history:
            role_label = "Người dùng" if turn.get("role") == "user" else "Trợ lý"
            content = turn.get("content", "")
            if content:
                history_lines.append(f"{role_label}: {content}")
    history_block = (
        "=== LỊCH SỬ HỘI THOẠI TRƯỚC ĐÓ ===\n" + "\n".join(history_lines) + "\n\n"
        if history_lines
        else ""
    )

    if not articles:
        return (
            "Bạn là Chuyên gia Trợ lý Pháp lý Thuế Giá trị gia tăng (GTGT) Việt Nam chuyên nghiệp.\n"
            f"{history_block}"
            f"=== BỐI CẢNH & VẤN ĐỀ ===\n- Câu hỏi: {query}\n- Mốc thời gian thẩm định hiệu lực (as_of_date): {as_of_date}\n\n"
            "=== QUY PHẠM PHÁP LUẬT HIỆU LỰC (RULE) ===\nKhông tìm thấy điều khoản pháp luật thuế GTGT nào điều chỉnh nội dung này tại thời điểm tra cứu.\n\n"
            "=== HƯỚNG DẪN TRẢ LỜI ===\n"
            "Do không có điều khoản điều chỉnh, thông báo rõ rằng chưa đủ căn cứ pháp luật thuế GTGT hiện hành để kết luận. "
            "Tuyệt đối không tự suy diễn hoặc bịa đặt mức thuế hay điều kiện áp dụng.\n\n"
            "=== ĐỊNH DẠNG ĐẦU RA BẮT BUỘC ===\n"
            "Trả lời đúng 4 phần, mỗi phần bắt đầu bằng nhãn trên một dòng riêng:\n"
            "Issue: ...\n"
            "Rule: ...\n"
            "Application: ...\n"
            "Conclusion: ...\n"
            "Không dùng markdown heading (###), không thêm lời chào hoặc kết luận ngoài 4 phần."
        )

    return (
        "Bạn là Chuyên gia Trợ lý Pháp lý Thuế Giá trị gia tăng (GTGT) Việt Nam chuyên nghiệp.\n"
        "Hãy phân tích và trả lời câu hỏi dựa trên các quy định pháp luật được cung cấp.\n"
        "Bắt buộc tuân thủ phương pháp lập luận IRAC (Issue, Rule, Application, Conclusion):\n"
        "1. Issue: Nêu rõ vấn đề pháp lý cần giải quyết.\n"
        "2. Rule: Liệt kê chính xác quy phạm pháp luật áp dụng, bắt buộc dùng mã trích dẫn dạng [DOC_ID_D...] (ví dụ: [LUAT_48_2024_D9], [ND_181_2025_D5]). Nếu không có quy phạm phù hợp, ghi rõ 'Không có quy phạm điều chỉnh'.\n"
        "3. Application: Phân tích quy định vào bối cảnh câu hỏi tại mốc thời gian quy định.\n"
        "4. Conclusion: Đưa ra câu trả lời kết luận súc tích, trực tiếp.\n\n"
        "=== QUY TẮC ĐẶC BIỆT VỀ TƯ VẤN PHÂN NHÁNH & TƯƠNG TÁC ===\n"
        "- Đối với câu hỏi về hộ kinh doanh, cá nhân kinh doanh nội địa (không có từ khóa xuất khẩu):\n"
        "  + TUYỆT ĐỐI KHÔNG viện dẫn quy định thuế suất 0% hoặc chính sách xuất khẩu. Dùng đúng thuật ngữ 'hộ kinh doanh' hoặc 'cá nhân kinh doanh'.\n"
        "  + TUYỆT ĐỐI KHÔNG gọi người nộp thuế là 'Doanh nghiệp'.\n"
        "  + Căn cứ vào các ngưỡng doanh thu và phương pháp tính thuế quy định cụ thể trong các điều luật được cung cấp tại phần RULE để phân tích và kết luận.\n"
        "- Khi câu hỏi thuộc đối tượng hộ kinh doanh, cá nhân kinh doanh nhưng khuyết dữ liệu doanh thu hằng năm, phân tích theo các kịch bản phân nhánh căn cứ theo quy định tại phần RULE.\n"
        "- Chỉ trả lời 'Chưa đủ căn cứ pháp lý trong các văn bản pháp luật thuế GTGT hiện hành' khi câu hỏi hoàn toàn nằm ngoài phạm vi các quy phạm trên.\n\n"
        f"{history_block}"
        f"=== BỐI CẢNH & VẤN ĐỀ (ISSUE) ===\n"
        f"- Câu hỏi: {query}\n"
        f"- Mốc thời gian thẩm định hiệu lực (as_of_date): {as_of_date}\n\n"
        f"=== QUY PHẠM PHÁP LUẬT HIỆU LỰC (RULE) ===\n"
        f"{rule_text}\n\n"
        "=== HƯỚNG DẪN TRÍCH DẪN ===\n"
        "Chỉ viện dẫn thông tin từ các căn cứ trên. Mọi viện dẫn căn cứ phải sử dụng tag định dạng [DOC_ID_D...] (ví dụ: [LUAT_48_2024_D9]).\n\n"
        "=== ĐỊNH DẠNG ĐẦU RA BẮT BUỘC ===\n"
        "Trả lời đúng 4 phần, mỗi phần bắt đầu bằng nhãn trên một dòng riêng:\n"
        "Issue: ...\n"
        "Rule: ...\n"
        "Application: ...\n"
        "Conclusion: ...\n"
        "Không dùng markdown heading (###), không thêm lời chào hoặc kết luận ngoài 4 phần."
    )
