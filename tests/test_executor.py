"""
Unit tests for SandboxExecutor and DockerManager using mocked Docker SDK.
Validates orchestration logic, security controls, cleanup ordering, and timeouts without live Docker.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from docker.errors import APIError, DockerException

from app.config import config
from app.docker_manager import DockerManager
from app.executor import SandboxExecutor


@pytest.fixture
def mock_docker_client(monkeypatch):
    monkeypatch.setattr(config, "DEV_MOCK_DOCKER", False)
    client = MagicMock()
    mock_container = MagicMock()
    mock_container.id = "mock-container-id-12345"
    mock_container.logs.side_effect = lambda stdout=True, stderr=True: (
        b"Hello World\n" if stdout else b""
    )
    
    # client.containers.create / run
    client.containers.create.return_value = mock_container
    client.containers.list.return_value = [mock_container]
    
    # client.api.wait
    client.api.wait.return_value = {"StatusCode": 0}
    client.info.return_value = {"Runtimes": {}}
    client.ping.return_value = True
    
    return client, mock_container


def test_executor_hello_world(mock_docker_client):
    client, mock_container = mock_docker_client
    docker_mgr = DockerManager(client=client)
    executor = SandboxExecutor(docker_manager=docker_mgr)

    result = executor.execute('print("Hello World")')

    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["stdout"] == "Hello World\n"
    assert result["stderr"] == ""
    assert result["exit_code"] == 0
    assert "execution_id" in result
    assert result["truncated"] is False
    assert result["execution_time_ms"] >= 0

    # Verify container creation parameters
    client.containers.create.assert_called_once()
    _, kwargs = client.containers.create.call_args
    assert kwargs["image"] == config.EXECUTION_IMAGE
    assert kwargs["network_mode"] == "none"
    assert kwargs["read_only"] is True
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges:true"]
    assert kwargs["environment"] == {}
    assert kwargs["mem_limit"] == config.MEMORY_LIMIT
    assert kwargs["memswap_limit"] == config.MEMSWAP_LIMIT
    assert kwargs["nano_cpus"] == config.CPU_NANO
    assert kwargs["pids_limit"] == config.PIDS_LIMIT
    assert "/tmp" in kwargs["tmpfs"]

    # Verify container removal occurred
    mock_container.remove.assert_called_once_with(force=True, v=True)


def test_executor_staging_cleanup_on_success(mock_docker_client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STAGING_ROOT", str(tmp_path))
    client, _ = mock_docker_client
    docker_mgr = DockerManager(client=client)
    executor = SandboxExecutor(docker_manager=docker_mgr)

    result = executor.execute('print("test")')
    exec_id = result["execution_id"]
    staged_dir = tmp_path / exec_id

    # Verify staging directory was deleted
    assert not staged_dir.exists()


def test_executor_staging_cleanup_on_failure(mock_docker_client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STAGING_ROOT", str(tmp_path))
    client, _ = mock_docker_client
    client.containers.create.side_effect = DockerException("Failed to create container")
    
    docker_mgr = DockerManager(client=client)
    executor = SandboxExecutor(docker_manager=docker_mgr)

    with pytest.raises(DockerException):
        executor.execute('print("fail")')

    # Verify no staging dirs remain in tmp_path
    remaining = list(tmp_path.iterdir())
    assert len(remaining) == 0


def test_executor_timeout_handling(mock_docker_client):
    client, mock_container = mock_docker_client
    
    # Simulate Docker wait timing out
    with patch.object(DockerManager, "_wait_container_bounded") as mock_wait:
        mock_wait.return_value = {
            "status": "timeout",
            "error": "Execution timed out after 3 seconds.",
            "stdout": "partial",
            "stderr": "",
            "truncated": False,
        }
        
        docker_mgr = DockerManager(client=client)
        executor = SandboxExecutor(docker_manager=docker_mgr)
        res = executor.execute("while True: pass")

        assert res["success"] is False
        assert res["status"] == "timeout"
        assert "timed out" in res["error"]
        assert res["stdout"] == "partial"


def test_output_truncation_when_exceeding_max(monkeypatch):
    monkeypatch.setattr(config, "DEV_MOCK_DOCKER", False)
    large_stdout = b"A" * (config.MAX_OUTPUT_SIZE + 100)
    mock_container = MagicMock()
    mock_container.logs.side_effect = lambda stdout=True, stderr=True: (
        large_stdout if stdout else b""
    )
    
    client = MagicMock()
    docker_mgr = DockerManager(client=client)
    stdout, stderr, is_truncated = docker_mgr._fetch_logs(mock_container)

    assert is_truncated is True
    assert "[Output truncated: maximum output limit exceeded]" in stdout
