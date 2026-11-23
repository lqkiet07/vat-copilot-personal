import hashlib
import json
import re
from datetime import date
from pathlib import Path

from src.ui.corpus_viewer import _CORPUS_CACHE, load_corpus_index, load_corpus_tree
from src.graph.connection import get_neo4j_driver
from src.verifier.models import CitationStatus, VerificationReport

_CITATION_REGEX = re.compile(
    r"(?<![\w\[])(\[(?:[A-Z]+_\d+_\d+)_D\d+[a-zđ]?(?:_K(?:\d+[a-zđ]?|[a-zđ])(?:_[a-zđ](?:_\d+)?)?)?\]|(?:LUAT|ND|TT|QD|NQ|VBHN)_\d+_\d+_D\d+[a-zđ]?(?:_K(?:\d+[a-zđ]?|[a-zđ])(?:_[a-zđ](?:_\d+)?)?)?)(?![\w\]])",
    re.IGNORECASE,
)

_VERIFY_CYPHER = """
UNWIND $ids AS cid
OPTIONAL MATCH (n) WHERE (n:Article OR n:Clause OR n:Point) AND n.id = cid
OPTIONAL MATCH (a:Article)-[:CONTAINS*0..2]->(n)
OPTIONAL MATCH (amender:Article)-[r:AMENDS]->(a)
OPTIONAL MATCH (source) WHERE source.id = r.source_unit
  AND (source:Article OR source:Clause OR source:Point)
RETURN cid,
       n IS NOT NULL AS exists,
       toString(n.valid_from) AS valid_from,
       toString(n.valid_to) AS valid_to,
       [x IN collect(DISTINCT amender.id) WHERE x IS NOT NULL] AS amended_by,
       collect(DISTINCT {
           amender_id: amender.id,
           scope: r.target_scope,
           target_clause: r.target_clause,
            target_point: r.target_point,
            source_unit: r.source_unit,
            valid_from: toString(CASE WHEN r.source_unit IS NULL THEN amender.valid_from ELSE source.valid_from END),
            valid_to: toString(CASE WHEN r.source_unit IS NULL THEN amender.valid_to ELSE source.valid_to END)
       }) AS amendments
"""


def _canonicalize_citation(cid: str) -> str:
    """Canonicalize citation ID to uppercase doc/article/clause, preserving lowercase point suffixes."""
    parts = re.split(r"_[kK]", cid, maxsplit=1)
    article = re.sub(r"(_D\d+)([A-ZĐ])$", lambda m: m[1] + m[2].lower(), parts[0].upper())
    if len(parts) == 2:
        doc_art = article
        clause_and_point = parts[1].split("_", 1)
        if len(clause_and_point) == 2:
            return f"{doc_art}_K{clause_and_point[0].lower()}_{clause_and_point[1].lower()}"
        return f"{doc_art}_K{clause_and_point[0].lower()}"
    return article


def extract_citations(text: str) -> list[str]:
    """Extract citation IDs enclosed in brackets or canonical legal prefixes matching the pattern."""
    matches = _CITATION_REGEX.findall(text)
    return list(dict.fromkeys(_canonicalize_citation(cid.strip("[]")) for cid in matches))


def _parse_cid_parts(cid: str) -> tuple[str, str | None, str | None]:
    """Return (article_id, clause_code, point_code) for a citation ID."""
    parts = cid.split("_K")
    art_id = parts[0]
    if len(parts) == 1:
        return art_id, None, None
    sub = parts[1].split("_", 1)
    kl_code = f"Khoan {sub[0]}"
    pt_code = f"Diem {sub[1].lower()}" if len(sub) == 2 else None
    return art_id, kl_code, pt_code


_META_CACHE: dict[tuple[str, str], dict[str, dict]] = {}
_PROJECTION_CACHE: dict[tuple[str, str, str], list[tuple[str, dict]]] = {}


