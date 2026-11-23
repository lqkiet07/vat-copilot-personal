from dataclasses import dataclass, replace
from datetime import date, timedelta
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re

from src.ui.corpus_viewer import load_corpus_index, load_corpus_tree


@dataclass(frozen=True)
class ProvisionEvidence:
    id: str
    text: str
    status: str
    valid_from: str
    valid_to: str
    issued_on: str
    provenance: tuple[dict, ...]
    hierarchy: tuple[dict, ...] = ()


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    provisions: tuple[ProvisionEvidence, ...]
    citable: bool


@dataclass(frozen=True)
class EvidenceResolution:
    items: tuple[EvidenceItem, ...]
    blocked: dict[str, str]


class SourceIntegrityError(ValueError):
    """Raised when pinned corpus or source files fail integrity checks."""


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Expected ISO calendar date")
    date.fromisoformat(value)
    return value


def _safe_path(root, name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise SourceIntegrityError("Invalid evidence path")
    path = Path(name)
    if path.is_absolute() or PureWindowsPath(name).drive or ".." in path.parts:
        raise SourceIntegrityError("Evidence path escape")
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise SourceIntegrityError("Evidence path escape")
    return target


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _normalize(text):
    return " ".join(text.split())


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _decoded_text(content, name):
    if Path(name).suffix.lower() == ".pdf":
        raise ValueError("PDF text binding requires separately reviewed extraction")
    raw = content.decode("utf-8")
    html = Path(name).suffix.lower() in {".html", ".htm"}
    if Path(name).suffix.lower() == ".json":
        snapshot = json.loads(raw)
        if "html" in snapshot:
            raw, html = snapshot["html"], True
        else:
            raw = snapshot.get("text", snapshot.get("innerText"))
        if not isinstance(raw, str):
            raise ValueError("Unsupported snapshot text shape")
    if html:
        parser = _HTMLText()
        parser.feed(raw)
        parser.close()
        raw = " ".join(parser.parts)
    return _normalize(raw)


class _Sources:
    def __init__(self, root, pins):
        self.root, self.pins, self.bytes, self.decoded = root, pins, {}, {}

    def check_pin(self, name):
        path = _safe_path(self.root, name)
        pin = self.pins.get(name)
        if not isinstance(pin, str) or not re.fullmatch(r"[0-9a-f]{64}", pin):
            raise SourceIntegrityError("Missing evidence pin")
        if not path.is_file():
            raise SourceIntegrityError("Evidence unavailable")
        return path, pin

    def read(self, name):
        path, pin = self.check_pin(name)
        if name not in self.bytes:
            try:
                content = path.read_bytes()
            except OSError as exc:
                raise SourceIntegrityError("Evidence unavailable") from exc
            if _sha(content) != pin:
                raise SourceIntegrityError("Evidence SHA mismatch")
            self.bytes[name] = content
        return self.bytes[name]

    def verify_doc_sources(self, paths):
        text_suffixes = {".html", ".htm", ".json", ".txt", ".md"}
        has_primary_text = any(Path(p).suffix.lower() in text_suffixes for p in paths)
        for name in paths:
            path, _ = self.check_pin(name)
            suffix = Path(name).suffix.lower()
            # Skip multi-MB binary archive/scanned attachments when a pinned text source exists unless explicitly bound.
            if has_primary_text and suffix not in text_suffixes:
                continue
            self.read(name)

    def ref(self, ref, *, bind=False):
        if not isinstance(ref, dict) or ref.get("sha256") != self.pins.get(ref.get("path")):
            raise SourceIntegrityError("Unpinned evidence reference")
        self.check_pin(ref["path"])
        if not isinstance(ref.get("excerpt"), str) or not ref["excerpt"].strip():
            raise ValueError("Missing evidence excerpt")
        if bind:
            content = self.read(ref["path"])
            text = self.decoded.get(ref["path"])
            if text is None:
                text = _decoded_text(content, ref["path"])
                self.decoded[ref["path"]] = text
            excerpt = _normalize(ref["excerpt"])
            locator = ref.get("locator", {})
            if not isinstance(locator, dict) or set(locator) - {"normalized_text_start", "block_index"}:
                raise ValueError("Unsupported evidence locator; exact normalized offset required")
            start = locator.get("normalized_text_start")
            if locator and start is None:
                raise ValueError("Exact normalized offset required")
            if start is not None:
                if type(start) is not int or start < 0 or text[start:start + len(excerpt)] != excerpt:
                    raise ValueError("Evidence locator does not bind excerpt")
            elif excerpt not in text:
                raise ValueError("Evidence excerpt absent from pinned source")
            return excerpt

    def text(self, ref, allowed):
        if not isinstance(ref, dict) or ref.get("path") not in allowed:
            raise ValueError("Text source not owned by reviewed document")
        content = self.read(ref["path"])
        if ref.get("sha256") != self.pins[ref["path"]]:
            raise ValueError("Text SHA mismatch")
        text = ref["text"]
        if "locator" in ref:
            locator = ref["locator"]
            if not isinstance(locator, dict) or set(locator) != {"normalized_text_start", "normalized_text_end"}:
                raise ValueError("Invalid normalized text locator")
            start, end = locator["normalized_text_start"], locator["normalized_text_end"]
            decoded = self.decoded.get(ref["path"])
            if decoded is None:
                decoded = _decoded_text(content, ref["path"])
                self.decoded[ref["path"]] = decoded
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(decoded)
                    or not isinstance(text, str) or not text.strip() or text != _normalize(text)
                    or decoded[start:end] != text):
                raise ValueError("Invalid normalized text binding")
            return text
        start, end = ref["byte_start"], ref["byte_end"]
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(content)
                or not isinstance(text, str) or not text.strip()
                or content[start:end] != text.encode("utf-8")):
            raise ValueError("Invalid exact text byte binding")
        return text


