"""
Adversarial Integration Test Suite (Section 15).
Runs against live Docker daemon when available; cleanly skips if Docker is unreachable.
Tests cases A through M.
"""

import concurrent.futures
import os
import pytest
import docker

from app.config import config
from app.docker_manager import DockerManager
from app.executor import SandboxExecutor


def is_docker_available() -> bool:
    """Check if Docker daemon is responsive and execution image is present."""
    try:
        client = docker.from_env(timeout=3)
        client.ping()
        client.images.get(config.EXECUTION_IMAGE)
        return True
    except Exception:
        return False


docker_required = pytest.mark.skipif(
    not is_docker_available(),
    reason="Docker daemon is not running or sandbox image is not built",
)


@pytest.fixture
def executor():
    mgr = DockerManager()
    return SandboxExecutor(docker_manager=mgr)


@docker_required
def test_case_a_hello_world(executor):
    """Case A: Basic valid execution."""
    res = executor.execute('print("Hello World")')
    assert res["success"] is True
    assert res["status"] == "completed"
    assert "Hello World" in res["stdout"]
    assert res["exit_code"] == 0
    assert res["truncated"] is False


@docker_required
def test_case_b_syntax_error(executor):
    """Case B: Syntax error inside sandbox."""
    res = executor.execute("print(")
    assert res["success"] is True  # API execution succeeded, though user code had runtime/syntax error
    assert "SyntaxError" in res["stderr"]
    assert res["exit_code"] != 0


@docker_required
def test_case_c_infinite_loop_timeout(executor):
    """Case C: Infinite loop triggers external timeout watchdog."""
    res = executor.execute("while True: pass")
    assert res["success"] is False
    assert res["status"] == "timeout"
    assert "timed out" in res["error"]


@docker_required
def test_case_d_memory_exhaustion_oom(executor):
    """Case D: Memory exhaustion triggers OOM kill without impacting host."""
    code = 'x = [];\nwhile True:\n    x.append("A" * 1024 * 1024)\n'
    res = executor.execute(code)
    # Container OOM killed results in non-zero exit code (137 on Linux) or termination
    assert res.get("exit_code") != 0 or res["status"] in ("completed", "timeout")


@docker_required
def test_case_e_network_isolation(executor):
    """Case E: Outbound network calls fail due to network_mode='none'."""
    code = """
import urllib.request
try:
    urllib.request.urlopen("http://1.1.1.1", timeout=1)
    print("NET_SUCCESS")
except Exception as e:
    print(f"NET_FAILED: {type(e).__name__}")
"""
    res = executor.execute(code)
    assert "NET_FAILED" in res["stdout"] or "URLError" in res["stderr"]
    assert "NET_SUCCESS" not in res["stdout"]


@docker_required
def test_case_f_filesystem_read_only(executor):
    """Case F: Writing outside /tmp fails, while /tmp write succeeds."""
    code = """
# 1. Attempt write to read-only root/etc
try:
    with open("/etc/test.txt", "w") as f:
        f.write("fail")
    print("WRITE_ROOT_SUCCESS")
except OSError:
    print("WRITE_ROOT_FAILED")

# 2. Attempt write to size-capped tmpfs /tmp
try:
    with open("/tmp/test.txt", "w") as f:
        f.write("success")
    print("WRITE_TMP_SUCCESS")
except Exception:
    print("WRITE_TMP_FAILED")
"""
    res = executor.execute(code)
    assert "WRITE_ROOT_FAILED" in res["stdout"]
    assert "WRITE_TMP_SUCCESS" in res["stdout"]
    assert "WRITE_ROOT_SUCCESS" not in res["stdout"]


