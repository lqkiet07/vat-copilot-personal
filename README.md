# Temporal KG-RAG for Verifiable Legal Reasoning over Vietnamese VAT Regulations

A verifiable Vietnamese VAT legal QA system combining a Neo4j temporal knowledge graph (`Document -> Article -> Clause -> Point`), hybrid retrieval (Lucene full-text + 1024-dim `bge-m3` vectors fused via RRF `k=60`), Clause/Point BM25+coverage reranking, deterministic provision-level temporal resolution against operative amendments, token-bounded evidence packing, two-stage LLM generation + structured Claim Assessment, and a 0–100 Citation Verifier.

## Architecture Overview

- **Backend (`src/api/`, `src/engine/`, `src/graph/`, `src/verifier/`)**:
  - Temporal Provision Evidence Resolver (`src/engine/provision_evidence.py`) computing active, amended, repeatable/inserted, blocked, or unresolved provisions at any `as_of_date` (`YYYY-MM-DD`) bound to explicit amendment review ledgers (`tests/fixtures/amendment_review.staging.json` for staging).
  - Hybrid Retrieval & Clause/Point Selection (`src/engine/hybrid_retriever.py`, `src/engine/retrieval_service.py`, `src/engine/provision_selector.py`) preserving RRF `top_k=15` article recall while ranking clauses/points into provider token budgets (`src/engine/evidence_pack.py`).
  - Two-stage Reviewed Answer Pipeline (`src/api/reviewed_answer.py`, `src/engine/answer_assessment.py`) returning verified `ANSWERED`, `INSUFFICIENT_BASIS`, or `OUT_OF_SCOPE` outcomes with exact blocker provenance.
- **Frontend (`frontend/src/app/`)**:
  - React 18 + Vite + TailwindCSS portal connected to `POST /api/query`, `GET /api/documents`, and `GET /api/documents/{doc_id}` (with offline 5-document demo fallback).

## Corpus Profiles

- **Canonical (5 core VAT documents, default port `7687`)**: `LUAT_48_2024`, `ND_181_2025`, `TT_69_2025`, `ND_359_2025`, `ND_144_2026`.
- **Staging (33 VAT-ecosystem documents, `VAT_ENV=staging`, isolated Neo4j port `17687`)**: uses `data/processed/vat_corpus.staging.json`, `article_embeddings.staging.json`, `embedding_meta.staging.json` (494 `bge-m3` vectors), and `tests/fixtures/amendment_review.staging.json`.

## Tech Stack

- Backend: Python 3.11+, FastAPI, Neo4j 5.26 (Docker)
- LLM Providers: Gemini (`GEMINI_API_KEY`, default `gemini-2.5-flash`) or local Ollama (`qwen2.5:7b` + `bge-m3` embeddings on `http://localhost:11434`)
- Frontend: React 18, Vite 6, TailwindCSS 4 (`frontend/src/app/`)
- Tests: pytest (backend offline/Neo4j), Node test runner + esbuild (frontend)

## Quick Start

**Prerequisites:** Docker Desktop, Python 3.11+, Node.js 20+, and Ollama (`bge-m3` required for 1024-dim vector retrieval; `qwen2.5:7b` optional for running local LLM without Gemini API).

### Recommended: Full 33-Document System (Staging Graph, Port `17687`)
Run these steps right after `git clone` to seed the 33-document Neo4j graph (`5,083` nodes, `494` vectors) and start the web app:

1. **Start isolated staging Neo4j (`127.0.0.1:17687`) & pull Ollama embedding/LLM models** (wait ~15s for Neo4j to become ready):
   ```bash
   docker compose -f docker-compose.staging.yml up -d
   ollama pull bge-m3
   # Optional: local offline LLM instead of Gemini API
   ollama pull qwen2.5:7b
   ```

2. **Install Python dependencies**:
   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
   *(On macOS/Linux: `source .venv/bin/activate`.)*

3. **Create `.env` from the staging template**:
   - Windows PowerShell: `Copy-Item .env.staging.example .env`
   - macOS/Linux Bash: `cp .env.staging.example .env`

   Edit `.env` to set your `GEMINI_API_KEY` (default `LLM_PROVIDER=gemini`), or set `LLM_PROVIDER=ollama` to run fully offline with `qwen2.5:7b`.

