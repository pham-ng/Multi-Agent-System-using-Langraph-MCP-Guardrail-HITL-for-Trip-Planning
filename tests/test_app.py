from fastapi.testclient import TestClient

from app import app


def test_health_and_security_headers():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "x-request-id" in response.headers


def test_auth_me_supports_guest_mode():
    client = TestClient(app)
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_state_change_with_cross_site_origin_is_blocked_when_session_exists():
    client = TestClient(app)
    client.cookies.set("tripmate_session", "test-session")
    response = client.post("/api/auth/logout", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_blocked"
