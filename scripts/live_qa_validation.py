import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import time
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]
ORACLE_FREEZE = "69ae2a512452a60dfbd4c87319f155dbbfd806b6261c1be0e8679d8d1a89c0d7"
ORACLE = "tests/fixtures/live_qa_oracle.json"
CORPUS = "data/processed/vat_corpus.staging.json"
VECTORS = "data/processed/article_embeddings.staging.json"
META = "data/processed/embedding_meta.staging.json"
REVIEW = "tests/fixtures/amendment_review.staging.json"
LOCAL_EXPERIMENT_REVISION = "local-ollama-20261120"
LOCAL_EXPERIMENT_CODE_PINS = {
    "src/config.py": "98e844e4329971ee9e236c0ce3aca6ce12136f38905bb35808831b474e881741",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     default=str, separators=(",", ":")).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(root, experiment_revision=None):
    if file_hash(root / ORACLE) != ORACLE_FREEZE:
        raise ValueError("oracle_drift")
    oracle = json.loads((root / ORACLE).read_bytes())
    if digest({k: v for k, v in oracle.items() if k != "freeze_sha256"}) != oracle["freeze_sha256"]:
        raise ValueError("oracle_internal_freeze_drift")
    if not oracle["frozen"] or len(oracle["cases"]) != 12:
        raise ValueError("oracle_shape")
    if experiment_revision not in {None, LOCAL_EXPERIMENT_REVISION}:
        raise ValueError("unknown_experiment_revision")
    revised = LOCAL_EXPERIMENT_CODE_PINS if experiment_revision else {}
    for name, expected in oracle["pins"].items():
        if file_hash(root / name) != revised.get(name, expected):
            raise ValueError("pinned_bytes_drift")
    identity = artifact_identity(root)
    paths = {p for folder in ("src", "scripts", "tests", "frontend/src")
             for p in (root / folder).rglob("*")
             if p.is_file() and p.suffix in {".py", ".ts", ".tsx", ".js", ".css"}}
    paths.update(p for p in (root / "frontend").glob("*")
                 if p.is_file() and p.suffix in {".ts", ".js", ".json", ".html"})
    paths.update(p for p in (root / "requirements.txt", root / "pytest.ini") if p.is_file())
    code = {p.relative_to(root).as_posix(): file_hash(p) for p in sorted(paths)}
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    return {"head": head, "code_sha256": code, "working_code_sha256": digest(code),
             "experiment_revision": experiment_revision,
             "baseline_code_pins": {p: h for p, h in oracle["pins"].items() if p.startswith("src/")},
             "experiment_code_pins": dict(revised),
            "oracle_freeze_sha256": ORACLE_FREEZE, "oracle_internal_sha256": oracle["freeze_sha256"],
            "profile": {"documents": 33, "vectors": 494, "review_path": REVIEW,
                        "bolt_port": 17687, "api_port": 18000, "ui_port": 15173},
            "artifacts": identity}, oracle


def artifact_identity(root):
    corpus = json.loads((root / CORPUS).read_bytes())
    vectors = json.loads((root / VECTORS).read_bytes())
    meta = json.loads((root / META).read_bytes())
    ids = [a["id"] for doc in corpus for a in doc["articles"]]
    valid = (len(corpus) == 33 and len(vectors) == 494 and len(ids) == len(set(ids))
             and set(ids) == set(vectors)
             and (meta.get("provider"), meta.get("model"), meta.get("dimension"), meta.get("count"))
             == ("ollama", "bge-m3", 1024, 494)
             and meta.get("cache_sha256") == file_hash(root / VECTORS)
             and meta.get("corpus_sha256") == file_hash(root / CORPUS))
    if not valid or any(len(v) != 1024 or any(type(x) not in (int, float) or not (-float("inf") < x < float("inf"))
                                               for x in v) for v in vectors.values()):
        raise ValueError("staging_artifacts_invalid")
    return {"corpus_sha256": file_hash(root / CORPUS), "cache_sha256": file_hash(root / VECTORS),
            "meta_sha256": file_hash(root / META), "review_sha256": file_hash(root / REVIEW),
            **{k: meta[k] for k in ("provider", "model", "dimension", "count")}}


