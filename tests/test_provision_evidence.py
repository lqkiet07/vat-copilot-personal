import hashlib
import importlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest


try:
    resolve = importlib.import_module("src.engine.provision_evidence").resolve_provision_evidence
except ModuleNotFoundError as exc:
    if exc.name != "src.engine.provision_evidence":
        raise

    def resolve(*args, **kwargs):
        return SimpleNamespace(items=(), blocked={"missing_capability": "resolver not implemented"})


A = "LAW_1_2024_D1"
K = A + "_K1"
S = A + "_K2"
SOURCE = "LAW_2_2025_D1"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def case(tmp_path):
    raw = tmp_path / "body.txt"
    raw.write_text("old rule\nnew rule\nfinal rule\nold\nnew\nrepeal\ninserted rule\n"
                   "Issued 2025-03-01. Effective retroactively 2025-02-01.\n"
                   "Clause 2 effective 2025-04-01.", encoding="utf-8")
    ref = {"path": "body.txt", "sha256": digest(raw), "excerpt": "old rule"}
    doc = {"id": "LAW_1_2024", "issue_date": "2024-01-01", "valid_from": "2024-02-01",
           "valid_to": "9999-12-31", "status": "ACTIVE", "articles": [
               {"id": A, "content": "Heading", "clauses": [
                   {"id": K, "content": "old rule"}, {"id": S, "content": "sibling"}]}]}
    amender = {"id": "LAW_2_2025", "issue_date": "2025-03-01", "valid_from": "2025-02-01",
               "valid_to": "2025-12-31", "status": "EXPIRED",
               "articles": [{"id": SOURCE, "content": "Replace old rule as follows", "clauses": [
                   {"id": SOURCE + "_K1", "content": "first change"},
                   {"id": SOURCE + "_K2", "content": "later change", "valid_from": "2025-04-01"}]}]}
    corpus = tmp_path / "corpus.json"
    ledger = tmp_path / "review.json"
    data = [doc, amender]
    review = {"schema_version": 1, "oracle": "independent_raw_source_review", "review_complete": True,
              "pins": {"corpus.json": "", "body.txt": digest(raw)},
               "documents": [{"doc_id": d["id"], "review_status": "reviewed", "source_paths": ["body.txt"],
                              "issued_on": d["issue_date"], "effective_on": d["valid_from"],
                              "provision_effective_dates": []} for d in data], "candidates": []}
    review["documents"][1]["date_evidence"] = {
        "path": "body.txt", "issued_excerpt": "Issued 2025-03-01.",
        "effective_excerpt": "Effective retroactively 2025-02-01."}
    review["documents"][1]["provision_effective_dates"] = [{
        "target_ids": [SOURCE + "_K2"], "effective_on": "2025-04-01", "mapping_status": "approved",
        "evidence": {**ref, "excerpt": "Clause 2 effective 2025-04-01."}}]
    state = SimpleNamespace(root=tmp_path, raw=raw, corpus=corpus, ledger=ledger,
                            data=data, review=review, ref=ref)

    def flush():
        save(corpus, data)
        review["pins"]["corpus.json"] = digest(corpus)
        save(ledger, review)

    state.flush = flush
    flush()
    return state


def binding(case, text):
    content = case.raw.read_bytes()
    start = content.index(text.encode())
    return {"path": "body.txt", "sha256": digest(case.raw), "byte_start": start,
            "byte_end": start + len(text.encode()), "text": text}


def amendment(case, target=K, action="replace", effective="2025-02-01", identity="change", usable=True):
    issued = {**case.ref, "excerpt": "Issued 2025-03-01."}
    commencement = {**case.ref, "excerpt": "Clause 2 effective 2025-04-01." if effective == "2025-04-01"
                    else "Effective retroactively 2025-02-01."}
    source = SOURCE + "_K2" if effective == "2025-04-01" else SOURCE + "_K1"
    interval = {"from": effective, "to": "2025-12-31", "issued_on": "2025-03-01",
                "end_status": "expired", "inventory_fields": ["effective_on"],
                "date_evidence_refs": [commencement], "issuance_evidence": issued,
                "commencement_evidence": commencement, "commencement_basis": "retroactive"}
    target_spec = {"doc_id": "LAW_1_2024", "article_id": A}
    if target != A:
        target_spec["clause_id"] = target
    candidate = {"id": identity, "source_doc": "LAW_2_2025", "source_unit": source,
                 "classification": "resolved", "evidence": case.ref,
                 "source_effective_intervals": [interval],
                 "expected": {"scope": "article" if target == A else "clause",
                              "action": "sua_doi", "targets": [target_spec]}}
    if usable:
        op = {"candidate_id": identity, "source_unit": source, "target_id": target,
              "action": action, "effective_on": effective, "issued_on": "2025-03-01",
              "issuance_evidence": issued, "commencement_evidence": commencement}
        if action in {"replace", "insert"}:
            op["replacement"] = binding(case, "inserted rule" if action == "insert" else "new rule")
        if action == "phrase_replace":
            op.update(old=binding(case, "old"), new=binding(case, "new"))
        candidate["operations"] = [op]
    case.review["candidates"].append(candidate)
    case.flush()
    return candidate


def run(case, ids=None, day="2025-06-01"):
    return resolve(ids or [K], day, corpus_path=str(case.corpus), reviewed_amendment_path=str(case.ledger))


def provision(result, unit=K):
    return next(p for item in result.items for p in item.provisions if p.id == unit)


