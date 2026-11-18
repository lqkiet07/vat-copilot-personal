import hashlib
import json
import os
import socket
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from src.graph.connection import close_driver, get_neo4j_driver
from src.parser.relation_extractor import extract_amends_from_article


def _properties(properties):
    return tuple(sorted((key, str(value) if hasattr(value, "isoformat") else value)
                        for key, value in properties.items()))


def _expected_units(doc):
    yield "Document", {
        "id": doc["id"], "code": doc.get("code", ""), "title": doc.get("title", ""),
        "valid_from": doc["valid_from"], "valid_to": doc.get("valid_to", "9999-12-31"),
        "status": doc.get("status", "ACTIVE"), "issue_date": doc.get("issue_date") or doc["valid_from"],
    }
    for chapter in doc.get("chapters", []):
        yield "Chapter", {"id": chapter["id"], "code": chapter.get("code", ""),
                          "title": chapter.get("title", ""), "doc_id": doc["id"]}
    for article in doc.get("articles", []):
        units = [("Article", article)]
        for clause in article.get("clauses", []):
            units.append(("Clause", clause))
            units.extend(("Point", point) for point in clause.get("points", []))
        for label, unit in units:
            properties = {"id": unit["id"], "code": unit.get("code", ""),
                          "content": unit.get("content", ""), "doc_id": doc["id"],
                          "valid_from": unit["valid_from"], "valid_to": unit.get("valid_to", "9999-12-31")}
            if label == "Article":
                properties.update(title=unit.get("title", ""), status=unit.get("status") or doc.get("status", "ACTIVE"))
                properties["content"] += "\n" + unit["lead"] if unit.get("lead") else ""
            yield label, properties


def expected_graph_snapshot(corpus_path: Path) -> dict:
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    nodes, edges = {}, set()
    targets = {}
    for doc in corpus:
        for label, properties in _expected_units(doc):
            nodes[label, properties["id"]] = _properties(properties)
        for chapter in doc.get("chapters", []):
            edges.add((doc["id"], "CONTAINS", chapter["id"], ()))
            edges.update((chapter["id"], "CONTAINS", art["id"], ()) for art in chapter.get("articles", []))
        for article in doc.get("articles", []):
            targets.setdefault((doc["id"], article.get("code", "")), []).append(article["id"])
            edges.add((doc["id"], "CONTAINS", article["id"], ()))
            for clause in article.get("clauses", []):
                edges.add((article["id"], "CONTAINS", clause["id"], ()))
                edges.update((clause["id"], "CONTAINS", point["id"], ()) for point in clause.get("points", []))
    if corpus_path.name == "vat_corpus.staging.json":
        ledger_path = Path(__file__).resolve().parent / "fixtures/amendment_review.staging.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        if hashlib.sha256(corpus_path.read_bytes()).hexdigest() != ledger["pins"]["data/processed/vat_corpus.staging.json"]:
            raise ValueError("Staging corpus does not match reviewed amendment oracle")
        codes = {unit["id"]: unit.get("code", "") for doc in corpus
                 for article in doc.get("articles", [])
                 for unit in [article] + article.get("clauses", []) +
                 [point for clause in article.get("clauses", []) for point in clause.get("points", [])]}
        for candidate in ledger["candidates"]:
            if candidate["classification"] != "resolved":
                continue
            spec = candidate["expected"]
            for target in spec["targets"]:
                properties = _properties({
                    "target_scope": spec["scope"], "action": spec["action"],
                    "target_clause": codes.get(target.get("clause_id"), ""),
                    "target_point": codes.get(target.get("point_id"), ""),
                    "source_unit": candidate["source_unit"],
                })
                edges.add((candidate["source_unit"].split("_K")[0], "AMENDS", target["article_id"], properties))
        return {"nodes": sorted((label, node_id, props) for (label, node_id), props in nodes.items()),
                "edges": sorted(edges)}
    for doc in corpus:
        for article in doc.get("articles", []):
            for relation in extract_amends_from_article(doc["id"], article):
                if not relation.get("resolved", True) or not relation.get("target_doc"):
                    continue
                properties = _properties({
                    "target_scope": relation.get("target_scope", "article"),
                    "target_clause": relation.get("target_clause") or "",
                    "target_point": relation.get("target_point") or "",
                    "source_unit": relation.get("source_unit", relation["source_article"]),
                    "action": relation["action"],
                })
                for target in targets.get((relation["target_doc"], relation["target_article"]), []):
                    edges.add((relation["source_article"], "AMENDS", target, properties))
    return {"nodes": sorted((label, node_id, props) for (label, node_id), props in nodes.items()),
            "edges": sorted(edges)}


def read_graph_snapshot(driver) -> dict:
    with driver.session() as session:
        nodes = session.run("MATCH (n) RETURN labels(n) AS labels, properties(n) AS properties").data()
        edges = session.run(
            "MATCH (s)-[r]->(t) RETURN s.id AS source, type(r) AS type, t.id AS target, properties(r) AS properties"
        ).data()
    return {
        "nodes": sorted((":".join(sorted(row["labels"])), row["properties"].get("id", ""),
                         _properties(row["properties"])) for row in nodes),
        "edges": sorted((row["source"], row["type"], row["target"], _properties(row["properties"])) for row in edges),
    }


def pytest_addoption(parser):
    parser.addoption("--allow-neo4j-mutation", action="store_true", default=False,
                     help="Approve deletion and import on the isolated localhost:17687 test graph")


@pytest.fixture
def isolated_graph(request):
    if not request.config.getoption("--allow-neo4j-mutation"):
        pytest.skip("Graph deletion/import requires explicit --allow-neo4j-mutation approval")
    configure_test_neo4j()
    driver = get_neo4j_driver()
    try:
        with driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n").consume()
        yield driver
    finally:
        with driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n").consume()


def configure_test_neo4j():
    uri = os.getenv("NEO4J_TEST_URI", "bolt://127.0.0.1:17687")
    parsed = urlsplit(uri)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise pytest.UsageError("Neo4j tests require a localhost test container")
    if parsed.scheme != "bolt" or parsed.port != 17687:
        raise pytest.UsageError("Neo4j tests require bolt port 17687, never application port 7687")
    close_driver()
    os.environ["NEO4J_URI"] = uri
    os.environ["NEO4J_USER"] = os.getenv("NEO4J_TEST_USER", "neo4j")
    os.environ["NEO4J_PASSWORD"] = os.getenv("NEO4J_TEST_PASSWORD", "")
    return parsed.hostname, parsed.port


def pytest_configure(config):
    configure_test_neo4j()


def _neo4j_up(host: str = "127.0.0.1", port: int = 17687, timeout: float = 0.5) -> bool:
    with socket.socket() as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def pytest_collection_modifyitems(config, items):
    host, port = configure_test_neo4j()
    if os.getenv("NEO4J_TEST_PASSWORD") and _neo4j_up(host, port):
        return
    skip = pytest.mark.skip(reason="Isolated Neo4j test container or NEO4J_TEST_PASSWORD unavailable")
    for item in items:
        if "requires_neo4j" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def cleanup_neo4j_driver():
    yield
    close_driver()
