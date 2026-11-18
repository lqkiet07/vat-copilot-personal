import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests import conftest, test_graph_importer


def test_failed_staging_import_never_seeds_canonical(monkeypatch):
    from scripts import seed_neo4j
    from src.graph import importer

    calls = []
    monkeypatch.setattr(seed_neo4j, "seed", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(importer, "plan_corpus_import", lambda path: {"stats": {"documents": 33}})
    monkeypatch.setattr(test_graph_importer, "get_neo4j_driver", MagicMock())

    def fail_import(path):
        raise RuntimeError("import interrupted")

    monkeypatch.setattr(importer, "import_corpus_to_neo4j", fail_import)
    with pytest.raises(RuntimeError, match="import interrupted"):
        test_graph_importer.test_staging_corpus_parity_and_temporal_replay_in_neo4j()
    assert calls == []


@pytest.mark.parametrize("duplicate_edge", [False, True])
def test_staging_reimport_requires_exact_stable_snapshot(monkeypatch, duplicate_edge):
    from src.graph import importer

    expected = {"nodes": [("Article", "A", (("id", "A"),))], "edges": [("A", "AMENDS", "A", ())]}
    second = {"nodes": expected["nodes"], "edges": expected["edges"] * (2 if duplicate_edge else 1)}
    import_nodes = MagicMock(return_value={"documents": 33})
    import_edges = MagicMock()
    monkeypatch.setattr(importer, "plan_corpus_import", lambda path: {"stats": {"documents": 33}})
    monkeypatch.setattr(importer, "import_corpus_to_neo4j", import_nodes)
    monkeypatch.setattr(importer, "import_amends_relations", import_edges)
    monkeypatch.setattr(test_graph_importer, "get_neo4j_driver", MagicMock())
    monkeypatch.setattr(test_graph_importer, "expected_graph_snapshot", lambda path: expected)
    monkeypatch.setattr(test_graph_importer, "read_graph_snapshot", MagicMock(side_effect=[expected, second]))
    monkeypatch.setattr(test_graph_importer, "_assert_temporal_replay", MagicMock())
    if duplicate_edge:
        with pytest.raises(AssertionError):
            test_graph_importer.test_staging_corpus_parity_and_temporal_replay_in_neo4j()
    else:
        test_graph_importer.test_staging_corpus_parity_and_temporal_replay_in_neo4j()
    assert import_nodes.call_count == import_edges.call_count == 2
    assert import_nodes.call_args_list[0] == import_nodes.call_args_list[1]
    assert import_edges.call_args_list[0] == import_edges.call_args_list[1]


def test_cleanup_uses_only_explicit_isolated_driver(monkeypatch):
    cleanup = getattr(conftest, "isolated_graph", None)
    assert cleanup is not None, "An explicitly targeted graph lifecycle fixture is required"
    driver = MagicMock()
    monkeypatch.setattr(conftest, "get_neo4j_driver", lambda: driver)
    monkeypatch.setenv("NEO4J_TEST_URI", "bolt://127.0.0.1:17687")
    request = SimpleNamespace(config=SimpleNamespace(getoption=lambda name: True))
    lifecycle = cleanup.__wrapped__(request)
    assert next(lifecycle) is driver
    with pytest.raises(RuntimeError, match="test failed"):
        lifecycle.throw(RuntimeError("test failed"))
    queries = [call.args[0] for call in driver.session.return_value.__enter__.return_value.run.call_args_list]
    assert queries == ["MATCH (n) DETACH DELETE n"] * 2


def test_mutation_requires_separate_approval(monkeypatch):
    fixture = getattr(conftest, "isolated_graph", None)
    assert fixture is not None, "Graph mutation must require an explicit approval flag"
    driver_factory = MagicMock()
    monkeypatch.setattr(conftest, "get_neo4j_driver", driver_factory)
    request = SimpleNamespace(config=SimpleNamespace(getoption=lambda name: False))
    with pytest.raises(pytest.skip.Exception):
        next(fixture.__wrapped__(request))
    driver_factory.assert_not_called()


def test_mutation_rejects_application_target_before_driver_creation(monkeypatch):
    fixture = getattr(conftest, "isolated_graph", None)
    assert fixture is not None, "Graph lifecycle must validate its target"
    driver_factory = MagicMock()
    monkeypatch.setattr(conftest, "get_neo4j_driver", driver_factory)
    monkeypatch.setenv("NEO4J_TEST_URI", "bolt://localhost:7687")
    request = SimpleNamespace(config=SimpleNamespace(getoption=lambda name: True))
    with pytest.raises(pytest.UsageError, match="17687"):
        next(fixture.__wrapped__(request))
    driver_factory.assert_not_called()


@pytest.fixture
def tiny_corpus(tmp_path):
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps([{
        "id": "D", "code": "Doc", "title": "Document", "valid_from": "2025-01-01",
        "chapters": [{"id": "CH", "articles": [{"id": "A"}]}],
        "articles": [{
            "id": "A", "code": "Dieu 1", "title": "Article", "content": "body", "lead": "lead",
            "valid_from": "2025-02-01", "clauses": [{
                "id": "C", "code": "1", "content": "clause", "valid_from": "2025-03-01",
                "points": [{"id": "P", "code": "a", "content": "point", "valid_from": "2025-04-01"}],
            }],
        }],
    }]), encoding="utf-8")
    return path