def credentials():
    container = json.loads(subprocess.check_output(
        ["docker", "inspect", "vat-neo4j-test"], text=True, stderr=subprocess.DEVNULL))[0]
    ports = container["NetworkSettings"]["Ports"].get("7687/tcp")
    if not container["State"]["Running"] or ports != [{"HostIp": "127.0.0.1", "HostPort": "17687"}]:
        raise ValueError("staging_container_not_isolated")
    auth = next(v.split("=", 1)[1] for v in container["Config"]["Env"] if v.startswith("NEO4J_AUTH="))
    user, password = auth.split("/", 1)
    gemini = os.environ.get("GEMINI_API_KEY", "").strip()
    if not gemini and sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            gemini = winreg.QueryValueEx(key, "GEMINI_API_KEY")[0].strip()
    if not user or not password or not gemini:
        raise ValueError("credentials_missing")
    return user, password, gemini


def environment(root, auth):
    user, password, gemini = auth
    allowed = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "COMSPEC", "USERPROFILE",
               "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "SYSTEMDRIVE"}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env.update(PYTHON_DOTENV_DISABLED="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
               OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", VAT_ENV="staging",
               NEO4J_URI="bolt://127.0.0.1:17687", NEO4J_USER=user, NEO4J_PASSWORD=password,
               GEMINI_API_KEY=gemini, LLM_PROVIDER="gemini", VECTOR_ENABLED="1",
               VAT_CORPUS_PATH=str(root / CORPUS), VAT_EMBEDDINGS_PATH=str(root / VECTORS),
               VAT_EMBEDDING_META_PATH=str(root / META), VAT_AMENDMENT_REVIEW_PATH=str(root / REVIEW),
               VAT_VITE_HOST="127.0.0.1", VAT_VITE_PORT="15173", VAT_VITE_STRICT_PORT="1",
               VAT_API_PROXY_TARGET="http://127.0.0.1:18000")
    return env


def commands(root):
    vite = ("import {createServer,loadConfigFromFile} from 'vite'; "
            "const c=await loadConfigFromFile({command:'serve',mode:'development'},'vite.config.ts'); "
            "if(!c)throw Error('missing config'); "
            "const s=await createServer({...c.config,configFile:false,envFile:false}); await s.listen();")
    return [(root, [sys.executable, "-m", "uvicorn", "src.api.main:app", "--host", "127.0.0.1", "--port", "18000"]),
            (root / "frontend", ["node", "--input-type=module", "-e", vite])]


def listening(port):
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("redirect_blocked")


def local_json(url, payload=None):
    parsed = urllib.parse.urlsplit(url)
    allowed = {("127.0.0.1", 18000, "/api/health"), ("127.0.0.1", 15173, "/api/health"),
               ("127.0.0.1", 18000, "/api/query"), ("127.0.0.1", 11434, "/api/tags")}
    if (parsed.scheme != "http" or parsed.username or parsed.password or parsed.query or parsed.fragment
            or (parsed.hostname, parsed.port, parsed.path) not in allowed):
        raise ValueError("url_not_allowed")
    request = urllib.request.Request(url, data=None if payload is None else json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=180 if payload else 5) as response:
        return json.load(response)


def snapshot(root, env):
    from neo4j import GraphDatabase, READ_ACCESS
    files = {p.relative_to(root).as_posix(): file_hash(p)
             for p in sorted((root / "data").rglob("*")) if p.is_file()}
    files[REVIEW] = file_hash(root / REVIEW)
    with GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USER"], env["NEO4J_PASSWORD"]),
                              connection_timeout=5) as driver:
        with driver.session(default_access_mode=READ_ACCESS) as session:
            def read(tx):
                nodes = tx.run("MATCH (n) RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS props ORDER BY id").data()
                edges = tx.run("MATCH (a)-[r]->(b) RETURN elementId(r) AS id, elementId(a) AS source, elementId(b) AS target, type(r) AS type, properties(r) AS props ORDER BY id").data()
                return nodes, edges
            nodes, edges = session.execute_read(read)
    docs = [n for n in nodes if "Document" in n["labels"]]
    vectors = [n for n in nodes if "embedding" in n["props"]]
    expected = json.loads((root / VECTORS).read_bytes())
    graph_vectors = {n["props"].get("id"): n["props"]["embedding"] for n in vectors}
    if len(docs) != 33 or len(vectors) != 494 or graph_vectors != expected:
        raise ValueError("staging_graph_identity_mismatch")
    return {"files": files, "graph_sha256": digest([nodes, edges]), "nodes": len(nodes),
            "relationships": len(edges), "documents": len(docs), "vectors": len(vectors),
            "vectors_sha256": digest(graph_vectors)}


