"""FastAPI application initialization and middleware configuration."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import api_router

app = FastAPI(
    title="VAT Legal Temporal GraphRAG API",
    version="1.0.0",
    description="Backend RESTful API for Vietnamese VAT Legal Reasoning",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/")
def read_root() -> dict[str, str]:
    """Root endpoint providing service greeting and Swagger documentation link."""
    return {"message": "VAT Legal Temporal GraphRAG API is running. Visit /docs for Swagger UI."}