def test_unchanged_exact_hierarchy_and_provenance(case):
    result = run(case, [A])
    assert not result.blocked
    assert [p.id for p in result.items[0].provisions] == [A, K, S]
    p = provision(result)
    assert (p.text, p.status, p.valid_from, p.valid_to, p.issued_on) == (
        "old rule", "UNCHANGED", "2024-02-01", "9999-12-31", "2024-01-01")
    assert p.provenance[0]["sha256"] == digest(case.corpus)


@pytest.mark.parametrize("classification", ["resolved", "unresolved"])
def test_relation_only_blocks_overlap_not_sibling(case, classification):
    c = amendment(case, usable=False)
    c["classification"] = classification
    case.flush()
    result = run(case, [A, K, S])
    assert set(result.blocked) == {A, K}
    assert "change" in result.blocked[K]
    assert provision(result, S).text == "sibling"


@pytest.mark.parametrize("day,changed", [("2025-01-31", False), ("2025-02-01", True),
                                        ("2025-12-31", True), ("2026-01-01", True)])
def test_effective_event_is_inclusive_retroactive_and_persistent(case, day, changed):
    amendment(case)
    p = provision(run(case, day=day))
    assert p.text == ("new rule" if changed else "old rule")
    if changed:
        assert p.status == "AMENDED"
        assert (p.valid_from, p.issued_on) == ("2025-02-01", "2025-03-01")
        assert p.provenance[-1]["candidate_id"] == "change"


def test_replacement_chain(case):
    amendment(case)
    c = amendment(case, effective="2025-04-01", identity="second")
    c["operations"][0]["replacement"] = binding(case, "final rule")
    case.flush()
    p = provision(run(case))
    assert p.text == "final rule"
    assert [r["candidate_id"] for r in p.provenance[1:]] == ["change", "second"]


def test_same_day_operations_are_ambiguous(case):
    amendment(case)
    amendment(case, identity="second")
    assert K in run(case).blocked


@pytest.mark.parametrize("text,blocked", [("old rule", False), ("old old rule", True), ("absent", True)])
def test_phrase_requires_exactly_one_occurrence(case, text, blocked):
    case.data[0]["articles"][0]["clauses"][0]["content"] = text
    amendment(case, action="phrase_replace")
    result = run(case)
    assert (K in result.blocked) is blocked
    if not blocked:
        assert provision(result).text == "new rule"


def test_repeal_suppresses_text_forever(case):
    amendment(case, action="repeal")
    result = run(case, day="2026-01-01")
    assert not result.items
    assert "repeal" in result.blocked[K].lower()


@pytest.mark.parametrize("explicit", [True, False])
def test_insertion_requires_explicit_parent_identity_and_text(case, explicit):
    target = A + "_K3"
    c = amendment(case, target=target, action="insert")
    c["expected"]["scope"] = "clause_insertion"
    c["operations"][0]["parent_id"] = A
    if not explicit:
        del c["operations"][0]["replacement"]
    case.flush()
    result = run(case, [target, S])
    assert (target in result.blocked) is not explicit
    assert provision(result, S).text == "sibling"
    if explicit:
        assert provision(result, target).text == "inserted rule"


def test_parent_replacement_does_not_guess_child_text(case):
    amendment(case, target=A)
    assert K in run(case).blocked


@pytest.mark.parametrize("mutation", ["missing", "wrong_text", "bad_dates", "wrong_source", "wrong_target"])
def test_incomplete_or_unbound_operations_block(case, mutation):
    c = amendment(case)
    op = c["operations"][0]
    if mutation == "missing":
        del op["replacement"]
    elif mutation == "wrong_text":
        op["replacement"]["text"] = "invented"
    elif mutation == "bad_dates":
        op["effective_on"] = "2025-02-02"
    elif mutation == "wrong_source":
        op["source_unit"] = K
    else:
        op["target_id"] = S
    case.flush()
    result = run(case)
    assert K in result.blocked
    assert not result.items


@pytest.mark.parametrize("mutation", ["corpus", "source", "unpinned", "escape", "absolute"])
def test_source_integrity_rejects_mutation_and_path_escape(case, mutation):
    amendment(case)
    if mutation == "corpus":
        case.corpus.write_bytes(case.corpus.read_bytes() + b" ")
    elif mutation == "source":
        case.raw.write_bytes(case.raw.read_bytes() + b"!")
    else:
        c = case.review["candidates"][0]
        path = {"unpinned": "unknown.txt", "escape": "../body.txt", "absolute": str(case.raw)}[mutation]
        c["evidence"] = {**case.ref, "path": path}
        case.review["pins"][path] = digest(case.raw)
        save(case.ledger, case.review)
    with pytest.raises(ValueError):
        run(case)


def test_cached_loader_cannot_hide_corpus_mutation(case):
    assert provision(run(case)).text == "old rule"
    case.data[0]["articles"][0]["clauses"][0]["content"] = "changed"
    case.flush()
    with pytest.raises(ValueError):
        run(case)


def test_mixed_span_container_and_unscoped_inheritance_block(case):
    case.data[0]["articles"][0]["provision_spans"] = [
        {"subject": "part only", "valid_from": "2025-04-01", "valid_to": "9999-12-31"}]
    case.flush()
    result = run(case, [A, K])
    assert {A, K}.issubset(result.blocked)


def test_explicit_span_scope_preserves_unaffected_sibling(case):
    case.data[0]["articles"][0]["provision_spans"] = [
        {"target_id": K, "valid_from": "2025-04-01", "valid_to": "2025-07-01"}]
    case.flush()
    result = run(case, [A, K, S], day="2025-03-01")
    assert set(result.blocked) == {A, K}
    assert provision(result, S).text == "sibling"


