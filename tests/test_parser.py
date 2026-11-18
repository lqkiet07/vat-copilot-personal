import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from src.parser.vbpl_parser import parse_vbpl_document
from src.parser.utils import normalize_text


RAW = Path(__file__).resolve().parents[1] / "data" / "raw"


def test_real_raw04_nd359_keeps_commencement_in_article_two():
    html = (RAW / "04_nd_359_2025.html").read_text(encoding="utf-8")
    doc = parse_vbpl_document(html, META)
    assert [a["code"] for a in doc["articles"]] == ["Dieu 1", "Dieu 2"]
    first, second = doc["articles"]
    assert [c["code"] for c in first["clauses"]] == ["Khoan 1", "Khoan 2"]
    assert [c["code"] for c in second["clauses"]] == ["Khoan 1", "Khoan 2", "Khoan 3"]
    assert "01 tháng 01 năm 2026" in second["clauses"][0]["content"]
    assert "hiệu lực thi hành" not in first["clauses"][0]["content"]
    assert not doc["unresolved_blocks"]


@pytest.mark.parametrize("opening,closing", [("“", '"'), ('"', '"'), ("“", "”")])
def test_quote_delimiters_keep_nested_replacement_inside_outer_clause(opening, closing):
    html = (
        '<p>Điều 1. Sửa đổi</p><p>1. Thay thế như sau:</p>'
        f'<p>{opening}Điều 7. Nội dung thay thế</p>'
        '<p>1. Cụm từ “nội dung” được giữ nguyên.</p>'
        '<p>Điều 8. Vẫn thuộc nội dung thay thế</p>'
        f'<p>2. Kết thúc.{closing}</p>'
        '<p>2. Quy định ngoài trích dẫn.</p><p>Điều 2. Thi hành</p>'
    )
    doc = parse_vbpl_document(html, META)
    assert [a["code"] for a in doc["articles"]] == ["Dieu 1", "Dieu 2"]
    clauses = doc["articles"][0]["clauses"]
    assert [c["code"] for c in clauses] == ["Khoan 1", "Khoan 2"]
    assert "Điều 8. Vẫn thuộc" in clauses[0]["content"]
    assert clauses[0]["content"].endswith(f"Kết thúc.{closing}")
    assert not doc["unresolved_blocks"]


def test_ascii_nested_quote_does_not_close_curly_replacement():
    html = (
        '<p>Điều 1. Sửa đổi</p><p>1. Thay thế:</p>'
        '<p>“Điều 7. Cụm từ "được trích dẫn</p>'
        '<p>Điều 8. Vẫn trong trích dẫn</p><p>kết thúc cụm từ".</p><p>Kết thúc.”</p>'
        '<p>Điều 2. Thi hành</p>'
    )
    doc = parse_vbpl_document(html, META)
    assert [a["code"] for a in doc["articles"]] == ["Dieu 1", "Dieu 2"]
    assert "Điều 8. Vẫn trong trích dẫn" in doc["articles"][0]["clauses"][0]["content"]
    assert not doc["unresolved_blocks"]


def test_quote_closing_in_table_releases_next_article_and_keeps_spans():
    html = (
        '<p>Điều 1. Sửa đổi</p><p>1. Thay thế như sau:</p>'
        '<p>“Điều 9. Bảng thay thế</p>'
        '<table><tr><td rowspan="2"><p>Nội dung</p></td>'
        '<td colspan="2"><p>Kết thúc."</p></td></tr><tr><td>A</td></tr></table>'
        '<p>Điều 2. Thi hành</p><p>1. Có hiệu lực.</p>'
    )
    doc = parse_vbpl_document(html, META)
    assert [a["code"] for a in doc["articles"]] == ["Dieu 1", "Dieu 2"]
    table = doc["source_blocks"][3]
    assert table["kind"] == "table"
    assert 'rowspan="2"' in table["html"] and 'colspan="2"' in table["html"]
    assert len(doc["source_blocks"]) == 6
    assert len(doc["articles"][0]["tables"]) == 1
    assert not doc["unresolved_blocks"]


def test_layout_table_structures_articles_without_duplicate_source_text():
    html = (
        '<p>Mở đầu.</p><table><tr><td colspan="2">'
        '<p>Điều 1. Phạm vi</p><p>1. Nội dung thứ nhất.</p>'
        '<p>2. Nội dung thứ hai.</p></td></tr><tr><td>'
        '<p>Điều 2. Thi hành</p><p>1. Có hiệu lực.</p></td></tr></table>'
    )
    doc = parse_vbpl_document(html, META)
    assert [a["code"] for a in doc["articles"]] == ["Dieu 1", "Dieu 2"]
    assert [len(a["clauses"]) for a in doc["articles"]] == [2, 1]
    assert all(not a["tables"] for a in doc["articles"])
    assert len(doc["source_blocks"]) == 2
    assert sum(b["text"].count("Nội dung thứ nhất.") for b in doc["source_blocks"]) == 1
    assert 'colspan="2"' in doc["source_blocks"][1]["html"]


