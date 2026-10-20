"""
SandboxExecutor module.
Orchestrates end-to-end sandbox execution: staging, Docker execution, timing, and cleanup.
"""

import logging
import os
import shutil
import stat
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import config
from app.docker_manager import DockerManager
from app.utils import generate_execution_id

logger = logging.getLogger(__name__)


def _remove_readonly(func, path, excinfo):
    """Error handler for shutil.rmtree on read-only files (especially on Windows)."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


class SandboxExecutor:
    """Orchestrates ephemeral staging and Docker container execution."""

    def __init__(self, docker_manager: Optional[DockerManager] = None) -> None:
        self.docker_manager = docker_manager or DockerManager()

    def execute(self, code: str) -> Dict[str, Any]:
        """
        Execute user-submitted Python code in an isolated Docker container.
        Ensures staging directory and container cleanup in finally block.
        """
        execution_id = generate_execution_id()
        staging_dir = Path(config.STAGING_ROOT) / execution_id
        script_path = staging_dir / "script.py"

        start_time = time.perf_counter()
        
        try:
            # 1. Create staging directory
            staging_dir.mkdir(parents=True, exist_ok=True)

            # 2. Write script.py with read-only permissions (0o444)
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)
            
            try:
                # Set permissions to read-only
                os.chmod(script_path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            except Exception as e:
                logger.warning("Could not set 0o444 permissions on %s: %s", script_path, str(e))

            # 3. Delegate container execution to DockerManager
            res = self.docker_manager.create_and_run_sandbox(
                execution_id=execution_id,
                host_script_path=str(script_path.resolve()),
            )

            execution_time_ms = max(0, int((time.perf_counter() - start_time) * 1000))

            if res.get("status") == "timeout":
                return {
                    "success": False,
                    "execution_id": execution_id,
                    "status": "timeout",
                    "error": res.get("error", f"Execution timed out after {config.EXECUTION_TIMEOUT} seconds."),
                    "stdout": res.get("stdout", ""),
                    "stderr": res.get("stderr", ""),
                    "execution_time_ms": execution_time_ms,
                    "truncated": res.get("truncated", False),
                }

            exit_code = res.get("exit_code", 0)
            return {
                "success": True,
                "execution_id": execution_id,
                "status": "completed",
                "stdout": res.get("stdout", ""),
                "stderr": res.get("stderr", ""),
                "exit_code": exit_code,
                "execution_time_ms": execution_time_ms,
                "truncated": res.get("truncated", False),
            }

        except Exception as e:
            execution_time_ms = max(0, int((time.perf_counter() - start_time) * 1000))
            logger.error(
                "Execution %s failed with exception: %s",
                execution_id,
                str(e),
                exc_info=True,
                extra={"execution_id": execution_id},
            )
            raise
        finally:
            # Guaranteed cleanup of the staging directory
            if staging_dir.exists():
                try:
                    # Restore write permission on script file so rmtree can delete it on Windows
                    if script_path.exists():
                        try:
                            os.chmod(script_path, stat.S_IWRITE | stat.S_IREAD)
                        except Exception:
                            pass
                    shutil.rmtree(str(staging_dir), onerror=_remove_readonly)
                except Exception as cleanup_err:
                    logger.warning(
                        "Failed to clean up staging directory %s: %s",
                        staging_dir,
                        str(cleanup_err),
                    )