@pytest.mark.parametrize("day,allowed", [("2024-01-31", False), ("2024-02-01", True),
                                        ("2025-06-01", True), ("2025-06-02", False)])
def test_base_endpoints_inclusive(case, day, allowed):
    case.data[0]["valid_to"] = "2025-06-01"
    case.flush()
    result = run(case, day=day)
    assert bool(result.items) is allowed


def test_unknown_and_missing_review_are_explicit_blockers(case):
    assert "unknown" in run(case, ["unknown"]).blocked
    result = resolve([K], "2025-06-01", corpus_path=str(case.corpus))
    assert K in result.blocked


@pytest.mark.parametrize("day", ["2025-02-30", "2025-2-01"])
def test_invalid_dates_rejected(case, day):
    with pytest.raises(ValueError):
        run(case, day=day)


@pytest.mark.parametrize("mutation", ["extra_operation", "conditional_scope", "outscope_target",
                                    "source_not_reviewed", "source_ref_wrong_owner"])
def test_review_cannot_authorize_unproven_operations(case, mutation):
    c = amendment(case)
    if mutation == "extra_operation":
        c["operations"].append({**c["operations"][0], "target_id": S})
    elif mutation == "conditional_scope":
        c["expected"]["scope"] = "conditional_document_replacement"
    elif mutation == "outscope_target":
        c["classification"] = "outscope"
    elif mutation == "source_not_reviewed":
        case.review["documents"][1]["review_status"] = "pending"
    else:
        case.review["documents"][1]["source_paths"] = []
    case.flush()
    assert K in run(case).blocked


def test_subarticle_unknown_scope_does_not_expose_stale_child(case):
    c = amendment(case, target=A, usable=False)
    c["expected"]["scope"] = "unresolved_subarticle"
    c["classification"] = "unresolved"
    case.flush()
    assert K in run(case).blocked


def test_missing_source_document_pin_rejects_unchanged_evidence(case):
    case.review["documents"][0]["source_paths"] = []
    case.flush()
    assert K in run(case).blocked


def test_explicit_span_uses_inclusive_interval(case):
    case.data[0]["articles"][0]["clauses"][0]["provision_spans"] = [
        {"target_id": K, "valid_from": "2025-04-01", "valid_to": "2025-07-01"}]
    case.flush()
    for day in ("2025-04-01", "2025-07-01"):
        assert provision(run(case, day=day)).valid_to == "2025-07-01"
    assert K in run(case, day="2025-07-02").blocked


def test_repealed_text_cannot_be_resurrected_by_later_replace(case):
    amendment(case, action="repeal")
    amendment(case, effective="2025-04-01", identity="later")
    assert K in run(case).blocked


@pytest.mark.parametrize("mutation", ["scope_kind", "insert_scope", "duplicate_id", "duplicate_doc",
                                    "null_path", "foreign_date_ref"])
def test_malformed_ownership_and_scope_fail_closed(case, mutation):
    c = amendment(case)
    if mutation == "scope_kind":
        c["expected"]["scope"] = "point"
    elif mutation == "insert_scope":
        c["operations"][0]["action"] = "insert"
        c["operations"][0]["parent_id"] = A
    elif mutation == "duplicate_id":
        case.data[0]["articles"][0]["clauses"].append({"id": K, "content": "shadow"})
    elif mutation == "duplicate_doc":
        case.data.append(case.data[0])
    elif mutation == "null_path":
        c["evidence"]["path"] = "bad\x00name"
        case.review["pins"]["bad\x00name"] = digest(case.raw)
    else:
        case.review["documents"][1]["source_paths"] = []
    case.flush()
    try:
        result = run(case)
    except ValueError:
        return
    assert K in result.blocked


def test_unchanged_provenance_includes_verified_ledger_sources(case):
    p = provision(run(case))
    assert p.provenance[0]["sources"] == [{"path": "body.txt", "sha256": digest(case.raw)}]


def test_container_insertion_is_blocked_until_full_structure_reviewed(case):
    c = amendment(case, target=A + "_K3", action="insert")
    c["expected"]["scope"] = "clause_insertion"
    c["operations"][0]["parent_id"] = A
    case.flush()
    assert A in run(case, [A]).blocked


def test_conflicting_exact_span_rules_block(case):
    case.data[0]["articles"][0]["clauses"][0]["provision_spans"] = [
        {"target_id": K, "valid_from": "2025-04-01", "valid_to": "2025-07-01"},
        {"target_id": K, "valid_from": "2025-05-01", "valid_to": "2025-08-01"}]
    case.flush()
    assert K in run(case).blocked


@pytest.mark.parametrize("usable", [False, True])
def test_container_preserves_safe_siblings_without_citable_parent(case, usable):
    amendment(case, usable=usable)
    result = run(case, [A])
    item = result.items[0]
    assert item.id == A
    assert item.citable is False
    assert A in result.blocked
    assert A not in {p.id for p in item.provisions}
    assert provision(result, S).text == "sibling"
    if usable:
        assert provision(result, K).text == "new rule"
    else:
        assert K in result.blocked
        assert K not in {p.id for p in item.provisions}


