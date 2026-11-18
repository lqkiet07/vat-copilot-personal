import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.parser.relation_extractor import extract_amends_from_article, extract_cites_relations


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


def test_extract_amends_preserves_clause_and_point_scope_without_whole_article_broadening():
    article = {
        "id": "ND_359_2025_D1",
        "code": "Dieu 1",
        "title": "Sửa đổi, bổ sung một số điều của Nghị định số 181/2025/NĐ-CP",
        "clauses": [
            {
                "id": "ND_359_2025_D1_K1",
                "code": "Khoan 1",
                "content": "Sửa đổi, bổ sung khoản 2 Điều 4 như sau:",
                "points": [],
            },
            {
                "id": "ND_359_2025_D1_K2",
                "code": "Khoan 2",
                "content": "Bãi bỏ khoản 3 Điều 37 và khoản 3 Điều 39.",
                "points": [],
            },
            {
                "id": "ND_144_2026_D1_K1",
                "code": "Khoan 3",
                "content": "Sửa đổi, bổ sung điểm b khoản 1 Điều 9 như sau:",
                "points": [],
            },
        ],
    }
    rels = extract_amends_from_article("ND_359_2025", article)
    by_article = {r["target_article"]: r for r in rels}
    assert by_article["Dieu 4"]["target_clause"] == "Khoan 2"
    assert by_article["Dieu 4"]["target_scope"] == "clause"
    assert by_article["Dieu 37"]["target_clause"] == "Khoan 3"
    assert by_article["Dieu 37"]["action"] == "bai_bo"
    assert by_article["Dieu 39"]["target_clause"] == "Khoan 3"
    assert by_article["Dieu 9"]["target_clause"] == "Khoan 1"
    assert by_article["Dieu 9"]["target_point"] == "Diem b"
    assert by_article["Dieu 9"]["target_scope"] == "point"


def test_extract_amends_quarantines_unresolved_subarticle_reference():
    article = {
        "id": "ND_359_2025_D1",
        "code": "Dieu 1",
        "title": "Sửa đổi, bổ sung một số điều của Nghị định số 181/2025/NĐ-CP",
        "clauses": [
            {
                "id": "ND_359_2025_D1_K9",
                "code": "Khoan 9",
                "content": "Sửa đổi một số khoản của Điều 19 như sau:",
                "points": [],
            }
        ],
    }
    rels = extract_amends_from_article("ND_359_2025", article)
    assert len(rels) == 1
    assert rels[0]["target_article"] == "Dieu 19"
    assert rels[0]["target_scope"] == "unresolved_subarticle"
    assert rels[0]["resolved"] is False


def test_importer_dry_run_quarantines_unresolved_amendments_without_neo4j(tmp_path):
    import json
    from src.graph.importer import plan_corpus_import

    corpus = [
        {
            "id": "ND_181_2025",
            "code": "181/2025/ND-CP",
            "title": "Nghi dinh 181",
            "valid_from": "2025-07-01",
            "valid_to": "9999-12-31",
            "chapters": [],
            "articles": [
                {
                    "id": "ND_181_2025_D9",
                    "code": "Dieu 9",
                    "title": "Thue suat",
                    "content": "Dieu 9",
                    "valid_from": "2025-07-01",
                    "valid_to": "9999-12-31",
                    "clauses": [
                        {
                            "id": "ND_181_2025_D9_K1",
                            "code": "Khoan 1",
                            "content": "Khoan 1",
                            "valid_from": "2025-07-01",
                            "valid_to": "9999-12-31",
                            "points": [
                                {
                                    "id": "ND_181_2025_D9_K1_b",
                                    "code": "Diem b",
                                    "content": "Diem b",
                                    "valid_from": "2025-07-01",
                                    "valid_to": "9999-12-31",
                                }
                            ],
                        }
                    ],
                },
                {
                    "id": "ND_181_2025_D19",
                    "code": "Dieu 19",
                    "title": "Khau tru",
                    "content": "Dieu 19",
                    "valid_from": "2025-07-01",
                    "valid_to": "9999-12-31",
                    "clauses": [],
                },
            ],
        },
        {
            "id": "ND_144_2026",
            "code": "144/2026/ND-CP",
            "title": "Nghi dinh 144",
            "valid_from": "2026-06-20",
            "valid_to": "9999-12-31",
            "chapters": [],
            "articles": [
                {
                    "id": "ND_144_2026_D1",
                    "code": "Dieu 1",
                    "title": "Sửa đổi, bổ sung một số điều của Nghị định số 181/2025/NĐ-CP",
                    "content": "Dieu 1",
                    "valid_from": "2026-06-20",
                    "valid_to": "9999-12-31",
                    "clauses": [
                        {
                            "id": "ND_144_2026_D1_K1",
                            "code": "Khoan 1",
                            "content": "Sửa đổi, bổ sung điểm b khoản 1 Điều 9 như sau:",
                            "valid_from": "2026-06-20",
                            "valid_to": "9999-12-31",
                            "points": [],
                        },
                        {
                            "id": "ND_144_2026_D1_K2",
                            "code": "Khoan 2",
                            "content": "Sửa đổi một số khoản của Điều 19 như sau:",
                            "valid_from": "2026-06-20",
                            "valid_to": "9999-12-31",
                            "points": [],
                        },
                    ],
                }
            ],
        },
    ]
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
    plan = plan_corpus_import(str(p))
    assert plan["stats"] == {"documents": 2, "chapters": 0, "articles": 3, "clauses": 3, "points": 1}
    assert len(plan["resolved_amends"]) == 1
    assert plan["resolved_amends"][0]["target_scope"] == "point"
    assert plan["resolved_amends"][0]["target_point"] == "Diem b"
    assert len(plan["unresolved_amends"]) == 1
    assert plan["unresolved_amends"][0]["target_article"] == "Dieu 19"


