import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from src.verifier.citation_verifier import (
    extract_citations,
    prune_invalid_citations,
    verify_citations,
)
from src.verifier.models import CitationStatus, VerificationReport
from src.verifier.risk_score import calculate_risk_score, verify_and_score


@pytest.fixture
def exact_corpus(tmp_path):
    article = {
        "id": "ND_181_2025_D9", "code": "Dieu 9",
        "clauses": [
            {"id": f"ND_181_2025_D9_K{k}", "code": f"Khoan {k}",
             "points": [{"id": f"ND_181_2025_D9_K{k}_{p}", "code": f"Diem {p}"}
                        for p in ("a", "b")]}
            for k in ("1", "10", "a")
        ],
    }
    document = {"id": "ND_181_2025", "valid_from": "2025-07-01",
                "valid_to": "9999-12-31", "articles": [article]}
    path = tmp_path / "exact.json"

    def save():
        path.write_text(json.dumps([document]), encoding="utf-8")
        return str(path)

    return document, article, save


@pytest.fixture
def graph_rows():
    with patch("src.verifier.citation_verifier.get_neo4j_driver") as factory:
        session = factory.return_value.session.return_value.__enter__.return_value
        settings = {"exists": True, "valid_from": "2025-07-01",
                    "valid_to": "9999-12-31", "amendments": []}
        session.run.side_effect = lambda query, **kw: MagicMock(
            data=lambda: [{"cid": cid, **settings} for cid in kw["ids"]])
        yield settings


@pytest.mark.parametrize("as_of", ["2026-03-20", "2026-06-20"])
def test_r7_span_guard_preserves_all_reviewed_provenance(graph_rows, as_of):
    from src.verifier.citation_verifier import _load_verification_metadata, _load_reviewed_amendments, _reviewed_uncertainty

    root = Path(__file__).resolve().parents[1]
    corpus = str(root / "data/processed/vat_corpus.staging.json")
    ledger = str(root / "tests/fixtures/amendment_review.staging.json")
    index = _load_verification_metadata(corpus)
    _load_reviewed_amendments(ledger, corpus, index)
    reviews = index["LUAT_48_2024_D5"]["reviewed_uncertainties"]
    expected = {(r["source_unit"], r["candidate_id"]) for r in reviews
                if any(i["from"] <= as_of for i in r["intervals"])}
    assert len(expected) > 1
    index["LUAT_48_2024_D5"]["reviewed_uncertainties"].append(reviews[0])
    reason = _reviewed_uncertainty("LUAT_48_2024_D5", as_of, index)
    report = verify_citations("[LUAT_48_2024_D5]", as_of, corpus_path=corpus,
                              reviewed_amendment_path=ledger)
    citation, = report.citations
    assert citation.status == "UNRESOLVED" and citation.is_active is None
    assert report.valid_citations == 0 and report.is_clean is False
    assert citation.message.count("provision spans") == 1
    for source, candidate in expected:
        assert reason.count(f"{source} ({candidate})") == 1
        assert citation.message.count(f"{source} ({candidate})") == 1


@pytest.mark.parametrize("cid,as_of", [
    ("ND_68_2026_D3", "2026-03-06"),
    ("ND_68_2026_D4", "2026-05-01"),
    ("ND_68_2026_D8_K1", "2026-05-01"),
    ("ND_68_2026_D8", "2026-05-01"),
    ("ND_181_2025_D23", "2026-06-20"),
    ("ND_181_2025_D23_K2", "2026-06-20"),
    ("TT_72_2014_D6_K2", "2026-07-01"),
])
def test_r4_reviewed_missing_edges_prevent_false_valid(graph_rows, cid, as_of):
    root = Path(__file__).resolve().parents[1]
    corpus = root / "data/processed/vat_corpus.staging.json"
    ledger = root / "tests/fixtures/amendment_review.staging.json"
    baseline = verify_citations(cid, as_of, corpus_path=str(corpus))
    assert baseline.citations[0].status == ("UNRESOLVED" if cid == "ND_68_2026_D8_K1" else "VALID")
    report = verify_citations(cid, as_of, corpus_path=str(corpus), reviewed_amendment_path=str(ledger))
    assert report.citations[0].status == "UNRESOLVED"
    assert report.citations[0].is_active is None
    assert report.valid_citations == 0 and report.is_clean is False


