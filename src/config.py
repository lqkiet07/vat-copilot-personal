import os
from datetime import datetime
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class RuntimeConfig:
    environment: str
    corpus_path: str
    embeddings_path: str
    embedding_meta_path: str


def get_runtime_config() -> RuntimeConfig:
    environment = os.getenv("VAT_ENV", "canonical")
    if environment not in {"canonical", "staging"}:
        raise ValueError("VAT_ENV must be canonical or staging")
    names = ("VAT_CORPUS_PATH", "VAT_EMBEDDINGS_PATH", "VAT_EMBEDDING_META_PATH")
    if environment == "staging":
        if any(not os.getenv(name, "").strip() for name in names):
            raise ValueError("Staging requires all three explicit artifact paths")
        try:
            uri = urlsplit(os.getenv("NEO4J_URI", ""))
            valid = (uri.scheme == "bolt" and uri.hostname in {"localhost", "127.0.0.1", "::1"}
                     and uri.port == 17687 and not uri.username and not uri.password
                     and not uri.path and not uri.query and not uri.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Staging requires explicit local NEO4J_URI on bolt port 17687")
    defaults = ("data/processed/vat_corpus.json", "data/processed/article_embeddings.json",
                "data/processed/embedding_meta.json")
    return RuntimeConfig(environment, *(os.getenv(name, default) for name, default in zip(names, defaults)))


def validate_runtime_artifacts(selected: RuntimeConfig) -> dict:
    if selected.environment != "staging":
        return {}
    from pathlib import Path
    from src.engine.embedding_meta import validate_artifact_identity

    identity = validate_artifact_identity(
        Path(selected.embedding_meta_path), Path(selected.embeddings_path), Path(selected.corpus_path),
    )
    review_path = selected_review_path(selected.corpus_path)
    if review_path is not None:
        from src.verifier.citation_verifier import _load_reviewed_amendments, _load_verification_metadata
        _load_reviewed_amendments(review_path, selected.corpus_path, _load_verification_metadata(selected.corpus_path))
        import hashlib
        identity["review_sha256"] = hashlib.sha256(Path(review_path).read_bytes()).hexdigest()
    return identity


def selected_review_path(corpus_path: str) -> str | None:
    path = os.getenv("VAT_AMENDMENT_REVIEW_PATH", "").strip()
    if not path:
        return None
    from pathlib import Path
    selected = get_runtime_config()
    if selected.environment != "staging" or Path(corpus_path).resolve() != Path(selected.corpus_path).resolve():
        raise ValueError("Reviewed amendments require the explicitly selected staging corpus")
    return path


def selected_corpus_kwargs() -> dict[str, str]:
    selected = get_runtime_config()
    if selected.corpus_path == "data/processed/vat_corpus.json":
        return {}
    return {"corpus_path": selected.corpus_path}


DATA_FREEZE_DATE: str = "2026-09-30"


def today_iso() -> str:
    return datetime.now().date().isoformat()


def default_as_of_date() -> str:
    return min(today_iso(), DATA_FREEZE_DATE)


def is_vector_enabled() -> bool:
    return os.getenv("VECTOR_ENABLED", "1").lower() not in ("0", "false", "no")


def is_strict_grounding() -> bool:
    return os.getenv("STRICT_GROUNDING", "0").lower() in ("1", "true", "yes")
