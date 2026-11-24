"""Idempotent clause-level bge-m3 embedding generator and Neo4j importer."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.engine.embedder import OllamaEmbedder
from src.graph.importer import import_clause_embeddings
from src.graph.schema import init_schema

DEFAULT_STAGING_CORPUS = "data/processed/vat_corpus.staging.json"
DEFAULT_CLAUSE_CACHE = ".cache/clause_embeddings.staging.json"
_MAX_EMBED_CHARS = 2000


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_clause_embed_text(doc: dict, art: dict, cl: dict) -> str:
    """Combine document, article, clause, and child point text into one contextual embedding string."""
    doc_code = str(doc.get("code") or doc.get("id") or "").strip()
    art_code = str(art.get("code") or "").strip()
    art_title = str(art.get("title") or "").strip()
    cl_code = str(cl.get("code") or "").strip()
    cl_content = str(cl.get("content") or "").strip()

    point_lines = []
    for pt in cl.get("points", []) or []:
        pt_code = str(pt.get("code") or "").strip()
        pt_content = str(pt.get("content") or "").strip()
        if pt_content:
            point_lines.append(f"{pt_code}: {pt_content}" if pt_code else pt_content)

    header = " | ".join(part for part in (doc_code, art_code, art_title) if part)
    body = f"{cl_code}: {cl_content}" if cl_code else cl_content
    full = "\n".join(part for part in (header, body, *point_lines) if part).strip()
    return full[:_MAX_EMBED_CHARS]


def collect_clause_texts(corpus_path: str) -> dict[str, str]:
    """Collect embeddable contextual text keyed by canonical Clause ID."""
    corpus = json.loads(Path(corpus_path).read_bytes().decode("utf-8"))
    texts: dict[str, str] = {}
    for doc in corpus:
        for art in doc.get("articles", []) or []:
            for cl in art.get("clauses", []) or []:
                cid = str(cl.get("id") or "").strip()
                if not cid:
                    continue
                embed_text = build_clause_embed_text(doc, art, cl)
                if embed_text:
                    texts[cid] = embed_text
    return texts


def _load_cache(path: Path) -> tuple[dict[str, list[float]], dict[str, str]]:
    if not path.is_file():
        return {}, {}
    try:
        payload = json.loads(path.read_bytes().decode("utf-8"))
        vectors = payload.get("vectors", {}) if isinstance(payload, dict) else {}
        hashes = payload.get("hashes", {}) if isinstance(payload, dict) else {}
        return dict(vectors), dict(hashes)
    except Exception:
        return {}, {}


def _save_cache(path: Path, vectors: dict[str, list[float]], hashes: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"vectors": vectors, "hashes": hashes}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)


def seed_clause_embeddings(
    *,
    corpus_path: str = DEFAULT_STAGING_CORPUS,
    cache_path: str = DEFAULT_CLAUSE_CACHE,
    batch_size: int = 16,
    dry_run: bool = False,
    embedder: Any = None,
    schema_fn: Callable[[], None] = init_schema,
    importer_fn: Callable[[dict[str, list[float]]], int] = import_clause_embeddings,
) -> dict[str, int]:
    """Generate/reuse cached bge-m3 clause vectors and optionally import into Neo4j."""
    texts = collect_clause_texts(corpus_path)
    cache_file = Path(cache_path)
    vectors, hashes = _load_cache(cache_file)

    text_hashes = {cid: _sha256_text(text) for cid, text in texts.items()}
    stale_ids = [
        cid for cid in texts
        if cid not in vectors or hashes.get(cid) != text_hashes[cid]
    ]

    if dry_run:
        return {
            "total_clauses": len(texts),
            "cached_clauses": len(texts) - len(stale_ids),
            "pending_embed": len(stale_ids),
            "newly_embedded": 0,
            "imported": 0,
        }

    if stale_ids:
        active_embedder = embedder or OllamaEmbedder(model="bge-m3", timeout=120.0)
        step = max(1, batch_size)
        for batch_idx, start in enumerate(range(0, len(stale_ids), step), start=1):
            batch_ids = stale_ids[start : start + step]
            batch_texts = [texts[cid] for cid in batch_ids]
            batch_vecs = active_embedder.embed_texts(batch_texts)
            for cid, vec in zip(batch_ids, batch_vecs):
                vectors[cid] = vec
                hashes[cid] = text_hashes[cid]
            if batch_idx % 5 == 0:
                _save_cache(cache_file, vectors, hashes)
        _save_cache(cache_file, vectors, hashes)

    active_vectors = {cid: vectors[cid] for cid in texts if cid in vectors}
    schema_fn()
    imported = importer_fn(active_vectors)
    return {
        "total_clauses": len(texts),
        "cached_clauses": len(texts) - len(stale_ids),
        "pending_embed": 0,
        "newly_embedded": len(stale_ids),
        "imported": imported,
    }


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Seed bge-m3 Clause embeddings into Neo4j.")
    parser.add_argument("--corpus", default=os.getenv("VAT_CORPUS_PATH", DEFAULT_STAGING_CORPUS))
    parser.add_argument("--cache", default=DEFAULT_CLAUSE_CACHE)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    stats = seed_clause_embeddings(
        corpus_path=args.corpus,
        cache_path=args.cache,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