@pytest.mark.parametrize("cid,as_of", [
    ("ND_68_2026_D8_K2", "2026-05-01"),
    ("ND_68_2026_D2", "2026-05-01"),
    ("ND_181_2025_D23_K1", "2026-06-20"),
    ("ND_181_2025_D23_K2_b", "2026-06-20"),
    ("ND_181_2025_D22", "2026-06-20"),
    ("ND_181_2025_D23_K2", "2026-06-19"),
])
def test_r4_review_does_not_taint_siblings_or_precommencement(graph_rows, cid, as_of):
    root = Path(__file__).resolve().parents[1]
    report = verify_citations(
        cid, as_of, corpus_path=str(root / "data/processed/vat_corpus.staging.json"),
        reviewed_amendment_path=str(root / "tests/fixtures/amendment_review.staging.json"))
    assert report.citations[0].status == "VALID"


@pytest.mark.parametrize("damage", ["hash", "json", "schema", "date", "target", "evidence"])
def test_r4_invalid_review_fails_before_graph(tmp_path, damage):
    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "tests/fixtures/amendment_review.staging.json").read_text(encoding="utf-8"))
    candidate = next(c for c in ledger["candidates"] if c["source_unit"] == "ND_141_2026_D1_K1")
    if damage == "hash":
        ledger["pins"]["data/processed/vat_corpus.staging.json"] = "0" * 64
    elif damage == "schema":
        ledger["schema_version"] = 99
    elif damage == "date":
        candidate["source_effective_intervals"][0]["from"] = "invalid"
    elif damage == "target":
        candidate["expected"]["targets"][0]["article_id"] = "MISSING"
    elif damage == "evidence":
        candidate["evidence"]["sha256"] = "0" * 64
    path = tmp_path / "review.json"
    path.write_text("{" if damage == "json" else json.dumps(ledger), encoding="utf-8")
    with patch("src.verifier.citation_verifier.get_neo4j_driver") as driver:
        with pytest.raises(ValueError):
            verify_citations("ND_68_2026_D3", "2026-05-01",
                             corpus_path=str(root / "data/processed/vat_corpus.staging.json"),
                             reviewed_amendment_path=str(path))
        driver.assert_not_called()


def test_r4_review_commencement_not_issuance_and_no_reversion(tmp_path, graph_rows):
    root = Path(__file__).resolve().parents[1]
    corpus = root / "data/processed/vat_corpus.staging.json"
    ledger = json.loads((root / "tests/fixtures/amendment_review.staging.json").read_text(encoding="utf-8"))
    candidate = next(c for c in ledger["candidates"] if c["source_unit"] == "ND_141_2026_D1_K1")
    candidate["source_effective_intervals"][0]["to"] = "2026-04-30"
    path = tmp_path / "review.json"
    path.write_text(json.dumps(ledger), encoding="utf-8")
    report = verify_citations("ND_68_2026_D3", "2026-05-01", corpus_path=str(corpus),
                              reviewed_amendment_path=str(path))
    assert report.citations[0].status == "UNRESOLVED"


@pytest.fixture
def r4_review_case(tmp_path):
    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "tests/fixtures/amendment_review.staging.json").read_text(encoding="utf-8"))
    candidate = next(c for c in ledger["candidates"] if c["source_unit"] == "ND_141_2026_D1_K1")
    ledger["candidates"] = [candidate]
    path = tmp_path / "review.json"

    def save():
        path.write_text(json.dumps(ledger), encoding="utf-8")
        return str(path)

    return root / "data/processed/vat_corpus.staging.json", candidate, save


@pytest.mark.parametrize("cid", ["ND_68_2026_D3a", "ND_68_2026_D3a_K1", "ND_181_2025_D4_K4_d_1"])
def test_r4_extract_preserves_exact_inserted_identity(cid):
    assert extract_citations(f"[{cid}] {cid}") == [cid]