def test_expected_snapshot_has_exact_nodes_dates_content_and_hierarchy(tiny_corpus):
    snapshotter = getattr(conftest, "expected_graph_snapshot", None)
    assert callable(snapshotter), "Expected snapshot helper is required"
    snapshot = snapshotter(tiny_corpus)
    nodes = {(label, node_id): dict(props) for label, node_id, props in snapshot["nodes"]}
    assert nodes == {
        ("Document", "D"): {"id": "D", "code": "Doc", "title": "Document", "valid_from": "2025-01-01", "valid_to": "9999-12-31", "status": "ACTIVE", "issue_date": "2025-01-01"},
        ("Chapter", "CH"): {"id": "CH", "code": "", "title": "", "doc_id": "D"},
        ("Article", "A"): {"id": "A", "code": "Dieu 1", "title": "Article", "content": "body\nlead", "doc_id": "D", "valid_from": "2025-02-01", "valid_to": "9999-12-31", "status": "ACTIVE"},
        ("Clause", "C"): {"id": "C", "code": "1", "content": "clause", "doc_id": "D", "valid_from": "2025-03-01", "valid_to": "9999-12-31"},
        ("Point", "P"): {"id": "P", "code": "a", "content": "point", "doc_id": "D", "valid_from": "2025-04-01", "valid_to": "9999-12-31"},
    }
    assert snapshot["edges"] == sorted([
        ("D", "CONTAINS", "CH", ()), ("CH", "CONTAINS", "A", ()),
        ("D", "CONTAINS", "A", ()), ("A", "CONTAINS", "C", ()), ("C", "CONTAINS", "P", ()),
    ])


def test_expected_snapshot_preserves_amendment_identity_and_excludes_unresolved(tiny_corpus, monkeypatch):
    snapshotter = getattr(conftest, "expected_graph_snapshot", None)
    assert callable(snapshotter), "Expected snapshot helper is required"
    base = {"source_article": "A", "target_doc": "D", "target_article": "Dieu 1", "action": "AMEND", "resolved": True}
    relations = [dict(base, target_scope="clause", target_clause="1", source_unit="C"),
                 dict(base, target_scope="point", target_clause="1", target_point="a", source_unit="P"),
                 dict(base, action="REPEAL", source_unit="A"),
                 dict(base, resolved=False, source_unit="unresolved"),
                 dict(base, target_doc="MISSING"), dict(base, target_article="Dieu 99")]
    monkeypatch.setattr(conftest, "extract_amends_from_article", lambda *args: relations + relations)
    snapshot = snapshotter(tiny_corpus)
    edges = [edge for edge in snapshot["edges"] if edge[1] == "AMENDS"]
    assert len(edges) == 3
    assert {tuple(dict(edge[3])[key] for key in ("target_scope", "target_clause", "target_point", "source_unit", "action")) for edge in edges} == {
        ("clause", "1", "", "C", "AMEND"), ("point", "1", "a", "P", "AMEND"),
        ("article", "", "", "A", "REPEAL"),
    }


