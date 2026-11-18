import copy
import hashlib
import importlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


ROOT = Path(__file__).resolve().parents[1]


def repair():
    path = ROOT / "scripts/repair_staging_amends.py"
    assert path.exists(), "R6 targeted repair script is required"
    return importlib.import_module("scripts.repair_staging_amends")


@pytest.mark.parametrize("uri", ["bolt://localhost:7687", "neo4j://localhost:17687",
    "bolt://example.com:17687", "bolt://user@localhost:17687", "bolt://localhost:17687/",
    "bolt://localhost:17687?x=1", "bolt://localhost:17687#x", "bolt://localhost:17687.evil"])
def test_rejects_unsafe_target_before_driver(uri):
    module = repair()
    factory = MagicMock()
    with pytest.raises(ValueError):
        module.run(uri=uri, user="neo4j", password="explicit", driver_factory=factory)
    factory.assert_not_called()


@pytest.mark.parametrize("user,password", [("", "x"), ("neo4j", ""), (" ", "x")])
def test_requires_explicit_credentials_before_driver(user, password):
    factory = MagicMock()
    with pytest.raises(ValueError, match="credentials"):
        repair().run(uri="bolt://localhost:17687", user=user, password=password, driver_factory=factory)
    factory.assert_not_called()


def test_independent_23_edge_oracle_and_reviewed_rejected_sources(monkeypatch):
    module = repair()
    from src.graph import importer
    monkeypatch.setattr(importer, "extract_amends_from_article", lambda *args: [])
    plan = module.load_plan()
    assert len(plan["expected"]) == 23
    assert len({module.canonical(edge) for edge in plan["expected"]}) == 23
    assert {"ND_144_2026_D1", "ND_144_2026_D3", "ND_144_2026_D4", "LUAT_90_2025_D4"} <= set(plan["sources"])
    assert not any(source.endswith("DOCUMENT_SCAN") for source in plan["sources"])
    assert "ND_144_2026_D4a" not in plan["sources"]
    from tests.conftest import expected_graph_snapshot
    independent = [{"source": source, "type": kind, "target": target, "properties": dict(properties)}
                   for source, kind, target, properties in expected_graph_snapshot(module.CORPUS)["edges"]
                   if kind == "AMENDS"]
    assert plan["expected"] == module.ordered(independent)


def test_oracle_runs_r3_validation(monkeypatch):
    from src.graph import importer
    monkeypatch.setattr(importer, "_validate_amends_relation", lambda *args: ["bad ownership"])
    with pytest.raises(ValueError, match="bad ownership"):
        repair().load_plan()


@pytest.mark.parametrize("artifact", ["corpus", "ledger"])
def test_changed_review_or_corpus_is_rejected(tmp_path, artifact):
    module = repair()
    paths = {"corpus": module.CORPUS, "ledger": module.LEDGER}
    changed = tmp_path / paths[artifact].name
    changed.write_bytes(paths[artifact].read_bytes() + b" ")
    paths[artifact] = changed
    with pytest.raises(ValueError, match="SHA"):
        module.load_plan(**paths)


def graph_fixture(module):
    plan = module.load_plan()
    corpus = json.loads(module.CORPUS.read_text(encoding="utf-8"))
    nodes = [{"labels": ["Article"], "properties": {"id": article["id"], "embedding": [0.5, -1.0]}}
             for doc in corpus for article in doc.get("articles", [])]
    edges = copy.deepcopy(plan["expected"])
    stale = copy.deepcopy(edges[0])
    stale["source"] = "ND_144_2026_D3"
    stale["properties"]["source_unit"] = "ND_144_2026_D3_K2"
    stale["properties"]["target_point"] = "Diem a"
    edges += [stale, copy.deepcopy(stale), copy.deepcopy(edges[0])]
    edges += [{"source": nodes[0]["properties"]["id"], "target": nodes[1]["properties"]["id"],
               "type": "CITES", "properties": {"x": "unchanged"}}]
    return plan, {"nodes": nodes, "edges": edges}


class GraphDriver:
    def __init__(self, graph, corrupt=False):
        self.graph = copy.deepcopy(graph)
        self.corrupt = corrupt
        self.transactions = []
        self.closed = False

    def session(self, **kwargs):
        assert kwargs == {"database": "neo4j"}
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def begin_transaction(self):
        transaction = GraphTransaction(self)
        self.transactions.append(transaction)
        return transaction

    def close(self):
        self.closed = True