def _load_verification_metadata(corpus_path: str) -> dict[str, dict]:
    target = Path(corpus_path)
    if not target.is_absolute() and not target.exists():
        target = Path(__file__).resolve().parents[2] / target
    target = target.resolve()
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    cache_key = (str(target), digest)
    cached = _META_CACHE.get(cache_key)
    if cached is not None:
        return {k: dict(v) for k, v in cached.items()}
    _CORPUS_CACHE.pop(str(target), None)
    index = load_corpus_index(str(target))
    for document in load_corpus_tree(str(target)):
        spans = [{**span, "source_id": document["id"]}
                 for span in document.get("provision_spans", [])]
        if spans:
            for article in index.values():
                if article["doc_id"] == document["id"]:
                    for meta in article["provisions"].values():
                        meta["inherited_provision_spans"].extend(spans)
    if len(_META_CACHE) >= 4:
        _META_CACHE.clear()
    _META_CACHE[cache_key] = index
    return {k: dict(v) for k, v in index.items()}


def _validate_review_evidence(ref: dict, pins: dict) -> None:
    if (not isinstance(ref, dict) or not isinstance(ref["excerpt"], str)
            or not ref["excerpt"].strip() or not isinstance(ref["path"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", ref["sha256"])
            or pins.get(ref["path"]) != ref["sha256"]):
        raise ValueError("Missing or unpinned reviewed amendment evidence")


def _review_intervals(candidate: dict, pins: dict) -> list[dict]:
    intervals = candidate["source_effective_intervals"]
    if not isinstance(intervals, list) or not intervals:
        raise ValueError("Missing reviewed amendment intervals")
    _validate_review_evidence(candidate["evidence"], pins)
    enriched = {"issuance_evidence", "commencement_evidence", "commencement_basis"}
    for interval in intervals:
        start = date.fromisoformat(interval["from"])
        if interval["to"] is not None and date.fromisoformat(interval["to"]) < start:
            raise ValueError("Invalid reviewed amendment interval")
        date.fromisoformat(interval["issued_on"])
        if {"end_status", "inventory_fields"}.intersection(interval):
            if not isinstance(interval.get("end_status"), str) or not interval["end_status"].strip():
                raise ValueError("Missing reviewed amendment end status")
            fields = interval.get("inventory_fields")
            if not isinstance(fields, list) or not fields or any(not isinstance(f, str) or not f for f in fields):
                raise ValueError("Missing reviewed amendment inventory fields")
        refs = interval["date_evidence_refs"]
        if not isinstance(refs, list) or not refs:
            raise ValueError("Missing reviewed amendment date evidence")
        present = enriched.intersection(interval)
        if present and present != enriched:
            raise ValueError("Incomplete reviewed amendment enrichment")
        if present:
            if not isinstance(interval["commencement_basis"], str) or not interval["commencement_basis"].strip():
                raise ValueError("Missing reviewed amendment commencement basis")
            refs = refs + [interval["issuance_evidence"], interval["commencement_evidence"]]
        for ref in refs:
            _validate_review_evidence(ref, pins)
    return intervals


def _review_target(target: dict, scope: str) -> str:
    keys = set(target)
    shapes = ({"doc_id"}, {"doc_id", "article_id"},
              {"doc_id", "article_id", "clause_id"},
              {"doc_id", "article_id", "clause_id", "point_id"})
    if keys not in shapes or not re.fullmatch(r"[A-Z]+_\d+_\d+", target["doc_id"]):
        raise ValueError("Invalid reviewed amendment target shape")
    if keys == {"doc_id"}:
        if scope not in {"document", "conditional_document_replacement", "conditional_document_and_article"}:
            raise ValueError("Document target requires document scope")
        return target["doc_id"]
    article = target["article_id"]
    if not re.fullmatch(re.escape(target["doc_id"]) + r"_D\d+[a-zđ]?", article):
        raise ValueError("Invalid reviewed amendment article")
    clause = target.get("clause_id")
    point = target.get("point_id")
    if "clause_id" in keys and not re.fullmatch(re.escape(article) + r"_K(?:\d+[a-zđ]?|[a-zđ])", clause):
        raise ValueError("Invalid reviewed amendment clause hierarchy")
    if "point_id" in keys and not re.fullmatch(re.escape(clause) + r"_[a-zđ](?:_\d+)?", point):
        raise ValueError("Invalid reviewed amendment point hierarchy")
    return point or clause or article


def _ledger_uncertainties(ledger: dict, index: dict) -> list[tuple[str, dict]]:
    loaded_docs = {article["doc_id"] for article in index.values()}
    projections = []
    for candidate in ledger["candidates"]:
        classification = candidate["classification"]
        if classification in {"nonoperative", "outscope"}:
            continue
        if classification not in {"resolved", "unresolved"}:
            raise ValueError("Unknown reviewed amendment classification")
        spec = candidate["expected"]
        if not isinstance(spec["targets"], list) or not isinstance(spec["scope"], str) or not spec["scope"]:
            raise ValueError("Invalid reviewed amendment scope")
        targets = [(target, _review_target(target, spec["scope"])) for target in spec["targets"]]
        relevant = [(target, unit) for target, unit in targets if target["doc_id"] in loaded_docs]
        if not relevant:
            continue
        intervals = _review_intervals(candidate, ledger["pins"])
        if not isinstance(candidate["id"], str) or not candidate["id"] or not candidate["source_unit"]:
            raise ValueError("Missing reviewed amendment identity")
        for target, unit in relevant:
            article = target.get("article_id")
            if article and article not in index and spec["scope"] != "article_insertion":
                raise ValueError(f"Unknown reviewed amendment article: {article}")
            if article in index and index[article]["doc_id"] != target["doc_id"]:
                raise ValueError("Reviewed amendment document mismatch")
            if classification == "unresolved":
                owners = [article] if article else [aid for aid, meta in index.items() if meta["doc_id"] == unit]
                for owner in owners:
                    projections.append((owner, {
                        "target_id": unit, "scope": spec["scope"], "intervals": intervals,
                        "evidence": candidate["evidence"], "candidate_id": candidate["id"],
                        "source_unit": candidate["source_unit"], "doc_id": target["doc_id"],
                    }))
    return projections


def _load_reviewed_amendments(review_path: str, corpus_path: str, index: dict) -> None:
    target = Path(corpus_path)
    if not target.is_absolute() and not target.exists():
        target = Path(__file__).resolve().parents[2] / target
    try:
        review_bytes = Path(review_path).read_bytes()
        review_sha = hashlib.sha256(review_bytes).hexdigest()
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        cache_key = (str(Path(review_path).resolve()), review_sha, digest)
        projections = _PROJECTION_CACHE.get(cache_key)
        if projections is None:
            ledger = json.loads(review_bytes.decode("utf-8"))
            if (ledger["schema_version"] != 1 or ledger["oracle"] != "independent_raw_source_review"
                    or ledger["review_complete"] is not True):
                raise ValueError("Unsupported or incomplete amendment review")
            pinned = [pin for name, pin in ledger["pins"].items()
                      if any((root / name).resolve() == target.resolve() for root in target.resolve().parents)]
            if pinned != [digest]:
                raise ValueError("Reviewed amendment corpus hash mismatch")
            projections = _ledger_uncertainties(ledger, index)
            if len(_PROJECTION_CACHE) >= 8:
                _PROJECTION_CACHE.clear()
            _PROJECTION_CACHE[cache_key] = projections
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid reviewed amendment ledger") from exc
    for owner, projection in projections:
        article = index.setdefault(owner, {"doc_id": projection["doc_id"], "provisions": {}})
        article["reviewed_uncertainties"] = [*article.get("reviewed_uncertainties", ()), projection]


def _reviewed_uncertainty(cid: str, as_of_date: str, corpus_meta: dict | None) -> str | None:
    article = (corpus_meta or {}).get(_parse_cid_parts(cid)[0], {})
    reasons = []
    for review in article.get("reviewed_uncertainties", []):
        target = review["target_id"]
        overlaps = cid == target or target.startswith(cid + "_")
        if review["scope"] != "unresolved_subarticle":
            overlaps = overlaps or cid.startswith(target + "_")
        if overlaps and any(interval["from"] <= as_of_date for interval in review["intervals"]):
            reasons.append(f"phạm vi sửa đổi chưa được hợp nhất: {review['source_unit']} ({review['candidate_id']}).")
    return " ".join(dict.fromkeys(reasons)) or None


def _unresolved(cid: str, reason: str) -> CitationStatus:
    return CitationStatus(id=cid, status="UNRESOLVED", exists=True, is_active=None,
                          message=f"Điều khoản {cid}: {reason}", amended_by=[], in_context=True)


def _has_dates(meta: dict) -> bool:
    try:
        start = date.fromisoformat(meta.get("valid_from") or "")
        end = date.fromisoformat(meta.get("valid_to") or "")
    except (ValueError, TypeError):
        return False
    return start <= end


def _scope_metadata(cid: str, corpus_meta: dict | None) -> tuple[dict, list[dict]]:
    article = (corpus_meta or {}).get(_parse_cid_parts(cid)[0], {})
    provisions = article.get("provisions", {})
    meta = provisions.get(cid, {})
    spans = meta.get("provision_spans", []) + meta.get("inherited_provision_spans", [])
    spans += [span for child_id in meta.get("descendant_ids", [])
              for span in provisions.get(child_id, {}).get("provision_spans", [])]
    return meta, spans


def _matches_amendment(cid: str, amd: dict) -> bool | None:
    """Determine whether a scoped AMENDS relation targets the citation."""
    if not amd or not amd.get("amender_id"):
        return False
    scope = (amd.get("scope") or "").lower()
    tgt_kl = (amd.get("target_clause") or "").strip()
    tgt_pt = (amd.get("target_point") or "").strip().lower()
    if scope not in {"article", "clause", "point"}:
        return None
    if scope in {"clause", "point"} and not re.fullmatch(r"Khoan (?:\d+|[a-zđ])", tgt_kl):
        return None
    if scope == "point" and not re.fullmatch(r"diem [a-zđ]", tgt_pt):
        return None

    art_id, kl, pt = _parse_cid_parts(cid)

    if scope == "article":
        return True
    if scope == "clause":
        if kl is None:
            return True
        return kl == tgt_kl
    if scope == "point":
        if pt is not None and kl is not None:
            return kl == tgt_kl and pt.lower() == tgt_pt
        if kl is not None:
            return kl == tgt_kl
        return True
    return True


def _evaluate_status(
    row: dict,
    as_of_date: str,
    is_relevant: bool = True,
    in_context: bool = True,
    corpus_meta: dict[str, dict] | None = None,
) -> CitationStatus:
    """Evaluate validity status of a single citation record against as_of_date and relevance."""
    cid = row["cid"]
    if not in_context:
        return CitationStatus(
            id=cid,
            status="IRRELEVANT",
            exists=row["exists"],
            is_active=False,
            message=f"Điều khoản {cid} không nằm trong tài liệu căn cứ được cung cấp cho câu hỏi.",
            amended_by=[],
            in_context=False,
        )

    if not row["exists"]:
        return CitationStatus(
            id=cid, status="NOT_FOUND", exists=False, is_active=False,
            message=f"Không tìm thấy điều khoản {cid} trong cơ sở dữ liệu tri thức.",
            amended_by=[],
            in_context=True,
        )

    if not is_relevant:
        return CitationStatus(
            id=cid, status="IRRELEVANT", exists=True, is_active=False,
            message=f"Điều khoản {cid} không liên quan trực tiếp đến nội dung câu hỏi.",
            amended_by=[],
            in_context=True,
        )

    meta, spans = _scope_metadata(cid, corpus_meta)
    if not meta:
        return _unresolved(cid, "chưa đủ metadata chính xác trong corpus để xác minh hiệu lực.")
    if meta.get("status") == "CONSOLIDATION_REFERENCE" or "consolidation" in (meta.get("document_role") or "").lower():
        return _unresolved(cid, "thuộc Văn bản hợp nhất (tài liệu đối chiếu), chưa đủ dữ liệu để xác minh hiệu lực độc lập.")
    reviewed_reason = _reviewed_uncertainty(cid, as_of_date, corpus_meta)
    if spans:
        reason = "có hiệu lực hỗn hợp (provision spans), cần thẩm định theo từng đối tượng áp dụng."
        return _unresolved(cid, " ".join(filter(None, (reason, reviewed_reason))))
    if not _has_dates(meta) or not _has_dates(row):
        return _unresolved(cid, "chưa đủ mốc hiệu lực để xác minh chắc chắn.")
    valid_from = meta["valid_from"]
    valid_to = meta["valid_to"]
    amendments = [amd for amd in row.get("amendments") or [] if amd.get("amender_id")]
    matching_amenders = []
    uncertain_scope = bool(set(row.get("amended_by") or []) - {amd["amender_id"] for amd in amendments})
    for amd in amendments:
        match = _matches_amendment(cid, amd)
        if match is False:
            continue
        dated = "valid_from" in amd or "valid_to" in amd or bool(amd.get("source_unit"))
        if dated and not _has_dates(amd):
            uncertain_scope = True
            continue
        if dated and not amd["valid_from"] <= as_of_date <= amd["valid_to"]:
            continue
        if match is None:
            uncertain_scope = True
        elif amd["amender_id"] not in matching_amenders:
            matching_amenders.append(amd["amender_id"])

    if reviewed_reason:
        return _unresolved(cid, reviewed_reason)
    if as_of_date < valid_from:
        return CitationStatus(
            id=cid, status="NOT_YET_EFFECTIVE", exists=True, is_active=False,
            message=f"Điều khoản {cid} chưa có hiệu lực tại ngày {as_of_date} (hiệu lực từ {valid_from}).",
            amended_by=matching_amenders,
            in_context=True,
        )
    if as_of_date > valid_to:
        return CitationStatus(
            id=cid, status="EXPIRED", exists=True, is_active=False,
            message=f"Điều khoản {cid} đã hết hiệu lực trước ngày {as_of_date} (hết hiệu lực từ {valid_to}).",
            amended_by=matching_amenders,
            in_context=True,
        )
    if uncertain_scope:
        return _unresolved(cid, "chưa xác định được phạm vi sửa đổi từ quan hệ legacy.")
    if matching_amenders:
        return CitationStatus(
            id=cid, status="AMENDED", exists=True, is_active=True,
            message=f"Điều khoản {cid} đã bị sửa đổi/bổ sung bởi {', '.join(matching_amenders)}.",
            amended_by=matching_amenders,
            in_context=True,
        )
    return CitationStatus(
        id=cid, status="VALID", exists=True, is_active=True,
        message=f"Điều khoản {cid} có đầy đủ hiệu lực tại ngày {as_of_date}.",
        amended_by=[],
        in_context=True,
    )


def _resolved_status(row, day, relevant, in_context, resolution, supplied, corpus_meta):
    cid = row["cid"]
    if not in_context or not row["exists"] or not relevant:
        return _evaluate_status(row, day, is_relevant=relevant, in_context=in_context)
    provisions = {p.id: p for item in resolution.items for p in item.provisions}
    provision = provisions.get(cid)
    if supplied is not None:
        expected = {p.id: p for item in supplied.items for p in item.provisions}
        if provision != expected.get(cid):
            return _unresolved(cid, "Evidence projection absent or differs from pinned source.")
    if provision is None:
        reasons = [reason for unit, reason in resolution.blocked.items()
                   if unit == cid or unit.startswith(cid + "_")]
        meta, spans = _scope_metadata(cid, corpus_meta)
        if spans:
            reasons.append("provision spans: exact scoped evidence required.")
        reviewed = _reviewed_uncertainty(cid, day, corpus_meta)
        if reviewed:
            reasons.append(reviewed)
        return _unresolved(cid, " ".join(dict.fromkeys(reasons)) or "Missing exact resolved evidence.")
    return CitationStatus(
        id=cid, status="AMENDED" if provision.status in {"AMENDED", "INSERTED"} else "VALID",
        exists=True, is_active=True, message="", amended_by=[], in_context=True,
    )


def verify_citations(
    text: str,
    as_of_date: str,
    relevance_map: dict[str, bool] | None = None,
    context_ids: set[str] | None = None,
    *,
    corpus_path: str = "data/processed/vat_corpus.json",
    reviewed_amendment_path: str | None = None,
    resolved_evidence=None,
) -> VerificationReport:
    """Query Neo4j to verify status of all extracted citations as of given date."""
    citation_ids = extract_citations(text)
    if not citation_ids:
        return VerificationReport(
            as_of_date=as_of_date,
            total_citations=0,
            valid_citations=0,
            citations=[],
            is_clean=False,
        )

    date.fromisoformat(as_of_date)
    corpus_meta = None
    resolution = None
    if reviewed_amendment_path is not None:
        from src.engine.provision_evidence import resolve_provision_evidence
        corpus_meta = _load_verification_metadata(corpus_path)
        _load_reviewed_amendments(reviewed_amendment_path, corpus_path, corpus_meta)
        resolution = resolve_provision_evidence(
            citation_ids, as_of_date, corpus_path=corpus_path,
            reviewed_amendment_path=reviewed_amendment_path,
        )
    else:
        corpus_meta = _load_verification_metadata(corpus_path)

    driver = get_neo4j_driver()
    with driver.session() as session:
        records = session.run(_VERIFY_CYPHER, ids=citation_ids, as_of_date=as_of_date).data()

    row_map = {r["cid"]: r for r in records}
    statuses = []
    for cid in citation_ids:
        row = row_map.get(cid, {"cid": cid, "exists": False, "valid_from": None, "valid_to": None, "amended_by": [], "amendments": []})
        in_ctx = True
        if context_ids is not None:
            in_ctx = cid in context_ids or (resolution is None and cid.split("_K")[0] in context_ids)

        is_rel = True
        if relevance_map is not None:
            if cid in relevance_map:
                is_rel = relevance_map[cid]
            else:
                art_id = cid.split("_K")[0]
                is_rel = relevance_map.get(art_id, True)

        if resolution is not None:
            statuses.append(_resolved_status(row, as_of_date, is_rel, in_ctx, resolution, resolved_evidence, corpus_meta))
        else:
            statuses.append(_evaluate_status(row, as_of_date, is_relevant=is_rel, in_context=in_ctx, corpus_meta=corpus_meta))

    total = len(statuses)
    valid_count = sum(1 for s in statuses if s.status == "VALID")
    is_clean = total > 0 and valid_count == total

    return VerificationReport(
        as_of_date=as_of_date,
        total_citations=total,
        valid_citations=valid_count,
        citations=statuses,
        is_clean=is_clean,
    )


def prune_invalid_citations(text: str, report: VerificationReport) -> str:
    """Remove hallucinated, expired, or irrelevant citations from text and clean up spacing."""
    invalid_ids = {c.id for c in report.citations if c.status in {"NOT_FOUND", "EXPIRED", "IRRELEVANT"}}
    if not invalid_ids:
        return text

    result = text
    for cid in invalid_ids:
        result = re.sub(rf"\[{re.escape(cid)}\]", "", result, flags=re.IGNORECASE)

    result = re.sub(r"[ \t]+([,.\?!;:])", r"\1", result)
    result = re.sub(r"[ \t]{2,}", " ", result)
    return result.strip()
