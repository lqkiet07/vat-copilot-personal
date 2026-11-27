# Upgrade Evaluation Audit Summary

- **Generated UTC:** `2026-11-27T08:02:59.691619+00:00` | **Git HEAD:** `1ce306c`
- **Env:** `staging` | **LLM:** `gemini` (`gemini-3.5-flash-lite`) | **Embedding:** `ollama` | **E2E Cases:** `25/25`

## Retrieval Ablation

| Mode | In-Scope | Top-5 Hit | Primary Top-5 Hit | Forbidden Noise | Mean Latency (ms) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| baseline | 22 | 0.9545 | 0.7727 | 0.0000 | 5922.9 |
| upgraded | 22 | 1.0000 | 0.9545 | 0.0000 | 8905.6 |

## End-to-End QA Evaluation

- **Cases:** `25` | **Strict Pass:** `0.8` | **Answered:** `0.7273` | **Scope:** `1.0` | **Status:** `0.92` | **Primary Citation:** `0.88` | **Conclusion:** `1.0` | **Mean Latency:** `36673.0 ms`

### By Category

| Category | Total | Strict Pass | Answered | Primary Citation | Conclusion OK | Mean Latency (ms) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| colloquial_everyday | 5 | 0.6000 | 0.6 | 0.6000 | 1.0000 | 43238.9 |
| temporal_amendment | 5 | 0.6000 | 0.6 | 1.0000 | 1.0000 | 27684.8 |
| conditional_needs_facts | 5 | 1.0000 | 0.6 | 1.0000 | 1.0000 | 62244.1 |
| multi_turn_followup | 5 | 0.8000 | 1.0 | 0.8000 | 1.0000 | 43157.7 |
| out_of_scope_and_boundary | 5 | 1.0000 | 1.0 | 1.0000 | 1.0000 | 7039.6 |

### Per-Case Audit

