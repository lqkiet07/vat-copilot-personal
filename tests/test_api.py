import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.api.models import DocumentSummary, HealthResponse, QueryRequest, QueryResponse


def test_query_request_validation():
    req = QueryRequest(query="Doanh nghiệp xã hội là gì?", as_of_date="2024-01-01")
    assert req.query == "Doanh nghiệp xã hội là gì?"
    assert req.as_of_date == "2024-01-01"
    assert req.pipeline == "P3_FullSystem"
    assert req.use_llm is False
    assert req.model == "qwen2.5:7b"

    with pytest.raises(ValidationError):
        QueryRequest(query="Thiếu ngày")

    with pytest.raises(ValidationError):
        QueryRequest(query="Test", as_of_date="invalid")

    with pytest.raises(ValidationError):
        QueryRequest(query="Test", as_of_date="2024-01-01", pipeline="INVALID_PIPELINE")

    with pytest.raises(ValidationError):
        QueryRequest(query="x" * 4001, as_of_date="2024-01-01")


def test_document_summary_model():
    doc = DocumentSummary(
        id="doc_1",
        code="59/2020/QH14",
        title="Luật Doanh nghiệp 2020",
        valid_from="2021-01-01",
        valid_to="2030-01-01",
    )
    assert doc.id == "doc_1"
    assert doc.code == "59/2020/QH14"
    assert doc.title == "Luật Doanh nghiệp 2020"
    assert doc.valid_from == "2021-01-01"
    assert doc.valid_to == "2030-01-01"

    with pytest.raises(ValidationError):
        DocumentSummary(id="doc_1", code="59/2020/QH14")


def test_health_response_model():
    health = HealthResponse(status="ok", neo4j_connected=True)
    assert health.status == "ok"
    assert health.neo4j_connected is True
    assert health.version == "1.0.0"

    health_custom = HealthResponse(status="degraded", neo4j_connected=False, version="1.1.0")
    assert health_custom.status == "degraded"
    assert health_custom.neo4j_connected is False
    assert health_custom.version == "1.1.0"

    with pytest.raises(ValidationError):
        HealthResponse(status="ok")


def test_query_response_model():
    res = QueryResponse(
        query="Quy định thành lập doanh nghiệp?",
        as_of_date="2024-01-01",
        pipeline="P3_FullSystem",
        risk_score=0,
        risk_level="LOW",
        irac_sections={"Issue": "Thành lập doanh nghiệp", "Rule": "Điều 17 Luật Doanh nghiệp"},
    )
    assert res.query == "Quy định thành lập doanh nghiệp?"
    assert res.as_of_date == "2024-01-01"
    assert res.pipeline == "P3_FullSystem"
    assert res.citations == []
    assert res.active_article_ids == []
    assert res.risk_score == 0
    assert res.risk_level == "LOW"
    assert res.warnings == []
    assert res.irac_sections == {
        "Issue": "Thành lập doanh nghiệp",
        "Rule": "Điều 17 Luật Doanh nghiệp",
    }
    assert res.llm_used is False
    assert res.raw_llm_response is None

    res_full = QueryResponse(
        query="Quy định?",
        as_of_date="2024-01-01",
        pipeline="P3_FullSystem",
        citations=["LUAT_59_2020_D17"],
        active_article_ids=["LUAT_59_2020_D17"],
        risk_score=15,
        risk_level="LOW",
        warnings=["Cảnh báo kiểm tra lại"],
        irac_sections={"Analysis": "Phân tích"},
        llm_used=True,
        raw_llm_response="Nội dung LLM",
    )
    assert res_full.citations == ["LUAT_59_2020_D17"]
    assert res_full.active_article_ids == ["LUAT_59_2020_D17"]
    assert res_full.risk_score == 15
    assert res_full.warnings == ["Cảnh báo kiểm tra lại"]
    assert res_full.llm_used is True
    assert res_full.raw_llm_response == "Nội dung LLM"


@pytest.fixture
def client():
    from src.api.main import app

    return TestClient(app)