def check_node(report):
    try:
        node = subprocess.check_output(["node", "--version"], text=True, stderr=subprocess.DEVNULL).strip()
        if int(node.lstrip("v").split(".")[0]) < 20:
            report["blockers"].append("node_requires_20_or_newer")
    except Exception:
        report["blockers"].append("node_unavailable")


def preflight(root):
    report = {"mode": "preflight", "ready": False, "blockers": [], "provider_called": False,
              "services_started": False}
    env = {}
    try:
        report["manifest"], _ = fingerprint(root)
    except Exception:
        report["blockers"].append("manifest_or_pinned_artifacts_invalid")
    try:
        env = environment(root, credentials())
    except Exception:
        report["blockers"].append("staging_container_or_credentials_unavailable")
    for port in (18000, 15173):
        if listening(port):
            report["blockers"].append(f"port_{port}_occupied")
    try:
        models = local_json("http://127.0.0.1:11434/api/tags")["models"]
        if not any(m.get("name") in {"bge-m3", "bge-m3:latest"} for m in models):
            report["blockers"].append("existing_bge_m3_unavailable")
    except Exception:
        report["blockers"].append("existing_embedding_service_unavailable")
    if not (root / "frontend/node_modules/vite/bin/vite.js").is_file():
        report["blockers"].append("vite_not_installed")
    if env:
        check_node(report)
    if env:
        try:
            report["snapshot"] = snapshot(root, env)
        except Exception:
            report["blockers"].append("readonly_staging_graph_check_failed")
    report["ready"] = not report["blockers"]
    return report, env


def redact(value, env):
    text = json.dumps(value, ensure_ascii=False, default=str)
    for name in ("GEMINI_API_KEY", "NEO4J_PASSWORD"):
        secret = env.get(name)
        if secret:
            text = text.replace(json.dumps(secret, ensure_ascii=False)[1:-1], "[REDACTED]")
    return json.loads(text)


def save(path, value, env):
    path.write_text(json.dumps(redact(value, env), indent=2, ensure_ascii=False), encoding="utf-8")


def trace_events(trace):
    return [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines() if line.strip()]


def generate_cases(oracle, output, trace, env):
    rows = []
    for case in oracle["cases"]:
        row = {"id": case["id"], "trace_start": len(trace_events(trace))}
        try:
            row["actual"] = local_json("http://127.0.0.1:18000/api/query", payload={
                "query": case["question"], "as_of_date": case["as_of_date"], "history": case["history"],
                "pipeline": "P3_FullSystem", "use_llm": True, "model": "gemini-2.5-flash"})
        except Exception:
            row["actual"] = {}
            row["error"] = "request_failed_no_retry"
        row["trace_end"] = len(trace_events(trace))
        rows.append(redact(row, env))
        save(output / "actual.json", rows, env)
    return rows


def valid_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def output_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def failed_provider_attempt(traces, prompt, output):
    providers = [e for e in traces if e.get("event") == "provider_generation"
                 and e.get("prompt_sha256") == prompt and e.get("output_sha256") == output]
    if len(providers) != 1 or not valid_hash(prompt):
        return False
    provider = providers[0]
    if provider.get("failed") is not True or provider.get("has_output") is not False:
        return False
    config = provider.get("request_config_sha256")
    diagnostics = [e for e in traces if e.get("event") == "gemini_response"
                   and e.get("prompt_sha256") == prompt and e.get("request_config_sha256") == config]
    return (valid_hash(config) and bool(diagnostics) and diagnostics[-1].get("status") in {
        "http_error", "http_unauthorized", "http_forbidden", "http_rate_limited", "http_server_error",
        "transport_timeout", "transport_error", "invalid_json", "invalid_schema", "prompt_blocked",
        "no_candidates", "no_text", "thought_only", "max_tokens", "unexpected_error"}
        and diagnostics[-1].get("will_retry") is False)


