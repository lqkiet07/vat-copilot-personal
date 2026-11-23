import copy
from collections import Counter
from datetime import date
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/live_qa_oracle.json"
COUNTS = {"positive": 4, "missing_facts": 2, "system_unresolved": 2,
          "temporal": 2, "adversarial": 2}


class SourceText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def extracted_text(content, suffix):
    text = content.decode("utf-8")
    if suffix == ".json":
        snapshot = json.loads(text)
        if "html" in snapshot:
            text, suffix = snapshot["html"], ".html"
        else:
            text = snapshot.get("text", snapshot.get("innerText"))
    if not isinstance(text, str):
        raise ValueError("source text shape")
    if suffix in {".html", ".htm"}:
        parser = SourceText()
        parser.feed(text)
        parser.close()
        text = " ".join(parser.parts)
    return " ".join(text.split())


def pinned_bytes(root, name, pin):
    if not isinstance(name, str) or "\\" in name or ":" in name:
        raise ValueError("unsafe path")
    target = (root / name).resolve()
    if Path(name).is_absolute() or ".." in Path(name).parts or not target.is_relative_to(root.resolve()):
        raise ValueError("unsafe path")
    if not re.fullmatch(r"[0-9a-f]{64}", pin):
        raise ValueError("invalid pin")
    content = target.read_bytes()
    if hashlib.sha256(content).hexdigest() != pin:
        raise ValueError("source SHA mismatch")
    return content


def validate_sources(oracle, root=ROOT):
    for name, pin in oracle["pins"].items():
        if not name.startswith("src/"):
            pinned_bytes(root, name, pin)
    for ref in oracle["source_refs"].values():
        name = ref["path"]
        if not name.startswith("data/raw/") or ref["sha256"] != oracle["pins"].get(name):
            raise ValueError("unbound source pin")
        text = extracted_text(pinned_bytes(root, name, ref["sha256"]), Path(name).suffix)
        start = ref["locator"]["normalized_text_start"]
        excerpt = ref["excerpt"]
        if (type(start) is not int or start < 0 or not excerpt or
                excerpt != " ".join(excerpt.split()) or text[start:start + len(excerpt)] != excerpt):
            raise ValueError("source excerpt locator mismatch")
        if not ref["locator"]["legal_unit"]:
            raise ValueError("missing legal locator")


def validate_freeze(oracle):
    payload = {key: value for key, value in oracle.items() if key != "freeze_sha256"}
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    if oracle.get("freeze_sha256") != digest:
        raise ValueError(f"freeze digest mismatch: {digest}")


def validate_schema(oracle):
    if oracle["schema_version"] != 1 or oracle["frozen"] is not True:
        raise ValueError("freeze contract")
    policy = oracle["holdout_policy"]
    if policy["withheld_from_prompt_tuning_after_freeze"] is not True or policy["unseen_by_all_agents_claimed"] is not False:
        raise ValueError("holdout contract")
    cases = oracle["cases"]
    if len(cases) != 12 or Counter(c["category"] for c in cases) != COUNTS:
        raise ValueError("case distribution")
    if len({c["id"] for c in cases}) != 12 or sum(c["held_out"] is True for c in cases) != 4:
        raise ValueError("case identities or holdouts")
    for case in cases:
        validate_case(case, oracle["source_refs"])


