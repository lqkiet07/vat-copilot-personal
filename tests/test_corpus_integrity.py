import json
from pathlib import Path
from src.parser.vbpl_parser import parse_vbpl_document
from scripts.validate_corpus import validate_corpus, validate_document

META = {
    "doc_id": "TEST_DOC",
    "code": "01/2025/ND-CP",
    "title": "Nghi dinh kiem tra",
    "valid_from": "2025-07-01",
    "valid_to": "9999-12-31",
    "status": "ACTIVE",
}


def test_parser_stops_at_appendix_boundary():
    """Verify parser stops at appendix boundary and does not attach appendix clauses/tables to Điều 40."""
    html = """
    <div class="preview-content">
      <p class="prov-article"><b>Dieu 40. Hieu luc thi hanh</b></p>
      <p class="prov-clause">1. Nghi dinh nay co hieu luc thi hanh tu ngay 01 thang 7 nam 2025.</p>
      <p class="prov-clause">2. Cac bo, nganh chiu trach nhiem thi hanh.</p>
      <p><b>PHU LUC I</b></p>
      <p class="prov-clause">1. Hang hoa thuoc dien ap dung bieu thue dac biet.</p>
      <table>
        <tr><th>Ma hang</th><th>Mo ta</th></tr>
        <tr><td>28.23.00.00.40</td><td>San pham quang</td></tr>
      </table>
    </div>
    """
    doc = parse_vbpl_document(html, META)
    assert len(doc["articles"]) == 1
    art = doc["articles"][0]
    assert art["code"] == "Dieu 40"
    assert len(art["clauses"]) == 2
    clause_ids = [c["id"] for c in art["clauses"]]
    assert clause_ids == ["TEST_DOC_D40_K1", "TEST_DOC_D40_K2"]
    assert len(art["tables"]) == 0


def test_clause_id_uniqueness_when_numbering_repeats():
    """Verify clause IDs within an article are guaranteed unique even when numbers repeat."""
    html = """
    <div class="preview-content">
      <p class="prov-article"><b>Dieu 1. Pham vi</b></p>
      <p class="prov-clause">1. Khoan 1 ban dau.</p>
      <p class="prov-clause">1. Khoan 1 trung lap.</p>
      <p class="prov-clause">2. Khoan 2 ke tiep.</p>
    </div>
    """
    doc = parse_vbpl_document(html, META)
    art = doc["articles"][0]
    assert len(art["clauses"]) == 3
    clause_ids = [c["id"] for c in art["clauses"]]
    assert len(clause_ids) == len(set(clause_ids)), f"Duplicate clause IDs found: {clause_ids}"
    assert clause_ids[0] == "TEST_DOC_D1_K1"
    assert clause_ids[1] != clause_ids[0]
    assert "TEST_DOC_D1_K1" in clause_ids[1]


def test_nd_181_2025_d40_integrity():
    """Verify ND_181_2025_D40 has only real clauses (K1, K2, K3), NO K2823, and exactly 1 K1."""
    corpus_path = Path("data/processed/vat_corpus.json")
    assert corpus_path.exists(), "vat_corpus.json must exist"
    with open(corpus_path, encoding="utf-8") as f:
        corpus = json.load(f)

    nd181 = next((d for d in corpus if d["id"] == "ND_181_2025"), None)
    assert nd181 is not None, "ND_181_2025 document not found in corpus"

    d40 = next((a for a in nd181["articles"] if a["id"] == "ND_181_2025_D40"), None)
    assert d40 is not None, "ND_181_2025_D40 not found"

    clause_ids = [c["id"] for c in d40["clauses"]]
    assert clause_ids == [
        "ND_181_2025_D40_K1",
        "ND_181_2025_D40_K2",
        "ND_181_2025_D40_K3",
    ], f"Unexpected clause IDs in D40: {clause_ids}"
    assert clause_ids.count("ND_181_2025_D40_K1") == 1
    assert not any("2823" in cid for cid in clause_ids)


def test_validate_corpus_passes():
    """Verify validate_corpus passes on vat_corpus.json with 0 errors."""
    corpus_path = Path("data/processed/vat_corpus.json")
    assert corpus_path.exists(), "vat_corpus.json must exist"
    with open(corpus_path, encoding="utf-8") as f:
        corpus = json.load(f)

    errors = validate_corpus(corpus)
    assert errors == [], f"Validation errors found: {errors}"