_LEDGER_CACHE: dict[tuple[str, str], dict] = {}
_CORPUS_BUILD_CACHE: dict[tuple[str, str], tuple[list, dict, dict]] = {}


def _review(corpus, review_path):
    review_bytes = Path(review_path).read_bytes()
    review_key = (str(Path(review_path).resolve()), _sha(review_bytes))
    ledger = _LEDGER_CACHE.get(review_key)
    if ledger is None:
        ledger = json.loads(review_bytes)
        if len(_LEDGER_CACHE) >= 8:
            _LEDGER_CACHE.clear()
        _LEDGER_CACHE[review_key] = ledger
    if (ledger.get("schema_version") != 1 or ledger.get("oracle") != "independent_raw_source_review"
            or ledger.get("review_complete") is not True):
        raise ValueError("Unsupported or incomplete review")
    matches = []
    for root in corpus.parents:
        for name in ledger["pins"]:
            if _safe_path(root, name) == corpus:
                matches.append((root, name))
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous corpus pin")
    root, name = matches[0]
    sources = _Sources(root, ledger["pins"])
    content = sources.read(name)
    documents = {}
    for doc in ledger["documents"]:
        if doc["doc_id"] in documents:
            raise ValueError("Duplicate reviewed document")
        documents[doc["doc_id"]] = doc
        for path in doc["source_paths"]:
            sources.check_pin(path)
    return ledger, sources, documents, {"path": name, "sha256": _sha(content)}


def _target(spec):
    keys = ("doc_id", "article_id", "clause_id", "point_id")
    present = [key for key in keys if key in spec]
    if not present or set(spec) != set(keys[:len(present)]):
        raise ValueError("Invalid target hierarchy")
    values = [spec[key] for key in present]
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("Invalid target identity")
    if any(not child.startswith(parent + "_") for parent, child in zip(values, values[1:])):
        raise ValueError("Invalid target ownership")
    return values[-1]


def _overlap(left, right):
    return left == right or left.startswith(right + "_") or right.startswith(left + "_")


def _intervals(candidate, sources):
    sources.ref(candidate["evidence"])
    intervals = candidate["source_effective_intervals"]
    if not isinstance(intervals, list) or not intervals:
        raise ValueError("Missing amendment dates")
    for interval in intervals:
        start = _day(interval["from"])
        _day(interval["issued_on"])
        if interval["to"] is not None and _day(interval["to"]) < start:
            raise ValueError("Invalid amendment interval")
        refs = interval["date_evidence_refs"]
        if not refs:
            raise ValueError("Missing amendment date evidence")
        for ref in refs:
            sources.ref(ref)
        for key in ("issuance_evidence", "commencement_evidence"):
            if key in interval:
                sources.ref(interval[key])
    return intervals


def _event_date(candidate, interval, reviews):
    review = reviews.get(candidate["source_doc"], {})
    source = candidate["source_unit"]
    dates = []
    mixed = False
    for rule in review.get("provision_effective_dates", []):
        if any(source == target or source.startswith(target + "_") for target in rule.get("target_ids", [])):
            if rule.get("mapping_status") == "approved" and rule.get("effective_on"):
                dates.append(_day(rule["effective_on"]))
        if rule.get("container_id") and _overlap(source, rule["container_id"]):
            mixed = True
    if not dates and not mixed and review.get("effective_on"):
        dates.append(_day(review["effective_on"]))
    if not dates or len(set(dates)) != 1:
        return "0001-01-01"
    if dates[0] != interval["from"] or review.get("issued_on") != interval["issued_on"]:
        return min(dates[0], interval["from"])
    return interval["from"]