4. **Seed the 33-document staging graph & vectors** (one-time per machine; idempotent `MERGE`):
   - Windows PowerShell:
     ```powershell
     $env:NEO4J_URI='bolt://127.0.0.1:17687'
     $env:NEO4J_PASSWORD='vat-temporal-2026'
     python scripts/seed_neo4j.py --corpus data/processed/vat_corpus.staging.json --embeddings data/processed/article_embeddings.staging.json --meta data/processed/embedding_meta.staging.json
     ```
   - macOS/Linux Bash:
     ```bash
     NEO4J_URI=bolt://127.0.0.1:17687 NEO4J_PASSWORD=vat-temporal-2026 python scripts/seed_neo4j.py --corpus data/processed/vat_corpus.staging.json --embeddings data/processed/article_embeddings.staging.json --meta data/processed/embedding_meta.staging.json
     ```

5. **Start Backend & Frontend in two separate terminals**:
   - **Terminal 1 — Backend API (`http://localhost:8000`):**
     ```bash
     uvicorn src.api.main:app --port 8000 --env-file .env --reload
     ```
     Verify `http://localhost:8000/api/health` shows `"environment": "staging"` and `"neo4j_connected": true` (Swagger UI: `http://localhost:8000/docs`).
   - **Terminal 2 — Frontend Web App (`http://localhost:5173`):**
     ```bash
     cd frontend
     npm install
     npm run dev
     ```
     Open `http://localhost:5173` — the UI automatically detects `staging` via `/api/health`, shows `Staging (33 VB · 23/11/2026)`, locks `P3`, and loads all 33 documents in the legal library.

### Alternative: Canonical Profile (5 Core Documents, Port `7687`)
```bash
docker compose up -d
python scripts/seed_neo4j.py
uvicorn src.api.main:app --port 8000 --reload
```

## Verification Commands

### Backend Offline Test Suite
```powershell
$env:OPENBLAS_NUM_THREADS='1'; $env:OMP_NUM_THREADS='1'; $env:NEO4J_TEST_PASSWORD=''; $env:PYTHON_DOTENV_DISABLED='1'
pytest tests/ -q --tb=short
```

### Frontend Build & Compatibility Tests
```powershell
cd frontend
npm run build
node --test tests/citation-compatibility.test.mjs
```

### Staging Preflight Check (requires isolated `vat-neo4j-test:17687`, `bge-m3`, `GEMINI_API_KEY`)
```bash
python scripts/live_qa_validation.py --preflight
```

## Key API Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Neo4j connectivity, runtime profile (`canonical`/`staging`), artifact digests |
| `GET /api/documents` | List document summaries in active corpus |
| `GET /api/documents/{doc_id}` | Full article/clause tree for a document |
| `POST /api/query` | Execute temporal legal QA with `answer_status`, citations, warnings, and IRAC sections |
| `POST /api/chat` / `POST /api/chat/stream` | Multi-turn reviewed chat (buffered / SSE) |

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `VAT_ENV` | `canonical` | Runtime profile: `canonical` or `staging` |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j Bolt URI (staging requires `17687`) |
| `NEO4J_USER` / `NEO4J_PASSWORD` | `neo4j` / — | Neo4j credentials |
| `LLM_PROVIDER` | auto | `gemini`, `ollama`, or `openai` |
| `GEMINI_API_KEY` | — | API key when using Gemini provider |
| `EMBEDDING_PROVIDER` | `deterministic` | `deterministic`, `ollama` (`bge-m3`), or `auto` |
| `CLOUD_LLM_CONTEXT_LIMIT` | `24576` | Cloud provider prompt context budget (`4096..131072`) |
| `OLLAMA_NUM_CTX` | `8192` | Local Ollama context window |

## Project Structure
```
src/api/        FastAPI routes, query/chat services, reviewed answer pipeline
src/engine/     Hybrid/temporal retrieval, provision resolver, selector, evidence packing, LLM client
src/graph/      Neo4j schema, importer, connection
src/parser/     Corpus builder, relation extractor
src/verifier/   Citation verifier, guardrails, risk scoring
src/ui/         Corpus tree & index loaders, demo query service
frontend/       React + Vite application (src/app/) and frontend tests
scripts/        Seeding, staging QA preflight/validation runner, amendment ledger helpers
tests/          Pytest test suites and staging fixtures
```
