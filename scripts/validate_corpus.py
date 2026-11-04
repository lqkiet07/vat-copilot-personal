"""Corpus integrity validation script.

Validates that parsed legal documents in the VAT corpus have:
- Unique clause IDs within each article
- Unique point IDs within each clause
- No appendix leakages (tariff codes, duplicate clauses, appendix tables in articles)
"""
import json
from pathlib import Path
import re
import sys

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.parser.utils import fold_ascii


def _validate_points(doc_id: str, art_id: str, cid: str, points: list[dict]) -> list[str]:
    errors = []
    seen = set()
    for point in points:
        pid = point.get("id", "")
        if not pid:
            errors.append(f"{doc_id} -> {art_id} -> {cid}: empty point ID found")
            continue
        if pid in seen:
            errors.append(f"{doc_id} -> {art_id} -> {cid}: duplicate point ID '{pid}'")
        seen.add(pid)
    return errors


def _validate_clauses(doc_id: str, art_id: str, clauses: list[dict]) -> list[str]:
    errors = []
    seen = set()
    for clause in clauses:
        cid = clause.get("id", "")
        if not cid:
            errors.append(f"{doc_id} -> {art_id}: empty clause ID found")
            continue
        if cid in seen:
            errors.append(f"{doc_id} -> {art_id}: duplicate clause ID '{cid}'")
        seen.add(cid)

        km = re.search(r"_K(\d+)", cid)
        if km and int(km.group(1)) > 50:
            errors.append(
                f"{doc_id} -> {art_id}: suspicious clause ID '{cid}' (suggests appendix/tariff table leakage)"
            )

        ccontent = clause.get("content", "")
        if re.match(r"^\s*\d{2}\.\d{2}\.\s*\d{2}", ccontent):
            errors.append(f"{doc_id} -> {art_id} -> {cid}: clause content matches tariff item code")

        errors.extend(_validate_points(doc_id, art_id, cid, clause.get("points", [])))
    return errors


def _validate_article_tables(doc_id: str, art_id: str, tables: list[str]) -> list[str]:
    errors = []
    for idx, table_md in enumerate(tables):
        folded_t = fold_ascii(table_md).lower()
        if "ma hang" in folded_t and "mo ta hang ho" in folded_t:
            errors.append(f"{doc_id} -> {art_id}: table #{idx + 1} contains leaked tariff headers")
    return errors


def validate_document(doc: dict) -> list[str]:
    """Check for duplicate clause IDs, duplicate point IDs within any article, and strange appendix leakages."""
    errors = []
    doc_id = doc.get("id", "UNKNOWN")

    for article in doc.get("articles", []):
        art_id = article.get("id", "UNKNOWN")
        folded_title = fold_ascii(article.get("title", "")).lower().strip()
        folded_code = fold_ascii(article.get("code", "")).lower().strip()
        if folded_title.startswith("phu luc") or folded_code.startswith("phu luc"):
            errors.append(f"{doc_id} -> {art_id}: article appears to be an appendix heading")

        errors.extend(_validate_clauses(doc_id, art_id, article.get("clauses", [])))
        errors.extend(_validate_article_tables(doc_id, art_id, article.get("tables", [])))

    return errors


def validate_corpus(corpus: list[dict]) -> list[str]:
    """Run validate_document on each doc and check for intra-article ID collisions."""
    errors = []
    seen_doc_ids = set()

    for doc in corpus:
        doc_id = doc.get("id", "UNKNOWN")
        if doc_id in seen_doc_ids:
            errors.append(f"Duplicate document ID in corpus: '{doc_id}'")
        seen_doc_ids.add(doc_id)
        doc_errors = validate_document(doc)
        errors.extend(doc_errors)

    return errors


if __name__ == "__main__":
    corpus_path = Path("data/processed/vat_corpus.json")
    if not corpus_path.exists():
        print(f"Error: {corpus_path} not found.")
        sys.exit(1)

    with open(corpus_path, encoding="utf-8") as f:
        corpus_data = json.load(f)

    corpus_errors = validate_corpus(corpus_data)
    if corpus_errors:
        print(f"Validation FAILED: {len(corpus_errors)} error(s) found:")
        for err in corpus_errors:
            print(f"  [ERROR] {err}")
        sys.exit(1)
    else:
        total_arts = sum(len(d.get("articles", [])) for d in corpus_data)
        total_clauses = sum(len(a.get("clauses", [])) for d in corpus_data for a in d.get("articles", []))
        print(f"Validation PASSED: {len(corpus_data)} docs, {total_arts} articles, {total_clauses} clauses checked cleanly.")
        sys.exit(0)