class GraphTransaction:
    def __init__(self, driver):
        self.driver = driver
        self.graph = copy.deepcopy(driver.graph)
        self.writes = []
        self.committed = False
        self.rolled_back = False

    def run(self, query, **params):
        if "DELETE r" in query:
            assert "r:AMENDS" in query and "$sources" in query
            assert "DETACH" not in query
            self.writes.append(query)
            self.graph["edges"] = [edge for edge in self.graph["edges"]
                if edge["type"] != "AMENDS" or edge["source"] not in params["sources"]]
            rows = []
        elif "CREATE" in query:
            assert "CREATE (s)-[r:AMENDS]->(t)" in query
            self.writes.append(query)
            self.graph["edges"].extend(copy.deepcopy(params["edges"]))
            if self.driver.corrupt:
                self.graph["edges"].pop()
            rows = []
        elif "labels(n)" in query:
            rows = copy.deepcopy(self.graph["nodes"])
        elif "type(r)" in query:
            rows = copy.deepcopy(self.graph["edges"])
        else:
            raise AssertionError(query)
        result = MagicMock()
        result.data.return_value = rows
        return result

    def commit(self):
        self.driver.graph = copy.deepcopy(self.graph)
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def test_dry_run_reads_only_without_backup(tmp_path):
    module = repair()
    plan, original = graph_fixture(module)
    driver = GraphDriver(original)
    report = module.run(uri="bolt://localhost:17687", user="neo4j", password="explicit",
                        driver_factory=lambda *a, **kw: driver)
    assert report["actual_amends"] == 26 and report["expected_amends"] == 23
    assert driver.graph == original
    assert not any(tx.writes for tx in driver.transactions)
    assert not list(tmp_path.iterdir())
    assert driver.closed


def test_apply_rehearses_rollback_and_restore_reconstructs_exact_duplicates(tmp_path, monkeypatch):
    module = repair()
    monkeypatch.setattr(module, "SCRATCH", tmp_path)
    plan, original = graph_fixture(module)
    driver = GraphDriver(original)
    backup = tmp_path / "r6.json"
    args = dict(uri="bolt://localhost:17687", user="neo4j", password="explicit",
                driver_factory=lambda *a, **kw: driver, writers_stopped=True)
    report = module.run(**args, apply=True, backup=backup)
    assert report["mode"] == "apply"
    writes = [tx for tx in driver.transactions if tx.writes]
    assert len(writes) == 2
    assert writes[0].rolled_back and not writes[0].committed
    assert writes[1].committed
    repaired = copy.deepcopy(driver.graph)
    assert module.amends(repaired) == module.ordered(plan["expected"])
    assert module.fingerprints(repaired)["non_amends"] == module.fingerprints(original)["non_amends"]
    assert module.fingerprints(repaired)["vectors"] == module.fingerprints(original)["vectors"]
    captured = module.load_backup(backup, plan)
    assert captured["graph"] == module.normalize_graph(original)
    assert len(module.amends(captured["graph"])) == 26
    assert module.run(**args, restore=backup)["mode"] == "restore"
    assert module.normalize_graph(driver.graph) == module.normalize_graph(original)
    assert all("DETACH" not in query for tx in driver.transactions for query in tx.writes)


def test_failed_readback_rolls_back_without_commit(tmp_path, monkeypatch):
    module = repair()
    monkeypatch.setattr(module, "SCRATCH", tmp_path)
    _, original = graph_fixture(module)
    driver = GraphDriver(original, corrupt=True)
    with pytest.raises(ValueError, match="readback"):
        module.run(uri="bolt://localhost:17687", user="neo4j", password="explicit", apply=True,
                   backup=tmp_path / "r6.json", writers_stopped=True, driver_factory=lambda *a, **kw: driver)
    assert driver.graph == original
    assert all(not tx.committed for tx in driver.transactions)
    assert all(tx.rolled_back for tx in driver.transactions)


@pytest.mark.parametrize("source", ["FOREIGN", "ND_144_2026_D4a"])
def test_unknown_source_aborts_without_erasing_foreign_edges(tmp_path, monkeypatch, source):
    module = repair()
    monkeypatch.setattr(module, "SCRATCH", tmp_path)
    _, original = graph_fixture(module)
    original["edges"][0]["source"] = source
    driver = GraphDriver(original)
    with pytest.raises(ValueError, match="source"):
        module.run(uri="bolt://localhost:17687", user="neo4j", password="explicit", apply=True,
                   backup=tmp_path / "r6.json", writers_stopped=True, driver_factory=lambda *a, **kw: driver)
    assert driver.graph == original
    assert not any(tx.writes for tx in driver.transactions)
    assert not (tmp_path / "r6.json").exists()


