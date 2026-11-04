"""Sinh vector embeddings offline cho 76 Dieu luat vat_corpus.json va nap vao Neo4j."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine.embedder import OllamaEmbedder, get_embedder
from src.engine.embedding_meta import load_embedding_meta, save_embedding_meta, validate_embeddings
from src.graph.importer import import_article_embeddings
from src.graph.schema import init_schema

CORPUS_PATH = Path("data/processed/vat_corpus.json")
OUTPUT_PATH = Path("data/processed/article_embeddings.json")


def _extract_article_texts(corpus: list[dict]) -> list[tuple[str, str]]:
    article_items: list[tuple[str, str]] = []
    for doc in corpus:
        for art in doc["articles"]:
            title = art.get("title", "") or ""
            lead = art.get("lead", "") or ""
            clauses_content = " ".join(
                cl.get("content", "") for cl in art.get("clauses", []) if cl.get("content")
            )
            full_text = " ".join(p for p in (title, lead, clauses_content) if p).strip()
            article_items.append((art["id"], full_text))
    return article_items


def generate_and_import_embeddings(
    corpus_path: Path = CORPUS_PATH,
    output_path: Path = OUTPUT_PATH,
    provider: str | None = None,
    meta_path: Path | None = None,
    allow_downgrade: bool = False,
    import_fn: Any = None,
) -> tuple[int, int]:
    chosen_provider = provider or os.getenv("EMBEDDING_PROVIDER", "deterministic")
    resolved_meta_path = meta_path or Path("data/processed/embedding_meta.json")

    existing_meta = load_embedding_meta(resolved_meta_path)
    if (
        chosen_provider == "deterministic"
        and existing_meta
        and existing_meta.get("provider") not in (None, "deterministic")
        and not allow_downgrade
    ):
        raise RuntimeError(
            f"Refusing to overwrite a '{existing_meta.get('provider')}' index with deterministic embeddings; "
            "pass --allow-downgrade to proceed with downgrade."
        )

    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    embedder = get_embedder(provider=chosen_provider, dimension=1024)
    article_items = _extract_article_texts(corpus)

    embeddings: dict[str, list[float]] = {}
    if isinstance(embedder, OllamaEmbedder):
        batch_texts = [text for _, text in article_items]
        batch_vecs = embedder.embed_texts(batch_texts)
        for (art_id, _), vec in zip(article_items, batch_vecs):
            embeddings[art_id] = vec
    else:
        for art_id, text in article_items:
            embeddings[art_id] = embedder.embed_text(text)

    errors = validate_embeddings(embeddings, dimension=1024)
    if errors:
        raise RuntimeError(f"Generated embeddings failed validation: {'; '.join(errors)}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(embeddings, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated {len(embeddings)} embeddings ({chosen_provider}) -> {output_path}")

    save_embedding_meta(
        {
            "provider": chosen_provider,
            "model": getattr(embedder, "model", "shake_256"),
            "dimension": 1024,
            "count": len(embeddings),
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        path=resolved_meta_path,
    )
    if import_fn is not None:
        imported = import_fn(embeddings)
    else:
        init_schema()
        imported = import_article_embeddings(embeddings)
    return len(embeddings), imported


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate and import VAT article embeddings.")
    parser.add_argument(
        "--provider",
        choices=["ollama", "deterministic", "auto"],
        default=os.getenv("EMBEDDING_PROVIDER", "deterministic"),
        help="Embedding provider (ollama, deterministic, auto)",
    )
    parser.add_argument(
        "--allow-downgrade",
        action="store_true",
        help="Allow overwriting a semantic embedding index with deterministic hashes",
    )
    args = parser.parse_args()
    generate_and_import_embeddings(provider=args.provider, allow_downgrade=args.allow_downgrade)
