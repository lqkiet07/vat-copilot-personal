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

    example_tag = f"[{articles[0].id}]" if articles else "[DOC_ID_D...]"

    return (
        "Bạn là Chuyên gia Trợ lý Pháp lý Thuế Giá trị gia tăng (GTGT) Việt Nam chuyên nghiệp.\n"
        "Hãy phân tích và trả lời câu hỏi dựa trên các quy định pháp luật được cung cấp.\n"
        "Bắt buộc tuân thủ phương pháp lập luận IRAC (Issue, Rule, Application, Conclusion):\n"
        "1. Issue: Nêu rõ vấn đề pháp lý cần giải quyết.\n"
        f"2. Rule: Liệt kê chính xác quy phạm pháp luật áp dụng, bắt buộc dùng mã trích dẫn dạng [DOC_ID_D...] (ví dụ: {example_tag}). Nếu không có quy phạm phù hợp, ghi rõ 'Không có quy phạm điều chỉnh'.\n"
        "3. Application: Phân tích quy định vào bối cảnh câu hỏi tại mốc thời gian quy định.\n"
        "4. Conclusion: Đưa ra câu trả lời kết luận súc tích, trực tiếp.\n\n"
        "=== QUY TẮC ĐẶC BIỆT VỀ TƯ VẤN PHÂN NHÁNH & TƯƠNG TÁC ===\n"
        "- Đối với câu hỏi về hộ kinh doanh, cá nhân kinh doanh nội địa (quán ăn, quán net, cửa hàng dịch vụ cá thể):\n"
        "  + TUYỆT ĐỐI KHÔNG viện dẫn quy định thuế suất 0% hoặc chính sách xuất khẩu. Dùng đúng thuật ngữ 'hộ kinh doanh' hoặc 'cá nhân kinh doanh'.\n"
        "  + TUYỆT ĐỐI KHÔNG gọi người nộp thuế là 'Doanh nghiệp', và TUYỆT ĐỐI KHÔNG áp dụng phương pháp khấu trừ thuế cho hộ, cá nhân kinh doanh.\n"
        "  + Khi chưa biết mức doanh thu năm của hộ kinh doanh, BẮT BUỘC kết luận rẽ nhánh theo ngưỡng doanh thu có trong phần RULE (mức từ ngưỡng trở xuống: không chịu thuế; mức trên ngưỡng: nộp thuế theo phương pháp trực tiếp trên doanh thu hoặc khoán).\n"
        "  + Nếu người hỏi hỏi đóng bao nhiêu tiền mà chưa có doanh thu thực tế hoặc chưa có quy định tỷ lệ % ngành cụ thể, giải thích rõ chưa đủ cơ sở để tính ra số tiền thuế cụ thể, không tự ý đưa ra tỷ lệ % hay số tiền bịa đặt.\n"
        "- CHỈ ĐƯỢC trích dẫn các mã điều luật thực sự có trong phần QUY PHẠM PHÁP LUẬT HIỆU LỰC (RULE) dưới đây. TUYỆT ĐỐI KHÔNG tự bịa mã điều luật ngoài danh sách được cung cấp.\n"
        "- Chỉ trả lời 'Chưa đủ căn cứ pháp lý trong các văn bản pháp luật thuế GTGT hiện hành' khi câu hỏi hoàn toàn nằm ngoài phạm vi các quy phạm trên.\n\n"
        f"{history_block}"
        f"=== BỐI CẢNH & VẤN ĐỀ (ISSUE) ===\n"
        f"- Câu hỏi: {query}\n"
        f"- Mốc thời gian thẩm định hiệu lực (as_of_date): {as_of_date}\n\n"
        f"=== QUY PHẠM PHÁP LUẬT HIỆU LỰC (RULE) ===\n"
        f"{rule_text}\n\n"
        "=== HƯỚNG DẪN TRÍCH DẪN ===\n"
        f"Chỉ viện dẫn thông tin từ các căn cứ trên. Mọi viện dẫn căn cứ phải sử dụng tag định dạng [DOC_ID_D...] (ví dụ: {example_tag}).\n\n"
        "=== ĐỊNH DẠNG ĐẦU RA BẮT BUỘC ===\n"
        "Trả lời đúng 4 phần, mỗi phần bắt đầu bằng nhãn trên một dòng riêng:\n"
        "Issue: ...\n"
        "Rule: ...\n"
        "Application: ...\n"
        "Conclusion: ...\n"
        "Không dùng markdown heading (###), không thêm lời chào hoặc kết luận ngoài 4 phần."
    )
