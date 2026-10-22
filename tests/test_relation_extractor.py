from src.parser.relation_extractor import extract_amends_from_article


def test_extract_amends_nd359_dieu1():
    article = {
        "id": "ND_359_2025_D1",
        "code": "Dieu 1",
        "title": "Sửa đổi, bổ sung một số điều của Nghị định số 181/2025/NĐ-CP",
        "clauses": [
            {"id": "ND_359_2025_D1_K1", "code": "Khoan 1",
             "content": "Bổ sung khoản 1b sau khoản 1 Điều 4 như sau:", "points": []},
            {"id": "ND_359_2025_D1_K2", "code": "Khoan 2",
             "content": "Bãi bỏ khoản 3 Điều 37 và khoản 3 Điều 39.", "points": []},
        ],
    }
    rels = extract_amends_from_article("ND_359_2025", article)
    targets = {(r["target_doc"], r["target_article"]) for r in rels}
    assert ("ND_181_2025", "Dieu 4") in targets
    assert ("ND_181_2025", "Dieu 37") in targets
    assert ("ND_181_2025", "Dieu 39") in targets
    assert all(r["source_article"] == "ND_359_2025_D1" for r in rels)


def test_extract_amends_nd144_inherit_title():
    article = {
        "id": "ND_144_2026_D2",
        "code": "Dieu 2",
        "title": "Sửa đổi, bổ sung điểm b khoản 1 Điều 9",
        "clauses": [],
    }
    rels = extract_amends_from_article("ND_144_2026", article)
    assert {(r["target_doc"], r["target_article"]) for r in rels} == {("ND_181_2025", "Dieu 9")}


def test_extract_cites_internal_and_cross_doc():
    from src.parser.relation_extractor import extract_cites_relations

    cites = extract_cites_relations(
        "LUAT_48_2024", "LUAT_48_2024_D5_K27",
        "được áp dụng mức thuế suất 0% quy định tại khoản 1 Điều 9 của Luật này.")
    assert len(cites) == 1
    assert cites[0]["target_doc"] == "LUAT_48_2024"
    assert cites[0]["target_article"] == "Dieu 9"
    assert cites[0]["target_clause"] == "Khoan 1"

    cites2 = extract_cites_relations(
        "ND_181_2025", "ND_181_2025_D3_K1",
        "Người nộp thuế quy định tại khoản 1 Điều 4 Luật Thuế giá trị gia tăng.")
    assert len(cites2) == 1
    assert cites2[0]["target_doc"] == "LUAT_48_2024"
    assert cites2[0]["target_article"] == "Dieu 4"
