"""Hybrid temporal retrieval combining fulltext and vector search with RRF."""
from ..api.chat_domain import normalize_query, understand_query
from ..graph.connection import get_neo4j_driver
from .embedder import DeterministicEmbedder
from .embedding_meta import resolve_embedder_for_index
from .models import ArticleNode

_LUCENE_STOPWORDS = {
    "tôi", "cho", "tại", "năm", "tháng", "ngày", "được", "theo", "quy", "định",
    "với", "là", "bao", "nhiêu", "của", "và", "các", "có", "phải", "không", "thì",
    "dưới", "trên", "trong", "về", "một", "số", "khi", "ra", "sao", "tiền", "thế",
}


def reciprocal_rank_fusion(
    fulltext_ranks: list[str],
    vector_ranks: list[str],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Compute Reciprocal Rank Fusion (RRF) scores across rankings."""
    scores: dict[str, float] = {}
    for rank_list in (fulltext_ranks, vector_ranks):
        seen: set[str] = set()
        for idx, doc_id in enumerate(rank_list, start=1):
            if doc_id not in seen:
                seen.add(doc_id)
                scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + idx)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def _format_lucene_query(query: str, synonyms: list[str] | None = None) -> str:
    """Format user query into high-precision Lucene query with phrase and AND matching."""
    clean_kw = "".join(
        c if c not in r'+-&|!(){}[]^"~*?:\/' else " " for c in query
    ).strip()
    words = clean_kw.split()
    if not words:
        return ""
    if len(words) == 1:
        return words[0]

    content_words = [w for w in words if w.lower() not in _LUCENE_STOPWORDS]
    chosen_words = content_words if len(content_words) >= 2 else words

    exact_phrase = f'"{clean_kw}"'
    bigrams = [f'"{words[i]} {words[i+1]}"' for i in range(len(words) - 1)]
    and_query = " AND ".join(chosen_words)

    parts = [f"{exact_phrase}^4"]
    if bigrams:
        parts.append(" OR ".join(f"{bg}^2" for bg in bigrams[:6]))
    if synonyms:
        syn_clauses = [f'"{s}"^3' for s in synonyms if s.strip()]
        if syn_clauses:
            parts.append(" OR ".join(syn_clauses))
    parts.append(f"({and_query})")
    return " OR ".join(parts)


def _search_fulltext(
    session, query: str, as_of_date: str, limit: int = 30, synonyms: list[str] | None = None
) -> list[str]:
    """Search candidate article IDs using Lucene fulltext index with fallback."""
    formatted_kw = _format_lucene_query(query, synonyms=synonyms)
    if formatted_kw:
        try:
            cypher_lucene = """
            CALL db.index.fulltext.queryNodes('article_content_fulltext', $kw) YIELD node, score
            OPTIONAL MATCH (parent:Article)-[:CONTAINS]->(node:Clause)
            WITH coalesce(parent, node) AS art, score
            WHERE 'Article' IN labels(art)
              AND art.valid_from <= date($as_of_date)
              AND art.valid_to >= date($as_of_date)
            RETURN art.id AS id, max(score) AS score
            ORDER BY score DESC
            LIMIT $limit
            """
            rows = session.run(
                cypher_lucene, kw=formatted_kw, as_of_date=as_of_date, limit=limit
            ).data()
            if rows:
                return [r["id"] for r in rows if r.get("id")]
        except Exception:
            pass

    cypher_fallback = """
    MATCH (parent:Article)
    WHERE parent.valid_from <= date($as_of_date)
      AND parent.valid_to >= date($as_of_date)
    OPTIONAL MATCH (parent)-[:CONTAINS]->(cl:Clause)
    WITH parent, cl
    WHERE toLower(parent.content) CONTAINS toLower($kw)
       OR (cl IS NOT NULL AND toLower(cl.content) CONTAINS toLower($kw))
    RETURN DISTINCT parent.id AS id
    LIMIT $limit
    """
    rows = session.run(
        cypher_fallback, kw=query, as_of_date=as_of_date, limit=limit
    ).data()
    return [r["id"] for r in rows if r.get("id")]


def _search_vector(
    session, vec: list[float], as_of_date: str, top_k: int = 30
) -> list[str]:
    """Search candidate article IDs using vector index with temporal filter."""
    try:
        cypher_vector = """
        CALL db.index.vector.queryNodes('article_embedding', $top_k, $vec) YIELD node, score
        WHERE node.valid_from <= date($as_of_date)
          AND node.valid_to >= date($as_of_date)
        RETURN node.id AS id, score
        ORDER BY score DESC
        """
        rows = session.run(
            cypher_vector, top_k=top_k, vec=vec, as_of_date=as_of_date
        ).data()
        return [r["id"] for r in rows if r.get("id")]
    except Exception:
        return []


def _enrich_articles(
    session, candidate_ids: list[str], as_of_date: str
) -> list[ArticleNode]:
    """Fetch article details, active clauses, and 1-hop relations as of specified date."""
    if not candidate_ids:
        return []

    cypher_enrich = """
    MATCH (doc:Document)-[:CONTAINS]->(art:Article)
    WHERE art.id IN $ids
    OPTIONAL MATCH (amender:Article)-[:AMENDS]->(art)
      WHERE amender.valid_from <= date($as_of_date)
    OPTIONAL MATCH (art)-[:CONTAINS]->(:Clause)-[:CITES]->(cited:Article)
      WHERE cited.valid_from <= date($as_of_date)
        AND cited.valid_to >= date($as_of_date)
    WITH art, doc,
         collect(DISTINCT amender.id) + collect(DISTINCT cited.id) AS related_ids
    OPTIONAL MATCH (art)-[:CONTAINS]->(cl:Clause)
      WHERE cl.valid_from <= date($as_of_date)
        AND cl.valid_to >= date($as_of_date)
    WITH art, doc, related_ids, cl
    ORDER BY cl.code
    WITH art, doc, related_ids,
         collect(DISTINCT CASE WHEN cl IS NOT NULL THEN cl.code + ': ' + cl.content ELSE null END) AS clause_texts
    RETURN art.id AS id, doc.id AS doc_id, art.code AS code,
           art.title AS title, art.content AS raw_content,
           clause_texts,
           toString(art.valid_from) AS valid_from,
           toString(art.valid_to) AS valid_to,
           art.embedding AS embedding,
           related_ids
    """
    rows = session.run(
        cypher_enrich, ids=candidate_ids, as_of_date=as_of_date
    ).data()
    nodes: dict[str, ArticleNode] = {}
    for r in rows:
        related = sorted({x for x in r.pop("related_ids") if x})
        clause_texts = [x for x in r.pop("clause_texts", []) if x]
        raw_content = r.pop("raw_content", "") or ""
        if clause_texts:
            full_content = f"{raw_content}\n\n" + "\n".join(clause_texts)
        else:
            full_content = raw_content

        node = ArticleNode(
            **r,
            content=full_content.strip(),
            status="ACTIVE",
            related_ids=related,
        )
        nodes[node.id] = node
    return [nodes[cid] for cid in candidate_ids if cid in nodes]


def query_hybrid_temporal(
    query: str,
    as_of_date: str,
    top_k: int = 15,
    embedder=None,
) -> list[ArticleNode]:
    """Execute hybrid search combining fulltext and vector retrieval."""
    query = normalize_query(query)
    active_embedder = embedder if embedder is not None else resolve_embedder_for_index()
    entities = understand_query(query)
    synonyms = list(entities.industry_synonyms)
    if entities.business_type == "HỘ_KINH_DOANH":
        synonyms.extend([
            "hộ, cá nhân sản xuất, kinh doanh",
            "hộ kinh doanh",
            "cá nhân kinh doanh",
            "200 triệu",
            "phương pháp tính trực tiếp",
            "doanh thu",
        ])
    if any(m in query.lower() for m in ("xuất khẩu", "xuat khau")):
        synonyms.extend(["xuất khẩu", "phần mềm", "thuế suất 0%"])

    driver = get_neo4j_driver()
    with driver.session() as session:
        ft_ranks = _search_fulltext(session, query, as_of_date, limit=30, synonyms=synonyms)
        vec_ranks: list[str] = []
        if active_embedder is not None:
            query_vec = active_embedder.embed_text(query)
            vec_ranks = _search_vector(session, query_vec, as_of_date, top_k=30)
        fused = reciprocal_rank_fusion(ft_ranks, vec_ranks, k=60)
        candidate_ids = [doc_id for doc_id, _ in fused[:top_k]]
        return _enrich_articles(session, candidate_ids, as_of_date)
