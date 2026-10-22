# Temporal KG-RAG for Verifiable Legal Reasoning over Vietnamese VAT Regulations

Hệ thống hỏi đáp pháp luật VAT có thể kiểm chứng: knowledge graph Neo4j 2 tầng
(Văn bản → Điều → Khoản → Điểm), hybrid retrieval (Lucene full-text + vector 1024
chiều, RRF k=60) có lọc hiệu lực theo thời gian, Citation Verifier tất định 5 trạng
thái và Risk Score 0–100.

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

### 3. Seed knowledge graph
```bash
python scripts/seed_neo4j.py
```
Idempotent (`MERGE`); chạy lại nhiều lần không sinh dữ liệu trùng.

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
Cần Neo4j đang chạy. Kỳ vọng: `117 passed`.

## Environment variables
| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j Bolt endpoint |
| `NEO4J_USER` | `neo4j` | Tài khoản Neo4j |
| `NEO4J_PASSWORD` | `vat-temporal-2026` | Mật khẩu demo local, đổi khi deploy |

## Cấu trúc
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

## Dữ liệu
Corpus pháp lý thuần sạch từ [vbpl.vn](https://vbpl.vn): Luật 48/2024/QH15,
NĐ 181/2025, TT 69/2025, NĐ 359/2025, NĐ 144/2026.
