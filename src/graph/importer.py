"""Load vat_corpus.json into Neo4j using Dual-Node + CONTAINS model."""
import json

from ..parser.relation_extractor import extract_amends_from_article, extract_cites_relations
from .connection import get_neo4j_driver


def _insert_document(session, doc: dict) -> None:
    session.run(
        """MERGE (d:Document {id: $id})
           SET d.code = $code, d.title = $title,
               d.valid_from = date($vf), d.valid_to = date($vt),
               d.status = $st, d.issue_date = $issue_date""",
        id=doc["id"],
        code=doc.get("code", ""),
        title=doc.get("title", ""),
        vf=doc["valid_from"],
        vt=doc.get("valid_to", "9999-12-31"),
        st=doc.get("status", "ACTIVE"),
        issue_date=doc.get("issue_date") or doc.get("valid_from"),
    )


def _insert_chapter(session, doc_id: str, chapter: dict) -> None:
    session.run(
        """MERGE (c:Chapter {id: $id})
           SET c.code = $code, c.title = $title, c.doc_id = $doc_id
           WITH c MATCH (d:Document {id: $doc_id})
           MERGE (d)-[:CONTAINS]->(c)""",
        id=chapter["id"],
        code=chapter.get("code", ""),
        title=chapter.get("title", ""),
        doc_id=doc_id,
    )
    for art in chapter.get("articles", []):
        session.run(
            """MATCH (c:Chapter {id: $cid})
               MERGE (a:Article {id: $aid})
               MERGE (c)-[:CONTAINS]->(a)""",
            cid=chapter["id"],
            aid=art["id"],
        )


def _insert_article(session, doc_id: str, art: dict, default_status: str = "ACTIVE") -> None:
    content = art.get("content", "") + ("\n" + art["lead"] if art.get("lead") else "")
    status = art.get("status") or default_status
    session.run(
        """MERGE (a:Article {id: $id})
           SET a.code = $code, a.title = $title, a.content = $content, a.doc_id = $doc_id,
               a.valid_from = date($vf), a.valid_to = date($vt), a.status = $status
           WITH a MATCH (d:Document {id: $doc_id})
           MERGE (d)-[:CONTAINS]->(a)""",
        id=art["id"],
        code=art.get("code", ""),
        title=art.get("title", ""),
        content=content,
        doc_id=doc_id,
        vf=art["valid_from"],
        vt=art.get("valid_to", "9999-12-31"),
        status=status,
    )


def _insert_clause(session, doc_id: str, art_id: str, cl: dict) -> None:
    session.run(
        """MERGE (c:Clause {id: $id})
           SET c.code = $code, c.content = $content, c.doc_id = $doc_id,
               c.valid_from = date($vf), c.valid_to = date($vt)
           WITH c MATCH (a:Article {id: $aid})
           MERGE (a)-[:CONTAINS]->(c)""",
        id=cl["id"],
        code=cl.get("code", ""),
        content=cl.get("content", ""),
        doc_id=doc_id,
        aid=art_id,
        vf=cl["valid_from"],
        vt=cl.get("valid_to", "9999-12-31"),
    )


def _insert_point(session, doc_id: str, cl_id: str, pt: dict) -> None:
    session.run(
        """MERGE (p:Point {id: $id})
           SET p.code = $code, p.content = $content, p.doc_id = $doc_id,
               p.valid_from = date($vf), p.valid_to = date($vt)
           WITH p MATCH (c:Clause {id: $cid})
           MERGE (c)-[:CONTAINS]->(p)""",
        id=pt["id"],
        code=pt.get("code", ""),
        content=pt.get("content", ""),
        doc_id=doc_id,
        cid=cl_id,
        vf=pt["valid_from"],
        vt=pt.get("valid_to", "9999-12-31"),
    )


def _import_clauses(session, doc_id: str, art_id: str, clauses: list[dict], stats: dict) -> None:
    for cl in clauses:
        _insert_clause(session, doc_id, art_id, cl)
        stats["clauses"] += 1
        for pt in cl.get("points", []):
            _insert_point(session, doc_id, cl["id"], pt)
            stats["points"] += 1


def _import_articles(session, doc_id: str, articles: list[dict], default_status: str, stats: dict) -> None:
    for art in articles:
        _insert_article(session, doc_id, art, default_status)
        stats["articles"] += 1
        _import_clauses(session, doc_id, art["id"], art.get("clauses", []), stats)


def _import_document(session, doc: dict, stats: dict) -> None:
    _insert_document(session, doc)
    stats["documents"] += 1
    for ch in doc.get("chapters", []):
        _insert_chapter(session, doc["id"], ch)
        stats["chapters"] += 1
    _import_articles(session, doc["id"], doc.get("articles", []), doc.get("status", "ACTIVE"), stats)