def test_nested_container_blocks_exact_ids_but_keeps_unaffected_points(case):
    point = K + "_a"
    sibling = K + "_b"
    case.data[0]["articles"][0]["clauses"][0]["points"] = [
        {"id": point, "content": "point"}, {"id": sibling, "content": "safe point"}]
    c = amendment(case, target=K, usable=False)
    c["expected"] = {"scope": "point", "targets": [{"doc_id": "LAW_1_2024", "article_id": A,
                       "clause_id": K, "point_id": point}]}
    case.flush()
    result = run(case, [A])
    assert {A, K, point} <= result.blocked.keys()
    assert {p.id for p in result.items[0].provisions} == {sibling, S}


@pytest.mark.parametrize("mutation", ["fabricated_excerpt", "bad_locator", "agreeing_effective",
                                    "agreeing_issued", "irrelevant_date", "unapproved_exception",
                                    "wrong_exception_scope", "false_review_excerpt"])
def test_dates_require_independent_metadata_and_bound_source_text(case, mutation):
    c = amendment(case)
    interval, op = c["source_effective_intervals"][0], c["operations"][0]
    if mutation == "fabricated_excerpt":
        interval["issuance_evidence"] = {**case.ref, "excerpt": "invented 2025-03-01"}
    elif mutation == "bad_locator":
        interval["commencement_evidence"] = {**interval["commencement_evidence"],
                                             "locator": {"normalized_text_start": 0}}
    elif mutation == "agreeing_effective":
        interval["from"] = op["effective_on"] = "2025-02-02"
    elif mutation == "agreeing_issued":
        interval["issued_on"] = op["issued_on"] = "2025-03-02"
    elif mutation == "irrelevant_date":
        interval["commencement_evidence"] = op["commencement_evidence"] = case.ref
    elif mutation in {"unapproved_exception", "wrong_exception_scope"}:
        c["source_unit"] = op["source_unit"] = SOURCE + "_K2"
        interval["from"] = op["effective_on"] = "2025-04-01"
        interval["commencement_evidence"] = op["commencement_evidence"] = {
            **case.ref, "excerpt": "Clause 2 effective 2025-04-01."}
        exception = case.review["documents"][1]["provision_effective_dates"][0]
        if mutation == "unapproved_exception":
            exception["mapping_status"] = "pending"
        else:
            exception["target_ids"] = [SOURCE + "_K1"]
    else:
        case.review["documents"][1]["date_evidence"]["effective_excerpt"] = "fabricated"
    case.flush()
    try:
        result = run(case)
    except ValueError:
        return
    assert K in result.blocked


@pytest.mark.parametrize("event_day,blocked", [("2025-01-15", True), ("2025-02-01", True),
                                            ("2025-04-01", False)])
def test_unresolved_source_amendment_blocks_operation_at_its_event_date(case, event_day, blocked):
    amendment(case)
    dependency = amendment(case, usable=False, effective=event_day, identity="source-change")
    dependency["source_doc"] = "LAW_1_2024"
    dependency["source_unit"] = S
    case.review["documents"][0]["provision_effective_dates"] = [{
        "target_ids": [S], "effective_on": event_day, "mapping_status": "approved"}]
    dependency["classification"] = "unresolved"
    dependency["expected"] = {"scope": "clause", "targets": [
        {"doc_id": "LAW_2_2025", "article_id": SOURCE, "clause_id": SOURCE + "_K1"}]}
    case.flush()
    result = run(case, day="2026-01-01")
    assert (K in result.blocked) is blocked
    if not blocked:
        assert provision(result).text == "new rule"


@pytest.mark.parametrize("usable", [False, True])
def test_valid_to_stops_before_next_known_event(case, usable):
    amendment(case, usable=usable)
    p = provision(run(case, day="2025-01-31"))
    assert p.valid_to == "2025-01-31"
    assert provision(run(case, [S], day="2025-01-31"), S).valid_to == "9999-12-31"


def test_chain_versions_stop_before_next_change(case):
    amendment(case)
    amendment(case, effective="2025-04-01", identity="later")
    p = provision(run(case, day="2025-03-31"))
    assert (p.text, p.valid_to) == ("new rule", "2025-03-31")


@pytest.mark.parametrize("kind", ["html", "snapshot_html", "snapshot_text"])
def test_date_evidence_decodes_actual_source_shapes(case, kind):
    text = case.raw.read_text(encoding="utf-8")
    name = "body.html" if kind == "html" else "body.snapshot.json"
    html = "<div>" + "".join("<p>" + line.replace(" ", "&#32;") + "</p>" for line in text.splitlines()) + "</div>"
    path = case.root / name
    path.write_text(html if kind == "html" else json.dumps(
        {"html": html} if kind == "snapshot_html" else {"innerText": text}), encoding="utf-8")
    case.review["pins"][name] = digest(path)
    for d in case.review["documents"]:
        d["source_paths"].append(name)
    c = amendment(case)
    for owner in [c["source_effective_intervals"][0], c["operations"][0]]:
        for key in ("issuance_evidence", "commencement_evidence"):
            owner[key] = {**owner[key], "path": name, "sha256": digest(path)}
    case.review["documents"][1]["date_evidence"]["path"] = name
    case.flush()
    assert provision(run(case)).text == "new rule"


@pytest.mark.parametrize("mutation", ["op_excerpt", "op_locator", "review_issued", "review_effective",
                                    "unsupported_locator", "invalid_iso_suffix"])