def provenance_failures(traces):
    queries = [e for e in traces if e.get("event") == "query"]
    if len(queries) != 1:
        return ["query_trace_count_invalid"]
    query = queries[0]
    failures = []
    providers = [e for e in traces if e.get("event") == "provider_generation"]
    assessments = [e for e in traces if e.get("event") == "answer_assessment"]
    count = query.get("assessment_call_count")
    if type(count) is not int or count not in (0, 1) or len(assessments) != count:
        failures.append("assessment_trace_mismatch")
    if query.get("generation_disposition") == "safety_bypass":
        keys = ("prompt_sha256", "output_sha256", "assessment_prompt_sha256", "assessment_output_sha256")
        if providers or assessments or any(query.get(k) is not None for k in keys) or count != 0:
            failures.append("bypass_provenance_present")
        return failures
    no_output = (query.get("generation_disposition") == "provider_no_output"
                 and query.get("output_sha256") is None
                 and failed_provider_attempt(traces, query.get("prompt_sha256"), None))
    for key in ("prompt_sha256", "output_sha256", "delivered_sha256"):
        if not valid_hash(query.get(key)) and not (key == "output_sha256" and no_output):
            failures.append("generation_hash_missing")
    expected = [(query.get("prompt_sha256"), query.get("output_sha256"))]
    if count == 1:
        expected.append((query.get("assessment_prompt_sha256"), query.get("assessment_output_sha256")))
        failed_assessment = (query.get("assessment_outcome") == "unknown"
                             and query.get("assessment_reason") in {"transport", "timeout", "deadline", "no_output",
                                 "invalid_json", "invalid_schema", "truncated"}
                             and expected[-1][1] is None and failed_provider_attempt(traces, *expected[-1]))
        if (not valid_hash(expected[-1][0]) or not (valid_hash(expected[-1][1]) or failed_assessment)
                or expected[-1][0] == expected[0][0]):
            failures.append("assessment_hash_missing")
        fields = ("assessment_prompt_sha256", "assessment_output_sha256", "assessment_call_count",
                  "assessment_outcome", "assessment_reason", "assessment_claim_count", "assessment_coverage")
        if len(assessments) != 1 or any(assessments[0].get(k) != query.get(k)
                or type(assessments[0].get(k)) is not type(query.get(k)) for k in fields):
            failures.append("assessment_trace_mismatch")
    elif any(query.get(k) is not None for k in ("assessment_prompt_sha256", "assessment_output_sha256")):
        failures.append("unexpected_assessment_hash")
    pairs = [(e.get("prompt_sha256"), e.get("output_sha256")) for e in providers]
    if len(pairs) != len(expected) or any(pairs.count(pair) != 1 for pair in expected):
        failures.append("provider_hash_mismatch")
    return failures


def delivery_failures(query, actual):
    disposition = query.get("generation_disposition")
    delivered = disposition == "generated_delivered"
    failures = []
    if (disposition not in {"generated_delivered", "draft_replaced", "safety_bypass", "provider_no_output"}
            or query.get("generated_delivered") is not delivered
            or query.get("generation_attempted") is not (disposition != "safety_bypass")):
        failures.append("delivery_disposition_mismatch")
    if query.get("answer_status", actual.get("answer_status")) != actual.get("answer_status"):
        failures.append("query_status_mismatch")
    if "llm_used" in query and query["llm_used"] is not actual.get("llm_used"):
        failures.append("query_llm_mismatch")
    if actual.get("answer_status") == "ANSWERED":
        if (not delivered or type(query.get("assessment_call_count")) is not int
                or query.get("assessment_call_count") != 1 or query.get("assessment_outcome") != "supported"
                or query.get("assessment_reason") != "accepted" or query.get("assessment_coverage") is not True
                or type(query.get("assessment_claim_count")) is not int or query["assessment_claim_count"] < 1):
            failures.append("answered_without_supported_assessment")
    elif delivered and not (actual.get("answer_status") == "INSUFFICIENT_BASIS"
                           and query.get("assessment_outcome") == "needs_facts"
                           and query.get("assessment_reason") == "accepted"):
        failures.append("delivered_status_mismatch")
    raw = actual.get("raw_llm_response")
    if disposition == "provider_no_output" and query.get("output_sha256") is None:
        if raw is not None:
            failures.append("raw_output_hash_mismatch")
    elif disposition != "safety_bypass" and (not isinstance(raw, str)
                                            or output_hash(raw) != query.get("output_sha256")):
        failures.append("raw_output_hash_mismatch")
    sections = actual.get("irac_sections")
    if sections is not None:
        if (not isinstance(sections, dict) or not sections or any(not isinstance(v, str) for v in sections.values())
                or output_hash("\n".join(sections.values())) != query.get("delivered_sha256")):
            failures.append("delivered_hash_mismatch")
    elif disposition != "safety_bypass":
        failures.append("missing_delivered_sections")
    return failures