@pytest.mark.parametrize("damage", ["point_without_clause", "clause_prefix", "point_prefix", "article_extra"])
def test_r4_exact_target_hierarchy_rejects_prefix_matches(r4_review_case, damage):
    from src.verifier.citation_verifier import _load_reviewed_amendments, _load_verification_metadata

    corpus, candidate, save = r4_review_case
    target = candidate["expected"]["targets"][0]
    if damage == "point_without_clause":
        target.pop("clause_id", None)
        target["point_id"] = target["article_id"] + "_K1_a"
    elif damage == "clause_prefix":
        target["clause_id"] = target["article_id"] + "_K1_garbage"
    elif damage == "point_prefix":
        target["clause_id"] = target["article_id"] + "_K1"
        target["point_id"] = target["clause_id"] + "_a_garbage"
    else:
        target["article_id"] += "_extra"
    index = _load_verification_metadata(str(corpus))
    with pytest.raises(ValueError):
        _load_reviewed_amendments(save(), str(corpus), index)
    assert all("reviewed_uncertainties" not in meta for meta in index.values())


@pytest.mark.parametrize("enriched", [False, True])
def test_r4_adapter_preserves_review_projection(r4_review_case, enriched):
    from src.verifier.citation_verifier import _load_reviewed_amendments, _load_verification_metadata

    corpus, candidate, save = r4_review_case
    if not enriched:
        for interval in candidate["source_effective_intervals"]:
            for key in ("issuance_evidence", "commencement_evidence", "commencement_basis"):
                interval.pop(key)
    index = _load_verification_metadata(str(corpus))
    _load_reviewed_amendments(save(), str(corpus), index)
    projection = index["ND_68_2026_D8"]["reviewed_uncertainties"][0]
    assert projection["target_id"] == "ND_68_2026_D8_K1"
    assert projection["intervals"] == candidate["source_effective_intervals"]
    assert projection["evidence"] == candidate["evidence"]
    assert projection["scope"] == "phrase_replacement"
    assert projection["source_unit"] == candidate["source_unit"]


@pytest.mark.parametrize("damage", ["partial_enrichment", "inventory", "refs", "hierarchy", "end_status", "enriched_evidence", "basis"])
def test_r4_adapter_rejects_malformed_relevant_record(r4_review_case, damage):
    corpus, candidate, save = r4_review_case
    interval = candidate["source_effective_intervals"][0]
    if damage == "partial_enrichment":
        interval.pop("commencement_basis")
    elif damage == "inventory":
        interval["inventory_fields"] = []
    elif damage == "refs":
        interval["date_evidence_refs"] = []
    elif damage == "end_status":
        interval.pop("end_status")
    elif damage == "enriched_evidence":
        interval["commencement_evidence"]["sha256"] = "0" * 64
    elif damage == "basis":
        interval["commencement_basis"] = ""
    else:
        candidate["expected"]["targets"][0]["clause_id"] = "ND_68_2026_D4_K1"
    with patch("src.verifier.citation_verifier.get_neo4j_driver") as driver:
        with pytest.raises(ValueError):
            verify_citations("ND_68_2026_D3", "2026-05-01", corpus_path=str(corpus),
                             reviewed_amendment_path=save())
        driver.assert_not_called()


def test_r4_document_target_is_conditional_not_expiration(r4_review_case, graph_rows):
    corpus, candidate, save = r4_review_case
    candidate.pop("operative_scope", None)
    candidate.pop("operations", None)
    candidate["expected"].update(scope="conditional_document_replacement", targets=[{"doc_id": "ND_68_2026"}])
    report = verify_citations("ND_68_2026_D2", "2026-05-01", corpus_path=str(corpus),
                              reviewed_amendment_path=save())
    assert report.citations[0].status == "UNRESOLVED"
    assert candidate["source_unit"] in report.citations[0].message


def test_r4_absent_suffixed_article_never_taints_anchor(r4_review_case, graph_rows):
    corpus, candidate, save = r4_review_case
    candidate["expected"].update(scope="article_insertion", targets=[{
        "doc_id": "ND_68_2026", "article_id": "ND_68_2026_D3a"}])
    report = verify_citations("ND_68_2026_D3", "2026-05-01", corpus_path=str(corpus),
                              reviewed_amendment_path=save())
    assert report.citations[0].status == "VALID"