def _scope(candidate, sources):
    approved = candidate.get("operative_scope")
    if approved is None:
        return candidate["expected"]
    if approved.get("status") != "approved" or not approved.get("evidence_refs"):
        raise ValueError("Operative scope not independently approved")
    for ref in approved["evidence_refs"]:
        if ref["path"] != candidate["evidence"]["path"]:
            raise ValueError("Operative scope source ownership mismatch")
        sources.ref(ref, bind=True)
    approved = {**candidate["expected"], **approved}
    original = [_target(t) for t in candidate["expected"]["targets"]]
    if any(not any(t == parent or t.startswith(parent + "_") for parent in original)
           for t in map(_target, approved["targets"])):
        raise ValueError("Operative refinement outside original scope")
    return approved


def _covered(candidate, candidates, sources):
    coverage = candidate.get("coverage", {})
    children = [c for c in candidates if c["source_doc"] == candidate["source_doc"]
                and c["source_unit"].startswith(candidate["source_unit"] + "_")
                and c["classification"] != "nonoperative"]
    if (coverage.get("status") != "approved" or not children or not coverage.get("evidence_refs")
            or set(coverage.get("candidate_ids", [])) != {c["id"] for c in children}):
        return False
    for ref in coverage["evidence_refs"]:
        if ref["path"] != candidate["evidence"]["path"]:
            return False
        sources.ref(ref, bind=True)
    return all(c["expected"]["targets"] for c in children)


def _events(ledger, sources, units, reviews):
    events, seen = [], set()
    for candidate in ledger["candidates"]:
        identity = candidate["id"]
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError("Duplicate or missing amendment identity")
        seen.add(identity)
        classification = candidate["classification"]
        if classification == "nonoperative":
            continue
        if classification == "outscope" and not candidate["expected"]["targets"]:
            continue
        if classification not in {"resolved", "unresolved", "outscope"}:
            raise ValueError("Unknown review classification")
        intervals = _intervals(candidate, sources)
        spec = candidate["expected"]
        if spec["scope"] in {"group", "unresolved_subarticle"} and _covered(candidate, ledger["candidates"], sources):
            continue
        try:
            spec = _scope(candidate, sources)
        except (KeyError, TypeError, ValueError):
            pass
        targets = [_target(target) for target in spec["targets"]]
        original = [_target(target) for target in candidate["expected"]["targets"]]
        targets += [target for target in original if target not in targets
                    and not any(child.startswith(target + "_") for child in targets)]
        if not targets and spec["scope"] == "group":
            targets = list(dict.fromkeys(_target(t) for child in ledger["candidates"]
                           if child["source_unit"].startswith(candidate["source_unit"] + "_")
                           and child["source_doc"] == candidate["source_doc"]
                           for t in child["expected"]["targets"]))
        if not targets:
            if spec["scope"] == "external_directive":
                continue
            raise ValueError("Amendment without target scope")
        for interval in intervals:
            for target in targets:
                events.append((_event_date(candidate, interval, reviews), target, candidate, interval))
    return sorted(events, key=lambda event: event[0])


def _operation_scope(candidate, target, op, units, docs=None):
    scope = candidate.get("operative_scope", candidate["expected"])["scope"]
    if scope == "conditional_document_replacement":
        if op["action"] != "repeal" or not docs or target not in docs:
            raise ValueError("Conditional document replacement requires document repeal")
        return
    if scope == "enumerated_subarticles":
        action = op["action"]
        if action == "insert":
            parent = op["parent_id"]
            if target in units or not target.startswith(parent + "_"):
                raise ValueError("Insertion requires exact existing parent and new identity")
            if parent in units:
                if units[parent]["kind"] not in {"article", "clause"}:
                    raise ValueError("Unsupported insertion parent kind")
            elif not (docs and parent in docs and target.startswith(parent + "_D")):
                raise ValueError("Missing insertion parent")
        elif action in {"replace", "repeal", "phrase_replace"}:
            if target not in units:
                raise ValueError("Missing operation target")
        return
    if scope == "clause_text_correction":
        if op["action"] != "replace" or target not in units or units[target]["kind"] != "clause":
            raise ValueError("Clause text correction requires clause replacement")
        return
    insertion = scope.endswith("_insertion")
    if (op["action"] == "insert") != insertion:
        raise ValueError("Insertion action and scope mismatch")
    kind = scope.removesuffix("_insertion")
    if not insertion:
        if target not in units:
            raise ValueError("Missing operation target")
        if kind != "phrase_replacement" and units[target]["kind"] != kind:
            raise ValueError("Operation scope and target kind mismatch")
        return
    parent = op["parent_id"]
    if kind == "article":
        if (not docs or target in units or parent not in docs or target.rsplit("_", 1)[0] != parent
                or not target.startswith(parent + "_D")):
            raise ValueError("Insertion requires exact existing parent and new identity")
        return
    parent_kind = {"point": "clause", "clause": "article"}.get(kind)
    if (target in units or parent not in units or target.rsplit("_", 1)[0] != parent
            or units[parent]["kind"] != parent_kind):
        raise ValueError("Insertion requires exact existing parent and new identity")