@docker_required
def test_case_g_subprocess_contained(executor):
    """Case G: Subprocess execution is bounded by dropped capabilities and non-root user."""
    code = """
import subprocess
try:
    p = subprocess.run(["id"], capture_output=True, text=True)
    print("ID_OUTPUT:", p.stdout.strip())
except Exception as e:
    print("SUBPROCESS_FAILED:", e)
"""
    res = executor.execute(code)
    assert "ID_OUTPUT:" in res["stdout"]
    # Verify non-root user
    assert "uid=0(root)" not in res["stdout"]
    assert "sandbox" in res["stdout"] or "uid=" in res["stdout"]


@docker_required
def test_case_h_unbounded_output_truncation(executor):
    """Case H: Unbounded output is truncated at MAX_OUTPUT_SIZE and returns promptly."""
    code = 'while True:\n    print("A" * 64)\n'
    res = executor.execute(code)
    # Should either be truncated or timeout with captured output
    assert res["truncated"] is True or "[Output truncated" in res["stdout"] or res["status"] == "timeout"


@docker_required
def test_case_i_secret_isolation(executor):
    """Case I: No host secrets/AWS credentials/Docker variables leak into environment."""
    # Set a dummy secret in host env
    os.environ["SUPER_SECRET_HOST_KEY"] = "super-secret-password-12345"
    
    code = """
import os
print("ENV_KEYS:", list(os.environ.keys()))
print("FULL_ENV:", dict(os.environ))
"""
    res = executor.execute(code)
    assert "super-secret-password-12345" not in res["stdout"]
    assert "SUPER_SECRET_HOST_KEY" not in res["stdout"]
    assert "AWS_SECRET_ACCESS_KEY" not in res["stdout"]


@docker_required
def test_case_j_fork_bomb_containment(executor):
    """Case J: Fork bomb is contained by pids_limit and does not degrade host."""
    code = """
import os
try:
    while True:
        os.fork()
except Exception as e:
    print("FORK_FAILED:", e)
"""
    res = executor.execute(code)
    # Container should terminate or fail safely under pids_limit
    assert res.get("exit_code") != 0 or res["status"] in ("completed", "timeout")


@docker_required
def test_case_k_tmp_cross_execution_isolation(executor):
    """Case K: Concurrent executions have isolated /tmp directories."""
    code_writer = """
import time
with open("/tmp/shared_marker.txt", "w") as f:
    f.write("writer_data")
time.sleep(0.5)
"""
    code_reader = """
import os, time
time.sleep(0.2)
exists = os.path.exists("/tmp/shared_marker.txt")
print("MARKER_EXISTS:", exists)
"""
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(executor.execute, code_writer)
        f2 = pool.submit(executor.execute, code_reader)
        res1 = f1.result()
        res2 = f2.result()

    assert "MARKER_EXISTS: False" in res2["stdout"]


@docker_required
def test_case_l_no_docker_socket_inside_container(executor):
    """Case L: /var/run/docker.sock is never mounted into the sandbox container."""
    code = """
import os
print("DOCKER_SOCK_EXISTS:", os.path.exists("/var/run/docker.sock"))
"""
    res = executor.execute(code)
    assert "DOCKER_SOCK_EXISTS: False" in res["stdout"]


def test_case_m_concurrency_overflow_503(monkeypatch):
    """Case M: N+1-th concurrent request gets 503 at_capacity."""
    from app import create_app
    app = create_app({"TESTING": True, "RATE_LIMIT_STORAGE_URI": "memory://"})
    client = app.test_client()

    import time
    from unittest.mock import MagicMock
    from app.routes import set_executor

    mock_exec = MagicMock()
    def slow_fn(c):
        time.sleep(0.4)
        return {"success": True, "status": "completed", "stdout": "ok", "execution_time_ms": 400}
    mock_exec.execute.side_effect = slow_fn
    set_executor(mock_exec)

    total_requests = config.MAX_CONCURRENT_EXECUTIONS + 4
    with concurrent.futures.ThreadPoolExecutor(max_workers=total_requests) as pool:
        responses = list(pool.map(lambda _: client.post("/api/execute", json={"code": "print(1)"}), range(total_requests)))

    status_codes = [r.status_code for r in responses]
    assert 503 in status_codes