def test_independent_date_binding_rejects_additional_mutations(case, mutation):
    c = amendment(case)
    op = c["operations"][0]
    if mutation == "op_excerpt":
        op["issuance_evidence"] = {**case.ref, "excerpt": "old rule"}
    elif mutation == "op_locator":
        op["commencement_evidence"] = {**op["commencement_evidence"],
                                      "locator": {"normalized_text_start": 1}}
    elif mutation == "review_issued":
        case.review["documents"][1]["issued_on"] = "2025-03-02"
    elif mutation == "review_effective":
        case.review["documents"][1]["effective_on"] = "2025-02-02"
    elif mutation == "unsupported_locator":
        op["issuance_evidence"] = {**op["issuance_evidence"], "locator": {"block_index": 999}}
    else:
        ref = {**case.ref, "excerpt": "Issued 2025-03-010."}
        raw = case.raw.read_text().replace("Issued 2025-03-01.", ref["excerpt"])
        case.raw.write_text(raw, encoding="utf-8")
        case.review["pins"]["body.txt"] = digest(case.raw)
        for owner in [c, c["source_effective_intervals"][0], op]:
            for key, value in owner.items():
                if isinstance(value, dict) and value.get("path") == "body.txt":
                    value["sha256"] = digest(case.raw)
        op["issuance_evidence"] = {**ref, "sha256": digest(case.raw)}
        c["source_effective_intervals"][0]["issuance_evidence"] = op["issuance_evidence"]
        case.review["documents"][1]["date_evidence"]["issued_excerpt"] = ref["excerpt"]
        c["source_effective_intervals"][0]["date_evidence_refs"] = [op["commencement_evidence"]]
        exception = case.review["documents"][1]["provision_effective_dates"][0]
        exception["evidence"]["sha256"] = digest(case.raw)
    case.flush()
    try:
        result = run(case)
    except ValueError:
        return
    assert K in result.blocked


def test_verified_normalized_date_locator_is_accepted(case):
    c = amendment(case)
    text = " ".join(case.raw.read_text().split())
    for owner in [c["source_effective_intervals"][0], c["operations"][0]]:
        for key in ("issuance_evidence", "commencement_evidence"):
            owner[key] = {**owner[key], "locator": {"normalized_text_start": text.index(owner[key]["excerpt"])}}
    case.flush()
    assert provision(run(case)).text == "new rule"


def test_future_parent_event_caps_child_but_not_other_article(case):
    case.data[0]["articles"].append({"id": "LAW_1_2024_D2", "content": "separate"})
    amendment(case, target=A, usable=False)
    result = run(case, [K, "LAW_1_2024_D2"], day="2025-01-31")
    assert provision(result).valid_to == "2025-01-31"
    assert provision(result, "LAW_1_2024_D2").valid_to == "9999-12-31"


def test_source_sibling_uncertainty_does_not_block_independent_operation(case):
    amendment(case)
    dependency = amendment(case, usable=False, identity="source-sibling")
    dependency["classification"] = "unresolved"
    dependency["expected"] = {"scope": "clause", "targets": [
        {"doc_id": "LAW_2_2025", "article_id": SOURCE, "clause_id": SOURCE + "_K2"}]}
    case.flush()
    assert provision(run(case)).text == "new rule"


def test_relation_only_date_mutation_cannot_hide_known_amendment(case):
    c = amendment(case, usable=False)
    c["source_effective_intervals"][0]["from"] = "2025-10-01"
    case.flush()
    assert K in run(case, day="2025-06-01").blocked


def test_vietnamese_date_excerpts_are_verified_without_inference(case):
    raw = case.raw.read_text().replace("Issued 2025-03-01.", "Ban hành ngày 01 tháng 3 năm 2025.")
    raw = raw.replace("Effective retroactively 2025-02-01.", "Hiệu lực từ ngày 01 tháng 02 năm 2025.")
    case.raw.write_text(raw, encoding="utf-8")
    case.review["pins"]["body.txt"] = digest(case.raw)
    case.review["documents"][1]["provision_effective_dates"][0]["evidence"]["sha256"] = digest(case.raw)
    case.ref["sha256"] = digest(case.raw)
    c = amendment(case)
    issuance = {**case.ref, "excerpt": "Ban hành ngày 01 tháng 3 năm 2025."}
    commencement = {**case.ref, "excerpt": "Hiệu lực từ ngày 01 tháng 02 năm 2025."}
    for owner in [c["source_effective_intervals"][0], c["operations"][0]]:
        owner["issuance_evidence"] = issuance
        owner["commencement_evidence"] = commencement
    c["source_effective_intervals"][0]["date_evidence_refs"] = [commencement]
    case.review["documents"][1]["date_evidence"] = {
        "path": "body.txt", "issued_excerpt": issuance["excerpt"],
        "effective_excerpt": commencement["excerpt"]}
    case.flush()
    assert provision(run(case)).text == "new rule"


@pytest.mark.parametrize("mutation", [None, "text", "offset", "end", "scope", "approval"])
def test_normalized_snapshot_operation_independent_of_relation(case, mutation):
    c = amendment(case)
    c["classification"] = "unresolved"
    c["expected"]["emit_resolved"] = False
    c["operative_scope"] = {"status": "approved", "scope": "clause",
                            "targets": c["expected"]["targets"], "evidence_refs": [case.ref]}
    text = " ".join(case.raw.read_text().split())
    start = text.index("new rule")
    c["operations"][0]["replacement"] = {
        "path": "body.txt", "sha256": digest(case.raw), "text": "new rule",
        "locator": {"normalized_text_start": start, "normalized_text_end": start + 8}}
    if mutation == "text":
        c["operations"][0]["replacement"]["text"] = "fabricated"
    elif mutation in {"offset", "end"}:
        key = "normalized_text_start" if mutation == "offset" else "normalized_text_end"
        c["operations"][0]["replacement"]["locator"][key] += 1
    elif mutation == "scope":
        c["operative_scope"]["targets"] = [{"doc_id": "LAW_1_2024", "article_id": A, "clause_id": S}]
    elif mutation == "approval":
        c["operative_scope"]["status"] = "pending"
    case.flush()
    result = run(case)
    if mutation:
        assert K in result.blocked
    else:
        assert provision(result).text == "new rule"
        assert c["classification"] == "unresolved"


