"""
DockerManager module.
Isolates all docker-py interactions for container lifecycle, hardening, and cleanup.
"""

import concurrent.futures
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import docker
from docker.errors import APIError, DockerException, NotFound
from docker.models.containers import Container
from docker.types import Ulimit

from app.config import config
from app.utils import truncate_output

logger = logging.getLogger(__name__)


class DockerManager:
    """Manages ephemeral, hardened Docker containers for sandbox execution."""

    def __init__(self, client: Optional[docker.DockerClient] = None) -> None:
        self._client: Optional[docker.DockerClient] = client
        self._runtime_override: Optional[str] = None
        self._initialized: bool = False

    @property
    def client(self) -> docker.DockerClient:
        """Lazy-initialize Docker client if not injected."""
        if self._client is None:
            try:
                self._client = docker.from_env(timeout=config.DOCKER_TIMEOUT)
            except DockerException as e:
                logger.error("Failed to connect to Docker daemon: %s", str(e))
                raise
        return self._client

    def ping_daemon(self) -> Tuple[bool, Optional[str]]:
        """
        Actively ping the Docker daemon and verify the sandbox image exists.
        Returns (is_healthy, error_reason).
        """
        if config.DEV_MOCK_DOCKER:
            return True, None

        try:
            client = self.client
            client.ping()
        except Exception as e:
            logger.warning("Docker daemon ping failed: %s", str(e))
            return False, "docker_unreachable"

        try:
            client.images.get(config.EXECUTION_IMAGE)
        except Exception as e:
            logger.warning("Execution image '%s' not found: %s", config.EXECUTION_IMAGE, str(e))
            return False, "image_missing"

        return True, None

    def detect_runtime(self) -> Optional[str]:
        """
        Detect if gVisor (runsc) runtime is registered on the host Docker daemon.
        Returns 'runsc' if present, otherwise None.
        """
        if config.DEV_MOCK_DOCKER:
            return "Dev Emulation Runner (Docker Offline)"

        if self._runtime_override is not None:
            return self._runtime_override

        try:
            info = self.client.info()
            runtimes = info.get("Runtimes", {})
            if "runsc" in runtimes:
                logger.info("gVisor (runsc) runtime detected and will be used for execution.")
                self._runtime_override = "runsc"
                return "runsc"
        except Exception as e:
            logger.warning("Could not query Docker daemon runtimes: %s", str(e))

        self._runtime_override = None
        return None

    def create_and_run_sandbox(
        self,
        execution_id: str,
        host_script_path: str,
    ) -> Dict[str, Any]:
        """
        Create, run, and watch an ephemeral Docker sandbox container with strict security controls.
        Enforces timeout externally via ThreadPoolExecutor.
        """
        if config.DEV_MOCK_DOCKER:
            return self._run_mock_sandbox(execution_id, host_script_path)

        client = self.client
        container_name = f"sandbox-exec-{execution_id}"
        runtime = self.detect_runtime()
        
        # Define security parameters
        # Normalize host script path for Docker volume mounting
        normalized_host_path = os.path.abspath(host_script_path)
        
        volumes = {
            normalized_host_path: {
                "bind": "/sandbox/script.py",
                "mode": "ro",
            }
        }
        
        tmpfs_opts = {
            "/tmp": f"size={config.TMPFS_SIZE},noexec,nosuid,nodev"
        }
        
        ulimits = [
            Ulimit(name="nofile", soft=256, hard=256)
        ]
        
        container_kwargs: Dict[str, Any] = {
            "image": config.EXECUTION_IMAGE,
            "command": ["python", "-B", "/sandbox/script.py"],
            "name": container_name,
            "labels": {"sandbox": "true", "execution_id": execution_id},
            "volumes": volumes,
            "working_dir": "/sandbox",
            "network_mode": "none",
            "read_only": True,
            "tmpfs": tmpfs_opts,
            "mem_limit": config.MEMORY_LIMIT,
            "memswap_limit": config.MEMSWAP_LIMIT,
            "nano_cpus": config.CPU_NANO,
            "pids_limit": config.PIDS_LIMIT,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "ulimits": ulimits,
            "environment": {},  # Explicitly empty environment
            "init": True,       # Use docker-init (tini) for clean signal forwarding and zombie reaping
            "detach": True,
            "auto_remove": False,
        }
        
        if runtime:
            container_kwargs["runtime"] = runtime

        container: Optional[Container] = None
        try:
            logger.info("Creating sandbox container: %s", container_name, extra={"execution_id": execution_id})
            container = client.containers.create(**container_kwargs)
            container.start()
            container_id = container.id

            # External bounded wait watchdog
            wait_res = self._wait_container_bounded(container_id, container)
            return wait_res

        except Exception as e:
            logger.error(
                "Error during container run for execution %s: %s",
                execution_id,
                str(e),
                exc_info=True,
                extra={"execution_id": execution_id},
            )
            raise
        finally:
            if container:
                self.remove_container(container)

    def _wait_container_bounded(self, container_id: str, container: Container) -> Dict[str, Any]:
        """
        Wait for container completion bounded by EXECUTION_TIMEOUT + TIMEOUT_GRACE_PERIOD.
        """
        timeout = config.EXECUTION_TIMEOUT
        grace = config.TIMEOUT_GRACE_PERIOD
        total_wait_timeout = timeout + grace

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._docker_wait, container_id, timeout)
            try:
                exit_code = future.result(timeout=total_wait_timeout)
                # Completed within timeout
                stdout, stderr, is_truncated = self._fetch_logs(container)
                return {
                    "status": "completed",
                    "exit_code": exit_code,
                    "stdout": stdout,
                    "stderr": stderr,
                    "truncated": is_truncated,
                }
            except Exception as e:
                err_str = str(e).lower()
                if isinstance(e, (concurrent.futures.TimeoutError, TimeoutError)) or "timeout" in err_str or "timed out" in err_str:
                    logger.warning(
                        "Execution timed out after %s seconds for container %s: %s",
                        timeout,
                        container_id,
                        str(e),
                    )
                    self._safe_kill(container)
                    stdout, stderr, _ = self._fetch_logs(container)
                    return {
                        "status": "timeout",
                        "error": f"Execution timed out after {timeout} seconds.",
                        "stdout": stdout,
                        "stderr": stderr,
                        "truncated": False,
                    }
                raise

    def _docker_wait(self, container_id: str, timeout_sec: int) -> int:
        """Call low-level Docker API wait with internal socket timeout."""
        try:
            # Low-level API wait
            res = self.client.api.wait(container_id, timeout=timeout_sec)
            if isinstance(res, dict):
                return int(res.get("StatusCode", 0))
            return int(res)
        except Exception as e:
            err_str = str(e).lower()
            if "timeout" in err_str or "timed out" in err_str:
                raise TimeoutError(f"Docker wait timed out: {e}") from e
            raise

    def _safe_kill(self, container: Container) -> None:
        """Attempt to immediately kill a running container."""
        try:
            container.kill(signal="SIGKILL")
        except NotFound:
            pass
        except Exception as e:
            logger.warning("Failed to kill container %s: %s", getattr(container, "id", "unknown"), str(e))

    def _fetch_logs(self, container: Container) -> Tuple[str, str, bool]:
        """
        Fetch stdout and stderr separately, size-capped to MAX_OUTPUT_SIZE.
        Returns (stdout, stderr, is_truncated).
        """
        try:
            raw_stdout = container.logs(stdout=True, stderr=False)
            raw_stderr = container.logs(stdout=False, stderr=True)
            
            stdout_str = raw_stdout.decode("utf-8", errors="replace") if isinstance(raw_stdout, bytes) else str(raw_stdout)
            stderr_str = raw_stderr.decode("utf-8", errors="replace") if isinstance(raw_stderr, bytes) else str(raw_stderr)

            # Combined output truncation check
            total_bytes = len(stdout_str.encode("utf-8", errors="replace")) + len(stderr_str.encode("utf-8", errors="replace"))
            
            if total_bytes > config.MAX_OUTPUT_SIZE:
                # Truncate stdout first if it is large, or truncate both
                half_limit = config.MAX_OUTPUT_SIZE // 2
                stdout_trunc, trunc1 = truncate_output(stdout_str, half_limit)
                stderr_trunc, trunc2 = truncate_output(stderr_str, half_limit)
                return stdout_trunc, stderr_trunc, True

            return stdout_str, stderr_str, False

        except Exception as e:
            logger.warning("Failed to retrieve container logs: %s", str(e))
            return "", f"Failed to retrieve logs: {str(e)}", False

    def remove_container(self, container_or_id: Any) -> None:
        """Force remove a container, ignoring NotFound errors."""
        try:
            if hasattr(container_or_id, "remove") and callable(getattr(container_or_id, "remove")):
                container_or_id.remove(force=True, v=True)
            else:
                self.client.api.remove_container(str(container_or_id), force=True, v=True)
        except NotFound:
            pass
        except Exception as e:
            logger.warning("Failed to remove container %s: %s", str(container_or_id), str(e))

    def list_sandbox_containers(self) -> List[Container]:
        """List all containers labeled with sandbox=true."""
        if config.DEV_MOCK_DOCKER:
            return []
        try:
            return self.client.containers.list(all=True, filters={"label": "sandbox=true"})
        except Exception as e:
            logger.error("Failed to list sandbox containers: %s", str(e))
            return []

    def _run_mock_sandbox(self, execution_id: str, host_script_path: str) -> Dict[str, Any]:
        """
        Execute code in development emulation mode when Docker Engine is offline.
        Enforces execution timeout and maximum output bounds.
        """
        import subprocess
        import sys

        logger.info("Executing in development emulation mode (DEV_MOCK_DOCKER=True): %s", execution_id)
        
        try:
            proc = subprocess.run(
                [sys.executable, "-B", host_script_path],
                capture_output=True,
                text=True,
                timeout=config.EXECUTION_TIMEOUT,
            )
            stdout = proc.stdout
            stderr = proc.stderr
            exit_code = proc.returncode

            stdout_trunc, trunc1 = truncate_output(stdout, config.MAX_OUTPUT_SIZE // 2)
            stderr_trunc, trunc2 = truncate_output(stderr, config.MAX_OUTPUT_SIZE // 2)
            is_truncated = trunc1 or trunc2

            return {
                "status": "completed",
                "exit_code": exit_code,
                "stdout": stdout_trunc,
                "stderr": stderr_trunc,
                "truncated": is_truncated,
            }
        except subprocess.TimeoutExpired as te:
            stdout_str = te.stdout.decode("utf-8", errors="replace") if isinstance(te.stdout, bytes) else str(te.stdout or "")
            stderr_str = te.stderr.decode("utf-8", errors="replace") if isinstance(te.stderr, bytes) else str(te.stderr or "")
            return {
                "status": "timeout",
                "error": f"Execution timed out after {config.EXECUTION_TIMEOUT} seconds.",
                "stdout": stdout_str,
                "stderr": stderr_str,
                "truncated": False,
            }
        except Exception as e:
            return {
                "status": "completed",
                "exit_code": 1,
                "stdout": "",
                "stderr": str(e),
                "truncated": False,
            }