@pytest.mark.parametrize("paragraphs", [
    ["Nơi nhận:", "- Văn phòng Chính phủ;", "KT. BỘ TRƯỞNG", "Nguyễn Văn A"],
    ["TM. CHÍNH PHỦ", "THỦ TƯỚNG", "(Đã ký)", "Nguyễn Văn B"],
])
def test_plain_administrative_paragraphs_are_separate_from_clause(paragraphs):
    html = '<p>Điều 1. Thi hành</p><p>1. Quy định.</p>'
    html += ''.join(f'<p>{text}</p>' for text in paragraphs)
    doc = parse_vbpl_document(html, META)
    assert doc["articles"][0]["clauses"][0]["content"] == "Quy định."
    assert [b["text"] for b in doc["administrative_blocks"]] == paragraphs
    assert len(doc["source_blocks"]) == 2 + len(paragraphs)


def test_uncertain_plain_continuation_is_unresolved_not_clause_content():
    doc = parse_vbpl_document(
        '<p>Điều 1. Thi hành</p><p>1. Quy định.</p><p>Đoạn chưa rõ cấu trúc.</p>', META,
    )
    assert doc["articles"][0]["clauses"][0]["content"] == "Quy định."
    assert doc["unresolved_blocks"][0]["text"] == "Đoạn chưa rõ cấu trúc."


def test_unclosed_quote_is_explicitly_unresolved_without_reset_at_article():
    html = (
        '<p>Điều 1. Sửa đổi</p><p>1. Thay thế:</p><p>“Điều 7. Chưa đóng</p>'
        '<table><tr><td><p>Nội dung bảng.</p></td></tr></table>'
        '<p>Điều 8. Có thể vẫn là trích dẫn</p>'
    )
    doc = parse_vbpl_document(html, META)
    assert [a["code"] for a in doc["articles"]] == ["Dieu 1"]
    unresolved = [b for b in doc["unresolved_blocks"] if b["reason"] == "unclosed_quotation"]
    assert [b["text"] for b in unresolved] == [b["text"] for b in doc["source_blocks"][2:]]
    assert '"' not in doc["source_blocks"][2]["text"]
    assert doc["source_blocks"][2]["text"] == "“Điều 7. Chưa đóng"


def test_appendix_reference_does_not_start_new_appendix():
    html = (
        '<p>Điều 1. Danh mục</p><p>Phụ lục I ban hành kèm theo Nghị định này.</p>'
        '<p>Điều 2. Thi hành</p><p>PHỤ LỤC I</p><p>Danh mục.</p>'
    )
    doc = parse_vbpl_document(html, META)
    assert [a["code"] for a in doc["articles"]] == ["Dieu 1", "Dieu 2"]
    assert len(doc["appendices"]) == 1
    assert doc["appendices"][0]["title"] == "PHỤ LỤC I"


@pytest.mark.parametrize("filename,appendices", [
    ("04_nd_359_2025.html", 0),
    ("02_nd_181_2025.html", 5),
    ("source_ND_144_2026.snapshot.json", 2),
])
def test_real_sources_preserve_every_top_level_block_including_tables(filename, appendices):
    raw = (RAW / filename).read_text(encoding="utf-8")
    html = json.loads(raw)["html"] if filename.endswith(".json") else raw
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".preview-content") or soup
    elements = [el for el in root.find_all(["p", "table"])
                if el.find_parent("table") is None and normalize_text(el.get_text(" "))]
    doc = parse_vbpl_document(html, META)
    assert len(doc["appendices"]) == appendices
    assert [b["html"] for b in doc["source_blocks"] if b["kind"] in {"paragraph", "table"}] == [str(el) for el in elements]
    assert [b["text"] for b in doc["source_blocks"] if b["kind"] in {"paragraph", "table"}] == [normalize_text(el.get_text(" ")) for el in elements]
    assert [b["index"] for b in doc["source_blocks"]] == list(range(len(doc["source_blocks"])))
    assert normalize_text(" ".join(b["text"] for b in doc["source_blocks"])) == normalize_text(root.get_text(" "))
    for appendix in doc["appendices"]:
        assert len(appendix["blocks"]) > 1
        assert any(b["text"] for b in appendix["blocks"][1:])



def test_real_nd144_annex_row_counts_are_retained():
    snapshot = json.loads((RAW / "source_ND_144_2026.snapshot.json").read_text(encoding="utf-8"))
    doc = parse_vbpl_document(snapshot["html"], META)
    assert [len(BeautifulSoup(b["html"], "html.parser").find_all("tr"))
            for a in doc["appendices"] for b in a["blocks"] if b["kind"] == "table"] == [346, 415]