@pytest.mark.parametrize("mutation", [None, "missing", "candidate", "target", "effective", "evidence"])
def test_same_date_requires_explicit_reviewed_supersession(case, mutation):
    amendment(case)
    c = amendment(case, identity="second")
    op = c["operations"][0]
    op["replacement"] = binding(case, "final rule")
    op["supersedes"] = {"candidate_id": "change", "target_id": K,
                        "effective_on": "2025-02-01", "status": "approved",
                        "evidence_refs": [case.ref]}
    if mutation == "missing":
        del op["supersedes"]
    elif mutation:
        key, value = {"candidate": ("candidate_id", "unknown"), "target": ("target_id", S),
                      "effective": ("effective_on", "2025-02-02"),
                      "evidence": ("evidence_refs", [{**case.ref, "excerpt": "invented"}])}[mutation]
        op["supersedes"][key] = value
    case.review["candidates"].reverse()
    case.flush()
    result = run(case)
    if mutation:
        assert K in result.blocked
    else:
        assert provision(result).text == "final rule"
        assert [p["candidate_id"] for p in provision(result).provenance[1:]] == ["change", "second"]


@pytest.mark.parametrize("complete", [True, False])
def test_group_coverage_narrows_only_when_all_children_are_reviewed(case, complete):
    child = amendment(case)
    group = amendment(case, target=A, identity="group", usable=False)
    group["source_unit"] = SOURCE
    group["classification"] = "unresolved"
    group["expected"]["scope"] = "group"
    group["coverage"] = {"status": "approved", "candidate_ids": ["change"] if complete else [],
                         "evidence_refs": [case.ref]}
    case.flush()
    result = run(case, [K, S])
    if complete:
        assert provision(result).text == "new rule"
        assert provision(result, S).text == "sibling"
    else:
        assert {K, S} <= result.blocked.keys()


@pytest.mark.parametrize("bound", [True, False])
def test_parent_replacement_propagates_only_explicit_bound_subunit_mapping(case, bound):
    c = amendment(case, target=A)
    op = c["operations"][0]
    op["subunits"] = {K: binding(case, "new rule")}
    if not bound:
        op["subunits"][K]["text"] = "invented"
    case.flush()
    result = run(case, [K, S])
    assert S in result.blocked
    if bound:
        assert provision(result).text == "new rule"
    else:
        assert K in result.blocked


def test_targetless_group_without_complete_coverage_keeps_child_blockers(case):
    amendment(case)
    group = amendment(case, target=A, identity="group", usable=False)
    group["source_unit"] = SOURCE
    group["classification"] = "unresolved"
    group["expected"] = {"scope": "group", "targets": []}
    case.flush()
    assert K in run(case).blocked


def test_real_corpus_shape_and_relation_only_fail_closed():
    root = Path(__file__).resolve().parents[1]
    corpus = root / "data/processed/vat_corpus.staging.json"
    review = root / "tests/fixtures/amendment_review.staging.json"
    data = json.loads(corpus.read_text(encoding="utf-8"))
    ledger = json.loads(review.read_text(encoding="utf-8"))
    assert any(candidate.get("operations") for candidate in ledger["candidates"])
    operative = resolve(["TT_72_2014_D7_K9"], "2020-07-01",
                        corpus_path=str(corpus), reviewed_amendment_path=str(review))
    assert provision(operative, "TT_72_2014_D7_K9").status == "AMENDED"
    assert len(data) == 33
    result = resolve(["TT_18_2026_D1", "ND_174_2025_D1_K1_a"], "2026-06-01",
                     corpus_path=str(corpus), reviewed_amendment_path=str(review))
    assert provision(result, "TT_18_2026_D1").status == "AMENDED"
    p = provision(result, "ND_174_2025_D1_K1_a")
    assert p.text == data[0]["articles"][0]["clauses"][0]["points"][0]["content"]
    gap = resolve(["ND_68_2026_D8_K1"], "2026-07-01", corpus_path=str(corpus), reviewed_amendment_path=str(review))
    assert "ND_68_2026_D8_K1" in gap.blocked


def test_consolidation_reference_status_without_role_is_rejected(case):
    case.data[0]["status"] = "CONSOLIDATION_REFERENCE"
    case.flush()
    result = run(case, [K])
    assert not result.items
    assert "consolidation" in result.blocked[K].lower()


def test_active_inserted_children_are_discovered_from_parent_query(case):
    target_k = A + "_K3"
    c1 = amendment(case, target=target_k, action="insert", identity="ins-clause")
    c1["expected"]["scope"] = "clause_insertion"
    c1["operations"][0]["parent_id"] = A
    target_p = K + "_a"
    c2 = amendment(case, target=target_p, action="insert", identity="ins-point")
    c2["expected"] = {"scope": "point_insertion", "action": "bo_sung",
                      "targets": [{"doc_id": "LAW_1_2024", "article_id": A, "clause_id": K, "point_id": target_p}]}
    c2["operations"][0]["parent_id"] = K
    case.flush()

    before = run(case, [A], day="2025-01-31")
    assert {p.id for p in before.items[0].provisions} == {A, K, S}
    assert provision(before, A).valid_to == "2025-01-31"

    after = run(case, [A], day="2025-02-01")
    assert {A, K}.issubset(after.blocked)
    ids = {p.id for p in after.items[0].provisions}
    assert ids == {S, target_k, target_p}
    assert provision(after, target_k).status == "INSERTED"
    assert provision(after, target_p).status == "INSERTED"
    assert provision(after, target_k).text == "inserted rule"


