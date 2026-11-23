import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path

from neo4j import GraphDatabase


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data/processed/vat_corpus.staging.json"
LEDGER = ROOT / "tests/fixtures/amendment_review.staging.json"
SCRATCH = ROOT / ".scratch"
LEDGER_SHA = "b77a61c1edab66ed75502d6a116d60cbdd03a58575394aec9ec82d1afb11ab21"
CORPUS_SHA = "eda6af1c9cb9856ab8831e0ed9e07318c729e38c4a93beb19f25f3a1b0e91cb5"
TARGETS = {"bolt://localhost:17687", "bolt://127.0.0.1:17687", "bolt://[::1]:17687"}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def ordered(rows):
    return sorted(rows, key=canonical)


def normalize_value(value):
    if isinstance(value, dict):
        return {key: normalize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_value(item) for item in value]
    if value is None or type(value) in (str, int, float, bool):
        canonical(value)
        return value
    if hasattr(value, "iso_format"):
        return {"neo4j_type": type(value).__name__, "iso": value.iso_format()}
    raise ValueError("Unsupported graph property type; no mutation permitted")


def normalize_graph(graph):
    nodes = [{"labels": sorted(row["labels"]), "properties": normalize_value(row["properties"])}
             for row in graph["nodes"]]
    return {"nodes": ordered(nodes), "edges": ordered(normalize_value(graph["edges"]))}


def amends(graph):
    return ordered([edge for edge in graph["edges"] if edge["type"] == "AMENDS"])


def fingerprints(graph):
    graph = normalize_graph(graph)
    non_amends = {"nodes": graph["nodes"], "edges": [edge for edge in graph["edges"] if edge["type"] != "AMENDS"]}
    vectors = [{"labels": node["labels"], "id": node["properties"].get("id"),
                "embedding": node["properties"]["embedding"]}
               for node in graph["nodes"] if "embedding" in node["properties"]]
    return {"full_graph": digest(graph), "non_amends": digest(non_amends),
            "vectors": digest(vectors), "amends": digest(amends(graph))}


def reviewed_edge(candidate, target, index):
    from src.graph.importer import _validate_amends_relation

    spec = candidate["expected"]
    source_path = index["units"].get(candidate["source_unit"], [])
    target_path = index["units"].get(target["article_id"], [])
    if len(source_path) != 1 or len(target_path) != 1:
        raise ValueError("Missing or ambiguous reviewed source/target")
    properties = {"source_unit": candidate["source_unit"], "action": spec["action"],
                  "target_scope": spec["scope"], "target_clause": "", "target_point": ""}
    for field, prop in (("clause_id", "target_clause"), ("point_id", "target_point")):
        if target.get(field):
            paths = index["units"].get(target[field], [])
            if len(paths) != 1:
                raise ValueError("Missing or ambiguous reviewed subarticle")
            properties[prop] = paths[0][-1]["code"]
            if paths[0][:2] != target_path[0][:2]:
                raise ValueError("Reviewed target ownership mismatch")
    relation = dict(properties, resolved=True, source_article=source_path[0][1]["id"],
                    source_doc=source_path[0][0]["id"], target_doc=target["doc_id"],
                    target_article=target_path[0][1]["code"])
    errors = _validate_amends_relation(relation, index)
    if target_path[0][0]["id"] != target["doc_id"]:
        errors.append("Reviewed target document mismatch")
    if errors:
        raise ValueError("Invalid reviewed AMENDS: " + "; ".join(errors))
    return {"source": relation["source_article"], "type": "AMENDS",
            "target": target["article_id"], "properties": properties}