MOCK_HTML = """
<div class="preview-content">
  <p class="prov-chapter"><b>Chuong I: QUY DINH CHUNG</b></p>
  <p class="prov-article" id="a1"><b>Dieu 1. Pham vi dieu chinh</b></p>
  <p class="prov-clause">1. Luat nay quy dinh ve doi tuong chiu thue gia tri gia tang.</p>
  <p class="prov-clause">2. Luat nay ap dung doi voi:</p>
  <p class="prov-item">a) To chuc, ca nhan san xuat, kinh doanh;</p>
  <p class="prov-item">b) Nguoi nhap khau hang hoa.</p>
  <p class="prov-article" id="a2"><b>Dieu 2. Doi tuong chiu thue</b></p>
  <p class="prov-clause">1. Hang hoa, dich vu su dung cho san xuat, kinh doanh.</p>
</div>
"""

META = {
    "doc_id": "LUAT_48_2024",
    "code": "48/2024/QH15",
    "title": "Luat Thue gia tri gia tang",
    "valid_from": "2025-07-01",
    "valid_to": "9999-12-31",
    "status": "ACTIVE",
}


def test_continuation_paragraphs_are_preserved_in_active_clause():
    html = (
        '<p class="prov-article">Điều 1. Sửa đổi</p>'
        '<p class="prov-clause">1. Bổ sung khoản 3a như sau:</p>'
        '<p class="prov-content">“3a. Bảo hiểm sức khỏe và bảo hiểm cây trồng.</p>'
        '<p class="prov-content">Điều kiện áp dụng phải được xác minh.”</p>'
    )
    doc = parse_vbpl_document(html, META)
    clause = doc["articles"][0]["clauses"][0]
    assert "Bảo hiểm sức khỏe và bảo hiểm cây trồng." in clause["content"]
    assert "Điều kiện áp dụng phải được xác minh.”" in clause["content"]
    assert len(doc["articles"][0]["clauses"]) == 1


def test_multiple_article_leading_paragraphs_are_preserved():
    html = (
        '<p class="prov-article">Điều 1. Phạm vi</p>'
        '<p class="prov-content">Đoạn dẫn thứ nhất.</p>'
        '<p class="prov-content">Đoạn dẫn thứ hai.</p>'
    )
    doc = parse_vbpl_document(html, META)
    assert doc["articles"][0]["lead"] == "Đoạn dẫn thứ nhất. Đoạn dẫn thứ hai."


def test_semantic_clause_without_space_has_its_own_boundary():
    html = (
        '<p class="prov-article">Điều 2. Thi hành</p>'
        '<p class="prov-clause">2. Nội dung khoản hai.</p>'
        '<p class="prov-clause">3.Các Bộ trưởng chịu trách nhiệm thi hành.</p>'
    )
    doc = parse_vbpl_document(html, META)
    clauses = doc["articles"][0]["clauses"]
    assert [clause["code"] for clause in clauses] == ["Khoan 2", "Khoan 3"]
    assert clauses[1]["content"] == "Các Bộ trưởng chịu trách nhiệm thi hành."


def test_decimal_continuation_is_not_a_new_clause():
    html = (
        '<p class="prov-article">Điều 1. Ví dụ</p>'
        '<p class="prov-clause">1. Ví dụ tính toán.</p>'
        '<p class="prov-content">1.500 triệu đồng là số tiền minh họa.</p>'
    )
    doc = parse_vbpl_document(html, META)
    clauses = doc["articles"][0]["clauses"]
    assert len(clauses) == 1
    assert "1.500 triệu đồng" in clauses[0]["content"]


def test_quoted_replacement_does_not_create_outer_points_or_clause_zero():
    html = (
        '<p class="prov-article">Điều 1. Sửa đổi</p>'
        '<p class="prov-content">“1. Nội dung thay thế:</p>'
        '<p class="prov-item">a) Điều kiện thứ nhất;</p>'
        '<p class="prov-item">b) Điều kiện thứ hai.”</p>'
        '<p class="prov-clause">2. Quy định tiếp theo.</p>'
    )
    doc = parse_vbpl_document(html, META)
    article = doc["articles"][0]
    assert [c["code"] for c in article["clauses"]] == ["Khoan 2"]
    assert "a) Điều kiện thứ nhất;" in article["lead"]
    assert "b) Điều kiện thứ hai.”" in article["lead"]


def test_orphan_point_is_preserved_as_unresolved_not_clause_zero():
    doc = parse_vbpl_document('<p>Điều 1. Phạm vi</p><p>a) Điều kiện.</p>', META)
    assert doc["articles"][0]["clauses"] == []
    assert doc["unresolved_blocks"][0]["text"] == "a) Điều kiện."