_SIGNING_DATE_RE = re.compile(r"(?:kể\s+)?từ\s+ngày\s+ký(?:\s+ban\s+hành)?\b", re.IGNORECASE)


def _date_in_excerpt(value, excerpt):
    year, month, day = map(int, _day(value).split("-"))
    vietnamese = rf"ngày\s+0?{day}\s+tháng\s+0?{month}\s+năm\s+{year}\b"
    iso = rf"(?<![\d-]){re.escape(value)}(?![\d-])"
    return re.search(iso, excerpt) is not None or re.search(vietnamese, excerpt, re.IGNORECASE) is not None


def _reviewed_commencement(candidate, review):
    source = candidate["source_unit"]
    exceptions = []
    for rule in review.get("provision_effective_dates", []):
        targets = rule.get("target_ids", [])
        if any(source == target or source.startswith(target + "_") for target in targets):
            binding = rule.get("evidence")
            if not isinstance(binding, dict):
                binding = (review.get("operative_dates") or {}).get("exceptions", {}).get(rule.get("effective_on"))
            if rule.get("mapping_status") != "approved" or not isinstance(binding, dict):
                raise ValueError("Provision exception lacks independently bound review")
            exceptions.append({**rule, "evidence": binding})
        elif rule.get("container_id") and _overlap(source, rule["container_id"]):
            raise ValueError("Source has mixed provision dates")
    if len(exceptions) > 1:
        raise ValueError("Ambiguous source commencement exceptions")
    if exceptions:
        rule = exceptions[0]
        return _day(rule["effective_on"]), rule["evidence"]
    evidence = review.get("operative_dates") or review.get("date_evidence")
    return _day(review["effective_on"]), {"path": evidence["path"],
                                             "excerpt": evidence["effective_excerpt"]}


def _operation_dates(candidate, interval, op, sources, review):
    issued = _day(review["issued_on"])
    effective, commencement = _reviewed_commencement(candidate, review)
    if (interval["issued_on"] != issued or op["issued_on"] != issued
            or interval["from"] != effective or op["effective_on"] != effective):
        raise ValueError("Operation dates disagree with independent document review")
    evidence = review.get("operative_dates") or review.get("date_evidence")
    issuance = {"path": evidence["path"], "excerpt": evidence["issued_excerpt"]}
    signing_allowed = (interval.get("commencement_basis") in {"signing", "signing_date"} and effective == issued)
    for key, value, independent in (("issuance_evidence", issued, issuance),
                                    ("commencement_evidence", effective, commencement)):
        independent = {"sha256": sources.pins[independent["path"]], **independent}
        if independent["path"] not in review["source_paths"]:
            raise ValueError("Independent date evidence source ownership mismatch")
        expected = sources.ref(independent, bind=True)
        use_signing = (key == "commencement_evidence" and signing_allowed and not _date_in_excerpt(value, expected))
        if use_signing:
            if not _SIGNING_DATE_RE.search(expected):
                raise ValueError("Independent date excerpt does not support reviewed date")
        elif not _date_in_excerpt(value, expected):
            raise ValueError("Independent date excerpt does not support reviewed date")
        for owner in (interval, op):
            ref = owner.get(key, op[key])
            if ref["path"] not in review["source_paths"]:
                raise ValueError("Operation date source ownership mismatch")
            actual = sources.ref(ref, bind=True)
            valid_match = (_SIGNING_DATE_RE.search(actual) is not None) if use_signing else _date_in_excerpt(value, actual)
            if expected not in actual or not valid_match:
                raise ValueError("Date evidence differs from independent review")
    for ref in interval["date_evidence_refs"]:
        if ref["path"] not in review["source_paths"]:
            raise ValueError("Date reference source ownership mismatch")
        sources.ref(ref, bind=True)


