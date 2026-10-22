"""Seed Neo4j with corpus, relations, and embeddings (idempotent)."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.graph.importer import (
    import_amends_relations,
    import_article_embeddings,
    import_cites_relations,
    import_corpus_to_neo4j,
    import_guides_relations,
)
from src.graph.schema import init_schema

CORPUS_PATH = ROOT / "data" / "processed" / "vat_corpus.json"
EMBEDDINGS_PATH = ROOT / "data" / "processed" / "article_embeddings.json"


def seed() -> dict[str, int]:
    init_schema()
    stats = import_corpus_to_neo4j(str(CORPUS_PATH))
    stats["amends"] = import_amends_relations(str(CORPUS_PATH))
    stats["guides"] = import_guides_relations()
    stats["cites"] = import_cites_relations(str(CORPUS_PATH))
    embeddings = json.loads(EMBEDDINGS_PATH.read_text(encoding="utf-8"))
    stats["embeddings"] = import_article_embeddings(embeddings)
    return stats


if __name__ == "__main__":
    print(seed())