def test_appendices_keep_content_tables_and_source_order():
    html = (
        '<p>Điều 1. Thi hành</p><p>1. Nội dung.</p>'
        '<p>PHỤ LỤC I</p><p>Danh mục thứ nhất.</p>'
        '<table><tr><td>Mã</td><td rowspan="2">A</td></tr>'
        '<tr><td>01</td></tr></table>'
        '<p>PHỤ LỤC II</p><p>Ghi chú cuối.</p>'
    )
    doc = parse_vbpl_document(html, META)
    assert len(doc["appendices"]) == 2
    first, second = doc["appendices"]
    assert first["id"] == "LUAT_48_2024_PL1"
    assert first["blocks"][0]["text"] == "PHỤ LỤC I"
    assert first["blocks"][2]["kind"] == "table"
    assert 'rowspan="2"' in first["blocks"][2]["html"]
    assert second["blocks"][-1]["text"] == "Ghi chú cuối."
    assert [b["index"] for b in doc["source_blocks"]] == list(range(7))
    assert doc["articles"][0]["tables"] == []


def test_signature_table_is_not_an_article_rule_or_duplicated_paragraph():
    html = (
        '<p>Điều 1. Thi hành</p><p>1. Nội dung.</p>'
        '<table><tr><td><p>Nơi nhận:</p></td>'
        '<td><p>KT. BỘ TRƯỞNG</p></td></tr></table>'
    )
    doc = parse_vbpl_document(html, META)
    assert doc["articles"][0]["tables"] == []
    assert len(doc["source_blocks"]) == 3
    assert doc["administrative_blocks"][0]["kind"] == "table"


def test_real_nd144_preserves_both_annexes_and_replacement_text():
    import json
    from pathlib import Path

    snapshot = json.loads(Path("data/raw/source_ND_144_2026.snapshot.json").read_text(encoding="utf-8"))
    doc = parse_vbpl_document(snapshot["html"], {**META, "doc_id": "ND_144_2026"})
    assert len(doc["appendices"]) == 2
    assert [len(a["blocks"]) > 1 for a in doc["appendices"]] == [True, True]
    assert "Bảo hiểm nhân thọ" in doc["articles"][0]["clauses"][0]["content"]
    assert all(c["code"] != "Khoan 0" for a in doc["articles"] for c in a["clauses"])
    assert [len(__import__("bs4").BeautifulSoup(b["html"], "html.parser").find_all("tr"))
            for a in doc["appendices"] for b in a["blocks"] if b["kind"] == "table"] == [346, 415]


def test_parse_document_hierarchy():
    doc = parse_vbpl_document(MOCK_HTML, META)
    assert doc["id"] == "LUAT_48_2024"
    assert doc["valid_from"] == "2025-07-01"
    assert doc["valid_to"] == "9999-12-31"
    assert len(doc["articles"]) == 2
    art = doc["articles"][0]
    assert art["code"] == "Dieu 1"
    assert art["title"] == "Pham vi dieu chinh"
    assert art["valid_from"] == "2025-07-01"
    assert len(art["clauses"]) == 2
    assert art["clauses"][0]["code"] == "Khoan 1"
    assert len(art["clauses"][1]["points"]) == 2
    assert art["clauses"][1]["points"][0]["code"] == "Diem a"
    assert art["clauses"][1]["points"][0]["id"] == "LUAT_48_2024_D1_K2_a"


def test_parse_regex_fallback_without_classes():
    html = (
        "<div><p><b>Dieu 9. Thue suat 0%</b></p>"
        "<p>1. Dich vu xuat khau.</p>"
        "<p>a) Co hop dong ky voi to chuc nuoc ngoai;</p></div>"
    )
    doc = parse_vbpl_document(html, META)
    assert len(doc["articles"]) == 1
    assert doc["articles"][0]["code"] == "Dieu 9"
    assert len(doc["articles"][0]["clauses"]) == 1
    assert len(doc["articles"][0]["clauses"][0]["points"]) == 1


def test_parse_table_preserved_as_markdown():
    html = (
        "<div><p><b>Dieu 3. Bieu thue</b></p>"
        "<table><tr><th>Mat hang</th><th>Thue suat</th></tr>"
        "<tr><td>Xuat khau</td><td>0%</td></tr></table></div>"
    )
    doc = parse_vbpl_document(html, META)
    tables = doc["articles"][0]["tables"]
    assert len(tables) == 1
    assert "Mat hang" in tables[0] and "0%" in tables[0]