def test_explicit_numbered_document_beats_legal_family_alias():
    from src.parser.relation_extractor import extract_cites_relations

    cites = extract_cites_relations(
        "ND_181_2025", "ND_181_2025_D3_K1",
        "theo quy định tại khoản 1 Điều 3 Nghị định số 68/2026/NĐ-CP."
    )
    assert len(cites) == 1
    assert cites[0]["target_doc"] == "ND_68_2026"
    assert cites[0]["target_article"] == "Dieu 3"
    assert cites[0]["target_clause"] == "Khoan 1"
    assert cites[0]["resolved"] is True


def test_unknown_explicit_document_never_falls_back_to_source():
    from src.parser.relation_extractor import extract_cites_relations

    cites = extract_cites_relations(
        "ND_181_2025", "ND_181_2025_D3_K1",
        "căn cứ theo quy định tại Điều 4 Nghị định số 999/2030/NĐ-CP."
    )
    assert len(cites) == 1
    assert cites[0]["resolved"] is False
    assert cites[0]["target_doc"] is None
    assert cites[0]["unresolved_reason"] == "unknown_document"


def test_self_reference_and_bare_reference_remain_internal():
    from src.parser.relation_extractor import extract_cites_relations

    cites = extract_cites_relations(
        "ND_181_2025", "ND_181_2025_D3_K1",
        "thực hiện theo quy định tại Điều 4 Nghị định này và tại Điều 5."
    )
    assert len(cites) == 2
    assert all(c["target_doc"] == "ND_181_2025" for c in cites)
    assert all(c["resolved"] is True for c in cites)


def test_nd141_article_targets_are_not_document_wide():
    d1 = {
        "id": "ND_141_2026_D1",
        "code": "Dieu 1",
        "title": "Sửa đổi, bổ sung một số điều của Nghị định số 68/2026/NĐ-CP ngày 05 tháng 3 năm 2026",
        "clauses": [
            {
                "id": "ND_141_2026_D1_K2",
                "code": "Khoan 2",
                "content": "Sửa đổi, bổ sung khoản 5 Điều 8 như sau:",
                "points": [],
            }
        ],
    }
    d2 = {
        "id": "ND_141_2026_D2",
        "code": "Dieu 2",
        "title": "Bổ sung khoản 15 Điều 4 Nghị định số 320/2025/NĐ-CP ngày 15 tháng 12 năm 2025",
        "clauses": [
            {
                "id": "ND_141_2026_D2_K1",
                "code": "Khoan 1",
                "content": "Bổ sung khoản 15 Điều 4 Nghị định số 320/2025/NĐ-CP như sau:",
                "points": [],
            }
        ],
    }
    rels1 = extract_amends_from_article("ND_141_2026", d1)
    rels2 = extract_amends_from_article("ND_141_2026", d2)

    assert len(rels1) == 1
    assert rels1[0]["target_doc"] == "ND_68_2026"
    assert rels1[0]["target_article"] == "Dieu 8"
    assert rels1[0]["target_clause"] == "Khoan 5"

    assert not any(r.get("target_doc") == "ND_68_2026" for r in rels2)
    assert all(not r.get("resolved", True) for r in rels2)


