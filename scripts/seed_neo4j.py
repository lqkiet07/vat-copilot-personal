"""Seed Neo4j with corpus, relations, and embeddings (idempotent)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.reset_neo4j import reset_neo4j
from src.engine.embedding_meta import load_embedding_meta, validate_embedding_meta, validate_embeddings
from src.graph.importer import (
    GUIDES_PAIRS,
    import_amends_relations,
    import_article_embeddings,
    import_cites_relations,
    import_corpus_to_neo4j,
    import_guides_relations,
    plan_corpus_import,
)
from src.graph.schema import init_schema

CORPUS_PATH = ROOT / "data" / "processed" / "vat_corpus.json"
EMBEDDINGS_PATH = ROOT / "data" / "processed" / "article_embeddings.json"
META_PATH = ROOT / "data" / "processed" / "embedding_meta.json"


def _validate_target():
    try:
        uri = urlsplit(os.getenv("NEO4J_URI", "bolt://localhost:7687"))
        if (uri.scheme == "bolt" and uri.hostname in {"localhost", "127.0.0.1", "::1"}
                and uri.port == 17687 and not uri.username and not uri.password
                and not uri.path and not uri.query and not uri.fragment):
            return
    except ValueError:
        pass
    raise ValueError("Selected staging/reset target must be loopback bolt port 17687")


def _validate_paths(corpus_path, embeddings_path, meta_path):
    paths = [Path(path).resolve() for path in (corpus_path, embeddings_path, meta_path or META_PATH)]
    if len(set(paths)) != 3:
        raise ValueError("Corpus, embeddings and metadata paths must be separated")
    staging = any("staging" in path.name.lower() for path in paths)
    if staging:
        canonical = {path.resolve() for path in (CORPUS_PATH, EMBEDDINGS_PATH, META_PATH)}
        canonical.update((Path("data/processed") / path.name).resolve()
                         for path in (CORPUS_PATH, EMBEDDINGS_PATH, META_PATH))
        if meta_path is None or any(path in canonical for path in paths):
            raise ValueError("Staging requires explicit non-canonical corpus, embeddings and meta paths")
    return staging, paths


def _validated_embeddings(corpus_path, embeddings_path, meta_path, staging, require_meta=False):
    try:
        corpus_bytes = corpus_path.read_bytes()
        cache_bytes = embeddings_path.read_bytes()
        corpus, embeddings = json.loads(corpus_bytes), json.loads(cache_bytes)
        ids = [article["id"] for doc in corpus for article in doc["articles"]]
        if (not ids or any(not isinstance(item, str) or not item.strip() for item in ids)
                or len(ids) != len(set(ids)) or not isinstance(embeddings, dict) or set(ids) != set(embeddings)):
            raise ValueError("Invalid corpus/embedding article IDs")
        meta = load_embedding_meta(meta_path)
        if staging or require_meta or meta is not None:
            if meta is None:
                raise ValueError("Missing or invalid embedding metadata")
            if staging and (type(meta.get("count")) is not int or type(meta.get("dimension")) is not int
                            or (meta.get("provider"), meta.get("model"), meta.get("dimension"), meta.get("count")) != (
                                "ollama", "bge-m3", 1024, 494)):
                raise ValueError("Invalid staging metadata: requires ollama bge-m3 1024 count 494")
            errors = validate_embedding_meta(meta, embeddings)
            for field, data in (("corpus_sha256", corpus_bytes), ("cache_sha256", cache_bytes)):
                if (staging or field in meta) and meta.get(field) != hashlib.sha256(data).hexdigest():
                    errors.append(f"Invalid embedding metadata {field}")
            if errors:
                raise ValueError("; ".join(errors))
        errors = validate_embeddings(embeddings)
        if errors:
            raise ValueError("Invalid embeddings: " + "; ".join(errors))
        return corpus, embeddings
    except (OSError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid corpus/embedding artifacts or metadata: {exc}") from exc


def seed(
    do_reset: bool = False,
    dry_run: bool = False,
    corpus_path: Path = CORPUS_PATH,
    embeddings_path: Path = EMBEDDINGS_PATH,
    *,
    meta_path: Path | None = None,
) -> dict[str, int]:
    staging, (corpus_path, embeddings_path, resolved_meta) = _validate_paths(corpus_path, embeddings_path, meta_path)
    if staging or do_reset:
        _validate_target()
    if staging or meta_path is not None or not dry_run:
        corpus, embeddings = _validated_embeddings(
            corpus_path, embeddings_path, resolved_meta, staging, require_meta=meta_path is not None,
        )
    if dry_run:
        plan = plan_corpus_import(str(corpus_path))
        stats = dict(plan["stats"])
        stats["amends"] = len(plan["resolved_amends"])
        stats["unresolved_amends"] = len(plan["unresolved_amends"])
        stats["cites"] = len(plan["cites"])
        if staging or meta_path is not None:
            doc_ids = {doc["id"] for doc in corpus}
            stats["guides"] = sum(source in doc_ids and target in doc_ids for source, target in GUIDES_PAIRS)
            stats["embeddings"] = len(embeddings)
        return stats
    if do_reset:
        reset_neo4j(confirm=True)

    init_schema()
    stats = import_corpus_to_neo4j(str(corpus_path))
    stats["amends"] = import_amends_relations(str(corpus_path))
    stats["guides"] = import_guides_relations()
    stats["cites"] = import_cites_relations(str(corpus_path))
    imported = import_article_embeddings(embeddings)
    stats["embeddings"] = imported
    if imported != len(embeddings):
        raise RuntimeError(
            f"Embedding import mismatch: imported {imported} articles, expected {len(embeddings)}"
        )
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Neo4j database.")
    parser.add_argument("--reset", action="store_true", help="Wipe database before seeding")
    parser.add_argument("--dry-run", action="store_true", help="Plan import without writing to Neo4j")
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--embeddings", type=Path)
    parser.add_argument("--meta", type=Path)
    args = parser.parse_args()
    if any(path is not None and "staging" in path.name.lower()
           for path in (args.corpus, args.embeddings, args.meta)) and any(
               path is None for path in (args.corpus, args.embeddings, args.meta)):
        parser.error("Staging requires explicit --corpus --embeddings --meta paths")
    print(seed(do_reset=args.reset, dry_run=args.dry_run, corpus_path=args.corpus or CORPUS_PATH,
               embeddings_path=args.embeddings or EMBEDDINGS_PATH, meta_path=args.meta))