def test_parse_appendix_boundary_stops_attachment():
    html = (
        "<div>"
        "<p><b>Dieu 40. Hieu luc thi hanh</b></p>"
        "<p>1. Co hieu luc tu ngay 01/07/2025.</p>"
        "<p>PHU LUC I</p>"
        "<p>1. Bang danh muc mat hang.</p>"
        "<table><tr><td>Bang 1</td></tr></table>"
        "</div>"
    )
    doc = parse_vbpl_document(html, META)
    assert len(doc["articles"]) == 1
    art = doc["articles"][0]
    assert len(art["clauses"]) == 1
    assert art["clauses"][0]["id"] == "LUAT_48_2024_D40_K1"
    assert len(art["tables"]) == 0


def test_titled_appendix_keeps_provisions_out_of_last_article():
    html = (
        '<p>Điều 1. Thi hành</p><p>1. Quy định.</p>'
        '<p>PHỤ LỤC I: DANH MỤC HÀNG HÓA</p>'
        '<p>1. Hàng hóa A.</p><table><tr><td>Bảng phụ lục</td></tr></table>'
    )
    doc = parse_vbpl_document(html, META)
    assert len(doc["appendices"]) == 1
    assert len(doc["articles"][0]["clauses"]) == 1
    assert doc["articles"][0]["tables"] == []
    assert "Hàng hóa A." in doc["appendices"][0]["blocks"][1]["text"]


@pytest.mark.parametrize("role", ["Bộ trưởng Bộ Tài chính", "Thủ tướng Chính phủ"])
def test_role_led_duty_is_not_a_signature(role):
    html = (
        '<p>Điều 1. Trách nhiệm</p>'
        f'<p class="prov-content">{role} quy định hồ sơ, thủ tục.</p>'
        '<p class="prov-content">Cơ quan thuế tiếp nhận hồ sơ.</p>'
    )
    doc = parse_vbpl_document(html, META)
    assert f"{role} quy định" in doc["articles"][0]["lead"]
    assert "Cơ quan thuế tiếp nhận" in doc["articles"][0]["lead"]
    assert doc["administrative_blocks"] == []


def load_source(name):
    return json.loads((RAW / f"source_{name}.snapshot.json").read_text(encoding="utf-8"))["html"]


@pytest.mark.parametrize("heading", ["Điều 10 . Tiêu chuẩn", "Điều 10. Tiêu chuẩn"])
def test_spaced_article_separator_has_legal_identity(heading):
    doc = parse_vbpl_document(f'<p class="prov-article">{heading}</p>', META)
    assert doc["articles"][0]["id"] == f'{META["doc_id"]}_D10'
    assert doc["articles"][0]["title"] == "Tiêu chuẩn"


def test_real_tt84_article_ten_has_identity():
    doc = parse_vbpl_document(load_source("TT_84_2026"), META)
    article = next(a for a in doc["articles"] if a["code"] == "Dieu 10")
    assert article["id"] == f'{META["doc_id"]}_D10'
    assert article["title"].startswith("Tiêu chuẩn hồ sơ")
    assert all(a["id"] != f'{META["doc_id"]}_D' for a in doc["articles"])


@pytest.mark.parametrize("marker", ["6 .", "12 .", "1)"])
def test_semantic_clause_marker_keeps_number_and_body(marker):
    html = ('<p>Điều 1. Phạm vi</p><p class="prov-clause">5. Trước.</p>'
            f'<p class="prov-clause">{marker} Nội dung.</p>')
    clauses = parse_vbpl_document(html, META)["articles"][0]["clauses"]
    assert [c["code"] for c in clauses] == ["Khoan 5", f'Khoan {marker.split()[0].rstrip(")")}']
    assert clauses[-1]["content"] == "Nội dung."
    assert clauses[0]["content"] == "Trước."


def test_real_law108_spaced_clauses_six_and_twelve():
    doc = parse_vbpl_document(load_source("LUAT_108_2025"), META)
    article = next(a for a in doc["articles"] if a["code"] == "Dieu 4")
    clauses = {c["code"]: c["content"] for c in article["clauses"]}
    assert clauses["Khoan 6"].startswith("Trụ sở của người nộp thuế")
    assert clauses["Khoan 12"].startswith("Quản lý rủi ro trong quản lý thuế")
    assert "6 ." not in clauses["Khoan 5"]
    assert "12 ." not in clauses["Khoan 11"]


def test_real_tt72_parenthesized_semantic_clauses():
    doc = parse_vbpl_document(load_source("TT_72_2014"), META)
    article = next(a for a in doc["articles"] if a["code"] == "Dieu 2")
    assert [c["code"] for c in article["clauses"]] == [f"Khoan {n}" for n in range(1, 6)]
    assert article["clauses"][0]["content"].startswith("Người nước ngoài")
    assert "1) Người" not in article["lead"]