def assess(oracle, rows, events):
    results = []
    for index, case in enumerate(oracle["cases"]):
        row = next((r for r in rows if r["id"] == case["id"]), {})
        actual = row.get("actual", {})
        traces = events[row.get("trace_start", 0):row.get("trace_end", 0)]
        providers = [e for e in traces if e.get("event") == "provider_generation"]
        failures = []
        if actual.get("answer_status") != case["expected_status"]:
            failures.append("status_mismatch")
        queries = [e for e in traces if e.get("event") == "query"]
        bypass = len(queries) == 1 and queries[0].get("generation_disposition") == "safety_bypass"
        if bypass:
            query = queries[0]
            if (query.get("generation_attempted") is not False or query.get("generated_delivered") is not False
                    or query.get("assessment_call_count") != 0 or actual.get("llm_used") is not False
                    or actual.get("answer_status") not in {"INSUFFICIENT_BASIS", "OUT_OF_SCOPE"}
                    or actual.get("raw_llm_response") or providers
                    or any(e.get("event") == "gemini_response" for e in traces)):
                failures.append("inconsistent_safety_bypass")
        else:
            no_output = len(queries) == 1 and queries[0].get("generation_disposition") == "provider_no_output"
            if no_output:
                failures.append("provider_no_output")
                raw = actual.get("raw_llm_response")
                if actual.get("llm_used") is not False or (raw is not None and (
                        not isinstance(raw, str) or bool(raw.strip()))):
                    failures.append("missing_actual_generation")
            elif actual.get("llm_used") is not True or not actual.get("raw_llm_response"):
                failures.append("missing_actual_generation")
            if (len(queries) == 1 and queries[0].get("assessment_call_count") == 1
                    and queries[0].get("assessment_outcome") == "unknown"):
                failures.append("assessment_failed")
            if not queries or not providers:
                failures.append("missing_generation_trace")
            if not providers or any(e.get("provider") != "gemini" or e.get("model") != "gemini-2.5-flash"
                                     or (e.get("has_output") is not True and not failed_provider_attempt(
                                         traces, e.get("prompt_sha256"), e.get("output_sha256"))) for e in providers):
                failures.append("provider_not_successful_gemini")
        failures.extend(provenance_failures(traces))
        if len(queries) == 1:
            failures.extend(delivery_failures(queries[0], actual))
        citations = set(actual.get("citations", []))
        if not set(case["expected_evidence_ids"]).issubset(citations):
            failures.append("missing_expected_evidence")
        unsupported = set(case.get("resolver", {}).get("unsupported_ids", []))
        if unsupported & citations:
            failures.append("unsupported_evidence_cited")
        results.append({"id": case["id"], "failures": failures, "manual_verdict": "UNDECIDED",
                        "required_propositions": [{"text": p, "verdict": "UNDECIDED"} for p in case["required_propositions"]],
                        "prohibited_propositions": [{"text": p, "verdict": "UNDECIDED"} for p in case["prohibited_propositions"]],
                        "source_refs": {k: oracle.get("source_refs", {}).get(k) for k in case["source_refs"]},
                        "generation_events": providers, "assessment_method": "mechanical_only_no_model_judge"})
    failed = not results or len(rows) != len(oracle["cases"]) or any(r["failures"] for r in results)
    return {"exit_code": 2 if failed else 3, "status": "FAILED" if failed else "MANUAL_REVIEW_REQUIRED",
            "human_review_pending": True, "cases": results}


def wait_healthy(env, manifest, children):
    for attempt in range(60):
        if any(child.poll() is not None for child in children):
            raise RuntimeError("owned_service_exited")
        try:
            checks = [local_json(f"http://127.0.0.1:{port}/api/health") for port in (18000, 15173)]
        except Exception:
            time.sleep(2)
            continue
        if any(h.get("artifacts") != manifest["artifacts"] for h in checks):
            raise RuntimeError("health_artifact_mismatch")
        if all(h.get("status") == "healthy" and h.get("neo4j_connected") and h.get("environment") == "staging"
               and h.get("artifacts") == manifest["artifacts"] for h in checks):
            return
        time.sleep(2)
    raise RuntimeError("health_timeout")