@pytest.mark.requires_neo4j
def test_get_health_endpoint(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["version"] == "1.0.0"
    assert data["neo4j_connected"] is True


def test_get_documents_endpoint(client: TestClient):
    response = client.get("/api/documents")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 5
    for item in data:
        summary = DocumentSummary(**item)
        assert summary.id
        assert summary.code
        assert summary.title


def test_get_document_detail_success(client: TestClient):
    response = client.get("/api/documents/LUAT_48_2024")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert data["id"] == "LUAT_48_2024"
    assert "articles" in data
    assert isinstance(data["articles"], list)


def test_get_document_detail_not_found(client: TestClient):
    response = client.get("/api/documents/NON_EXISTENT_DOC")
    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found"


def test_get_scenarios_endpoint(client: TestClient):
    response = client.get("/api/scenarios")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 3


@pytest.mark.requires_neo4j
def test_post_query_endpoint(client: TestClient):
    payload = {
        "query": "Thuế suất phần mềm xuất khẩu năm 2026",
        "as_of_date": "2026-03-20",
        "pipeline": "P3_FullSystem",
    }
    response = client.post("/api/query", json=payload)
    assert response.status_code == 200
    data = response.json()
    res = QueryResponse(**data)
    assert res.risk_score is not None
    assert res.risk_level in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    assert isinstance(res.citations, list)
    assert isinstance(res.irac_sections, dict)
    assert "issue" in res.irac_sections or "Issue" in res.irac_sections


@pytest.mark.requires_neo4j
def test_post_query_endpoint_with_history(client: TestClient):
    payload = {
        "query": "Thế còn điều kiện áp dụng?",
        "as_of_date": "2026-03-20",
        "pipeline": "P3_FullSystem",
        "history": [
            {"role": "user", "content": "Thuế suất phần mềm xuất khẩu năm 2026"},
            {"role": "assistant", "content": "Thuế suất 0% theo Điều 9 Luật 48/2024."}
        ]
    }
    response = client.post("/api/query", json=payload)
    assert response.status_code == 200
    data = response.json()
    res = QueryResponse(**data)
    assert isinstance(res.citations, list)
    assert len(res.citations) > 0



def test_root_endpoint(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data


def test_post_query_rejects_invalid_as_of_date(client: TestClient):
    res1 = client.post("/api/query", json={"query": "Thuế suất", "as_of_date": "20-03-2026"})
    assert res1.status_code == 422

    res2 = client.post("/api/query", json={"query": "Thuế suất", "as_of_date": "1999-12-31"})
    assert res2.status_code == 422

    res3 = client.post("/api/query", json={"query": "Thuế suất", "as_of_date": "2101-01-01"})
    assert res3.status_code == 422


def test_post_query_rejects_unknown_pipeline(client: TestClient):
    res = client.post(
        "/api/query",
        json={"query": "Thuế suất", "as_of_date": "2026-03-20", "pipeline": "UnknownPipeline"},
    )
    assert res.status_code == 422


def test_cors_headers_allow_vite_port(client: TestClient):
    response = client.options(
        "/api/query",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert response.headers.get("access-control-allow-credentials") == "true"

    get_res = client.get(
        "/api/health",
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    assert get_res.status_code == 200
    assert get_res.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
    assert get_res.headers.get("access-control-allow-credentials") == "true"


def test_chat_request_validation():
    from src.api.chat_models import ChatMessage, ChatRequest

    req = ChatRequest(
        messages=[ChatMessage(role="user", content="Chào bạn")],
        as_of_date="2026-03-20",
    )
    assert req.as_of_date == "2026-03-20"

    with pytest.raises(ValidationError):
        ChatRequest(
            messages=[ChatMessage(role="user", content="Chào bạn")],
            as_of_date="invalid-date",
        )

    with pytest.raises(ValidationError):
        ChatRequest(
            messages=[ChatMessage(role="user", content="Chào bạn")],
            as_of_date="1999-01-01",
        )


def test_post_query_handles_service_error(client: TestClient):
    from unittest.mock import patch

    with patch("src.api.routes.execute_demo_query", side_effect=RuntimeError("Engine failure")):
        response = client.post(
            "/api/query",
            json={
                "query": "Lỗi hệ thống",
                "as_of_date": "2026-03-20",
                "pipeline": "P3_FullSystem",
            },
        )
        assert response.status_code == 503
        assert "Service unavailable: Engine failure" in response.json()["detail"]


def test_neo4j_driver_singleton():
    from unittest.mock import MagicMock
    import src.graph.connection as conn

    original_driver = conn._driver
    mock_driver = MagicMock()
    conn._driver = mock_driver
    try:
        d = conn.get_neo4j_driver()
        assert d is mock_driver
        mock_driver.verify_connectivity.assert_not_called()
    finally:
        conn._driver = original_driver