def test_real_nq05_forms_do_not_create_outer_articles():
    html = load_source("NQ_05_2025")
    doc = parse_vbpl_document(html, {**META, "doc_id": "NQ_05_2025"})
    assert [a["code"] for a in doc["articles"]] == [f"Dieu {n}" for n in range(1, 20)]
    assert len({a["id"] for a in doc["articles"]}) == 19
    assert len(doc["forms"]) == 8
    seventh = next(f for f in doc["forms"] if f["title"].startswith("Mẫu số 07."))
    assert [b["text"].split(".")[0] for b in seventh["blocks"] if b["text"].startswith("Điều ")] == [f"Điều {n}" for n in range(1, 6)]
    assert any(b["reason"] == "unknown_form_boundary" and "BẢN CÁO BẠCH" in b["text"]
               for b in doc["unresolved_blocks"])
    root = BeautifulSoup(html, "html.parser").select_one(".preview-content")
    elements = [e for e in root.find_all(["p", "table"])
                if not e.find_parent("table") and normalize_text(e.get_text(" "))]
    assert [b["html"] for b in doc["source_blocks"] if b["kind"] in {"paragraph", "table"}] == [str(e) for e in elements]
    assert [b["index"] for b in doc["source_blocks"]] == list(range(len(doc["source_blocks"])))
    assert normalize_text(" ".join(b["text"] for b in doc["source_blocks"])) == normalize_text(root.get_text(" "))


def test_forms_have_independent_stable_containers_and_keep_signatures():
    html = ('<p>Điều 1. Thi hành</p><p>Nơi nhận:</p>'
            '<p>Mẫu số 07. Giấy phép</p><p>Điều 1. Cấp phép</p>'
            '<table class="document-recipient-signature"><tr><td>CHỦ TỊCH QUỐC HỘI</td></tr></table>'
            '<p>Mẫu số 08. Đề nghị</p><p>Điều 1. Nội dung mẫu khác</p>')
    doc = parse_vbpl_document(html, META)
    assert len(doc["articles"]) == 1
    assert len(doc["forms"]) == 2
    first, second = doc["forms"]
    assert first["id"] != second["id"]
    assert first["blocks"][1]["text"] == "Điều 1. Cấp phép"
    assert "CHỦ TỊCH QUỐC HỘI" in first["tables"][0]
    assert second["blocks"][1]["text"] == "Điều 1. Nội dung mẫu khác"
    assert len(doc["administrative_blocks"]) == 1
    again = parse_vbpl_document(html, META)
    assert [f["id"] for f in again["forms"]] == [f["id"] for f in doc["forms"]]


def test_ambiguous_repeated_article_is_unresolved_without_invented_identity():
    html = ('<p>Điều 1. Quy định</p><p>1. Nội dung luật.</p>'
            '<p>Điều 1. Chưa biết có phải mẫu</p><p>1. Không gán vào luật.</p>'
            '<p>Điều 2. Cũng chưa rõ</p>')
    doc = parse_vbpl_document(html, META)
    assert [a["code"] for a in doc["articles"]] == ["Dieu 1"]
    assert doc["articles"][0]["clauses"][0]["content"] == "Nội dung luật."
    assert [b["text"] for b in doc["unresolved_blocks"]] == [b["text"] for b in doc["source_blocks"][2:]]
    assert all(b["reason"] == "duplicate_article_identity" for b in doc["unresolved_blocks"])
    assert doc["forms"] == []


def test_form_reference_in_rule_is_not_a_form_boundary():
    doc = parse_vbpl_document('<p>Điều 1. Hồ sơ</p><p class="prov-content">Mẫu số 07 được sử dụng để kê khai.</p>', META)
    assert doc.get("forms", []) == []
    assert "Mẫu số 07" in doc["articles"][0]["lead"]


@pytest.mark.parametrize("name", ["LUAT_149_2025", "LUAT_90_2025", "LUAT_108_2025"])
def test_real_law_signature_table_is_administrative_not_a_rule(name):
    html = load_source(name)
    doc = parse_vbpl_document(html, META)
    soup = BeautifulSoup(html, "html.parser")
    signature = soup.select_one("table.document-recipient-signature")
    assert "CHỦ TỊCH QUỐC HỘI" in normalize_text(signature.get_text(" "))
    assert any(b["html"] == str(signature) for b in doc["administrative_blocks"])
    assert all("CHỦ TỊCH QUỐC HỘI" not in table for a in doc["articles"] for table in a["tables"])


@pytest.mark.parametrize("semantic", [True, False])
def test_national_assembly_signature_is_administrative(semantic):
    css = ' class="document-recipient-signature"' if semantic else ''
    html = ('<p>Điều 1. Thi hành</p><p>1. Quy định.</p>'
            f'<table{css}><tr><td><p>CHỦ TỊCH QUỐC HỘI</p><p>Nguyễn Văn A</p></td></tr></table>')
    doc = parse_vbpl_document(html, META)
    assert doc["articles"][0]["tables"] == []
    assert len(doc["administrative_blocks"]) == 1