def test_r4_adapter_retains_absent_article_identity(r4_review_case):
    from src.verifier.citation_verifier import _load_reviewed_amendments, _load_verification_metadata

    corpus, candidate, save = r4_review_case
    candidate["expected"].update(scope="article_insertion", targets=[{
        "doc_id": "ND_68_2026", "article_id": "ND_68_2026_D3a"}])
    index = _load_verification_metadata(str(corpus))
    _load_reviewed_amendments(save(), str(corpus), index)
    assert index["ND_68_2026_D3a"]["reviewed_uncertainties"][0]["target_id"] == "ND_68_2026_D3a"
    assert "reviewed_uncertainties" not in index["ND_68_2026_D3"]


@pytest.mark.parametrize("classification", ["nonoperative", "outscope"])
def test_r4_skipped_classifications_need_no_invented_dates(r4_review_case, graph_rows, classification):
    corpus, candidate, save = r4_review_case
    candidate["classification"] = classification
    candidate.pop("source_effective_intervals")
    if classification == "outscope":
        candidate["expected"]["targets"] = []
    report = verify_citations("ND_68_2026_D3", "2026-05-01", corpus_path=str(corpus),
                              reviewed_amendment_path=save())
    assert report.citations[0].status == "VALID"


@pytest.mark.parametrize("day,expected", [("2025-12-31", "UNRESOLVED"), ("2026-01-01", "UNRESOLVED"),
                                          ("2026-03-05", "UNRESOLVED"), ("2026-05-01", "UNRESOLVED")])
def test_r4_retroactive_start_survives_source_expiry(r4_review_case, graph_rows, day, expected):
    corpus, candidate, save = r4_review_case
    interval = candidate["source_effective_intervals"][0]
    assert interval["from"] < interval["issued_on"]
    interval["to"] = "2026-04-30"
    report = verify_citations("ND_68_2026_D3", day, corpus_path=str(corpus), reviewed_amendment_path=save())
    assert report.citations[0].status == expected
    assert report.citations[0].is_active is None
    assert report.valid_citations == 0


def test_r4_wrapper_forwards_explicit_review_path(r4_review_case, graph_rows):
    corpus, candidate, save = r4_review_case
    result = verify_and_score("ND_68_2026_D3", "2026-05-01", corpus_path=str(corpus),
                              reviewed_amendment_path=save())
    assert result.report.citations[0].status == "UNRESOLVED"
    assert result.risk_score == 50


def test_r4_review_is_explicit_without_hidden_autoload(graph_rows, monkeypatch):
    monkeypatch.setenv("VAT_AMENDMENT_REVIEW_PATH", "must-not-autoload.json")
    root = Path(__file__).resolve().parents[1]
    with patch("src.verifier.citation_verifier._load_reviewed_amendments") as loader:
        verify_and_score("ND_68_2026_D3", "2026-05-01",
                         corpus_path=str(root / "data/processed/vat_corpus.staging.json"))
        loader.assert_not_called()


def test_extract_citations_supports_bare_and_bracketed_nq_vbhn():
    text = "Theo quy định tại [NQ_43_2026_D2] và NQ_43_2026_D2 hoặc VBHN_25_2026_D1."
    cites = extract_citations(text)
    assert "NQ_43_2026_D2" in cites
    assert "VBHN_25_2026_D1" in cites
    assert len(cites) == 2


def test_extract_citations_does_not_extract_appendix_as_article():
    text = "Xem tại [TT_89_2026_PL1] hoặc TT_89_2026_PL2."
    cites = extract_citations(text)
    assert cites == []