def _operation(candidate, target, interval, sources, documents, units, docs=None):
    operations = candidate.get("operations", [])
    if ((candidate["classification"] != "resolved" and "operative_scope" not in candidate)
            or not isinstance(operations, list)):
        raise ValueError("No reviewed deterministic operations")
    spec = _scope(candidate, sources)
    allowed_scopes = {
        "article", "clause", "point", "article_insertion",
        "clause_insertion", "point_insertion", "phrase_replacement",
        "enumerated_subarticles", "clause_text_correction",
        "conditional_document_replacement",
    }
    if spec["scope"] not in allowed_scopes:
        raise ValueError("Scope not independently deterministic")
    if spec["scope"] == "conditional_document_replacement" and "operative_scope" not in candidate:
        raise ValueError("Conditional document replacement requires approved operative_scope")
    targets = {_target(t) for t in spec["targets"]}
    dates = {i["from"] for i in candidate["source_effective_intervals"]}
    if any(op.get("target_id") not in targets or op.get("effective_on") not in dates for op in operations):
        raise ValueError("Operation outside reviewed scope")
    selected = [op for op in operations if op.get("target_id") == target
                and op.get("effective_on") == interval["from"]]
    if len(selected) != 1:
        raise ValueError("Missing or ambiguous operation")
    op = selected[0]
    source = candidate["source_unit"]
    if (op["candidate_id"] != candidate["id"] or op["source_unit"] != source
            or source not in units or units[source]["doc_id"] != candidate["source_doc"]
            or op["issued_on"] != interval["issued_on"]):
        raise ValueError("Operation identity or date mismatch")
    reviewed_source = documents[candidate["source_doc"]]
    if reviewed_source.get("review_status") != "reviewed":
        raise ValueError("Amendment source not reviewed")
    allowed = reviewed_source["source_paths"]
    sources.verify_doc_sources(allowed)
    _operation_dates(candidate, interval, op, sources, reviewed_source)
    sources.ref(candidate["evidence"], bind=True)
    action = op["action"]
    if action in {"replace", "insert"}:
        sources.text(op["replacement"], allowed)
        if action == "replace" and "retired_subunits" in op:
            retired = op["retired_subunits"]
            subunits = op.get("subunits", {})
            if not isinstance(retired, list) or any(
                not isinstance(r, str) or not r.startswith(target + "_") or r not in units or r in subunits
                for r in retired
            ):
                raise ValueError("Invalid retired subunits")
    elif action == "phrase_replace":
        sources.text(op["old"], allowed)
        sources.text(op["new"], allowed)
        expected_count = op.get("expected_count", 1)
        if type(expected_count) is not int or expected_count < 1:
            raise ValueError("Invalid phrase replacement expected_count")
        if op.get("scope_heading_only"):
            if units[target]["kind"] != "article":
                raise ValueError("Heading-only phrase replacement requires article target")
            old_phrase = op["old"]["text"]
            if any(old_phrase in units[child]["text"] for child in units[target].get("descendant_ids", [])):
                raise ValueError("Heading-only phrase appears in child provisions")
    elif action == "repeal":
        if "except_targets" in op:
            except_targets = op["except_targets"]
            if not isinstance(except_targets, list) or any(
                not isinstance(t, str) or not t.startswith(target + "_") for t in except_targets
            ):
                raise ValueError("Invalid conditional exception targets")
            if "except_evidence" in op:
                if op["except_evidence"].get("path") not in allowed:
                    raise ValueError("Exception evidence source ownership mismatch")
                sources.ref(op["except_evidence"], bind=True)
    else:
        raise ValueError("Unsupported operation")
    _operation_scope(candidate, target, op, units, docs)
    return op


def _span_dates(meta, units):
    start, end = _day(meta["valid_from"]), _day(meta["valid_to"] or "9999-12-31")
    exact = set()
    for span in meta["provision_spans"] + meta["inherited_provision_spans"]:
        target = span.get("target_id")
        if not target or target not in units or not _overlap(target, span["source_id"]):
            raise ValueError("Mixed or unscoped provision span")
        if target == meta["id"] or meta["id"].startswith(target + "_"):
            start, end = _day(span["valid_from"]), _day(span.get("valid_to") or "9999-12-31")
            exact.add((start, end))
            if len(exact) > 1:
                raise ValueError("Conflicting exact provision spans")
        elif target.startswith(meta["id"] + "_"):
            raise ValueError("Mixed container provision span")
    if end < start:
        raise ValueError("Invalid provision interval")
    return start, end


def _apply(provision, op, candidate, interval):
    action = op["action"]
    if action == "repeal":
        raise ValueError("Reviewed repeal suppresses provision")
    text = provision.text
    if action == "phrase_replace":
        old, new = op["old"]["text"], op["new"]["text"]
        expected_count = op.get("expected_count", 1)
        if type(expected_count) is not int or expected_count < 1 or text.count(old) != expected_count:
            raise ValueError("Phrase replacement occurrence count mismatch")
        text = text.replace(old, new)
    else:
        text = op["replacement"]["text"]
    provenance = {"candidate_id": candidate["id"], "source_unit": candidate["source_unit"],
                  "effective_on": interval["from"], "issued_on": interval["issued_on"],
                  "operation": op, "source_interval": interval}
    return replace(provision, text=text, status="INSERTED" if action == "insert" else "AMENDED",
                   valid_from=interval["from"], issued_on=interval["issued_on"],
                   provenance=provision.provenance + (provenance,))


