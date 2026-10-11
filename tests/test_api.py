from fastapi.testclient import TestClient

from t2sql.api import create_app


def test_health_and_schema(service):
    client = TestClient(create_app(service))
    assert client.get("/health").json() == {"status": "ok", "engine": "offline-planner", "dialect": "sqlite", "tables": 8}
    schema = client.get("/schema").json()
    assert schema["orders"]["rows"] == 4000
    assert any(c["fk"] == "customers.customer_id" for c in schema["orders"]["columns"])


def test_ask_and_follow_up_over_http(service):
    client = TestClient(create_app(service))
    first = client.post("/ask", json={"question": "Revenue by region in 2025", "session_id": "api-1"}).json()
    assert first["ok"] and first["session_id"] == "api-1" and first["chart"]["type"] == "bar"
    second = client.post("/ask", json={"question": "only 2026", "session_id": "api-1"}).json()
    assert second["ok"] and "2026" in second["title"]
    hist = client.get("/sessions/api-1").json()
    assert len(hist["turns"]) == 2
    client.delete("/sessions/api-1")
    assert client.get("/sessions/api-1").json()["turns"] == []


def test_validation(service):
    client = TestClient(create_app(service))
    assert client.post("/ask", json={"question": "hi"}).status_code == 422
    assert client.post("/ask", json={"question": "revenue", "session_id": "bad id!"}).status_code == 422


def test_api_key(service):
    service.settings = service.settings.model_copy(update={"api_key": "k"})
    try:
        client = TestClient(create_app(service))
        assert client.post("/ask", json={"question": "revenue by region"}).status_code == 401
        assert client.post("/ask", json={"question": "revenue by region"}, headers={"X-API-Key": "k"}).status_code == 200
        assert client.get("/health").status_code == 200
    finally:
        service.settings = service.settings.model_copy(update={"api_key": None})