def test_replacement_body_citations_are_not_amendment_targets():
    article = {
        "id": "ND_141_2026_D1",
        "code": "Dieu 1",
        "title": "Sửa đổi, bổ sung một số điều của Nghị định số 68/2026/NĐ-CP",
        "clauses": [
            {
                "id": "ND_141_2026_D1_K2",
                "code": "Khoan 2",
                "content": "Sửa đổi, bổ sung khoản 5 Điều 8 như sau:\n\"5. Hộ kinh doanh thực hiện nộp thuế theo quy định tại Điều 9 của Nghị định này.\"",
                "points": [],
            }
        ],
    }
    rels = extract_amends_from_article("ND_141_2026", article)
    targets = {(r["target_doc"], r["target_article"]) for r in rels if r.get("resolved", True)}
    assert targets == {("ND_68_2026", "Dieu 8")}
    assert not any(r["target_article"] == "Dieu 9" for r in rels)


def test_text_replacement_and_coordinated_targets_are_quarantined():
    article = {
        "id": "ND_141_2026_D1",
        "code": "Dieu 1",
        "title": "Sửa đổi, bổ sung một số điều của Nghị định số 68/2026/NĐ-CP",
        "clauses": [
            {
                "id": "ND_141_2026_D1_K1",
                "code": "Khoan 1",
                "content": "Sửa đổi cụm từ \"500 triệu đồng\" thành \"01 tỷ đồng\" tại Điều 3, Điều 4, khoản 1 Điều 8",
                "points": [],
            }
        ],
    }
    rels = extract_amends_from_article("ND_141_2026", article)
    assert len(rels) >= 1
    assert all(not r.get("resolved", True) for r in rels)
    assert any(r.get("unresolved_reason") == "text_phrase_replacement" for r in rels)


def test_insert_after_clause_does_not_amend_anchor_clause():
    article = {
        "id": "ND_359_2025_D1",
        "code": "Dieu 1",
        "title": "Sửa đổi, bổ sung một số điều của Nghị định số 181/2025/NĐ-CP",
        "clauses": [
            {
                "id": "ND_359_2025_D1_K1",
                "code": "Khoan 1",
                "content": "Bổ sung khoản 1b sau khoản 1 Điều 4 như sau:",
                "points": [],
            }
        ],
    }
    rels = extract_amends_from_article("ND_359_2025", article)
    assert len(rels) == 1
    assert rels[0]["resolved"] is False
    assert rels[0]["unresolved_reason"] == "insertion_anchor"


def test_same_target_from_distinct_source_units_is_not_dropped():
    article = {
        "id": "ND_359_2025_D1",
        "code": "Dieu 1",
        "title": "Sửa đổi, bổ sung một số điều của Nghị định số 181/2025/NĐ-CP",
        "clauses": [
            {
                "id": "ND_359_2025_D1_K1",
                "code": "Khoan 1",
                "content": "Sửa đổi khoản 1 Điều 8 như sau:",
                "points": [],
            },
            {
                "id": "ND_359_2025_D1_K2",
                "code": "Khoan 2",
                "content": "Sửa đổi khoản 1 Điều 8 như sau:",
                "points": [],
            },
        ],
    }
    rels = extract_amends_from_article("ND_359_2025", article)
    assert len(rels) == 2
    assert {r["source_unit"] for r in rels} == {"ND_359_2025_D1_K1", "ND_359_2025_D1_K2"}


def _amendment_article(text, title="", source="ND_359_2025", code="Dieu 1"):
    return {
        "id": f"{source}_D1", "code": code, "title": title,
        "clauses": [{"id": f"{source}_D1_K1", "content": text, "points": []}],
    }


def test_static_registry_matches_all_33_inventory_documents():
    from src.parser.relation_extractor import INVENTORY_DOC_CODES, _resolve_doc
    from src.parser.utils import fold_ascii

    inventory = json.loads((Path(__file__).resolve().parents[1] / "data/source_inventory.json").read_text(encoding="utf-8"))
    documents = inventory["documents"]
    assert len(documents) == 33
    assert set(INVENTORY_DOC_CODES.values()) == {d["id"] for d in documents}
    families = {"ND": "Nghị định", "LUAT": "Luật", "TT": "Thông tư",
                "NQ": "Nghị quyết", "QD": "Quyết định", "VBHN": "Văn bản hợp nhất"}
    for doc in documents:
        phrase = f"{families[doc['id'].split('_')[0]]} số {doc['code']}"
        assert INVENTORY_DOC_CODES[fold_ascii(doc["code"]).upper()] == doc["id"]
        assert _resolve_doc(phrase, "SOURCE") == doc["id"]
        cites = extract_cites_relations("SOURCE", "SOURCE_D1_K1", f"tại Điều 4 {phrase}.")
        assert len(cites) == 1
        assert cites[0]["target_doc"] == doc["id"]
        assert cites[0]["resolved"] is True