def _check_source_event(candidate, effective, events):
    source = candidate["source_unit"]
    overlaps = [other[2]["id"] for other in events
                if other[0] <= effective and _overlap(source, other[1])]
    if overlaps:
        raise ValueError("Source unit changed or unresolved at operation event: " + ", ".join(overlaps))


def _version_end(identity, day, end, events):
    future = [event[0] for event in events if event[0] > day and _overlap(identity, event[1])]
    if future:
        boundary = (date.fromisoformat(min(future)) - timedelta(days=1)).isoformat()
        return min(end, boundary)
    return end


def _sequence(relevant, sources):
    ordered = []
    pending = list(relevant)
    while pending:
        ready = []
        for event in pending:
            peers = [e for e in relevant if e[0] == event[0] and e[1] == event[1]]
            ops = [op for op in event[2].get("operations", [])
                   if op.get("target_id") == event[1] and op.get("effective_on") == event[0]]
            link = ops[0].get("supersedes") if len(ops) == 1 else None
            if len(peers) > 1 or link:
                linked = [e for e in peers if link and e[2]["id"] == link.get("candidate_id")
                          and e[2]["id"] != event[2]["id"]]
                if link:
                    if (len(linked) != 1 or link.get("status") != "approved"
                            or link.get("target_id") != event[1] or link.get("effective_on") != event[0]
                            or not link.get("evidence_refs")):
                        raise ValueError("Invalid reviewed supersession binding")
                    for ref in link["evidence_refs"]:
                        if ref["path"] != event[2]["evidence"]["path"]:
                            raise ValueError("Supersession source ownership mismatch")
                        sources.ref(ref, bind=True)
                    if linked[0] not in ordered:
                        continue
                elif any(e in ordered for e in peers):
                    raise ValueError("Same-date amendment ambiguity")
            ready.append(event)
        if not ready:
            raise ValueError("Cyclic or missing amendment precedence")
        first_day = min(e[0] for e in ready)
        ready = [e for e in ready if e[0] == first_day]
        if any(a[1] == b[1] for i, a in enumerate(ready) for b in ready[i + 1:]):
            raise ValueError("Same-date amendment ambiguity")
        for event in ready:
            ordered.append(event)
            pending.remove(event)
    return ordered


