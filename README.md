# Temporal KG-RAG for Verifiable Legal Reasoning over Vietnamese VAT Regulations

A verifiable Vietnamese VAT legal QA system: a two-level Neo4j knowledge graph
(Document → Article → Clause → Point), hybrid retrieval (Lucene full-text + 1024-dim
vectors, RRF k=60) with temporal validity filtering, a deterministic 5-state
Citation Verifier, and a 0–100 Risk Score.

The web app has **two views**:

- **VAT Copilot** (default) — a chat assistant that answers Vietnamese VAT questions
  with streamed answers, highlighted citations, a risk score, and a link to the full
  legal text of every cited article.
- **Trung tâm Thẩm định** — the original split-view workbench: legal corpus on the left,
  verified analysis on the right, plus the P1/P2/P3 ablation switch and printable
  consultation minutes.

## Tech stack
- Backend: Python 3.11+, FastAPI, Neo4j 5.26 (Docker), Streamlit
- LLM: Ollama local (`qwen2.5:7b`), with a deterministic fallback when it is offline
- Frontend: React 18, Vite, TailwindCSS, react-markdown
- Tests: pytest (147 tests)

## Quick start

### 1. Neo4j
```bash
docker compose up -d
# Browser: http://localhost:7474 — neo4j / vat-temporal-2026
```

### 2. Python dependencies
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Seed the knowledge graph
```bash
python scripts/seed_neo4j.py
```
Idempotent (`MERGE`); re-running does not duplicate data.

### 4. Local LLM (needed for the VAT Copilot chat answers)
```bash
ollama pull qwen2.5:7b
# Ollama must be running on http://localhost:11434
```
Without Ollama the chat still works: it falls back to a deterministic answer that lists
the applicable legal basis instead of a generated explanation.

### 5. Backend (port 8000)
```bash
uvicorn src.api.main:app --port 8000 --reload
# Swagger: http://localhost:8000/docs
```

### 6. Frontend (port 5173)
```bash
cd frontend
npm install
npm run dev
```

### 7. Streamlit prototype (optional)
```bash
streamlit run app_demo.py
```

### 8. Tests
```bash
pytest tests/ -q
```
Requires Neo4j to be running. Expected: `147 passed`.

## VAT Copilot (chatbot)

| Capability | Behaviour |
|---|---|
| Scope guard | Answers Vietnamese VAT law only; out-of-scope questions are refused with three sample VAT questions |
| Temporal reasoning | The reference date is read from the question (`tháng 5/2024`, `ngày 15/05/2024`) or taken from the date chip; default is the data freeze date `2026-09-30` |
| Grounded answers | The prompt carries the clause text of every retrieved article; the model may only cite the supplied basis |
| Citation verification | Every citation is checked against the graph (VALID / AMENDED / NOT_YET_EFFECTIVE / EXPIRED / NOT_FOUND) and scored 0–100 |
| Transparency | Citations and key figures are highlighted inline; clicking a citation opens the full legal text |
| Multi-turn | Follow-up questions keep the conversation context; a short follow-up is merged with the previous turn for retrieval |
| History | Sessions are stored in the browser (`localStorage`), up to 20 conversations |

Chat endpoints (used by the frontend):

| Endpoint | Purpose |
|---|---|
| `POST /api/chat` | One turn, complete answer with citations and risk score |
| `POST /api/chat/stream` | Same turn streamed as SSE: `meta` → `token` → `done` |

## Environment variables
| Variable | Default | Description |
|---|---|---|
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j Bolt endpoint |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | `vat-temporal-2026` | Local demo password; change before deployment |

## Project structure
```
src/api/        FastAPI routes, Pydantic models, chat service
src/engine/     hybrid/temporal retrieval, LLM client
src/graph/      Neo4j schema, importer, connection
src/parser/     corpus builder, relation extractor
src/verifier/   citation verifier, risk score
src/ui/         demo service, corpus viewer
frontend/       React + Vite UI (chat view + split-view dashboard)
data/           raw vbpl.vn HTML, processed corpus, embeddings, benchmark
scripts/        scrape, build corpus, embeddings, seed, benchmark
tests/          pytest suite
```

## Data
Legal corpus sourced from [vbpl.vn](https://vbpl.vn): Law 48/2024/QH15,
Decree 181/2025, Circular 69/2025, Decree 359/2025, Decree 144/2026.