def test_article_insertion_resolves_on_and_after_commencement(case):
    target_art = "LAW_1_2024_D2a"
    c = amendment(case, target=target_art, action="insert", identity="ins-art")
    c["expected"] = {"scope": "article_insertion", "action": "bo_sung",
                     "targets": [{"doc_id": "LAW_1_2024", "article_id": target_art}]}
    c["operations"][0]["parent_id"] = "LAW_1_2024"
    case.flush()

    assert target_art in run(case, [target_art], day="2025-01-31").blocked
    after = run(case, [target_art], day="2025-02-01")
    assert target_art not in after.blocked
    p = provision(after, target_art)
    assert p.status == "INSERTED"
    assert p.text == "inserted rule"


@pytest.mark.parametrize("expected_count,blocked", [(None, True), (2, True), (3, False)])
def test_phrase_replace_supports_explicit_expected_count(case, expected_count, blocked):
    case.data[0]["articles"][0]["clauses"][0]["content"] = "old a old b old"
    c = amendment(case, action="phrase_replace")
    if expected_count is not None:
        c["operations"][0]["expected_count"] = expected_count
    case.flush()
    result = run(case)
    assert (K in result.blocked) is blocked
    if not blocked:
        assert provision(result).text == "new a new b new"


def test_enumerated_subarticles_correction_and_subarticle_coverage(case):
    target_k3 = A + "_K3"
    c = amendment(case, target=K, identity="enum-multi")
    c["classification"] = "unresolved"
    c["expected"] = {"scope": "enumerated_subarticles", "targets": [
        {"doc_id": "LAW_1_2024", "article_id": A, "clause_id": K},
        {"doc_id": "LAW_1_2024", "article_id": A, "clause_id": target_k3},
    ]}
    c["operative_scope"] = {"status": "approved", "scope": "enumerated_subarticles",
                            "targets": c["expected"]["targets"], "evidence_refs": [case.ref]}
    op2 = deepcopy(c["operations"][0])
    op2.update(target_id=target_k3, action="insert", parent_id=A, replacement=binding(case, "inserted rule"))
    c["operations"].append(op2)

    parent = amendment(case, target=A, identity="subart-parent", usable=False)
    parent["source_unit"] = SOURCE
    parent["classification"] = "unresolved"
    parent["expected"]["scope"] = "unresolved_subarticle"
    parent["coverage"] = {"status": "approved", "candidate_ids": ["enum-multi"], "evidence_refs": [case.ref]}
    case.flush()

    res = run(case, [K, target_k3, S])
    assert provision(res, K).text == "new rule"
    assert provision(res, target_k3).text == "inserted rule"
    assert provision(res, S).text == "sibling"

    parent["coverage"]["candidate_ids"] = []
    case.flush()
    assert S in run(case, [S]).blocked


def test_clause_text_correction_allows_clause_only(case):
    c = amendment(case, target=K, identity="correction")
    c["classification"] = "unresolved"
    c["expected"]["scope"] = "clause_text_correction"
    c["operative_scope"] = {"status": "approved", "scope": "clause_text_correction",
                            "targets": c["expected"]["targets"], "evidence_refs": [case.ref]}
    case.flush()
    assert provision(run(case), K).text == "new rule"


@pytest.mark.parametrize("effective,excerpt,ok", [
    ("2025-03-01", "có hiệu lực thi hành kể từ ngày ký.", True),
    ("2025-03-02", "có hiệu lực thi hành kể từ ngày ký.", False),
    ("2025-03-01", "văn bản này được ban hành.", False),
])
def test_signing_date_commencement_verification(case, effective, excerpt, ok):
    raw = case.raw.read_text(encoding="utf-8") + f"\n{excerpt}\n"
    case.raw.write_text(raw, encoding="utf-8")
    case.review["pins"]["body.txt"] = digest(case.raw)
    case.review["documents"][1]["provision_effective_dates"][0]["evidence"]["sha256"] = digest(case.raw)
    case.ref["sha256"] = digest(case.raw)
    doc = case.review["documents"][1]
    doc["effective_on"] = effective
    doc["date_evidence"] = {"path": "body.txt", "issued_excerpt": "Issued 2025-03-01.", "effective_excerpt": excerpt}
    c = amendment(case, effective=effective)
    iv = c["source_effective_intervals"][0]
    op = c["operations"][0]
    comm = {**case.ref, "excerpt": excerpt}
    iv.update(commencement_basis="signing", commencement_evidence=comm, date_evidence_refs=[comm])
    iv["issuance_evidence"]["sha256"] = digest(case.raw)
    op.update(commencement_evidence=comm, issuance_evidence=iv["issuance_evidence"])
    op["replacement"]["sha256"] = digest(case.raw)
    c["evidence"]["sha256"] = digest(case.raw)
    case.flush()
    result = run(case, [K], day="2025-04-01")
    if ok:
        assert provision(result, K).text == "new rule"
    else:
        assert K in result.blocked