def test_consolidation_reference_citation_evaluates_to_unresolved(tmp_path):
    corpus = [
        {
            "id": "VBHN_25_2026",
            "code": "25/2026/VBHN-BTC",
            "title": "Văn bản hợp nhất 25",
            "status": "CONSOLIDATION_REFERENCE",
            "valid_from": "2026-01-01",
            "valid_to": "9999-12-31",
            "articles": [
                {
                    "id": "VBHN_25_2026_D1",
                    "code": "Dieu 1",
                    "title": "Dieu 1",
                    "clauses": [],
                }
            ],
        }
    ]
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")

    mock_record = {
        "cid": "VBHN_25_2026_D1",
        "exists": True,
        "valid_from": "2026-01-01",
        "valid_to": "9999-12-31",
        "amendments": [],
    }
    mock_session = MagicMock()
    mock_session.run.return_value.data.return_value = [mock_record]
    mock_driver = MagicMock()
    mock_driver.session.return_value.__enter__.return_value = mock_session

    with patch("src.verifier.citation_verifier.get_neo4j_driver", return_value=mock_driver):
        report = verify_citations("Căn cứ [VBHN_25_2026_D1]", as_of_date="2026-03-20", corpus_path=str(p))

    assert len(report.citations) == 1
    status = report.citations[0]
    assert status.status == "UNRESOLVED"
    assert status.exists is True
    assert status.is_active is None
    assert report.valid_citations == 0
    assert report.is_clean is False
    assert "hợp nhất" in status.message