| ID | Category | Status | Primary Citation | Conclusion OK | Strict Pass | Latency (ms) | Conclusion Preview |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| UPG_01 | colloquial_everyday | INSUFFICIENT_BASIS | PASS | PASS | PASS | 43349.2 | Để trả lời chính xác trường hợp của bạn, vui lòng cho biết thêm các thông tin bên dưới (một số quy định liên quan đang c |
| UPG_02 | colloquial_everyday | ANSWERED | PASS | PASS | PASS | 19683.4 | Việc xác định nghĩa vụ thuế giá trị gia tăng phụ thuộc vào việc người kinh doanh hoạt động dưới hình thức hộ kinh doanh, |
| UPG_03 | colloquial_everyday | ANSWERED | FAIL | PASS | FAIL | 68398.1 | Việc cá nhân chạy xe ôm công nghệ có phải đóng thuế GTGT hay không phụ thuộc vào các điều kiện cụ thể sau: - Trường hợp  |
| UPG_04 | colloquial_everyday | ANSWERED | FAIL | PASS | FAIL | 56237.7 | - Trường hợp hoạt động cho thuê phòng trọ thuộc diện chịu thuế giá trị gia tăng, hộ kinh doanh, cá nhân kinh doanh và do |
| UPG_05 | colloquial_everyday | INSUFFICIENT_BASIS | PASS | PASS | PASS | 28526.3 | Để trả lời chính xác trường hợp của bạn, vui lòng cho biết thêm các thông tin bên dưới (một số quy định liên quan đang c |
| UPG_06 | temporal_amendment | ANSWERED | PASS | PASS | PASS | 22919.7 | Việc xác định hộ kinh doanh có doanh thu 150 triệu đồng vào tháng 10/2025 có thuộc đối tượng không chịu thuế GTGT hay kh |
| UPG_07 | temporal_amendment | INSUFFICIENT_BASIS | PASS | PASS | FAIL | 20310.7 | Chưa đủ căn cứ để kết luận pháp lý cho tình huống; trên đây chỉ là trích đoạn chứng cứ đã xác minh. Hệ thống chưa xác mi |
| UPG_08 | temporal_amendment | ANSWERED | PASS | PASS | PASS | 23545.0 | Mặt hàng phân bón áp dụng mức thuế suất thuế GTGT 5% kể từ ngày 01/07/2025 [LUAT_48_2024_D9_K2_b]. |
| UPG_09 | temporal_amendment | ANSWERED | PASS | PASS | PASS | 29959.6 | Chính sách giảm thuế giá trị gia tăng xuống 8% theo Nghị quyết số 204/2025/QH15 và Nghị định số 174/2025/NĐ-CP được áp d |
| UPG_10 | temporal_amendment | INSUFFICIENT_BASIS | PASS | PASS | FAIL | 41689.0 | Để trả lời chính xác trường hợp của bạn, vui lòng cho biết thêm các thông tin bên dưới (một số quy định liên quan đang c |
| UPG_11 | conditional_needs_facts | INSUFFICIENT_BASIS | PASS | PASS | PASS | 41065.3 | Để trả lời chính xác trường hợp của bạn, vui lòng cho biết thêm các thông tin bên dưới (một số quy định liên quan đang c |
| UPG_12 | conditional_needs_facts | INSUFFICIENT_BASIS | PASS | PASS | PASS | 131840.5 | Chưa đủ căn cứ để kết luận pháp lý cho tình huống; trên đây chỉ là trích đoạn chứng cứ đã xác minh. Hệ thống chưa xác mi |
| UPG_13 | conditional_needs_facts | ANSWERED | PASS | PASS | PASS | 20787.1 | Việc bán sản phẩm nông nghiệp chưa qua chế biến hoặc qua sơ chế thông thường có các nghĩa vụ thuế GTGT khác nhau tùy thu |
| UPG_14 | conditional_needs_facts | ANSWERED | PASS | PASS | PASS | 91943.1 | - Trường hợp hoạt động trên sàn thương mại điện tử có chức năng đặt hàng trực tuyến và thanh toán, chủ quản sàn thực hiệ |
| UPG_15 | conditional_needs_facts | ANSWERED | PASS | PASS | PASS | 25584.3 | Dịch vụ phần mềm cung cấp cho khách hàng không thuộc đối tượng được áp dụng thuế suất thuế GTGT 0%. - Dịch vụ phần mềm t |
| UPG_16 | multi_turn_followup | ANSWERED | PASS | PASS | PASS | 62860.6 | Hộ kinh doanh có doanh thu 150 triệu đồng một năm được xác định nghĩa vụ cụ thể như sau: - Không phải nộp thuế giá trị g |
| UPG_17 | multi_turn_followup | ANSWERED | PASS | PASS | PASS | 19616.6 | Doanh nghiệp xuất khẩu phải đáp ứng các điều kiện cụ thể sau: - Trường hợp thực hiện đầy đủ giao dịch qua ngân hàng đúng |
| UPG_18 | multi_turn_followup | ANSWERED | PASS | PASS | PASS | 19453.7 | Hộ kinh doanh bán tạp hóa với doanh thu 650 triệu đồng trong năm 2026 không phải tính và nộp thuế GTGT do thuộc diện khô |
| UPG_19 | multi_turn_followup | ANSWERED | FAIL | PASS | FAIL | 14733.1 | - Trường hợp đáp ứng đầy đủ hóa đơn, chứng từ nộp thuế hợp pháp cùng các điều kiện khấu trừ theo quy định, cơ sở kinh do |
| UPG_20 | multi_turn_followup | ANSWERED | PASS | PASS | PASS | 99124.3 | - Sản phẩm trồng trọt do hợp tác xã nộp thuế GTGT theo phương pháp khấu trừ bán ra ở khâu thương mại cho doanh nghiệp ch |
| UPG_21 | out_of_scope_and_boundary | OUT_OF_SCOPE | PASS | PASS | PASS | 1.3 | Câu hỏi của bạn không thuộc phạm vi điều chỉnh của Hệ thống Pháp lý Thuế Giá trị gia tăng (GTGT). Vui lòng đặt câu hỏi l |
| UPG_22 | out_of_scope_and_boundary | OUT_OF_SCOPE | PASS | PASS | PASS | 0.8 | Câu hỏi của bạn không thuộc phạm vi điều chỉnh của Hệ thống Pháp lý Thuế Giá trị gia tăng (GTGT). Vui lòng đặt câu hỏi l |
| UPG_23 | out_of_scope_and_boundary | OUT_OF_SCOPE | PASS | PASS | PASS | 0.4 | Câu hỏi nằm ngoài phạm vi thuế GTGT. |
| UPG_24 | out_of_scope_and_boundary | ANSWERED | PASS | PASS | PASS | 17767.4 | Hoạt động chuyển nhượng quyền sử dụng đất thuộc diện không chịu thuế giá trị gia tăng. - Trường hợp thực hiện chuyển quy |
| UPG_25 | out_of_scope_and_boundary | ANSWERED | PASS | PASS | PASS | 17428.1 | Dịch vụ lưu trú khách sạn trong nước áp dụng mức thuế suất như sau: - Trường hợp cơ sở kinh doanh tính thuế giá trị gia  |

## Failed / Partial Cases

- **E2E Fail `UPG_03`** (colloquial_everyday): status=`ANSWERED`, primary_citation_ok=`False`, conclusion_ok=`True`, citations=`['LUAT_108_2025_D13_K1', 'LUAT_108_2025_D13_K1_b', 'ND_181_2025_D4_K9']`, gen=`verified_draft`, assess=`accepted`, conclusion=`Việc cá nhân chạy xe ôm công nghệ có phải đóng thuế GTGT hay không phụ thuộc vào các điều kiện cụ thể sau: - Trường hợp hoạt động vận chuyển thuộc đối tượng không chịu thuế GTGT th`
- **E2E Fail `UPG_04`** (colloquial_everyday): status=`ANSWERED`, primary_citation_ok=`False`, conclusion_ok=`True`, citations=`['ND_181_2025_D7_K1_a', 'ND_181_2025_D7_K1', 'ND_181_2025_D7_K1_b', 'ND_68_2026_D8_K4_d']`, gen=`verified_draft`, assess=`accepted`, conclusion=`- Trường hợp hoạt động cho thuê phòng trọ thuộc diện chịu thuế giá trị gia tăng, hộ kinh doanh, cá nhân kinh doanh và doanh nghiệp phải tính thuế dựa trên giá chưa có thuế giá trị `
- **E2E Fail `UPG_07`** (temporal_amendment): status=`INSUFFICIENT_BASIS`, primary_citation_ok=`True`, conclusion_ok=`True`, citations=`['ND_141_2026_D1_K2', 'ND_141_2026_D1_K1', 'ND_68_2026_D18_K4', 'ND_68_2026_D3_K1', 'ND_68_2026_D3_K2', 'LUAT_48_2024_D18_K2', 'LUAT_48_2024_D18', 'ND_141_2026_D4_K3', 'ND_141_2026_D4_K1']`, gen=`unsupported_draft`, assess=`invalid_schema`, conclusion=`Chưa đủ căn cứ để kết luận pháp lý cho tình huống; trên đây chỉ là trích đoạn chứng cứ đã xác minh. Hệ thống chưa xác minh được chứng cứ VBHN 25 2026 D3: Container is not citable; `
- **E2E Fail `UPG_10`** (temporal_amendment): status=`INSUFFICIENT_BASIS`, primary_citation_ok=`True`, conclusion_ok=`True`, citations=`['ND_359_2025_D1_K1', 'ND_359_2025_D1_K2', 'TT_77_2025_D3_K1', 'TT_101_2025_D1', 'ND_181_2025_D2_K1', 'ND_181_2025_D2_K2', 'ND_181_2025_D2_K3', 'ND_144_2026_D1', 'ND_144_2026_D1_K2']`, gen=`unsupported_draft`, assess=`unsupported`, conclusion=`Để trả lời chính xác trường hợp của bạn, vui lòng cho biết thêm các thông tin bên dưới (một số quy định liên quan đang có hiệu lực đã được đính kèm bên trong phần phân tích chi tiế`
- **E2E Fail `UPG_19`** (multi_turn_followup): status=`ANSWERED`, primary_citation_ok=`False`, conclusion_ok=`True`, citations=`['LUAT_48_2024_D11_K1_c', 'ND_181_2025_D20_K3']`, gen=`verified_draft`, assess=`accepted`, conclusion=`- Trường hợp đáp ứng đầy đủ hóa đơn, chứng từ nộp thuế hợp pháp cùng các điều kiện khấu trừ theo quy định, cơ sở kinh doanh được khấu trừ toàn bộ số thuế GTGT đầu vào ghi trên hóa `