def stop_owned(child):
    if child.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        import signal
        os.killpg(child.pid, signal.SIGTERM)
    child.wait(timeout=15)


def capture_service_errors(child, service, records):
    markers = {b"Application startup complete": "application_startup_complete",
               b"ModuleNotFoundError": "module_missing", b"address already in use": "address_in_use",
               b"failed to load config": "vite_config_failed", b"Error loading ASGI app": "asgi_load_failed",
               b"Application startup failed": "application_startup_failed"}
    seen = set()
    try:
        while chunk := child.stderr.readline(4096):
            for marker, code in markers.items():
                if marker in chunk and code not in seen:
                    records.append({"service": service, "code": code})
                    seen.add(code)
    except Exception:
        records.append({"service": service, "code": "stderr_capture_failed"})
    finally:
        child.stderr.close()


def run(root, output):
    report, env = preflight(root)
    report.update(mode="run", status="BLOCKED", human_review_pending=True,
                  provider_called=False, services_started=False)
    if not report["ready"]:
        save(output / "manifest.json", report, env)
        return 2, report
    children = []
    readers = []
    report["service_diagnostics"] = []
    report["stage"] = "fingerprint"
    before = None
    trace = None
    try:
        report["manifest"], oracle = fingerprint(root)
        report["stage"] = "snapshot"
        before = report["before"] = snapshot(root, env)
        report["stage"] = "trace_setup"
        trace = output / "generation.jsonl"
        trace.touch(exist_ok=False)
        env.update(VAT_DIAGNOSTICS=str(trace))
        for service, (cwd, command) in zip(("api", "vite"), commands(root)):
            report["stage"] = f"{service}_start"
            kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {"start_new_session": True}
            child = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **kwargs)
            children.append(child)
            report["services_started"] = True
            reader = threading.Thread(target=capture_service_errors,
                                      args=(child, service, report["service_diagnostics"]), daemon=True)
            readers.append(reader)
            reader.start()
        report["stage"] = "health_check"
        wait_healthy(env, report["manifest"], children)
        report["stage"] = "generation"
        rows = generate_cases(oracle, output, trace, env)
        report["stage"] = "assessment"
        assessment = assess(oracle, rows, trace_events(trace))
        save(output / "assessment.json", assessment, env)
        report.update(status=assessment["status"], exit_code=assessment["exit_code"])
    except BaseException as error:
        known = {"owned_service_exited", "health_artifact_mismatch", "health_timeout"}
        code = error.args[0] if (isinstance(error, RuntimeError) and error.args
                                 and type(error.args[0]) is str and error.args[0] in known) else f"{report['stage']}_failed"
        report.update(status="FAILED", exit_code=2, error="run_boundary_failed", error_code=code)
    finally:
        for child in reversed(children):
            try:
                stop_owned(child)
            except BaseException:
                report.update(status="FAILED", exit_code=2, cleanup_error="owned_cleanup_failed")
        for reader in readers:
            reader.join(timeout=5)
        if trace is not None and trace.exists():
            try:
                report["provider_called"] = any(e.get("event") in {"gemini_response", "provider_generation"}
                                                for e in trace_events(trace))
            except (ValueError, OSError):
                report.update(status="FAILED", exit_code=2, telemetry_error="trace_unreadable")
        verify_after(root, env, report, before)
        report["owned_services_stopped"] = "cleanup_error" not in report
        save(output / "manifest.json", report, env)
    return report.get("exit_code", 2), redact(report, env)


def verify_after(root, env, report, before):
    try:
        report["after"] = snapshot(root, env)
        report["unchanged"] = before is not None and before == report["after"]
        current, _ = fingerprint(root)
        report["manifest_unchanged"] = current == report["manifest"]
        if not report["unchanged"] or not report["manifest_unchanged"]:
            report.update(status="FAILED", exit_code=2)
    except BaseException:
        report.update(status="FAILED", exit_code=2, verification_error="after_fingerprint_failed")


def main(argv=None):
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    if args.preflight:
        report, _ = preflight(ROOT)
        print(json.dumps(report, ensure_ascii=True))
        return 0 if report["ready"] else 2
    output = ROOT / ".scratch" / ("live-qa-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    output.mkdir(parents=False, exist_ok=False)
    code, report = run(ROOT, output)
    print(json.dumps({"output": str(output), **report}, ensure_ascii=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