def validate_case(case, refs):
    required = {"id", "category", "subject", "held_out", "question", "as_of_date", "history",
                "facts", "expected_status", "required_propositions", "prohibited_propositions",
                "source_refs", "expected_evidence_ids", "unresolved_issues", "missing_facts",
                "resolver", "oracle_blocked", "oracle_blocked_reason"}
    if set(case) != required or type(case["held_out"]) is not bool:
        raise ValueError("case schema")
    if not isinstance(case["as_of_date"], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", case["as_of_date"]):
        raise ValueError("ISO date")
    date.fromisoformat(case["as_of_date"])
    if any(not isinstance(case[k], str) or not case[k].strip() for k in ("id", "question", "subject")) or not isinstance(case["facts"], dict):
        raise ValueError("question or facts")
    if not isinstance(case["history"], list) or any(set(m) != {"role", "content"} or m["role"] not in {"user", "assistant"} or not m["content"] for m in case["history"]):
        raise ValueError("history")
    for key in ("required_propositions", "prohibited_propositions", "source_refs", "expected_evidence_ids", "unresolved_issues", "missing_facts"):
        if not isinstance(case[key], list) or any(not isinstance(v, str) or not v.strip() for v in case[key]):
            raise ValueError("case list shape")
    if not case["required_propositions"] or not case["prohibited_propositions"] or not case["source_refs"] or not set(case["source_refs"]) <= refs.keys():
        raise ValueError("propositions or source references")
    if set(case["required_propositions"]) & set(case["prohibited_propositions"]):
        raise ValueError("contradictory propositions")
    if case["expected_status"] not in {"ANSWERED", "INSUFFICIENT_BASIS", "oracle_blocked"}:
        raise ValueError("expected status")
    if type(case["oracle_blocked"]) is not bool or case["oracle_blocked"] != (case["expected_status"] == "oracle_blocked"):
        raise ValueError("oracle blocked status")
    reason = case["oracle_blocked_reason"]
    if (case["oracle_blocked"] and (not isinstance(reason, str) or not reason.strip())) or (not case["oracle_blocked"] and reason is not None):
        raise ValueError("oracle blocked reason")
    resolver = case["resolver"]
    if set(resolver) != {"profile", "candidate_ids", "supported_ids", "unsupported_ids", "reason"} or resolver["profile"] != "explicit_reviewed_staging":
        raise ValueError("resolver profile")
    for key in ("candidate_ids", "supported_ids", "unsupported_ids"):
        values = resolver[key]
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values) or len(values) != len(set(values)):
            raise ValueError("resolver identities")
    if not resolver["candidate_ids"] or not isinstance(resolver["reason"], str) or not resolver["reason"].strip():
        raise ValueError("resolver rationale")
    if not case["oracle_blocked"]:
        answerable = not case["missing_facts"] and not case["unresolved_issues"]
        if (case["expected_status"] == "ANSWERED") != answerable:
            raise ValueError("operational status inconsistent")
        if answerable and not case["expected_evidence_ids"]:
            raise ValueError("answered without evidence")
    if set(resolver["supported_ids"]) & set(resolver["unsupported_ids"]) or not set(case["expected_evidence_ids"]) <= set(resolver["supported_ids"]):
        raise ValueError("unsupported evidence")
    if case["category"] == "missing_facts" and not case["missing_facts"]:
        raise ValueError("missing facts absent")
    if case["category"] == "system_unresolved" and (not case["unresolved_issues"] or case["missing_facts"] or not resolver["unsupported_ids"]):
        raise ValueError("system unresolved misclassified")


class LiveQAOracleTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(FIXTURE.is_file(), "frozen live QA oracle fixture is missing")
        self.oracle = json.loads(FIXTURE.read_bytes())

    def test_frozen_schema_and_distribution(self):
        validate_schema(self.oracle)

    def test_every_pin_and_exact_excerpt_binds_to_local_bytes(self):
        from scripts.live_qa_validation import fingerprint
        validate_sources(self.oracle)
        manifest, _ = fingerprint(ROOT)
        self.assertEqual(manifest["oracle_freeze_sha256"], hashlib.sha256(FIXTURE.read_bytes()).hexdigest())

    def test_rejects_schema_mutations(self):
        mutations = [lambda o: o["cases"].pop(),
                     lambda o: o["cases"][0].pop("history"),
                     lambda o: o["cases"][0].update(held_out="true"),
                     lambda o: o["cases"][0].update(expected_status="SUPPORTED"),
                     lambda o: o["cases"][0].update(expected_evidence_ids=["FABRICATED"]),
                     lambda o: o["cases"][4].update(missing_facts=[]),
                     lambda o: o["cases"][6].update(missing_facts=["revenue"]),
                     lambda o: o["cases"][0].update(oracle_blocked=True),
                     lambda o: o["holdout_policy"].update(unseen_by_all_agents_claimed=True)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                changed = copy.deepcopy(self.oracle)
                mutate(changed)
                with self.assertRaises((ValueError, KeyError)):
                    validate_schema(changed)

    def test_rejects_source_pin_excerpt_offset_and_path_mutations(self):
        for field, value in [("sha256", "0" * 64), ("excerpt", "Invented legal rule"),
                             ("locator", {"normalized_text_start": 0, "legal_unit": "wrong"}),
                             ("path", "../escape.html")]:
            with self.subTest(field=field):
                changed = copy.deepcopy(self.oracle)
                next(iter(changed["source_refs"].values()))[field] = value
                with self.assertRaises(ValueError):
                    validate_sources(changed)
        changed = copy.deepcopy(self.oracle)
        changed["pins"][next(iter(changed["pins"]))] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA mismatch"):
            validate_sources(changed)

    def test_freeze_digest_rejects_proposition_changes(self):
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
                         "69ae2a512452a60dfbd4c87319f155dbbfd806b6261c1be0e8679d8d1a89c0d7")
        validate_freeze(self.oracle)
        changed = copy.deepcopy(self.oracle)
        changed["cases"][0]["required_propositions"][0] = "Invented rule"
        with self.assertRaisesRegex(ValueError, "freeze digest"):
            validate_freeze(changed)

    def test_rejects_operational_and_schema_inconsistencies(self):
        mutations = [lambda o: o["cases"][0].update(expected_status="INSUFFICIENT_BASIS"),
                     lambda o: o["cases"][4].update(expected_status="ANSWERED"),
                     lambda o: o["cases"][6].update(expected_status="ANSWERED"),
                     lambda o: o["cases"][0]["resolver"].update(supported_ids=[]),
                     lambda o: o["cases"][0]["resolver"].update(candidate_ids="bad"),
                     lambda o: o["cases"][0].update(as_of_date="20261119"),
                     lambda o: o["cases"][0].update(oracle_blocked_reason=3),
                     lambda o: o["cases"][0].update(question=7)]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                changed = copy.deepcopy(self.oracle)
                mutate(changed)
                with self.assertRaises(ValueError):
                    validate_schema(changed)

    def test_unestablished_oracle_is_explicitly_blocked(self):
        changed = copy.deepcopy(self.oracle)
        case = changed["cases"][0]
        case.update(expected_status="oracle_blocked", oracle_blocked=True,
                    oracle_blocked_reason="Local source does not establish the requested conclusion.")
        validate_schema(changed)
        case["oracle_blocked_reason"] = None
        with self.assertRaisesRegex(ValueError, "oracle blocked reason"):
            validate_schema(changed)

    def test_changed_raw_bytes_are_rejected_without_writing_sources(self):
        original = Path.read_bytes
        raw = ROOT / next(iter(self.oracle["source_refs"].values()))["path"]

        def changed_bytes(path):
            content = original(path)
            return content + b" " if path == raw else content

        with patch.object(Path, "read_bytes", changed_bytes):
            with self.assertRaisesRegex(ValueError, "SHA mismatch"):
                validate_sources(self.oracle)

    def test_reviewed_resolver_effects_match_operational_expectations(self):
        from src.engine.provision_evidence import resolve_provision_evidence

        profile = self.oracle["runtime_profile"]
        for case in self.oracle["cases"]:
            with self.subTest(case=case["id"]):
                result = resolve_provision_evidence(
                    case["resolver"]["candidate_ids"], case["as_of_date"],
                    corpus_path=str(ROOT / profile["corpus_path"]),
                    reviewed_amendment_path=str(ROOT / profile["reviewed_amendment_path"]))
                supported = {p.id for item in result.items for p in item.provisions}
                self.assertEqual(set(case["resolver"]["supported_ids"]), supported)
                self.assertEqual(set(case["resolver"]["unsupported_ids"]), set(result.blocked))
                for item in result.items:
                    for provision in item.provisions:
                        self.assertTrue(provision.text.strip())

    def test_extraction_has_independently_written_known_answers(self):
        self.assertEqual(extracted_text(b"<p>A &amp; B</p><p>C</p>", ".html"), "A & B C")
        self.assertEqual(extracted_text(b'{"html":"<b>A</b> B"}', ".json"), "A B")
        self.assertEqual(extracted_text(b'{"innerText":" A  B "}', ".json"), "A B")
        with self.assertRaises(ValueError):
            extracted_text(b'{"metadata":1}', ".json")


if __name__ == "__main__":
    unittest.main()
