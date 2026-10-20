"""
Flask routes module.
Thin views handling HTTP request validation, rate limiting, and response shaping.
Contains NO direct Docker SDK calls.
"""

import logging
import threading
from typing import Optional

from flask import Blueprint, jsonify, render_template, request, current_app

from app.config import config
from app.executor import SandboxExecutor
from app.security import ValidationError, validate_execute_request

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__)
main_bp = Blueprint("main", __name__)

# Concurrency semaphore bounding simultaneous sandbox executions
_concurrency_semaphore = threading.BoundedSemaphore(value=config.MAX_CONCURRENT_EXECUTIONS)
_executor_instance: Optional[SandboxExecutor] = None


def get_executor() -> SandboxExecutor:
    """Retrieve or create the SandboxExecutor singleton."""
    global _executor_instance
    if _executor_instance is None:
        _executor_instance = SandboxExecutor()
    return _executor_instance


def set_executor(executor: Optional[SandboxExecutor]) -> None:
    """Allow injecting a mock executor for testing."""
    global _executor_instance
    _executor_instance = executor


@main_bp.route("/")
def index():
    """Render the dark modern IDE web interface."""
    return render_template("index.html")


@api_bp.route("/execute", methods=["POST"])
def execute_code():
    """
    POST /api/execute
    Accepts JSON with 'code' and runs in an ephemeral, isolated Docker container.
    """
    # 1. Validate JSON payload structure
    if not request.is_json:
        return (
            jsonify({
                "success": False,
                "status": "invalid_request",
                "error": "Request body must be valid JSON with Content-Type: application/json.",
            }),
            400,
        )

    try:
        data = request.get_json(silent=False)
    except Exception:
        return (
            jsonify({
                "success": False,
                "status": "invalid_request",
                "error": "Malformed JSON payload in request body.",
            }),
            400,
        )

    # 2. Strict request validation (size, type, UTF-8, no NUL bytes, no extra keys)
    try:
        code = validate_execute_request(data)
    except ValidationError as val_err:
        return (
            jsonify({
                "success": False,
                "status": val_err.status,
                "error": val_err.message,
            }),
            400,
        )

    # 3. Concurrency check (bounded semaphore)
    acquired = _concurrency_semaphore.acquire(blocking=False)
    if not acquired:
        logger.warning(
            "Execution request rejected: Sandbox is at capacity (%d concurrent)",
            config.MAX_CONCURRENT_EXECUTIONS,
        )
        return (
            jsonify({
                "success": False,
                "status": "at_capacity",
                "error": "Sandbox is at capacity. Try again shortly.",
            }),
            503,
        )

    try:
        # 4. Check Docker daemon availability
        executor = get_executor()
        is_healthy, reason = True, None
        if hasattr(executor, "docker_manager") and hasattr(executor.docker_manager, "ping_daemon"):
            ping_res = executor.docker_manager.ping_daemon()
            if isinstance(ping_res, tuple) and len(ping_res) == 2:
                is_healthy, reason = ping_res

        if not is_healthy:
            if reason == "image_missing":
                err_msg = f"Execution image '{config.EXECUTION_IMAGE}' not found. Please build it first."
            else:
                err_msg = "Docker daemon is unreachable. Please verify Docker Desktop or Docker Engine is running."
            return (
                jsonify({
                    "success": False,
                    "status": "internal_error",
                    "error": err_msg,
                }),
                500,
            )

        # 5. Execute user code
        result = executor.execute(code)
        
        # Log structured execution result
        logger.info(
            "Execution %s finished with status %s in %d ms",
            result.get("execution_id"),
            result.get("status"),
            result.get("execution_time_ms", 0),
            extra={
                "execution_id": result.get("execution_id"),
                "status": result.get("status"),
                "duration_ms": result.get("execution_time_ms"),
                "exit_code": result.get("exit_code"),
                "truncated": result.get("truncated"),
                "client_ip": request.remote_addr,
            },
        )
        
        return jsonify(result), 200

    except Exception as e:
        logger.error(
            "Internal error executing sandbox request: %s",
            str(e),
            exc_info=True,
            extra={"client_ip": request.remote_addr},
        )
        return (
            jsonify({
                "success": False,
                "status": "internal_error",
                "error": "Execution could not be completed.",
            }),
            500,
        )
    finally:
        _concurrency_semaphore.release()


@api_bp.route("/health", methods=["GET"])
def health_check():
    """
    GET /api/health
    Pings Docker daemon and checks image presence. Returns 200 if healthy, 503 if degraded.
    """
    executor = get_executor()
    is_healthy, reason = executor.docker_manager.ping_daemon()
    
    if not is_healthy:
        return (
            jsonify({
                "status": "degraded",
                "reason": reason or "docker_unreachable",
            }),
            503,
        )

    runtime = executor.docker_manager.detect_runtime() or "runc (default)"
    return (
        jsonify({
            "status": "healthy",
            "runtime": runtime,
            "image": config.EXECUTION_IMAGE,
            "max_concurrent": config.MAX_CONCURRENT_EXECUTIONS,
            "timeout_sec": config.EXECUTION_TIMEOUT,
        }),
        200,
    )
