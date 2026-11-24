"""Load vat_corpus.json into Neo4j using Dual-Node + CONTAINS model."""
import json
import re
from datetime import date

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


def _build_corpus_index(corpus: list[dict]) -> dict:
    index = {"documents": {}, "articles": {}, "units": {}}
    for doc in corpus:
        index["documents"].setdefault(doc["id"], []).append(doc)
        for art in doc.get("articles", []):
            index["articles"].setdefault((doc["id"], art.get("code")), []).append(art)
            paths = [(art, [doc, art])]
            for clause in art.get("clauses", []):
                paths.append((clause, [doc, art, clause]))
                paths.extend((point, [doc, art, clause, point]) for point in clause.get("points", []))
            for unit, path in paths:
                index["units"].setdefault(unit["id"], []).append(path)
    return index


def _unit_path_errors(path: list[dict]) -> list[str]:
    errors = []
    parent_id = path[0]["id"]
    if not re.fullmatch(r"(?:ND|LUAT|TT|NQ|QD|VBHN)_\d+_\d{4}", parent_id):
        errors.append("invalid document id")
    for unit, prefix, separator in zip(path[1:], ("Dieu", "Khoan", "Diem"), ("_D", "_K", "_")):
        pattern = rf"{prefix} ([a-zđ])" if prefix == "Diem" else rf"{prefix} ([1-9][0-9]*)"
        match = re.fullmatch(pattern, unit.get("code", ""))
        if not match or unit["id"] != parent_id + separator + match[1]:
            errors.append("invalid unit id/code ownership")
        parent_id = unit["id"]
    return errors


def _source_date_errors(path: list[dict]) -> list[str]:
    errors = []
    for unit in path:
        values = [unit.get("valid_from"), unit.get("valid_to")]
        try:
            if any(not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) for value in values):
                raise ValueError
            start, end = map(date.fromisoformat, values)
            if start > end:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"invalid source dates: {unit['id']}")
    return errors


def _validate_amends_relation(rel: dict, corpus_index: dict) -> list[str]:
    if rel.get("resolved") is False:
        return []
    if rel.get("resolved") is not True:
        return ["resolved must be explicitly True or False"]
    if corpus_index is None:
        return ["corpus index required for resolved amendment"]
    errors = []
    if rel.get("action") not in ("sua_doi", "thay_the", "bai_bo"):
        errors.append("unsupported action")
    scope = rel.get("target_scope")
    clause, point = rel.get("target_clause"), rel.get("target_point")
    if scope not in ("article", "clause", "point") or bool(clause) != (scope in ("clause", "point")) or bool(point) != (scope == "point"):
        errors.append("target scope/fields mismatch")
    if rel.get("unresolved_reason"):
        errors.append("resolved amendment has unresolved reason")
    sources = corpus_index["units"].get(rel.get("source_unit"), [])
    if len(sources) != 1:
        errors.append("source unit missing or ambiguous")
    else:
        path = sources[0]
        if path[1]["id"] != rel.get("source_article") or rel.get("source_doc", path[0]["id"]) != path[0]["id"]:
            errors.append("source ownership mismatch")
        if len(corpus_index["documents"].get(path[0]["id"], [])) != 1 or len(corpus_index["units"].get(path[1]["id"], [])) != 1:
            errors.append("ambiguous source ownership")
        errors.extend(_unit_path_errors(path))
        errors.extend(_source_date_errors(path))
    errors.extend(_amends_target_errors(rel, corpus_index))
    return errors


def _amends_target_errors(rel: dict, index: dict) -> list[str]:
    docs = index["documents"].get(rel.get("target_doc"), [])
    articles = index["articles"].get((rel.get("target_doc"), rel.get("target_article")), [])
    if len(docs) != 1 or len(articles) != 1:
        return ["target document/article missing or ambiguous"]
    path = [docs[0], articles[0]]
    for field, children in (("target_clause", "clauses"), ("target_point", "points")):
        code = rel.get(field)
        if code is None or code == "":
            continue
        matches = [unit for unit in path[-1].get(children, []) if unit.get("code") == code]
        if len(matches) != 1:
            return [f"{field} missing or ambiguous in target owner"]
        path.append(matches[0])
    errors = _unit_path_errors(path)
    if any(len(index["units"].get(unit["id"], [])) != 1 for unit in path[1:]):
        errors.append("ambiguous target unit id")
    return errors


