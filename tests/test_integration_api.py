"""Focused checks for versioned API authentication and tenant boundaries."""
import hashlib
import json
from unittest.mock import MagicMock

import pytest

from app import create_app
from app.integrations import store
from app.routes import set_executor


@pytest.fixture
def integration_client(tmp_path, monkeypatch):
    monkeypatch.setenv("INTEGRATION_DATABASE", str(tmp_path / "integration.sqlite3"))
    app = create_app({"TESTING": True, "RATELIMIT_ENABLED": False, "SECRET_KEY": "integration-test-key"})
    store.initialize()
    key = "scp_test_secret"
    with store.db() as c:
        c.execute("INSERT INTO organizations(id,name,created_at) VALUES('org-1','Northstar','2026-01-01T00:00:00+00:00')")
        c.execute("INSERT INTO organizations(id,name,created_at) VALUES('org-2','Other','2026-01-01T00:00:00+00:00')")
        scopes = ["executions:write", "executions:read", "exams:write", "exams:read", "sessions:write", "sessions:read", "submissions:read", "audit:read"]
        c.execute("INSERT INTO credentials VALUES('cred-1','org-1',?,?,?,NULL)", (hashlib.sha256(key.encode()).hexdigest(), json.dumps(scopes), "2026-01-01T00:00:00+00:00"))
    mock_executor = MagicMock()
    mock_executor.execute.return_value = {"success": True, "execution_id": "exec-1", "status": "completed", "stdout": "done\n", "stderr": "", "exit_code": 0, "execution_time_ms": 4, "truncated": False}
    set_executor(mock_executor)
    yield app.test_client(), key, mock_executor
    set_executor(None)


def test_versioned_execution_requires_key_and_stores_tenant_result(integration_client):
    client, key, executor = integration_client
    assert client.post("/api/v1/executions", json={"code": "print(1)", "language": "python"}).status_code == 401
    response = client.post("/api/v1/executions", headers={"Authorization": f"Bearer {key}"}, json={"code": "print(1)", "language": "python"})
    assert response.status_code == 200
    assert response.json["stdout"] == "done\n"
    assert client.get("/api/v1/executions/exec-1", headers={"Authorization": f"Bearer {key}"}).status_code == 200
    assert client.get("/api/v1/executions/exec-1", headers={"Authorization": "Bearer invalid"}).status_code == 401
    executor.execute.assert_called_once_with("print(1)")


def test_exam_session_submission_keeps_hidden_expectation_private(integration_client):
    client, key, _ = integration_client
    headers = {"Authorization": f"Bearer {key}"}
    exam = client.post("/api/v1/exams", headers=headers, json={"title": "Practice", "hidden_tests": [{"expected_stdout": "done\n"}]})
    assert exam.status_code == 201
    exam_id = exam.json["exam_id"]
    assert client.post(f"/api/v1/exams/{exam_id}/status", headers=headers, json={"status": "scheduled"}).status_code == 200
    assert client.post(f"/api/v1/exams/{exam_id}/status", headers=headers, json={"status": "active"}).status_code == 200
    session = client.post(f"/api/v1/exams/{exam_id}/sessions", headers=headers, json={"external_candidate_reference": "candidate-7"})
    assert session.status_code == 201
    candidate_headers = {"Authorization": f"Bearer {session.json['candidate_token']}", "Idempotency-Key": "attempt-1"}
    result = client.post(f"/api/v1/exams/{exam_id}/submissions", headers=candidate_headers, json={"code": "print('done')", "language": "python"})
    assert result.status_code == 201
    assert result.json["score"] == 100
    assert "expected_stdout" not in result.get_data(as_text=True)
    retry = client.post(f"/api/v1/exams/{exam_id}/submissions", headers=candidate_headers, json={"code": "print('done')", "language": "python"})
    assert retry.status_code == 200
    assert retry.json["submission_id"] == result.json["submission_id"]


def test_exam_lookup_does_not_cross_tenant(integration_client):
    client, key, _ = integration_client
    headers = {"Authorization": f"Bearer {key}"}
    exam = client.post("/api/v1/exams", headers=headers, json={"title": "Private"})
    exam_id = exam.json["exam_id"]
    with store.db() as c:
        c.execute("UPDATE credentials SET organization_id='org-2' WHERE id='cred-1'")
    response = client.get(f"/api/v1/exams/{exam_id}", headers=headers)
    assert response.status_code == 404