def test_mutation_requires_quiescence_and_scratch_backup_before_driver(tmp_path):
    module = repair()
    factory = MagicMock()
    args = dict(uri="bolt://localhost:17687", user="neo4j", password="explicit", driver_factory=factory)
    for kwargs in ({"apply": True}, {"apply": True, "writers_stopped": True},
                   {"apply": True, "writers_stopped": True, "backup": tmp_path / "outside.json"},
                   {"apply": True, "restore": tmp_path / "missing.json", "writers_stopped": True}):
        with pytest.raises(ValueError):
            module.run(**args, **kwargs)
    factory.assert_not_called()


def test_atomic_backup_rejects_overwrite_tamper_and_incomplete_reconstruction(tmp_path, monkeypatch):
    module = repair()
    monkeypatch.setattr(module, "SCRATCH", tmp_path)
    plan, original = graph_fixture(module)
    path = tmp_path / "backup.json"
    module.save_backup(path, original, plan)
    saved = path.read_bytes()
    with pytest.raises((ValueError, FileExistsError)):
        module.save_backup(path, original, plan)
    assert path.read_bytes() == saved
    envelope = json.loads(saved)
    envelope["payload"]["graph"]["edges"].pop()
    path.write_text(json.dumps(envelope), encoding="utf-8")
    factory = MagicMock()
    with pytest.raises(ValueError, match="SHA"):
        module.run(uri="bolt://localhost:17687", user="neo4j", password="explicit", restore=path,
                   writers_stopped=True, driver_factory=factory)
    factory.assert_not_called()
    envelope["sha256"] = module.digest(envelope["payload"])
    path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint"):
        module.load_backup(path, plan)


def test_restore_refuses_unrelated_graph_changes(tmp_path, monkeypatch):
    module = repair()
    monkeypatch.setattr(module, "SCRATCH", tmp_path)
    plan, original = graph_fixture(module)
    path = tmp_path / "backup.json"
    module.save_backup(path, original, plan)
    driver = GraphDriver(original)
    driver.graph["nodes"][0]["properties"]["embedding"] = [9.0]
    with pytest.raises(ValueError, match="changed"):
        module.run(uri="bolt://localhost:17687", user="neo4j", password="explicit", restore=path,
                   writers_stopped=True, driver_factory=lambda *a, **kw: driver)
    assert not any(tx.writes for tx in driver.transactions)


def test_backup_failure_prevents_any_mutation(tmp_path, monkeypatch):
    module = repair()
    monkeypatch.setattr(module, "SCRATCH", tmp_path)
    _, original = graph_fixture(module)
    driver = GraphDriver(original)

    def fail_publish(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(module.os, "link", fail_publish)
    with pytest.raises(OSError, match="disk unavailable"):
        module.run(uri="bolt://localhost:17687", user="neo4j", password="explicit", apply=True,
                   backup=tmp_path / "r6.json", writers_stopped=True, driver_factory=lambda *a, **kw: driver)
    assert driver.graph == original
    assert not any(tx.writes for tx in driver.transactions)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("change", ["missing", "duplicate", "wrong_label"])
def test_ambiguous_or_missing_endpoints_prevent_backup_and_mutation(tmp_path, monkeypatch, change):
    module = repair()
    monkeypatch.setattr(module, "SCRATCH", tmp_path)
    plan, original = graph_fixture(module)
    endpoint = plan["expected"][0]["target"]
    node = next(node for node in original["nodes"] if node["properties"]["id"] == endpoint)
    if change == "missing":
        original["nodes"].remove(node)
    elif change == "duplicate":
        original["nodes"].append(copy.deepcopy(node))
    else:
        node["labels"] = ["Clause"]
    driver = GraphDriver(original)
    with pytest.raises(ValueError, match="endpoint"):
        module.run(uri="bolt://localhost:17687", user="neo4j", password="explicit", apply=True,
                   backup=tmp_path / "r6.json", writers_stopped=True, driver_factory=lambda *a, **kw: driver)
    assert not any(tx.writes for tx in driver.transactions)
    assert not list(tmp_path.iterdir())


def test_replacement_refuses_graph_drift_before_delete():
    module = repair()
    plan, original = graph_fixture(module)
    driver = GraphDriver(original)
    driver.graph["nodes"][0]["properties"]["embedding"] = [99.0]
    with pytest.raises(ValueError, match="changed"):
        module.replace(driver, module.normalize_graph(original), plan["expected"], plan, commit=True)
    assert not any(tx.writes or tx.committed for tx in driver.transactions)


def test_all_74_original_pins_remain_unchanged():
    ledger = json.loads((ROOT / "tests/fixtures/amendment_review.staging.json").read_text(encoding="utf-8"))
    assert len(ledger["pins"]) == 74
    assert ledger["publication_approved"] is False
    assert all(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == sha
               for path, sha in ledger["pins"].items())