def load_plan(corpus=CORPUS, ledger=LEDGER):
    from src.graph.importer import _build_corpus_index

    corpus_bytes, ledger_bytes = Path(corpus).read_bytes(), Path(ledger).read_bytes()
    if hashlib.sha256(ledger_bytes).hexdigest() != LEDGER_SHA:
        raise ValueError("Reviewed ledger SHA mismatch")
    review = json.loads(ledger_bytes)
    if (hashlib.sha256(corpus_bytes).hexdigest() != CORPUS_SHA
            or review["pins"]["data/processed/vat_corpus.staging.json"] != CORPUS_SHA):
        raise ValueError("Reviewed corpus SHA mismatch")
    if review["oracle"] != "independent_raw_source_review" or review["review_complete"] is not True:
        raise ValueError("Independent completed review required")
    index = _build_corpus_index(json.loads(corpus_bytes))
    sources, edges = set(), []
    for candidate in review["candidates"]:
        paths = index["units"].get(candidate["source_unit"], [])
        if len(paths) == 1:
            sources.add(paths[0][1]["id"])
        elif not candidate["source_unit"].endswith("_DOCUMENT_SCAN"):
            raise ValueError("Unknown reviewed source unit")
        if candidate["classification"] == "resolved":
            edges.extend(reviewed_edge(candidate, target, index) for target in candidate["expected"]["targets"])
    edges = ordered({canonical(edge): edge for edge in edges}.values())
    if len(edges) != 23:
        raise ValueError("Independent oracle must contain exactly 23 AMENDS")
    return {"sources": sorted(sources), "expected": edges, "corpus_sha256": CORPUS_SHA,
            "ledger_sha256": LEDGER_SHA}


def read_graph(tx):
    nodes = tx.run("MATCH (n) RETURN labels(n) AS labels, properties(n) AS properties").data()
    edges = tx.run("MATCH (s)-[r]->(t) RETURN s.id AS source, type(r) AS type, "
                   "t.id AS target, properties(r) AS properties").data()
    for edge in edges:
        if edge["type"] == "AMENDS":
            try:
                canonical(edge["properties"])
            except (TypeError, ValueError) as exc:
                raise ValueError("Unsupported AMENDS properties; no mutation permitted") from exc
    return normalize_graph({"nodes": nodes, "edges": edges})


def validate_graph(graph, plan):
    ids = Counter(node["properties"].get("id") for node in graph["nodes"])
    for edge in amends(graph) + plan["expected"]:
        if edge["source"] not in plan["sources"]:
            raise ValueError("Unknown AMENDS source; refusing to erase foreign edges")
        for endpoint in (edge["source"], edge["target"]):
            matches = [node for node in graph["nodes"] if node["properties"].get("id") == endpoint]
            if ids[endpoint] != 1 or "Article" not in matches[0]["labels"]:
                raise ValueError("AMENDS endpoint missing, ambiguous, or not Article")


def backup_path(path, new=False):
    path = Path(path).resolve()
    if not path.is_relative_to(SCRATCH.resolve()) or not path.parent.is_dir():
        raise ValueError("Backup must be inside an existing .scratch directory")
    if new and path.exists():
        raise ValueError("Backup already exists; refusing overwrite")
    if not new and not path.is_file():
        raise ValueError("Backup file missing")
    return path