@pytest.mark.parametrize("phrase", [
    "Nghị định số 1181/2025/NĐ-CP",
    "Nghị định số 181/2025/NĐ-CPX",
    "Nghị định số 181/2025/NĐ-CP/1",
    "Nghị định ngày hôm nay",
    "Thông tư số 181/2025/NĐ-CP",
    "Nghị định số 181/2025/TT-BTC",
    "Thông tư số 101/2025/TT-BTC",
    "Luật số 19/2026/QH16",
    "Luật Thuế giá trị gia tăng số 999/2030/QH15",
])
def test_explicit_unknown_or_mismatched_document_is_not_guessed(phrase):
    cites = extract_cites_relations("SOURCE", "SOURCE_D1_K1", f"tại Điều 4 {phrase}.")
    assert len(cites) == 1
    assert cites[0]["resolved"] is False
    assert cites[0]["target_doc"] is None
    assert cites[0]["unresolved_reason"] == "unknown_document"


def test_self_reference_requires_exact_document_this_phrase():
    from src.parser.relation_extractor import _resolve_doc

    assert _resolve_doc("Nghị định ngaynay", "SOURCE") is None
    assert _resolve_doc("Nghị định ngày hôm nay", "SOURCE") is None
    assert _resolve_doc("Nghị định này", "SOURCE") == "SOURCE"


def test_replacement_body_cannot_choose_document_or_action():
    text = 'Sửa đổi khoản 1 Điều 8 như sau: “Bãi bỏ quy định tại Điều 9 Nghị định số 68/2026/NĐ-CP.”'
    article = _amendment_article(text)
    rels = extract_amends_from_article("ND_359_2025", article)
    assert len(rels) == 1
    assert rels[0]["target_doc"] == "ND_181_2025"
    assert rels[0]["action"] == "sua_doi"
    assert rels[0]["source_text"] == text


@pytest.mark.parametrize("target,expected", [
    ("181/2025/NĐ-CP", "ND_181_2025"),
    ("999/2030/NĐ-CP", None),
])
def test_explicit_operative_document_precedes_article_mapping(target, expected):
    source = "ND_141_2026"
    text = f"Sửa đổi khoản 1 Điều 8 Nghị định số {target} như sau:"
    article = _amendment_article(text, source=source)
    rels = extract_amends_from_article(source, article)
    assert len(rels) == 1
    assert rels[0]["target_doc"] == expected
    assert rels[0]["resolved"] is (expected is not None)


@pytest.mark.parametrize("text", [
    "Sửa đổi khoản 1 và khoản 2 Điều 8 như sau:",
    "Sửa đổi các khoản 1, 2 và 3 Điều 8 như sau:",
    "Bãi bỏ điểm a và điểm b khoản 1 Điều 8.",
    "Bãi bỏ các điểm a, b khoản 1 Điều 8.",
    "Sửa đổi khoản 1, khoản 2 Điều 8 như sau:",
])
def test_coordinated_subarticle_lists_are_quarantined(text):
    rels = extract_amends_from_article("ND_359_2025", _amendment_article(text))
    assert rels
    assert all(r["resolved"] is False for r in rels)
    assert all(r["target_scope"] == "unresolved_subarticle" for r in rels)
    assert all(r["source_text"] == text for r in rels)


@pytest.mark.parametrize("text,reason", [
    ("Bổ sung khoản 1b sau khoản 1 Điều 4 như sau:", "insertion_anchor"),
    ('Thay thế cụm từ "cũ" bằng "mới" tại Điều 4.', "text_phrase_replacement"),
])
def test_unresolved_amendments_preserve_verbatim_source_text(text, reason):
    rels = extract_amends_from_article("ND_359_2025", _amendment_article(text))
    assert len(rels) == 1
    assert rels[0]["resolved"] is False
    assert rels[0]["unresolved_reason"] == reason
    assert rels[0]["source_text"] == text


