from src.parser.vbpl_parser import parse_vbpl_document

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