def test_conditional_document_replacement_with_except_targets(case):
    d2 = "LAW_1_2024_D2"
    case.data[0]["articles"].append({"id": d2, "content": "other article"})
    raw = case.raw.read_text(encoding="utf-8") + "\nĐiều 17 tiếp tục thực hiện.\n"
    case.raw.write_text(raw, encoding="utf-8")
    case.review["pins"]["body.txt"] = digest(case.raw)
    case.review["documents"][1]["provision_effective_dates"][0]["evidence"]["sha256"] = digest(case.raw)
    case.ref["sha256"] = digest(case.raw)
    ex_ref = {**case.ref, "excerpt": "Điều 17 tiếp tục thực hiện."}
    c = amendment(case, target=A, action="repeal", effective="2025-04-01", identity="doc-replace")
    c["classification"] = "unresolved"
    c["evidence"]["sha256"] = digest(case.raw)
    c["expected"] = {"scope": "conditional_document_replacement", "targets": [{"doc_id": "LAW_1_2024"}]}
    c["operative_scope"] = {"status": "approved", "scope": "conditional_document_replacement",
                            "targets": [{"doc_id": "LAW_1_2024"}], "evidence_refs": [case.ref]}
    c["source_effective_intervals"][0]["issuance_evidence"]["sha256"] = digest(case.raw)
    c["source_effective_intervals"][0]["commencement_evidence"]["sha256"] = digest(case.raw)
    c["source_effective_intervals"][0]["date_evidence_refs"][0]["sha256"] = digest(case.raw)
    c["operations"] = [{
        "candidate_id": "doc-replace", "source_unit": SOURCE + "_K2", "target_id": "LAW_1_2024",
        "action": "repeal", "effective_on": "2025-04-01", "issued_on": "2025-03-01",
        "except_targets": [S], "except_evidence": ex_ref,
        "issuance_evidence": c["source_effective_intervals"][0]["issuance_evidence"],
        "commencement_evidence": c["source_effective_intervals"][0]["commencement_evidence"],
    }]
    case.flush()

    before = run(case, [K, S, d2], day="2025-03-31")
    assert {p.id for item in before.items for p in item.provisions} == {K, S, d2}
    assert provision(before, d2).valid_to == "2025-03-31"

    after = run(case, [K, S, d2], day="2025-04-01")
    assert not after.items
    assert "repeal" in after.blocked[K].lower()
    assert "repeal" in after.blocked[d2].lower()
    assert "readiness" in after.blocked[S].lower()


def test_heading_only_phrase_replace_keeps_unaffected_children_citable(case):
    case.data[0]["articles"][0]["content"] = "Article old heading"
    case.data[0]["articles"][0]["clauses"][0]["content"] = "clause one rule"
    c = amendment(case, target=A, action="phrase_replace")
    c["operations"][0]["scope_heading_only"] = True
    case.flush()
    res = run(case, [A])
    assert not res.blocked
    assert provision(res, A).text == "Article new heading"
    assert provision(res, K).status == "UNCHANGED"


def test_parent_replacement_with_subunits_retired_and_new_child_stays_citable(case):
    case.data[0]["articles"][0]["clauses"].append(
        {"id": A + "_K4", "content": "other", "points": [{"id": A + "_K4_a", "content": "pt"}]}
    )
    raw = case.raw.read_text(encoding="utf-8") + "\nfull replacement: new k1; added k3\n"
    case.raw.write_text(raw, encoding="utf-8")
    case.review["pins"]["body.txt"] = digest(case.raw)
    case.review["documents"][1]["provision_effective_dates"][0]["evidence"]["sha256"] = digest(case.raw)
    case.ref["sha256"] = digest(case.raw)
    new_k3 = A + "_K3"
    c = amendment(case, target=A, action="replace", identity="parent-full")
    c["evidence"]["sha256"] = digest(case.raw)
    c["source_effective_intervals"][0]["issuance_evidence"]["sha256"] = digest(case.raw)
    c["source_effective_intervals"][0]["commencement_evidence"]["sha256"] = digest(case.raw)
    c["source_effective_intervals"][0]["date_evidence_refs"][0]["sha256"] = digest(case.raw)
    op = c["operations"][0]
    op["issuance_evidence"]["sha256"] = digest(case.raw)
    op["commencement_evidence"]["sha256"] = digest(case.raw)
    op["replacement"] = binding(case, "full replacement: new k1; added k3")
    op["subunits"] = {K: binding(case, "new k1"), new_k3: binding(case, "added k3"),
                      A + "_K4": binding(case, "new k1"), A + "_K4_a": binding(case, "new k1")}
    op["retired_subunits"] = [S]
    case.flush()

    res = run(case, [A])
    assert A not in res.blocked
    assert S not in {p.id for p in res.items[0].provisions}
    assert {p.id for p in res.items[0].provisions} == {A, K, new_k3, A + "_K4", A + "_K4_a"}
    assert provision(res, new_k3).text == "added k3"
    assert S in run(case, [S]).blocked

    op["retired_subunits"] = [K]
    case.flush()
    assert A in run(case, [A]).blocked

    op["retired_subunits"] = ["OTHER_DOC_K1"]
    case.flush()
    assert A in run(case, [A]).blocked

    op["retired_subunits"] = [A + "_K4"]
    del op["subunits"][A + "_K4"]
    del op["subunits"][A + "_K4_a"]
    case.flush()
    assert A + "_K4_a" in run(case, [A + "_K4_a"]).blocked