def test_read_snapshot_is_read_only_and_detects_duplicate_edges():
    snapshotter = getattr(conftest, "read_graph_snapshot", None)
    assert callable(snapshotter), "Read snapshot helper is required"
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    node = {"labels": ["Article"], "properties": {"id": "A", "valid_from": date(2025, 1, 1), "content": "body"}}
    edge = {"source": "A", "type": "AMENDS", "target": "A", "properties": {"source_unit": "C", "action": "AMEND", "target_scope": "clause", "target_clause": "1", "target_point": ""}}
    session.run.side_effect = [MagicMock(data=lambda: [node]), MagicMock(data=lambda: [edge, edge])]
    snapshot = snapshotter(driver)
    assert snapshot["nodes"] == [("Article", "A", (("content", "body"), ("id", "A"), ("valid_from", "2025-01-01")))]
    assert len(snapshot["edges"]) == 2
    assert snapshot["edges"][0] == snapshot["edges"][1]
    for call in session.run.call_args_list:
        assert not any(word in call.args[0].upper().split() for word in ("SET", "MERGE", "CREATE", "DELETE", "REMOVE"))


def test_seeded_parity_only_plans_and_reads(monkeypatch):
    from scripts import seed_neo4j
    from src.graph import importer
    import sys

    module = sys.modules[__name__]
    planner = MagicMock()
    monkeypatch.setattr(seed_neo4j, "seed", planner)
    monkeypatch.setattr(importer, "GUIDES_PAIRS", [])
    monkeypatch.setattr(importer, "plan_corpus_import", lambda path: {"cites": []})
    root = Path(__file__).resolve().parents[1] / "data/processed"
    vectors = json.loads((root / "article_embeddings.staging.json").read_text(encoding="utf-8"))
    expected = {"nodes": [("Article", key, ()) for key in sorted(vectors)], "edges": []}
    actual = {"nodes": [("Article", key, (("embedding", vectors[key]),)) for key in sorted(vectors)], "edges": []}
    monkeypatch.setattr(conftest, "expected_graph_snapshot", lambda path: expected)
    monkeypatch.setattr(conftest, "read_graph_snapshot", lambda driver: actual)
    monkeypatch.setattr(conftest, "get_neo4j_driver", MagicMock())
    monkeypatch.setattr(test_graph_importer, "_assert_temporal_replay", MagicMock())
    module.test_seeded_staging_graph_read_only_parity()
    assert planner.call_args.kwargs["dry_run"] is True
    assert not planner.call_args.kwargs.get("do_reset", False)


@pytest.mark.requires_neo4j
def test_seeded_staging_graph_read_only_parity():
    from scripts import seed_neo4j
    from src.graph.importer import GUIDES_PAIRS, plan_corpus_import

    root = Path(__file__).resolve().parents[1] / "data/processed"
    corpus_path = root / "vat_corpus.staging.json"
    cache_path = root / "article_embeddings.staging.json"
    meta_path = root / "embedding_meta.staging.json"
    seed_neo4j.seed(dry_run=True, corpus_path=corpus_path, embeddings_path=cache_path, meta_path=meta_path)
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    vectors = json.loads(cache_path.read_text(encoding="utf-8"))
    expected = conftest.expected_graph_snapshot(corpus_path)
    targets = {}
    for doc in corpus:
        for article in doc.get("articles", []):
            targets.setdefault((doc["id"], article.get("code", "")), []).append(article["id"])
    cites = {}
    for relation in plan_corpus_import(str(corpus_path))["cites"]:
        if relation.get("resolved", True):
            for target in targets.get((relation.get("target_doc"), relation["target_article"]), []):
                cites[relation["source_clause"], target] = relation.get("target_clause") or ""
    expected["edges"].extend((source, "CITES", target, (("target_clause", clause),))
                             for (source, target), clause in cites.items())
    doc_ids = {doc["id"] for doc in corpus}
    expected["edges"].extend((source, "GUIDES", target, ()) for source, target in GUIDES_PAIRS
                             if source in doc_ids and target in doc_ids)
    driver = conftest.get_neo4j_driver()
    actual = conftest.read_graph_snapshot(driver)
    actual_vectors = {}
    nodes = []
    for label, node_id, properties in actual["nodes"]:
        properties = dict(properties)
        if "embedding" in properties:
            assert label == "Article"
            actual_vectors[node_id] = properties.pop("embedding")
        nodes.append((label, node_id, conftest._properties(properties)))
    assert actual_vectors == vectors
    assert nodes == expected["nodes"]
    assert actual["edges"] == sorted(expected["edges"])
    test_graph_importer._assert_temporal_replay(driver)