@pytest.mark.parametrize("source", ["UNKNOWN", "VBHN_25_2026", "QD_2551_2026", "NQ_43_2026", "ND_245_2026"])
def test_unapproved_amendment_sources_never_create_ordinary_amends(source):
    text = "Sửa đổi khoản 1 Điều 8 Nghị định số 68/2026/NĐ-CP như sau:"
    rels = extract_amends_from_article(source, _amendment_article(text, source=source))
    assert rels
    assert all(r["resolved"] is False for r in rels)
    assert all(r["unresolved_reason"] == "unknown_amendment_source" for r in rels)
    assert all(r["source_text"] == text for r in rels)


@pytest.mark.parametrize("source,target", [("TT_50_2026", "TT_18_2026"), ("TT_92_2019", "TT_72_2014")])
def test_known_circular_amendments_inherit_reviewed_document_target(source, target):
    rels = extract_amends_from_article(source, _amendment_article("Sửa đổi khoản 1 Điều 8 như sau:", source=source))
    assert len(rels) == 1
    assert rels[0]["resolved"] is True
    assert rels[0]["target_doc"] == target


@pytest.mark.parametrize("text", [
    "Sửa đổi một số khoản của Điều 8 như sau:",
    "Sửa đổi khoản 1 và khoản 2 Điều 8 như sau:",
    "Bổ sung khoản 1b sau khoản 1 Điều 8 như sau:",
    'Thay thế cụm từ "cũ" bằng "mới" tại Điều 8.',
    "Sửa đổi Điều 8 Nghị định số 999/2030/NĐ-CP như sau:",
])
def test_unresolved_amendments_execute_no_session_run(text):
    from src.graph.importer import _insert_amends_relation

    session = Mock()
    rels = extract_amends_from_article("ND_359_2025", _amendment_article(text))
    assert rels
    for rel in rels:
        _insert_amends_relation(session, rel)
    session.run.assert_not_called()


def test_unresolved_citation_executes_no_session_run():
    from src.graph.importer import _insert_cite_relation

    session = Mock()
    rels = extract_cites_relations("SOURCE", "SOURCE_D1_K1", "tại Điều 4 Nghị định số 1181/2025/NĐ-CP.")
    assert len(rels) == 1
    _insert_cite_relation(session, rels[0])
    session.run.assert_not_called()


def test_amendment_merge_identity_preserves_source_unit_and_action(r3_amendment_corpus):
    from src.graph.importer import _build_corpus_index, _insert_amends_relation

    article = r3_amendment_corpus[1]["articles"][0]
    article["clauses"][0]["content"] = "Sửa đổi khoản 1 Điều 8 như sau:"
    article["clauses"].append({"id": "ND_359_2025_D1_K2", "code": "Khoan 2",
                               "content": "Bãi bỏ khoản 1 Điều 8.",
                               "valid_from": "2025-07-01", "valid_to": "9999-12-31"})
    index = _build_corpus_index(r3_amendment_corpus)
    rels = extract_amends_from_article("ND_359_2025", article)
    assert len(rels) == 2
    session = Mock()
    for rel in rels:
        _insert_amends_relation(session, rel, index)
    assert session.run.call_count == 2
    identities = set()
    for call in session.run.call_args_list:
        query = call.args[0]
        merge = query.split("MERGE (s)-[r:AMENDS {", 1)[1].split("}]->(t)", 1)[0]
        assert "source_unit: $sunit" in merge
        assert "action: $action" in merge
        identities.add((call.kwargs["sunit"], call.kwargs["action"]))
    assert identities == {("ND_359_2025_D1_K1", "sua_doi"), ("ND_359_2025_D1_K2", "bai_bo")}


@pytest.mark.parametrize("text,reason", [
    ("Sửa đổi các khoản 1 đến 3 Điều 8 như sau:", "unresolved_subarticle"),
    ("Sửa đổi khoản 1a Điều 8 như sau:", "unresolved_subarticle"),
    ("Bổ sung khoản 1a trước khoản 1 Điều 8 như sau:", "insertion_anchor"),
])
def test_unsupported_range_alphanumeric_and_insert_before_are_quarantined(text, reason):
    from src.graph.importer import _insert_amends_relation

    rels = extract_amends_from_article("ND_359_2025", _amendment_article(text))
    assert len(rels) == 1
    assert rels[0]["resolved"] is False
    assert rels[0]["target_scope"] == "unresolved_subarticle"
    assert rels[0]["unresolved_reason"] == reason
    assert rels[0]["source_text"] == text
    session = Mock()
    _insert_amends_relation(session, rels[0])
    session.run.assert_not_called()