def _resolve_unit(identity, day, units, docs, events, sources, reviews, pin):
    meta = units.get(identity)
    relevant = [event for event in events if event[0] <= day and _overlap(identity, event[1])]
    if meta is None:
        inserts = [event for event in relevant if event[1] == identity]
        if inserts:
            op = _operation(inserts[0][2], identity, inserts[0][3], sources, reviews, units, docs)
            if op["action"] != "insert":
                raise ValueError("Missing explicit insertion")
            parent_id = op["parent_id"]
            if parent_id in units:
                parent_meta = units[parent_id]
                meta = {
                    **parent_meta, "id": identity, "text": "", "descendant_ids": [],
                    "ancestor_ids": list(parent_meta.get("ancestor_ids", [])) + [parent_id],
                    "provision_spans": [], "inherited_provision_spans": [],
                }
            elif parent_id in docs:
                parent_doc = docs[parent_id]
                meta = {
                    "id": identity, "kind": "article", "doc_id": parent_id, "text": "",
                    "code": op.get("code", ""), "title": op.get("title", ""),
                    "descendant_ids": [], "ancestor_ids": [],
                    "valid_from": parent_doc["valid_from"], "valid_to": parent_doc.get("valid_to", "9999-12-31"),
                    "status": parent_doc.get("status", "ACTIVE"),
                    "document_role": parent_doc.get("document_role", ""),
                    "provision_spans": [], "inherited_provision_spans": [],
                }
            else:
                raise ValueError("Missing insertion parent")
        else:
            parent_replaces = [
                event for event in relevant
                if identity.startswith(event[1] + "_")
                and identity in next(
                    (op.get("subunits", {}) for op in event[2].get("operations", [])
                     if op.get("target_id") == event[1] and op.get("effective_on") == event[0]),
                    {},
                )
            ]
            if not parent_replaces:
                raise ValueError("Unknown provision")
            parent_id = parent_replaces[0][1]
            parent_meta = units[parent_id]
            meta = {
                **parent_meta, "id": identity, "text": "", "descendant_ids": [],
                "ancestor_ids": list(parent_meta.get("ancestor_ids", [])) + [parent_id],
                "provision_spans": [], "inherited_provision_spans": [],
            }
    doc = docs[meta["doc_id"]]
    review = reviews.get(meta["doc_id"], {})
    if review.get("review_status") != "reviewed" or not review.get("source_paths"):
        raise ValueError("Document not reviewed or missing pinned source")
    sources.verify_doc_sources(review["source_paths"])
    try:
        start, end = _span_dates(meta, units)
    except ValueError:
        exact = [event for event in relevant if event[1] == identity]
        if not exact or _operation(exact[0][2], identity, exact[0][3], sources, reviews, units, docs)["action"] != "replace":
            raise
        start, end = _day(meta["valid_from"]), _day(meta["valid_to"] or "9999-12-31")
        if day < exact[0][0]:
            raise ValueError("Unscoped base span before full replacement")
    origin = {**pin, "sources": [{"path": path, "sha256": sources.pins[path]}
                                for path in review["source_paths"]]}
    hierarchy = ({"id": doc["id"], "code": doc.get("code", ""), "title": doc.get("title", "")},) + tuple(
        {"id": unit, "code": units.get(unit, meta).get("code", ""),
         "title": units.get(unit, meta).get("title", "")}
        for unit in list(meta.get("ancestor_ids", [])) + [identity])
    provision = ProvisionEvidence(identity, meta["text"], "UNCHANGED", start, end,
                                  _day(doc["issue_date"]), (origin,), hierarchy)
    for effective, target, candidate, interval in _sequence(relevant, sources):
        label = f"{candidate['id']}: "
        try:
            _check_source_event(candidate, effective, events)
            op = _operation(candidate, target, interval, sources, reviews, units, docs)
            if target != identity:
                is_conditional_doc_repeal = (
                    op["action"] == "repeal"
                    and candidate.get("operative_scope", {}).get("scope") == "conditional_document_replacement"
                    and target in docs
                    and identity.startswith(target + "_")
                )
                if is_conditional_doc_repeal:
                    except_targets = op.get("except_targets", [])
                    if any(_overlap(identity, ex) for ex in except_targets):
                        raise ValueError("Conditional transitional provision requires external readiness verification")
                elif op["action"] == "phrase_replace" and op.get("scope_heading_only"):
                    if op["old"]["text"] in provision.text:
                        raise ValueError("Heading-only phrase appears in child provision")
                    continue
                elif op["action"] == "replace" and any(
                    identity == r or identity.startswith(r + "_") for r in op.get("retired_subunits", [])
                ):
                    raise ValueError("Reviewed replacement retired subunit")
                else:
                    mapping = op.get("subunits", {})
                    if (op["action"] != "replace" or not identity.startswith(target + "_")
                            or identity not in mapping):
                        raise ValueError("Overlapping scope requires independent exact provision text")
                    text = sources.text(mapping[identity], reviews[candidate["source_doc"]]["source_paths"])
                    if _normalize(text) not in _normalize(op["replacement"]["text"]):
                        raise ValueError("Subunit text outside bound parent replacement")
                    op = {**op, "replacement": mapping[identity]}
            provision = _apply(provision, op, candidate, interval)
        except SourceIntegrityError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(label + str(exc)) from exc
    if not provision.valid_from <= day <= provision.valid_to:
        raise ValueError("Outside inclusive provision interval")
    if (meta.get("status") == "CONSOLIDATION_REFERENCE"
            or doc.get("status") == "CONSOLIDATION_REFERENCE"
            or (meta.get("document_role") and "consolidation" in meta["document_role"].lower())
            or (doc.get("document_role") and "consolidation" in doc["document_role"].lower())):
        raise ValueError("Consolidation reference is not operative evidence")
    if not provision.text.strip():
        raise ValueError("Missing exact provision text")
    return replace(provision, valid_to=_version_end(identity, day, provision.valid_to, events))


