"""Retrieval baseline pipelines for comparative ablation benchmarks."""
from pathlib import Path
from src.engine.embedder import DeterministicEmbedder, get_embedder
from src.engine.temporal_retriever import query_temporal_rag
from src.graph.connection import get_neo4j_driver
from src.verifier.citation_verifier import verify_citations

_CACHE_FILE = Path("data/processed/article_embeddings.json")
if not _CACHE_FILE.exists():
    _alt = Path(__file__).resolve().parent.parent.parent / "data" / "processed" / "article_embeddings.json"
    if _alt.exists():
        _CACHE_FILE = _alt

_P1_CYPHER = """
CALL db.index.vector.queryNodes('article_embedding', $top_k, $vec) YIELD node, score
RETURN node.id AS id
ORDER BY score DESC
"""


def run_p1_baseline_rag(
    question: str, as_of_date: str, top_k: int = 5
) -> list[str]:
    """Execute baseline flat RAG with unconstrained vector search."""
    # ponytail: ignores as_of_date and graph links to simulate naive flat RAG
    if top_k <= 0:
        return []
    try:
        embedder = get_embedder(cache_path=str(_CACHE_FILE))
    except Exception:
        embedder = DeterministicEmbedder(dimension=1024)
    vec = embedder.embed_text(question)

    driver = get_neo4j_driver()
    with driver.session() as session:
        rows = session.run(_P1_CYPHER, top_k=top_k, vec=vec).data()
        return [r["id"] for r in rows if r.get("id")]


def run_p2_temporal_graphrag(
    question: str, as_of_date: str, top_k: int = 5
) -> list[str]:
    """Execute hybrid temporal retrieval with 1-hop graph neighborhood expansion."""
    if top_k <= 0:
        return []
    result = query_temporal_rag(question, as_of_date)
    retrieved_ids: list[str] = []
    seen: set[str] = set()

    for art in result.articles:
        for cid in [art.id, *art.related_ids]:
            if cid and cid not in seen:
                seen.add(cid)
                retrieved_ids.append(cid)
    return retrieved_ids[:top_k]


def run_p3_full_system(
    question: str, as_of_date: str, top_k: int = 5
) -> list[str]:
    """Execute temporal graph retrieval gated by deterministic citation verification."""
    if top_k <= 0:
        return []
    p2_cites = run_p2_temporal_graphrag(question, as_of_date, top_k=top_k)
    if not p2_cites:
        return []

    citation_text = " ".join(f"[{c}]" for c in p2_cites)
    report = verify_citations(citation_text, as_of_date)
    allowed = {"VALID", "AMENDED"}
    verified = [c.id for c in report.citations if c.status in allowed]
    return list(dict.fromkeys(verified))[:top_k]