@pytest.mark.parametrize("text", [
    "Sửa đổi một số điểm của khoản 2 Điều 23 như sau:",
    "Sửa đổi các điểm của khoản 2 Điều 23 như sau:",
    "Sửa đổi điểm của khoản 2 Điều 23 như sau:",
    "Sửa đổi điểm aa khoản 2 Điều 23 như sau:",
    "Sửa đổi điểm 1 khoản 2 Điều 23 như sau:",
])
def test_r2_vague_or_unsupported_point_never_resolves_parent(text):
    rels = extract_amends_from_article("ND_359_2025", _amendment_article(text))
    assert rels
    assert all(not r["resolved"] for r in rels)
    assert all(r["target_scope"] == "unresolved_subarticle" for r in rels)
    assert all(r["target_point"] != "Diem cua" for r in rels)


@pytest.mark.parametrize("text", [
    "Bổ sung Điều 4a như sau:",
    "Sửa đổi khoản 2 Điều 4a như sau:",
])
def test_r2_article_suffix_is_preserved_without_resolving_numeric_prefix(text):
    rels = extract_amends_from_article("ND_359_2025", _amendment_article(text))
    assert len(rels) == 1
    assert rels[0]["target_article"] == "Dieu 4a"
    assert rels[0]["resolved"] is False


@pytest.mark.parametrize("text", [
    "Sửa đổi điểm a như sau:",
    "Bãi bỏ khoản 2.",
    "Bổ sung điểm b vào khoản 2 như sau:",
    "Sửa đổi khoản 1a như sau:",
])
def test_r2_incomplete_operative_subarticle_remains_unresolved(text):
    rels = extract_amends_from_article("ND_359_2025", _amendment_article(text))
    assert len(rels) == 1
    assert rels[0]["target_article"] == "unresolved"
    assert rels[0]["target_scope"] == "unresolved_subarticle"
    assert rels[0]["resolved"] is False
    assert rels[0]["source_text"] == text


@pytest.mark.parametrize("position", ["trước", "sau"])
def test_r2_bare_insertion_anchor_is_not_silently_dropped(position):
    text = f"Bổ sung điểm b vào {position} điểm a như sau:"
    rels = extract_amends_from_article("ND_359_2025", _amendment_article(text))
    assert len(rels) == 1
    assert rels[0]["resolved"] is False
    assert rels[0]["unresolved_reason"] == "insertion_anchor"


@pytest.mark.parametrize("boundary", ["như sau:", ': “', ': "'])
def test_r2_only_operative_heading_controls_phrase_and_targets(boundary):
    text = f'Sửa đổi khoản 2 Điều 23 {boundary} Thay thế cụm từ tại Điều 9.'
    rels = extract_amends_from_article("ND_359_2025", _amendment_article(text))
    assert len(rels) == 1
    assert rels[0]["resolved"] is True
    assert rels[0]["target_article"] == "Dieu 23"
    assert rels[0]["target_clause"] == "Khoan 2"
    assert rels[0]["action"] == "sua_doi"
    assert rels[0]["source_text"] == text


def test_r2_precise_points_use_single_letter_verifier_grammar():
    for point in ("b", "g", "đ"):
        text = f"Sửa đổi điểm {point} khoản 2 Điều 23 như sau:"
        rels = extract_amends_from_article("ND_359_2025", _amendment_article(text))
        assert len(rels) == 1
        assert rels[0]["resolved"] is True
        assert rels[0]["target_scope"] == "point"


def test_r2_resolved_mapping_set_matches_independent_accepted23_ledger():
    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "tests/fixtures/amendment_review.staging.json").read_text(encoding="utf-8"))
    corpus = json.loads((root / "data/processed/vat_corpus.staging.json").read_text(encoding="utf-8"))
    expected = set()
    for candidate in ledger["candidates"]:
        if candidate["classification"] != "resolved":
            continue
        for target in candidate["expected"]["targets"]:
            expected.add((candidate["source_unit"], target["doc_id"], target["article_id"],
                          target.get("clause_id"), target.get("point_id"),
                          candidate["expected"]["action"], candidate["expected"]["scope"]))
    actual = set()
    count = 0
    for doc in corpus:
        for article in doc["articles"]:
            for rel in extract_amends_from_article(doc["id"], article):
                if not rel["resolved"]:
                    continue
                count += 1
                target = f'{rel["target_doc"]}_D{rel["target_article"].split()[1]}'
                clause = f'{target}_K{rel["target_clause"].split()[1]}' if rel["target_clause"] else None
                point = f'{clause}_{rel["target_point"].split()[1]}' if rel["target_point"] else None
                actual.add((rel["source_unit"], rel["target_doc"], target, clause, point,
                            rel["action"], rel["target_scope"]))
    assert len(expected) == 23
    assert actual == expected, {"extra": actual - expected, "missing": expected - actual}
    assert count == len(actual) == 23