def plan_corpus_import(corpus_path: str = "data/processed/vat_corpus.json") -> dict:
    """Compute dry-run import statistics and partition resolved vs unresolved amendments without touching Neo4j."""
    with open(corpus_path, encoding="utf-8") as f:
        corpus = json.load(f)
    stats = {"documents": 0, "chapters": 0, "articles": 0, "clauses": 0, "points": 0}
    resolved_amends, unresolved_amends, cites, blockers = [], [], [], []
    corpus_index = _build_corpus_index(corpus)
    for doc in corpus:
        stats["documents"] += 1
        stats["chapters"] += len(doc.get("chapters", []))
        for art in doc.get("articles", []):
            stats["articles"] += 1
            for cl in art.get("clauses", []):
                stats["clauses"] += 1
                stats["points"] += len(cl.get("points", []))
                cites.extend(extract_cites_relations(doc["id"], cl["id"], cl.get("content", "")))
            for rel in extract_amends_from_article(doc["id"], art):
                errors = _validate_amends_relation(rel, corpus_index)
                if errors:
                    blockers.append({"relation": rel, "errors": errors})
                elif rel.get("resolved") is True:
                    resolved_amends.append(rel)
                else:
                    unresolved_amends.append(rel)
    return {
        "stats": stats,
        "resolved_amends": resolved_amends,
        "unresolved_amends": unresolved_amends,
        "amends_blockers": blockers,
        "cites": cites,
    }


def import_corpus_to_neo4j(
    corpus_path: str = "data/processed/vat_corpus.json",
    dry_run: bool = False,
) -> dict[str, int]:
    """Import entire corpus into Neo4j. Returns statistics dictionary. Idempotent via MERGE."""
    if dry_run:
        return plan_corpus_import(corpus_path)["stats"]
    with open(corpus_path, encoding="utf-8") as f:
        corpus = json.load(f)
    driver = get_neo4j_driver()
    stats = {"documents": 0, "chapters": 0, "articles": 0, "clauses": 0, "points": 0}
    with driver.session() as session:
        for doc in corpus:
            _import_document(session, doc, stats)
    return stats


def _insert_amends_relation(session, rel: dict, corpus_index: dict | None = None) -> None:
    errors = _validate_amends_relation(rel, corpus_index)
    if errors:
        raise ValueError("Invalid AMENDS relation: " + "; ".join(errors))
    if rel.get("resolved") is False:
        return
    session.run(
        """MATCH (s:Article {id: $src}), (t:Article {doc_id: $tdoc, code: $tcode})
           MERGE (s)-[r:AMENDS {
               target_scope: $tscope,
               target_clause: $tclause,
               target_point: $tpoint,
               source_unit: $sunit,
               action: $action
           }]->(t)""",
        src=rel["source_article"],
        sunit=rel.get("source_unit", rel["source_article"]),
        tdoc=rel["target_doc"],
        tcode=rel["target_article"],
        tscope=rel.get("target_scope", "article"),
        tclause=rel.get("target_clause") or "",
        tpoint=rel.get("target_point") or "",
        action=rel["action"],
    )


def _process_article_amends(session, doc_id: str, art: dict, corpus_index: dict | None = None) -> None:
    relations = extract_amends_from_article(doc_id, art)
    errors = [error for rel in relations for error in _validate_amends_relation(rel, corpus_index)]
    if errors:
        raise ValueError("Invalid AMENDS batch: " + "; ".join(errors))
    for rel in relations:
        _insert_amends_relation(session, rel, corpus_index)


def import_amends_relations(corpus_path: str = "data/processed/vat_corpus.json") -> int:
    """Create (:Article)-[:AMENDS {action}]->(:Article) edges from Decree 359/144 to Decree 181."""
    with open(corpus_path, encoding="utf-8") as f:
        corpus = json.load(f)
    corpus_index = _build_corpus_index(corpus)
    relations = [rel for doc in corpus for art in doc.get("articles", [])
                 for rel in extract_amends_from_article(doc["id"], art)]
    errors = [error for rel in relations for error in _validate_amends_relation(rel, corpus_index)]
    if errors:
        raise ValueError("Invalid AMENDS batch: " + "; ".join(errors))
    resolved = [rel for rel in relations if rel.get("resolved") is True]
    if not resolved:
        return 0
    driver = get_neo4j_driver()
    with driver.session() as session:
        for rel in resolved:
            _insert_amends_relation(session, rel, corpus_index)
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
    if not rel.get("resolved", True) or not rel.get("target_doc"):
        return
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


def import_clause_embeddings(
    embeddings_dict: dict[str, list[float]],
    batch_size: int = 200,
) -> int:
    """Batch-update Clause node embeddings in Neo4j. Idempotent."""
    if not embeddings_dict:
        return 0
    driver = get_neo4j_driver()
    rows = [{"id": cid, "vec": vec} for cid, vec in embeddings_dict.items()]
    total = 0
    with driver.session() as session:
        for start in range(0, len(rows), max(1, batch_size)):
            chunk = rows[start : start + batch_size]
            result = session.run(
                "UNWIND $rows AS row MATCH (c:Clause {id: row.id}) SET c.embedding = row.vec RETURN count(c) AS c",
                rows=chunk,
            ).single()
            if result:
                total += int(result["c"])
    return total
