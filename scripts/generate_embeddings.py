"""Sinh vector embeddings offline cho 76 Dieu luat vat_corpus.json va nap vao Neo4j."""
import json
from pathlib import Path
import sys

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine.embedder import DeterministicEmbedder
from src.graph.importer import import_article_embeddings
from src.graph.schema import init_schema

CORPUS_PATH = Path("data/processed/vat_corpus.json")
OUTPUT_PATH = Path("data/processed/article_embeddings.json")


def generate_and_import_embeddings(
    corpus_path: Path = CORPUS_PATH, output_path: Path = OUTPUT_PATH
) -> tuple[int, int]:
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    embedder = DeterministicEmbedder(dimension=1024)

    embeddings: dict[str, list[float]] = {}
    for doc in corpus:
        for art in doc["articles"]:
            title = art.get("title", "") or ""
            lead = art.get("lead", "") or ""
            clauses_content = " ".join(
                cl.get("content", "") for cl in art.get("clauses", []) if cl.get("content")
            )
            full_text = " ".join(p for p in (title, lead, clauses_content) if p).strip()
            embeddings[art["id"]] = embedder.embed_text(full_text)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(embeddings, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Generated {len(embeddings)} embeddings -> {output_path}")

    init_schema()
    imported = import_article_embeddings(embeddings)
    print(f"Imported {imported} article embeddings into Neo4j")
    return len(embeddings), imported


if __name__ == "__main__":
    generate_and_import_embeddings()