@pytest.fixture
def r3_amendment_corpus():
    dates = {"valid_from": "2025-07-01", "valid_to": "9999-12-31"}
    target = {"id": "ND_181_2025", **dates, "articles": [{
        "id": "ND_181_2025_D8", "code": "Dieu 8", **dates, "clauses": [{
            "id": "ND_181_2025_D8_K1", "code": "Khoan 1", **dates, "points": [{
                "id": "ND_181_2025_D8_K1_b", "code": "Diem b", **dates,
            }],
        }],
    }]}
    article = _amendment_article("Sửa đổi điểm b khoản 1 Điều 8 như sau:")
    article.update(dates)
    article["clauses"][0].update(dates, code="Khoan 1")
    source = {"id": "ND_359_2025", **dates, "articles": [article]}
    return [target, source]


@pytest.mark.parametrize("change", [
    {"target_point": "Diem cua"}, {"resolved": None}, {"resolved": 1},
    {"resolved": "True"}, {"source_unit": "ND_181_2025_D8_K1"},
    {"source_article": "ND_181_2025_D8"}, {"source_doc": "ND_181_2025"},
    {"target_clause": "Khoan 99"}, {"target_point": "Diem z"},
    {"target_article": "Dieu 99"}, {"target_doc": "ND_999_2025"},
    {"target_scope": "article"}, {"target_scope": "clause"},
    {"target_scope": "unknown"}, {"target_clause": None},
    {"action": "invented"}, {"unresolved_reason": "conditional"},
])
def test_r3_declared_resolved_malformed_relation_never_writes(r3_amendment_corpus, change):
    from src.graph import importer

    article = r3_amendment_corpus[1]["articles"][0]
    rel = extract_amends_from_article("ND_359_2025", article)[0]
    rel.update(change)
    session = Mock()
    with pytest.raises(ValueError):
        importer._insert_amends_relation(session, rel, importer._build_corpus_index(r3_amendment_corpus))
    session.run.assert_not_called()


def test_r3_missing_resolution_flag_never_writes(r3_amendment_corpus):
    from src.graph import importer

    rel = extract_amends_from_article("ND_359_2025", r3_amendment_corpus[1]["articles"][0])[0]
    del rel["resolved"]
    session = Mock()
    with pytest.raises(ValueError, match="resolved"):
        importer._insert_amends_relation(session, rel, importer._build_corpus_index(r3_amendment_corpus))
    session.run.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("valid_from", None), ("valid_to", None), ("valid_from", "2025-02-30"),
    ("valid_from", "20250701"), ("valid_to", "2025-01-01"),
])
@pytest.mark.parametrize("level", ["document", "article", "unit"])
def test_r3_source_dates_must_be_complete_iso_and_ordered(r3_amendment_corpus, field, value, level):
    from src.graph import importer

    doc = r3_amendment_corpus[1]
    article = doc["articles"][0]
    unit = {"document": doc, "article": article, "unit": article["clauses"][0]}[level]
    unit[field] = value
    rel = extract_amends_from_article(doc["id"], article)[0]
    session = Mock()
    with pytest.raises(ValueError, match="date"):
        importer._insert_amends_relation(session, rel, importer._build_corpus_index(r3_amendment_corpus))
    session.run.assert_not_called()


@pytest.mark.parametrize("level", ["document", "article", "clause", "point"])
def test_r3_duplicate_target_keys_are_blockers(r3_amendment_corpus, level):
    from copy import deepcopy
    from src.graph import importer

    target = r3_amendment_corpus[0]
    article = target["articles"][0]
    clause = article["clauses"][0]
    items = {"document": r3_amendment_corpus, "article": target["articles"],
             "clause": article["clauses"], "point": clause["points"]}[level]
    items.append(deepcopy(items[0]))
    rel = extract_amends_from_article("ND_359_2025", r3_amendment_corpus[1]["articles"][0])[0]
    assert importer._validate_amends_relation(rel, importer._build_corpus_index(r3_amendment_corpus))