def test_semantic_signature_table_without_role_text_is_administrative():
    html = ('<p>Điều 1. Thi hành</p>'
            '<table class="document-recipient-signature"><tr><td>Người ký</td></tr></table>')
    doc = parse_vbpl_document(html, META)
    assert doc["articles"][0]["tables"] == []
    assert len(doc["administrative_blocks"]) == 1


@pytest.mark.parametrize("boundary", ["PHỤ LỤC I", "Mẫu số 07. Giấy phép"])
def test_signature_table_inside_attachment_stays_in_attachment(boundary):
    html = ('<p>Điều 1. Thi hành</p><p>Nơi nhận:</p>'
            f'<p>{boundary}</p><table class="document-recipient-signature">'
            '<tr><td><p>CHỦ TỊCH QUỐC HỘI</p></td></tr></table>')
    doc = parse_vbpl_document(html, META)
    containers = doc["appendices"] if boundary.startswith("PHỤ") else doc["forms"]
    assert len(containers) == 1
    assert "CHỦ TỊCH QUỐC HỘI" in containers[0]["tables"][0]
    assert len(doc["administrative_blocks"]) == 1


@pytest.mark.parametrize("name", ["NQ_05_2025", "TT_84_2026", "TT_72_2014", "LUAT_108_2025", "LUAT_149_2025", "LUAT_90_2025"])
def test_confirmed_sources_keep_raw_block_html_and_order(name):
    html = load_source(name)
    root = BeautifulSoup(html, "html.parser").select_one(".preview-content")
    elements = [e for e in root.find_all(["p", "table"])
                if not e.find_parent("table") and normalize_text(e.get_text(" "))]
    doc = parse_vbpl_document(html, META)
    assert [b["html"] for b in doc["source_blocks"] if b["kind"] in {"paragraph", "table"}] == [str(e) for e in elements]
    assert [b["text"] for b in doc["source_blocks"] if b["kind"] in {"paragraph", "table"}] == [normalize_text(e.get_text(" ")) for e in elements]
    assert [b["index"] for b in doc["source_blocks"]] == list(range(len(doc["source_blocks"])))
    assert normalize_text(" ".join(b["text"] for b in doc["source_blocks"])) == normalize_text(root.get_text(" "))
    assert len({a["id"] for a in doc["articles"]}) == len(doc["articles"])


@pytest.mark.parametrize("filename", [
    "01_luat_48_2024.html", "02_nd_181_2025.html", "03_tt_69_2025.html",
    "04_nd_359_2025.html", "05_nd_144_2026.html",
])
def test_all_five_raw_sources_parse_without_source_block_loss(filename):
    html = (RAW / filename).read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".preview-content") or soup
    elements = [e for e in root.find_all(["p", "table"])
                if not e.find_parent("table") and normalize_text(e.get_text(" "))]
    doc = parse_vbpl_document(html, META)
    assert doc["articles"]
    assert [b["html"] for b in doc["source_blocks"] if b["kind"] in {"paragraph", "table"}] == [str(e) for e in elements]
    assert [b["index"] for b in doc["source_blocks"]] == list(range(len(doc["source_blocks"])))
    assert normalize_text(" ".join(b["text"] for b in doc["source_blocks"])) == normalize_text(root.get_text(" "))
    assert len({a["id"] for a in doc["articles"]}) == len(doc["articles"])


def test_semantic_decimal_continuation_does_not_gain_clause_identity():
    doc = parse_vbpl_document('<p>Điều 1. Ví dụ</p><p class="prov-clause">1. Ví dụ.</p>'
                              '<p class="prov-clause">1.500 triệu đồng.</p>', META)
    assert len(doc["articles"][0]["clauses"]) == 1
    assert "1.500 triệu đồng." in doc["articles"][0]["clauses"][0]["content"]


@pytest.mark.parametrize("content", [
    "%PDF-1.7 scanned document",
    "PK\u0003\u0004archive payload",
    '{"text": "Điều 1. Văn bản chỉ có text"}',
    "Điều 1. Văn bản không có HTML",
    "<div>Điều 1. Chưa có adapter cho khối này.</div>",
])
def test_unsupported_source_format_is_not_reported_as_empty_success(content):
    with pytest.raises(ValueError, match="HTML.*p.*table"):
        parse_vbpl_document(content, META)