def save_backup(path, graph, plan):
    path = backup_path(path, new=True)
    graph = normalize_graph(graph)
    payload = {"version": 1, "database": "neo4j", "target_port": 17687, "plan": plan,
               "graph": graph, "fingerprints": fingerprints(graph)}
    envelope = {"payload": payload, "sha256": digest(payload)}
    fd, temporary = tempfile.mkstemp(prefix=".r6-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical(envelope))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return load_backup(path, plan)


def load_backup(path, plan):
    try:
        envelope = json.loads(backup_path(path).read_text(encoding="utf-8"))
        payload = envelope["payload"]
        if digest(payload) != envelope["sha256"]:
            raise ValueError("Backup SHA mismatch")
        if (payload["version"] != 1 or payload["database"] != "neo4j"
                or payload["target_port"] != 17687 or payload["plan"] != plan):
            raise ValueError("Backup identity mismatch")
        if fingerprints(payload["graph"]) != payload["fingerprints"]:
            raise ValueError("Backup fingerprint mismatch")
        validate_graph(payload["graph"], plan)
        return payload
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid backup reconstruction") from exc


def snapshot(session):
    tx = session.begin_transaction()
    try:
        return read_graph(tx)
    finally:
        tx.rollback()
        tx.close()


def replace(session, before, edges, plan, commit):
    tx = session.begin_transaction()
    try:
        current = read_graph(tx)
        if fingerprints(current) != fingerprints(before):
            raise ValueError("Graph changed since verified backup/readback")
        validate_graph(current, plan)
        tx.run("MATCH (s)-[r:AMENDS]->() WHERE s.id IN $sources DELETE r", sources=plan["sources"]).consume()
        tx.run("UNWIND $edges AS edge MATCH (s:Article {id: edge.source}), "
               "(t:Article {id: edge.target}) CREATE (s)-[r:AMENDS]->(t) "
               "SET r = edge.properties", edges=edges).consume()
        after = read_graph(tx)
        if amends(after) != ordered(edges):
            raise ValueError("AMENDS readback mismatch before commit")
        if fingerprints(after)["non_amends"] != fingerprints(before)["non_amends"]:
            raise ValueError("Non-AMENDS/vector readback changed before commit")
        if commit:
            tx.commit()
        else:
            tx.rollback()
        return after
    except BaseException:
        tx.rollback()
        raise
    finally:
        tx.close()


def prepare(uri, user, password, apply, restore, backup, writers_stopped):
    if uri not in TARGETS:
        raise ValueError("Only exact localhost bolt port 17687 is allowed, never production")
    if not isinstance(user, str) or not user.strip() or not isinstance(password, str) or not password.strip():
        raise ValueError("Explicit nonempty credentials required")
    if apply and restore:
        raise ValueError("--apply and --restore are mutually exclusive")
    if (apply or restore) and not writers_stopped:
        raise ValueError("Explicit --writers-stopped acknowledgement required")
    if apply and backup is None:
        raise ValueError("--apply requires caller-selected --backup")
    if backup is not None and not apply:
        raise ValueError("--backup is only valid with --apply")
    if apply:
        backup_path(backup, new=True)
    plan = load_plan()
    captured = load_backup(restore, plan) if restore else None
    return plan, captured


def operate(session, plan, captured, apply, backup):
    before = snapshot(session)
    validate_graph(before, plan)
    report = {"mode": "restore" if captured else "apply" if apply else "dry-run",
              "actual_amends": len(amends(before)), "expected_amends": 23,
              "sources": plan["sources"], "expected": plan["expected"], "before": fingerprints(before)}
    if not apply and not captured:
        report["remove"] = list((Counter(map(canonical, amends(before))) - Counter(map(canonical, plan["expected"]))).elements())
        report["add"] = list((Counter(map(canonical, plan["expected"])) - Counter(map(canonical, amends(before)))).elements())
        return report
    if captured:
        if fingerprints(before)["non_amends"] != captured["fingerprints"]["non_amends"]:
            raise ValueError("Graph changed outside AMENDS; refusing restore")
        if amends(before) not in (amends(captured["graph"]), plan["expected"]):
            raise ValueError("AMENDS changed to unknown state; refusing restore")
        edges = amends(captured["graph"])
    else:
        captured = save_backup(backup, before, plan)
        if captured["fingerprints"] != fingerprints(before):
            raise ValueError("Backup reconstruction fingerprint mismatch")
        edges = plan["expected"]
    replace(session, before, edges, plan, commit=False)
    if fingerprints(snapshot(session)) != fingerprints(before):
        raise ValueError("Rollback rehearsal did not reconstruct original graph")
    if apply:
        load_backup(backup, plan)
    after = replace(session, before, edges, plan, commit=True)
    persisted = snapshot(session)
    if fingerprints(persisted) != fingerprints(after):
        raise ValueError("Postcommit readback mismatch; retain backup and investigate")
    report["after"] = fingerprints(persisted)
    return report


def run(*, uri, user, password, apply=False, restore=None, backup=None,
        writers_stopped=False, driver_factory=None):
    plan, captured = prepare(uri, user, password, apply, restore, backup, writers_stopped)
    factory = driver_factory or GraphDatabase.driver
    driver = factory(uri, auth=(user, password))
    try:
        with driver.session(database="neo4j") as session:
            return operate(session, plan, captured, apply, backup)
    finally:
        driver.close()


def main():
    parser = argparse.ArgumentParser(description="Staging-only targeted AMENDS repair; default read-only diff")
    parser.add_argument("--uri", required=True, choices=sorted(TARGETS))
    parser.add_argument("--user", required=True)
    parser.add_argument("--password-env", required=True, help="Explicit credential environment variable name")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--restore", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--writers-stopped", action="store_true")
    args = parser.parse_args()
    password = os.environ.get(args.password_env, "")
    try:
        report = run(uri=args.uri, user=args.user, password=password, apply=args.apply,
                     restore=args.restore, backup=args.backup, writers_stopped=args.writers_stopped)
    except Exception:
        parser.exit(1, "Repair refused or failed; no credentials logged. Retain backup; controller must inspect.\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