def import_corpus_to_neo4j(corpus_path: str = "data/processed/vat_corpus.json") -> dict[str, int]:
    """Import entire corpus into Neo4j. Returns statistics dictionary. Idempotent via MERGE."""
    corpus = json.loads(open(corpus_path, encoding="utf-8").read())
    driver = get_neo4j_driver()
    stats = {"documents": 0, "chapters": 0, "articles": 0, "clauses": 0, "points": 0}
    with driver.session() as session:
        for doc in corpus:
            _import_document(session, doc, stats)
    return stats


def _insert_amends_relation(session, rel: dict) -> None:
    session.run(
        """MATCH (s:Article {id: $src}), (t:Article {doc_id: $tdoc, code: $tcode})
           MERGE (s)-[r:AMENDS]->(t)
           SET r.action = $action""",
        src=rel["source_article"],
        tdoc=rel["target_doc"],
        tcode=rel["target_article"],
        action=rel["action"],
    )


def _process_article_amends(session, doc_id: str, art: dict) -> None:
    for rel in extract_amends_from_article(doc_id, art):
        _insert_amends_relation(session, rel)


def import_amends_relations(corpus_path: str = "data/processed/vat_corpus.json") -> int:
    """Create (:Article)-[:AMENDS {action}]->(:Article) edges from Decree 359/144 to Decree 181."""
    corpus = json.loads(open(corpus_path, encoding="utf-8").read())
    driver = get_neo4j_driver()
    with driver.session() as session:
        for doc in corpus:
            for art in doc.get("articles", []):
                _process_article_amends(session, doc["id"], art)
        created = session.run(
            "MATCH ()-[r:AMENDS]->() RETURN count(r) AS c"
        ).single()["c"]
    return created


GUIDES_PAIRS = [
    ("ND_181_2025", "LUAT_48_2024"),
    ("TT_69_2025", "LUAT_48_2024"),
    ("TT_69_2025", "ND_181_2025"),
]


def import_guides_relations() -> int:
    """Create (:Document)-[:GUIDES]->(:Document) edges by legal hierarchy (curated, factual)."""
    driver = get_neo4j_driver()
    with driver.session() as session:
        for src, tgt in GUIDES_PAIRS:
            session.run(
                """MATCH (s:Document {id: $src}), (t:Document {id: $tgt})
                   MERGE (s)-[:GUIDES]->(t)""",
                src=src,
                tgt=tgt,
            )
        created = session.run(
            "MATCH (:Document)-[r:GUIDES]->(:Document) RETURN count(r) AS c"
        ).single()["c"]
    return created


def _insert_cite_relation(session, rel: dict) -> None:
    session.run(
        """MATCH (s:Clause {id: $src}), (t:Article {doc_id: $tdoc, code: $tcode})
           MERGE (s)-[r:CITES]->(t)
           SET r.target_clause = $tclause""",
        src=rel["source_clause"],
        tdoc=rel["target_doc"],
        tcode=rel["target_article"],
        tclause=rel["target_clause"] or "",
    )


def _process_article_cites(session, doc_id: str, art: dict) -> None:
    for cl in art.get("clauses", []):
        for rel in extract_cites_relations(doc_id, cl["id"], cl["content"]):
            _insert_cite_relation(session, rel)


def _process_document_cites(session, doc: dict) -> None:
    for art in doc.get("articles", []):
        _process_article_cites(session, doc["id"], art)


def import_cites_relations(corpus_path: str = "data/processed/vat_corpus.json") -> int:
    """Create (:Clause)-[:CITES {target_clause}]->(:Article) cross-reference edges."""
    corpus = json.loads(open(corpus_path, encoding="utf-8").read())
    driver = get_neo4j_driver()
    with driver.session() as session:
        for doc in corpus:
            _process_document_cites(session, doc)
        created = session.run(
            "MATCH (:Clause)-[r:CITES]->(:Article) RETURN count(r) AS c"
        ).single()["c"]
    return created


def import_article_embeddings(embeddings_dict: dict[str, list[float]]) -> int:
    """Update article embeddings in Neo4j. Returns count of updated articles."""
    driver = get_neo4j_driver()
    total = 0
    with driver.session() as session:
        for article_id, vec in embeddings_dict.items():
            result = session.run(
                "MATCH (a:Article {id: $id}) SET a.embedding = $vec RETURN count(a) AS c",
                id=article_id,
                vec=vec,
            ).single()
            if result:
                total += result["c"]
    return total