def _resolve_items(candidate_ids, day, units, docs, events, sources, reviews, pin):
    items, blocked = [], {}
    for identity in dict.fromkeys(candidate_ids):
        meta = units.get(identity, {})
        identities = [identity, *meta.get("descendant_ids", [])]
        # Discover active inserted descendants so parent queries surface newly added provisions.
        idx = 0
        while idx < len(identities):
            parent = identities[idx]
            idx += 1
            for eff, target, cand, _ in events:
                if eff > day:
                    continue
                if target not in units and target not in identities and target.startswith(parent + "_"):
                    ops = [op for op in cand.get("operations", [])
                           if op.get("target_id") == target and op.get("effective_on") == eff]
                    if ops:
                        if ops[0].get("action") == "insert" and ops[0].get("parent_id") == parent:
                            identities.append(target)
                    elif target.rsplit("_", 1)[0] == parent or parent == identity:
                        identities.append(target)
                elif target == parent or parent.startswith(target + "_"):
                    for op in cand.get("operations", []):
                        if op.get("target_id") == target and op.get("effective_on") == eff and op.get("action") == "replace":
                            for sub_id in op.get("subunits", {}):
                                if sub_id not in units and sub_id not in identities and sub_id.startswith(parent + "_"):
                                    if sub_id.rsplit("_", 1)[0] == parent or parent == identity:
                                        identities.append(sub_id)
        resolved = {}
        for unit in identities:
            try:
                resolved[unit] = _resolve_unit(unit, day, units, docs, events, sources, reviews, pin)
            except SourceIntegrityError:
                raise
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                blocked[unit] = str(exc)
        for unit in reversed(identities):
            descendants = [child for child in identities if child != unit and child.startswith(unit + "_")]
            unit_prov = (
                resolved[unit].provenance[-1]["candidate_id"]
                if unit in resolved and resolved[unit].status == "AMENDED" and len(resolved[unit].provenance) > 1
                else None
            )
            partial = []
            for child in descendants:
                if child in blocked:
                    if unit_prov and blocked[child] == f"{unit_prov}: Reviewed replacement retired subunit":
                        continue
                    partial.append(child)
                elif child in resolved and resolved[child].status != "UNCHANGED":
                    child_prov = resolved[child].provenance[-1]["candidate_id"] if len(resolved[child].provenance) > 1 else None
                    if unit_prov and child_prov == unit_prov:
                        continue
                    partial.append(child)
            if partial:
                resolved.pop(unit, None)
                blocked[unit] = "Container is not citable; changed or blocked descendants: " + ", ".join(partial)
        if resolved:
            items.append(EvidenceItem(identity, tuple(resolved.values()), identity not in blocked))
    return EvidenceResolution(tuple(items), blocked)


def _validate_tree(tree):
    seen = set()

    def visit(node, parent=None):
        identity = node["id"]
        if not isinstance(identity, str) or identity in seen:
            raise ValueError("Duplicate corpus identity")
        if parent and not identity.startswith(parent + "_"):
            raise ValueError("Corpus hierarchy ownership mismatch")
        seen.add(identity)
        for key in ("clauses", "points"):
            for child in node.get(key, []):
                visit(child, identity)

    for doc in tree:
        visit(doc)
        articles = list(doc.get("articles", []))
        by_id = {article["id"]: article for article in articles}
        for chapter in doc.get("chapters", []):
            for article in chapter.get("articles", []):
                if article["id"] in by_id:
                    if article != by_id[article["id"]]:
                        raise ValueError("Conflicting chapter article mirror")
                else:
                    articles.append(article)
                    by_id[article["id"]] = article
        for article in articles:
            visit(article, doc["id"])


def _load_verified_corpus(corpus: Path, pin: dict):
    corpus_key = (str(corpus), pin["sha256"])
    cached_tree = load_corpus_tree(str(corpus))
    entry = _CORPUS_BUILD_CACHE.get(corpus_key)
    if entry is not None:
        verified_tree, units, docs = entry
        if verified_tree is not cached_tree and verified_tree != cached_tree:
            raise ValueError("Cached corpus differs from pinned bytes")
        return units, docs
    tree = json.loads(corpus.read_bytes())
    _validate_tree(tree)
    if tree != cached_tree:
        raise ValueError("Cached corpus differs from pinned bytes")
    index = load_corpus_index(str(corpus))
    units = {key: value for article in index.values() for key, value in article["provisions"].items()}
    docs = {doc["id"]: doc for doc in tree}
    if len(_CORPUS_BUILD_CACHE) >= 4:
        _CORPUS_BUILD_CACHE.clear()
    _CORPUS_BUILD_CACHE[corpus_key] = (tree, units, docs)
    return units, docs


def resolve_provision_evidence(candidate_ids: list[str], as_of_date: str, *, corpus_path: str,
                               reviewed_amendment_path: str | None = None) -> EvidenceResolution:
    _day(as_of_date)
    if not isinstance(candidate_ids, list) or any(not isinstance(i, str) or not i for i in candidate_ids):
        raise ValueError("Expected nonempty string candidate IDs")
    if reviewed_amendment_path is None:
        return EvidenceResolution((), dict.fromkeys(candidate_ids, "Reviewed source ledger required"))
    try:
        corpus = Path(corpus_path).resolve()
        ledger, sources, reviews, pin = _review(corpus, reviewed_amendment_path)
        units, docs = _load_verified_corpus(corpus, pin)
        events = _events(ledger, sources, units, reviews)
        return _resolve_items(candidate_ids, as_of_date, units, docs, events, sources, reviews, pin)
    except (KeyError, TypeError, OSError, AttributeError) as exc:
        raise ValueError("Invalid corpus or reviewed evidence schema") from exc
