"""
API Contract and Integration tests for Flask routes.
"""

from unittest.mock import MagicMock, patch
import pytest

from app import create_app
from app.config import config
from app.routes import set_executor


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(config, "DEV_MOCK_DOCKER", False)
    app = create_app({"TESTING": True, "RATE_LIMIT_STORAGE_URI": "memory://"})
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def test_health_check_healthy(client):
    mock_executor = MagicMock()
    mock_executor.docker_manager.ping_daemon.return_value = (True, None)
    mock_executor.docker_manager.detect_runtime.return_value = None
    set_executor(mock_executor)

    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "healthy"
    assert data["image"] == config.EXECUTION_IMAGE


def test_health_check_degraded(client):
    mock_executor = MagicMock()
    mock_executor.docker_manager.ping_daemon.return_value = (False, "docker_unreachable")
    set_executor(mock_executor)

    resp = client.get("/api/health")
    assert resp.status_code == 503
    data = resp.get_json()
    assert data["status"] == "degraded"
    assert data["reason"] == "docker_unreachable"


def test_execute_success_contract(client):
    mock_executor = MagicMock()
    mock_executor.execute.return_value = {
        "success": True,
        "execution_id": "test-id-12345",
        "status": "completed",
        "stdout": "Hello World\n",
        "stderr": "",
        "exit_code": 0,
        "execution_time_ms": 42,
        "truncated": False,
    }
    set_executor(mock_executor)

    resp = client.post("/api/execute", json={"code": 'print("Hello World")'})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["execution_id"] == "test-id-12345"
    assert data["stdout"] == "Hello World\n"
    assert data["stderr"] == ""
    assert data["exit_code"] == 0
    assert data["execution_time_ms"] == 42
    assert data["truncated"] is False


def test_execute_timeout_contract(client):
    mock_executor = MagicMock()
    mock_executor.execute.return_value = {
        "success": False,
        "execution_id": "timeout-id-12345",
        "status": "timeout",
        "error": "Execution timed out after 3 seconds.",
        "stdout": "partial output",
        "stderr": "",
        "execution_time_ms": 3000,
        "truncated": False,
    }
    set_executor(mock_executor)

    resp = client.post("/api/execute", json={"code": "while True: pass"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is False
    assert data["status"] == "timeout"
    assert "Execution timed out" in data["error"]
    assert data["stdout"] == "partial output"


def test_execute_validation_errors(client):
    # Missing code field
    resp = client.post("/api/execute", json={"invalid_field": "test"})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["status"] == "invalid_request"

    # Non-JSON content type
    resp2 = client.post("/api/execute", data="print(1)", content_type="text/plain")
    assert resp2.status_code == 400
    data2 = resp2.get_json()
    assert data2["status"] == "invalid_request"

    # Extra keys
    resp3 = client.post("/api/execute", json={"code": "print(1)", "image": "hack"})
    assert resp3.status_code == 400
    data3 = resp3.get_json()
    assert data3["status"] == "invalid_request"


def test_execute_internal_error_no_leaked_stacktrace(client):
    mock_executor = MagicMock()
    mock_executor.execute.side_effect = Exception("Low level socket connection failure /var/run/docker.sock secret trace")
    set_executor(mock_executor)

    resp = client.post("/api/execute", json={"code": "print(1)"})
    assert resp.status_code == 500
    data = resp.get_json()
    assert data["success"] is False
    assert data["status"] == "internal_error"
    assert data["error"] == "Execution could not be completed."
    # Ensure raw exception text is not returned
    assert "docker.sock" not in data["error"]


def test_security_headers_present(client):
    resp = client.get("/")
    assert "Content-Security-Policy" in resp.headers
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-XSS-Protection"] == "1; mode=block"


def test_concurrency_limit_at_capacity(client):
    import time
    from concurrent.futures import ThreadPoolExecutor
    
    mock_executor = MagicMock()
    # Simulate a slow execution
    def slow_exec(code):
        time.sleep(0.3)
        return {
            "success": True,
            "execution_id": "slow",
            "status": "completed",
            "stdout": "ok",
            "stderr": "",
            "exit_code": 0,
            "execution_time_ms": 300,
            "truncated": False,
        }
    
    mock_executor.execute.side_effect = slow_exec
    set_executor(mock_executor)

    # Launch MAX_CONCURRENT_EXECUTIONS + 2 requests in parallel
    num_requests = config.MAX_CONCURRENT_EXECUTIONS + 3
    
    def make_req(_):
        return client.post("/api/execute", json={"code": "import time; time.sleep(0.3)"})

    with ThreadPoolExecutor(max_workers=num_requests) as pool:
        responses = list(pool.map(make_req, range(num_requests)))

    status_codes = [r.status_code for r in responses]
    # At least one request should receive 503 at_capacity
    assert 503 in status_codes
    for r in responses:
        if r.status_code == 503:
            data = r.get_json()
            assert data["status"] == "at_capacity"
            assert "at capacity" in data["error"]