@pytest.mark.parametrize("wrapper", ["div", "span"])
def test_mixed_html_preserves_stray_text_without_inventing_hierarchy(wrapper):
    html = (
        '<div class="preview-content">Before &amp; after<!-- ignored -->'
        '<script>hidden script</script><style>hidden style</style> \n'
        '<p>Điều 1. Phạm vi</p>'
        f'<{wrapper}><b>Điều 99. Không suy diễn</b></{wrapper}>'
        '<p>1. Quy định.</p><div>Trước <p>Đoạn chưa rõ.</p> Sau</div>'
        '<table><tr><td><p>Trong bảng.</p><span>Chỉ một lần.</span></td></tr></table>'
        'Cuối.</div>'
    )
    doc = parse_vbpl_document(html, META)
    blocks = doc["source_blocks"]
    assert [b["text"] for b in blocks] == [
        "Before & after", "Điều 1. Phạm vi", "Điều 99. Không suy diễn",
        "1. Quy định.", "Trước", "Đoạn chưa rõ.", "Sau",
        "Trong bảng. Chỉ một lần.", "Cuối.",
    ]
    assert [b["index"] for b in blocks] == list(range(9))
    assert blocks[0]["html"] == "Before &amp; after"
    assert blocks[2]["html"] == f'<{wrapper}><b>Điều 99. Không suy diễn</b></{wrapper}>'
    assert [a["code"] for a in doc["articles"]] == ["Dieu 1"]
    assert doc["articles"][0]["clauses"][0]["content"] == "Quy định."
    unsupported = [b for b in doc["unresolved_blocks"] if b["reason"] == "unsupported_source_block"]
    assert [b["index"] for b in unsupported] == [0, 2, 4, 6, 8]
    assert all(b == {**blocks[b["index"]], "article_id": b["article_id"],
                     "reason": "unsupported_source_block"} for b in unsupported)


def test_layout_table_keeps_stray_descendants_once_in_source_blocks():
    html = ('<div>Trước.</div><table><tr><td><span>Nhãn.</span>'
            '<p>Điều 1. Phạm vi</p><p>1. Nội dung.</p>'
            '<table><tr><td>Bảng con.</td></tr></table></td></tr></table><span>Sau.</span>')
    doc = parse_vbpl_document(html, META)
    assert [b["text"] for b in doc["source_blocks"]] == [
        "Trước.", "Nhãn. Điều 1. Phạm vi 1. Nội dung. Bảng con.", "Sau.",
    ]
    assert [b["index"] for b in doc["source_blocks"]] == [0, 1, 2]
    assert doc["articles"][0]["clauses"][0]["content"] == "Nội dung."


def test_real_tt41_preserves_visible_text_and_stray_form_labels():
    html = load_source("TT_41_2026")
    root = BeautifulSoup(html, "html.parser").select_one(".preview-content")
    doc = parse_vbpl_document(html, META)
    blocks = doc["source_blocks"]
    assert normalize_text(" ".join(b["text"] for b in blocks)) == normalize_text(root.get_text(" "))
    assert [b["index"] for b in blocks] == list(range(len(blocks)))
    elements = [e for e in root.find_all(["p", "table"])
                if not e.find_parent(["p", "table"]) and normalize_text(e.get_text(" "))]
    assert [b["html"] for b in blocks if b["kind"] in {"paragraph", "table"}] == [str(e) for e in elements]
    labels = [b for b in blocks if "[02] Lần đầu" in b["text"] or "[03] Bổ sung" in b["text"]]
    assert len(labels) == 2
    assert sum(b["text"].count("[02] Lần đầu") for b in labels) == 2
    assert sum(b["text"].count("[03] Bổ sung") for b in labels) == 2
    assert all(any(u.get("index") == b["index"] and u["reason"] == "unsupported_source_block"
                   for u in doc["unresolved_blocks"]) for b in labels)


def test_clause_id_deduplication():
    html = (
        "<div>"
        "<p><b>Dieu 1. Pham vi</b></p>"
        "<p>1. Khoan mot.</p>"
        "<p>1. Khoan mot bi lap.</p>"
        "</div>"
    )
    doc = parse_vbpl_document(html, META)
    art = doc["articles"][0]
    assert len(art["clauses"]) == 2
    ids = [c["id"] for c in art["clauses"]]
    assert len(ids) == len(set(ids))
    assert ids[0] == "LUAT_48_2024_D1_K1"
    assert ids[1] != ids[0]


def test_point_id_deduplication():
    html = (
        "<div>"
        "<p><b>Dieu 1. Pham vi</b></p>"
        "<p>1. Khoan mot.</p>"
        "<p>a) Diem a.</p>"
        "<p>a) Diem a bi lap.</p>"
        "</div>"
    )
    doc = parse_vbpl_document(html, META)
    clause = doc["articles"][0]["clauses"][0]
    assert len(clause["points"]) == 2
    pids = [p["id"] for p in clause["points"]]
    assert len(pids) == len(set(pids))
    assert pids[0] == "LUAT_48_2024_D1_K1_a"
    assert pids[1] != pids[0]

