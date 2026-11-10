"""Generate article embeddings and optionally import them into Neo4j."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine.embedder import OllamaEmbedder, get_embedder
from src.engine.embedding_meta import atomic_write_bytes, load_embedding_meta, save_embedding_meta, validate_embeddings
from src.graph.importer import import_article_embeddings
from src.graph.schema import init_schema

CORPUS_PATH = Path("data/processed/vat_corpus.json")
OUTPUT_PATH = Path("data/processed/article_embeddings.json")
META_PATH = Path("data/processed/embedding_meta.json")


def _extract_article_texts(corpus: list[dict]) -> list[tuple[str, str]]:
    article_items = []
    ids = set()
    for doc in corpus:
        for art in doc["articles"]:
            article_id = art.get("id")
            if not isinstance(article_id, str) or not article_id.strip() or article_id in ids:
                raise ValueError(f"Invalid or duplicate article ID: {article_id!r}")
            ids.add(article_id)
            parts = [art.get("title"), art.get("lead"), art.get("content")]
            for clause in art.get("clauses", []):
                parts.append(clause.get("content"))
                parts.extend(point.get("content") for point in clause.get("points", []))
            article_items.append((article_id, " ".join(part for part in parts if part).strip()))
    if not article_items:
        raise ValueError("Article ID count is zero")
    return article_items


def _validate_paths(corpus_path, output_path, meta_path):
    paths = [Path(path).resolve() for path in (corpus_path, output_path, meta_path or META_PATH)]
    if len(set(paths)) != 3:
        raise ValueError("Corpus, output and metadata paths must be separated")
    staging = "staging" in Path(corpus_path).name.lower()
    if staging:
        root = Path(__file__).resolve().parents[1]
        canonical = {path.resolve() for path in (CORPUS_PATH, OUTPUT_PATH, META_PATH)}
        canonical.update((root / path).resolve() for path in (CORPUS_PATH, OUTPUT_PATH, META_PATH))
        if meta_path is None or paths[1] in canonical or paths[2] in canonical:
            raise ValueError("Staging requires explicit non-canonical output and meta paths")
    return staging, paths


def _generate_vectors(article_items, provider, staging):
    client = get_embedder(provider=provider, dimension=1024)
    if staging and (not isinstance(client, OllamaEmbedder) or client.model != "bge-m3" or client.dimension != 1024):
        raise RuntimeError("Staging requires ollama bge-m3 1024")
    texts = [text for _, text in article_items]
    vectors = client.embed_texts(texts) if isinstance(client, OllamaEmbedder) else [client.embed_text(text) for text in texts]
    if len(vectors) != len(article_items):
        raise RuntimeError(f"Returned vector count {len(vectors)} != article count {len(article_items)}")
    embeddings = {article_id: vector for (article_id, _), vector in zip(article_items, vectors)}
    errors = validate_embeddings(embeddings, dimension=1024)
    if errors:
        raise RuntimeError(f"Generated embeddings failed validation: {'; '.join(errors)}")
    return client, embeddings


def generate_and_import_embeddings(
    corpus_path: Path = CORPUS_PATH,
    output_path: Path = OUTPUT_PATH,
    provider: str | None = None,
    meta_path: Path | None = None,
    allow_downgrade: bool = False,
    import_fn: Any = None,
    *,
    no_import: bool = False,
) -> tuple[int, int]:
    staging, (corpus_path, output_path, resolved_meta_path) = _validate_paths(corpus_path, output_path, meta_path)
    chosen_provider = provider or os.getenv("EMBEDDING_PROVIDER", "deterministic")
    if staging and chosen_provider != "ollama":
        raise ValueError("Staging requires explicit provider ollama; downgrade is forbidden")
    existing_meta = load_embedding_meta(resolved_meta_path)
    if (chosen_provider == "deterministic" and existing_meta
            and existing_meta.get("provider") not in (None, "deterministic") and not allow_downgrade):
        raise RuntimeError("Refusing semantic index downgrade; pass --allow-downgrade for legacy indices")
    corpus_bytes = corpus_path.read_bytes()
    article_items = _extract_article_texts(json.loads(corpus_bytes))
    client, embeddings = _generate_vectors(article_items, chosen_provider, staging)
    cache_bytes = json.dumps(embeddings, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    metadata = {
        "provider": "ollama" if isinstance(client, OllamaEmbedder) else "deterministic",
        "model": getattr(client, "model", "shake_256"),
        "dimension": 1024,
        "count": len(embeddings),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
        "cache_sha256": hashlib.sha256(cache_bytes).hexdigest(),
    }
    atomic_write_bytes(output_path, cache_bytes)
    save_embedding_meta(metadata, resolved_meta_path)
    if no_import:
        return len(embeddings), 0
    if import_fn is not None:
        return len(embeddings), import_fn(embeddings)
    init_schema()
    return len(embeddings), import_article_embeddings(embeddings)


def main() -> tuple[int, int]:
    parser = argparse.ArgumentParser(description="Generate and optionally import VAT article embeddings.")
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--meta", type=Path)
    parser.add_argument("--no-import", action="store_true")
    parser.add_argument("--provider", choices=["ollama", "deterministic", "auto"],
                        default=os.getenv("EMBEDDING_PROVIDER", "deterministic"))
    parser.add_argument("--allow-downgrade", action="store_true")
    args = parser.parse_args()
    if args.corpus and "staging" in args.corpus.name.lower() and (args.output is None or args.meta is None):
        parser.error("Staging requires explicit --corpus --output --meta paths")
    return generate_and_import_embeddings(
        args.corpus or CORPUS_PATH, args.output or OUTPUT_PATH, args.provider, args.meta,
        args.allow_downgrade, no_import=args.no_import,
    )


if __name__ == "__main__":
    main()
