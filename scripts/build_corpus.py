"""Chay boc tach 5 van ban -> data/processed/vat_corpus.json."""
import json
from pathlib import Path
import sys

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.parser.vbpl_parser import parse_vbpl_document

DOCS = [
    ("01_luat_48_2024.html", {"doc_id": "LUAT_48_2024", "code": "48/2024/QH15",
     "title": "Luat Thue gia tri gia tang", "valid_from": "2025-07-01",
     "valid_to": "9999-12-31", "status": "ACTIVE"}),
    ("02_nd_181_2025.html", {"doc_id": "ND_181_2025", "code": "181/2025/ND-CP",
     "title": "Nghi dinh huong dan Luat Thue GTGT", "valid_from": "2025-07-01",
     "valid_to": "9999-12-31", "status": "ACTIVE"}),
    ("03_tt_69_2025.html", {"doc_id": "TT_69_2025", "code": "69/2025/TT-BTC",
     "title": "Thong tu huong dan Luat Thue GTGT", "valid_from": "2025-07-01",
     "valid_to": "9999-12-31", "status": "ACTIVE"}),
    ("04_nd_359_2025.html", {"doc_id": "ND_359_2025", "code": "359/2025/ND-CP",
     "title": "Nghi dinh sua doi ND 181/2025", "valid_from": "2026-01-01",
     "valid_to": "9999-12-31", "status": "ACTIVE"}),
    ("05_nd_144_2026.html", {"doc_id": "ND_144_2026", "code": "144/2026/ND-CP",
     "title": "Nghi dinh sua doi ND 181/2025", "valid_from": "2026-03-15",
     "valid_to": "9999-12-31", "status": "ACTIVE"}),
]

RAW = Path("data/raw")
OUT = Path("data/processed")
OUT.mkdir(parents=True, exist_ok=True)

corpus = []
for fname, meta in DOCS:
    html = (RAW / fname).read_text(encoding="utf-8")
    doc = parse_vbpl_document(html, meta)
    n_clause = sum(len(a["clauses"]) for a in doc["articles"])
    n_point = sum(len(c["points"]) for a in doc["articles"] for c in a["clauses"])
    n_table = sum(len(a["tables"]) for a in doc["articles"])
    print(f"{meta['doc_id']}: {len(doc['articles'])} Dieu, {n_clause} Khoan, "
          f"{n_point} Diem, {n_table} bang, {len(doc['chapters'])} Chuong")
    corpus.append(doc)

(OUT / "vat_corpus.json").write_text(
    json.dumps(corpus, ensure_ascii=False, indent=1), encoding="utf-8")
print("saved data/processed/vat_corpus.json")