def test_r3_accepted23_import_repeats_exact_merge_keys_without_graph(monkeypatch):
    from src.graph import importer

    path = Path(__file__).resolve().parents[1] / "data/processed/vat_corpus.staging.json"
    driver = MagicMock()
    session = driver.session.return_value.__enter__.return_value
    session.run.return_value.single.return_value = {"c": 23}
    factory = MagicMock(return_value=driver)
    monkeypatch.setattr(importer, "get_neo4j_driver", factory)
    assert importer.import_amends_relations(str(path)) == 23
    first = list(session.run.call_args_list)
    session.run.reset_mock()
    assert importer.import_amends_relations(str(path)) == 23
    assert session.run.call_args_list == first
    merges = [call for call in first if "MERGE" in call.args[0]]
    assert len(merges) == 23
    keys = {(call.kwargs["src"], call.kwargs["tdoc"], call.kwargs["tcode"],
             call.kwargs["tscope"], call.kwargs["tclause"], call.kwargs["tpoint"],
             call.kwargs["sunit"], call.kwargs["action"]) for call in merges}
    assert len(keys) == 23
    assert factory.call_count == 2


def test_r4_staging_amends_oracle_is_independent_of_extractor(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "tests/fixtures/amendment_review.staging.json").read_text(encoding="utf-8"))
    expected = set()
    for candidate in ledger["candidates"]:
        if candidate["classification"] != "resolved":
            continue
        spec = candidate["expected"]
        for target in spec["targets"]:
            clause = target.get("clause_id", "").split("_K")[-1] if target.get("clause_id") else ""
            point = target.get("point_id", "").rsplit("_", 1)[-1] if target.get("point_id") else ""
            properties = conftest._properties({
                "source_unit": candidate["source_unit"], "action": spec["action"],
                "target_scope": spec["scope"], "target_clause": f"Khoan {clause}" if clause else "",
                "target_point": f"Diem {point}" if point else "",
            })
            expected.add((candidate["source_unit"].split("_K")[0], "AMENDS", target["article_id"], properties))
    assert len(expected) == 23
    path = root / "data/processed/vat_corpus.staging.json"
    baseline = conftest.expected_graph_snapshot(path)
    monkeypatch.setattr(conftest, "extract_amends_from_article", lambda *args: [])
    actual = conftest.expected_graph_snapshot(path)
    assert {edge for edge in actual["edges"] if edge[1] == "AMENDS"} == expected
    assert actual == baseline


def test_staging_expected_snapshot_is_repeatable_and_complete():
    snapshotter = getattr(conftest, "expected_graph_snapshot", None)
    assert callable(snapshotter), "Expected snapshot helper is required"
    path = Path(__file__).resolve().parents[1] / "data/processed/vat_corpus.staging.json"
    first = snapshotter(path)
    assert first == snapshotter(path)
    assert sum(label == "Document" for label, _, _ in first["nodes"]) == 33
    assert len(first["nodes"]) == len(set(first["nodes"]))
    assert len(first["edges"]) == len(set(first["edges"]))