def test_mixed_date_provision_spans_evaluates_to_unresolved(tmp_path):
    corpus = [
        {
            "id": "LUAT_48_2024",
            "code": "48/2024/QH15",
            "title": "Luat 48",
            "valid_from": "2025-07-01",
            "valid_to": "9999-12-31",
            "articles": [
                {
                    "id": "LUAT_48_2024_D5",
                    "code": "Dieu 5",
                    "title": "Dieu 5",
                    "clauses": [
                        {
                            "id": "LUAT_48_2024_D5_K25",
                            "code": "Khoan 25",
                            "content": "Noi dung",
                            "valid_from": "2025-07-01",
                            "valid_to": "9999-12-31",
                            "provision_spans": [
                                {
                                    "subject": "Ho kinh doanh",
                                    "valid_from": "2026-01-01",
                                    "valid_to": "9999-12-31",
                                    "evidence": "Article 18(2)",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")

    mock_record = {
        "cid": "LUAT_48_2024_D5_K25",
        "exists": True,
        "valid_from": "2025-07-01",
        "valid_to": "9999-12-31",
        "amendments": [],
    }
    mock_session = MagicMock()
    mock_session.run.return_value.data.return_value = [mock_record]
    mock_driver = MagicMock()
    mock_driver.session.return_value.__enter__.return_value = mock_session

    with patch("src.verifier.citation_verifier.get_neo4j_driver", return_value=mock_driver):
        report = verify_citations("Căn cứ [LUAT_48_2024_D5_K25]", as_of_date="2026-03-20", corpus_path=str(p))

    assert len(report.citations) == 1
    status = report.citations[0]
    assert status.status == "UNRESOLVED"
    assert status.is_active is None
    assert report.valid_citations == 0
    assert "hỗn hợp" in status.message or "provision spans" in status.message


def test_amendment_scope_filters_sibling_point_and_clause(tmp_path):
    corpus = [
        {
            "id": "ND_181_2025",
            "code": "181/2025/ND-CP",
            "title": "ND 181",
            "valid_from": "2025-07-01",
            "valid_to": "9999-12-31",
            "articles": [
                {
                    "id": "ND_181_2025_D9",
                    "code": "Dieu 9",
                    "title": "Thue suat",
                    "clauses": [
                        {
                            "id": "ND_181_2025_D9_K1",
                            "code": "Khoan 1",
                            "content": "K1",
                            "points": [
                                {"id": "ND_181_2025_D9_K1_a", "code": "Diem a", "content": "Pa"},
                                {"id": "ND_181_2025_D9_K1_b", "code": "Diem b", "content": "Pb"},
                            ],
                        },
                        {
                            "id": "ND_181_2025_D9_K2",
                            "code": "Khoan 2",
                            "content": "K2",
                            "points": [],
                        },
                    ],
                }
            ],
        }
    ]
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")

    # Amendment specifically targets Diem b of Khoan 1
    amendments = [
        {
            "amender_id": "ND_144_2026_D1",
            "scope": "point",
            "target_clause": "Khoan 1",
            "target_point": "Diem b",
        }
    ]

    def mock_run_fn(query, **kwargs):
        ids = kwargs.get("ids", [])
        records = []
        for cid in ids:
            records.append({
                "cid": cid,
                "exists": True,
                "valid_from": "2025-07-01",
                "valid_to": "9999-12-31",
                "amendments": amendments,
            })
        m = MagicMock()
        m.data.return_value = records
        return m

    mock_session = MagicMock()
    mock_session.run.side_effect = mock_run_fn
    mock_driver = MagicMock()
    mock_driver.session.return_value.__enter__.return_value = mock_session

    with patch("src.verifier.citation_verifier.get_neo4j_driver", return_value=mock_driver):
        # 1. Targeted point b should be AMENDED
        rep_b = verify_citations("Căn cứ [ND_181_2025_D9_K1_b]", as_of_date="2026-06-25", corpus_path=str(p))
        assert rep_b.citations[0].status == "AMENDED"
        assert "ND_144_2026_D1" in rep_b.citations[0].amended_by

        # 2. Sibling point a should be VALID (not affected)
        rep_a = verify_citations("Căn cứ [ND_181_2025_D9_K1_a]", as_of_date="2026-06-25", corpus_path=str(p))
        assert rep_a.citations[0].status == "VALID"
        assert rep_a.citations[0].amended_by == []

        # 3. Sibling clause 2 should be VALID (not affected)
        rep_k2 = verify_citations("Căn cứ [ND_181_2025_D9_K2]", as_of_date="2026-06-25", corpus_path=str(p))
        assert rep_k2.citations[0].status == "VALID"
        assert rep_k2.citations[0].amended_by == []


def test_unresolved_citation_risk_score_and_warning():
    report = VerificationReport(
        as_of_date="2026-03-20",
        total_citations=2,
        valid_citations=1,
        citations=[
            CitationStatus(id="LUAT_48_2024_D9", status="VALID", exists=True, is_active=True, message=""),
            CitationStatus(id="VBHN_25_2026_D1", status="UNRESOLVED", exists=True, is_active=None, message="Chưa đủ căn cứ xác minh hiệu lực"),
        ],
        is_clean=False,
    )
    score, level, warnings = calculate_risk_score(report)
    assert score == 50
    assert level == "MEDIUM"
    assert len(warnings) == 1
    assert "chưa đủ" in warnings[0].lower() or "xác minh" in warnings[0].lower()


def test_prune_invalid_citations_preserves_unresolved():
    report = VerificationReport(
        as_of_date="2026-03-20",
        total_citations=2,
        valid_citations=0,
        citations=[
            CitationStatus(id="LUAT_48_2024_D5_K25", status="UNRESOLVED", exists=True, is_active=None, message=""),
            CitationStatus(id="BOGUS_D9", status="NOT_FOUND", exists=False, is_active=False, message=""),
        ],
        is_clean=False,
    )
    text = "Theo quy định tại [LUAT_48_2024_D5_K25] và [BOGUS_D9]."
    pruned = prune_invalid_citations(text, report)
    assert "[LUAT_48_2024_D5_K25]" in pruned
    assert "[BOGUS_D9]" not in pruned


@pytest.mark.parametrize("cid", ["ND_181_2025_D9", "ND_181_2025_D9_K1", "ND_181_2025_D9_K1_b"])
@pytest.mark.parametrize("day", ["2025-12-31", "2026-01-01", "2026-01-02"])
def test_spans_affect_only_containing_scope(exact_corpus, graph_rows, cid, day):
    _, article, save = exact_corpus
    article["clauses"][0]["points"][1]["provision_spans"] = [
        {"subject": "special", "valid_from": "2026-01-01", "valid_to": "9999-12-31"}]
    path = save()
    report = verify_citations(f"[{cid}] [ND_181_2025_D9_K1_a] [ND_181_2025_D9_K10]", day, corpus_path=path)
    assert report.citations[0].status == "UNRESOLVED"
    assert report.citations[0].is_active is None
    assert [c.status for c in report.citations[1:]] == ["VALID", "VALID"]


@pytest.mark.parametrize("level", ["document", "article", "clause"])
def test_ancestor_spans_apply_to_point(exact_corpus, graph_rows, level):
    document, article, save = exact_corpus
    node = {"document": document, "article": article, "clause": article["clauses"][0]}[level]
    node["provision_spans"] = [{"subject": "special", "valid_from": "2026-01-01"}]
    result = verify_citations("[ND_181_2025_D9_K1_a]", "2026-01-01", corpus_path=save())
    assert result.citations[0].status == "UNRESOLVED"
    assert result.citations[0].is_active is None


@pytest.mark.parametrize("cid", ["ND_181_2025_D9_K2", "NQ_999_2026_D1"])
@pytest.mark.parametrize("exists", [True, False])
def test_missing_exact_metadata_is_not_fake_valid(exact_corpus, graph_rows, cid, exists):
    graph_rows["exists"] = exists
    result = verify_citations(f"[{cid}]", "2026-01-01", corpus_path=exact_corpus[2]())
    assert result.citations[0].status == ("UNRESOLVED" if exists else "NOT_FOUND")
    assert result.citations[0].is_active is (None if exists else False)
    assert not result.is_clean


@pytest.mark.parametrize("field", ["valid_from", "valid_to"])
@pytest.mark.parametrize("location", ["graph", "corpus"])
def test_missing_dates_fail_closed(exact_corpus, graph_rows, field, location):
    if location == "graph":
        graph_rows[field] = None
    else:
        exact_corpus[0].pop(field)
    result = verify_citations("[ND_181_2025_D9_K1]", "2026-01-01", corpus_path=exact_corpus[2]())
    assert result.citations[0].status == "UNRESOLVED"
    assert result.citations[0].is_active is None


@pytest.mark.parametrize("scope", [None, "unknown", "clause", "point"])
def test_unknown_or_incomplete_legacy_scope_reports_uncertainty(exact_corpus, graph_rows, scope):
    graph_rows["amendments"] = [{"amender_id": "ND_144_2026_D1", "scope": scope}]
    result = verify_citations("[ND_181_2025_D9_K1]", "2026-06-20", corpus_path=exact_corpus[2]())
    assert result.citations[0].status == "UNRESOLVED"
    assert result.citations[0].is_active is None
    assert "phạm vi" in result.citations[0].message


def test_legacy_amended_by_without_scope_is_uncertain(exact_corpus, graph_rows):
    graph_rows["amended_by"] = ["ND_144_2026_D1"]
    result = verify_citations("[ND_181_2025_D9]", "2026-06-20", corpus_path=exact_corpus[2]())
    assert result.citations[0].status == "UNRESOLVED"


@pytest.mark.parametrize("bad_json", [False, True])
def test_corpus_errors_propagate(tmp_path, graph_rows, bad_json):
    path = tmp_path / "bad.json"
    if bad_json:
        path.write_text("{", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError if bad_json else FileNotFoundError):
        verify_citations("[ND_181_2025_D9]", "2026-01-01", corpus_path=str(path))


def test_corpus_reload_does_not_hide_corruption(exact_corpus, graph_rows):
    path = exact_corpus[2]()
    assert verify_citations("[ND_181_2025_D9]", "2026-01-01", corpus_path=path).is_clean
    from pathlib import Path
    Path(path).write_text("{", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        verify_citations("[ND_181_2025_D9]", "2026-01-01", corpus_path=path)


@pytest.mark.parametrize("day,expected", [("2025-06-30", "NOT_YET_EFFECTIVE"),
    ("2025-07-01", "VALID"), ("2025-07-02", "VALID"),
    ("2026-06-19", "VALID"), ("2026-06-20", "VALID"), ("2026-06-21", "EXPIRED")])
def test_exact_dates_override_article_dates(exact_corpus, graph_rows, day, expected):
    exact_corpus[1]["clauses"][0].update(valid_from="2025-07-01", valid_to="2026-06-20")
    result = verify_citations("[ND_181_2025_D9_K1]", day, corpus_path=exact_corpus[2]())
    assert result.citations[0].status == expected


@pytest.mark.parametrize("scope,target,expected", [
    ("clause", "Khoan 1", "VALID"), ("clause", "Khoan 10", "AMENDED"),
    ("point", "Khoan 1", "VALID"), ("point", "Khoan 10", "AMENDED")])
def test_k1_does_not_match_k10(exact_corpus, graph_rows, scope, target, expected):
    graph_rows["amendments"] = [{"amender_id": "ND_144_2026_D1", "scope": scope,
                                 "target_clause": target, "target_point": "Diem b"}]
    result = verify_citations("[ND_181_2025_D9_K10_b]", "2026-06-20", corpus_path=exact_corpus[2]())
    assert result.citations[0].status == expected


@pytest.mark.parametrize("text", ["[ND_181_2025_D9_K]", "ND_181_2025_D9_K1_",
    "[ND_181_2025_D9_K1_a_extra]", "[TT_89_2026_PL1_D2]", "ND_181_2025_D9oops"])
def test_malformed_and_appendix_citations_have_no_partial_matches(text):
    assert extract_citations(text) == []


def test_alpha_clause_and_point_are_canonical():
    assert extract_citations("[nd_181_2025_d9_ka_B] ND_181_2025_D9_KA_b") == ["ND_181_2025_D9_Ka_b"]


@pytest.mark.parametrize("kwargs,expected", [({"context_ids": set()}, "IRRELEVANT"),
    ({"relevance_map": {"ND_181_2025_D9_K2": False}}, "IRRELEVANT"), ({}, "NOT_FOUND")])
def test_context_and_relevance_priority_preserved(exact_corpus, graph_rows, kwargs, expected):
    graph_rows["exists"] = bool(kwargs)
    result = verify_citations("[ND_181_2025_D9_K2]", "2026-01-01", corpus_path=exact_corpus[2](), **kwargs)
    assert result.citations[0].status == expected


@pytest.mark.parametrize("day,expected", [("2026-04-01", "VALID"),
    ("2026-06-19", "VALID"), ("2026-06-20", "AMENDED"),
    ("2026-06-21", "AMENDED"), ("2026-06-22", "VALID")])
def test_amendment_source_unit_dates(exact_corpus, graph_rows, day, expected):
    graph_rows["amendments"] = [{"amender_id": "ND_144_2026_D1", "scope": "article",
        "source_unit": "ND_144_2026_D1_K1", "valid_from": "2026-06-20", "valid_to": "2026-06-21"}]
    result = verify_citations("[ND_181_2025_D9]", day, corpus_path=exact_corpus[2]())
    assert result.citations[0].status == expected


@pytest.mark.parametrize("missing", ["valid_from", "valid_to"])
def test_amendment_source_missing_date_is_uncertain(exact_corpus, graph_rows, missing):
    amendment = {"amender_id": "ND_144_2026_D1", "scope": "article",
                 "source_unit": "ND_144_2026_D1_K1", "valid_from": "2026-06-20", "valid_to": "9999-12-31"}
    amendment[missing] = None
    graph_rows["amendments"] = [amendment]
    result = verify_citations("[ND_181_2025_D9]", "2026-06-20", corpus_path=exact_corpus[2]())
    assert result.citations[0].status == "UNRESOLVED"
    assert result.citations[0].is_active is None


def test_verify_and_score_forwards_exact_corpus_path():
    report = VerificationReport(as_of_date="2026-06-20", total_citations=1,
        valid_citations=0, is_clean=False, citations=[CitationStatus(
            id="ND_181_2025_D9", status="UNRESOLVED", exists=True, is_active=None, message="")])
    with patch("src.verifier.risk_score.verify_citations", return_value=report) as verify:
        result = verify_and_score("[ND_181_2025_D9]", "2026-06-20",
            relevance_map={}, context_ids=set(), corpus_path="explicit.json")
    verify.assert_called_once_with("[ND_181_2025_D9]", "2026-06-20",
        relevance_map={}, context_ids=set(), corpus_path="explicit.json", reviewed_amendment_path=None)
    assert result.risk_score == 50
    assert result.warnings and "xác minh" in result.warnings[0]
    assert "[ND_181_2025_D9]" in prune_invalid_citations("[ND_181_2025_D9]", result.report)


def test_load_verification_metadata_hits_stat_cache_without_rereading_bytes(exact_corpus, monkeypatch):
    from src.verifier import citation_verifier

    path = exact_corpus[2]()
    first = citation_verifier._load_verification_metadata(path)
    assert "ND_181_2025_D9" in first

    def forbidden_read(self):
        raise AssertionError("Unchanged corpus file must hit stat cache without re-reading bytes")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    second = citation_verifier._load_verification_metadata(path)
    assert "ND_181_2025_D9" in second