def test_r3_resolved_standalone_requires_corpus_context(r3_amendment_corpus):
    from src.graph import importer

    rel = extract_amends_from_article("ND_359_2025", r3_amendment_corpus[1]["articles"][0])[0]
    session = Mock()
    with pytest.raises(ValueError, match="corpus"):
        importer._insert_amends_relation(session, rel)
    session.run.assert_not_called()


def test_r3_valid_scopes_and_repeat_identity_validate_without_mutation(r3_amendment_corpus):
    from copy import deepcopy
    from src.graph import importer

    original = deepcopy(r3_amendment_corpus)
    rel = extract_amends_from_article("ND_359_2025", r3_amendment_corpus[1]["articles"][0])[0]
    index = importer._build_corpus_index(r3_amendment_corpus)
    session = Mock()
    for candidate in [rel, rel, dict(rel, target_scope="clause", target_point=None),
                      dict(rel, target_scope="article", target_clause=None, target_point=None)]:
        assert importer._validate_amends_relation(candidate, index) == []
        importer._insert_amends_relation(session, candidate, index)
    assert session.run.call_count == 4
    assert session.run.call_args_list[0] == session.run.call_args_list[1]
    assert r3_amendment_corpus == original


def test_r3_all_accepted23_pass_independent_structure_and_dates():
    from src.graph import importer

    path = Path(__file__).resolve().parents[1] / "data/processed/vat_corpus.staging.json"
    plan = importer.plan_corpus_import(str(path))
    assert plan["amends_blockers"] == []
    assert len(plan["resolved_amends"]) == 23
    corpus = json.loads(path.read_text(encoding="utf-8"))
    index = importer._build_corpus_index(corpus)
    for rel in plan["resolved_amends"]:
        assert importer._validate_amends_relation(rel, index) == [], rel


@pytest.mark.parametrize("invalid", ["missing_flag", "late_invalid", "missing_target"])
def test_r3_entire_batch_preflight_precedes_driver(r3_amendment_corpus, tmp_path, monkeypatch, invalid):
    from src.graph import importer

    article = r3_amendment_corpus[1]["articles"][0]
    if invalid == "missing_target":
        article["clauses"][0]["content"] = "Sửa đổi khoản 99 Điều 8 như sau:"
    else:
        article["clauses"].append({
            "id": "ND_359_2025_D1_K2", "code": "Khoan 2", "content": "Bãi bỏ khoản 99 Điều 8.",
            "valid_from": "2025-07-01", "valid_to": "9999-12-31",
        })
    if invalid == "missing_flag":
        extract = importer.extract_amends_from_article

        def without_flag(doc_id, art):
            rels = extract(doc_id, art)
            for rel in rels:
                rel.pop("resolved")
            return rels

        monkeypatch.setattr(importer, "extract_amends_from_article", without_flag)
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(r3_amendment_corpus), encoding="utf-8")
    driver = Mock()
    monkeypatch.setattr(importer, "get_neo4j_driver", driver)
    plan = importer.plan_corpus_import(str(path))
    assert plan["amends_blockers"]
    assert plan["unresolved_amends"] == []
    with pytest.raises(ValueError):
        importer.import_amends_relations(str(path))
    driver.assert_not_called()


def test_r3_unresolved_only_batch_never_acquires_driver(tmp_path, monkeypatch):
    from src.graph import importer

    article = _amendment_article("Sửa đổi các điểm của khoản 2 Điều 23 như sau:")
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps([{"id": "ND_359_2025", "articles": [article]}]), encoding="utf-8")
    driver = Mock()
    monkeypatch.setattr(importer, "get_neo4j_driver", driver)
    assert importer.import_amends_relations(str(path)) == 0
    driver.assert_not_called()


def test_dry_run_never_acquires_driver(tmp_path, monkeypatch):
    from src.graph import importer

    driver = Mock(side_effect=AssertionError("dry run must not acquire driver"))
    monkeypatch.setattr(importer, "get_neo4j_driver", driver)
    article = _amendment_article("Sửa đổi khoản 1 và khoản 2 Điều 8 như sau:")
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps([{"id": "ND_359_2025", "articles": [article]}]), encoding="utf-8")
    plan = importer.plan_corpus_import(str(path))
    assert plan["resolved_amends"] == []
    assert len(plan["unresolved_amends"]) == 1
    assert importer.import_corpus_to_neo4j(str(path), dry_run=True) == plan["stats"]
    driver.assert_not_called()
