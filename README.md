# Temporal KG-RAG for Verifiable Legal Reasoning over Vietnamese VAT Regulations

A verifiable Vietnamese VAT legal QA system: a two-level Neo4j knowledge graph
(Document → Article → Clause → Point), hybrid retrieval (Lucene full-text + 1024-dim
vectors, RRF k=60) with temporal validity filtering, a deterministic 5-state
Citation Verifier, and a 0–100 Risk Score.

## Tech stack
- Backend: Python 3.11+, FastAPI, Neo4j 5.26 (Docker), Streamlit
- Frontend: React 18, Vite, TailwindCSS
- Tests: pytest (117 tests)

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

### 4. Backend (port 8000)
```bash
uvicorn src.api.main:app --port 8000 --reload
# Swagger: http://localhost:8000/docs
```

### 5. Frontend (port 5173)
```bash
cd frontend
npm install
npm run dev
```

### 6. Streamlit prototype (optional)
```bash
streamlit run app_demo.py
```

### 7. Tests
```bash
pytest tests/ -q
```
Requires Neo4j to be running. Expected: `117 passed`.

## Environment variables
| Variable | Default | Description |
|---|---|---|
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j Bolt endpoint |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | `vat-temporal-2026` | Local demo password; change before deployment |

## Project structure
```
src/api/        FastAPI routes, Pydantic models
src/engine/     hybrid/temporal retrieval, LLM client
src/graph/      Neo4j schema, importer, connection
src/parser/     corpus builder, relation extractor
src/verifier/   citation verifier, risk score
src/ui/         demo service, corpus viewer
frontend/       React + Vite UI
data/           raw vbpl.vn HTML, processed corpus, embeddings, benchmark
scripts/        scrape, build corpus, embeddings, seed, benchmark
tests/          pytest suite
```

## Data
Legal corpus sourced from [vbpl.vn](https://vbpl.vn): Law 48/2024/QH15,
Decree 181/2025, Circular 69/2025, Decree 359/2025, Decree 144/2026.
