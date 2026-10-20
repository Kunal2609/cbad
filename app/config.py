"""
Configuration module for Python Code Execution Sandbox.
Centralized, typed configuration with environment variable loading via python-dotenv.
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env if present
load_dotenv()

logger = logging.getLogger(__name__)


def _get_staging_root() -> str:
    """
    Determine the staging root directory.
    Prefers /dev/shm/sandbox for tmpfs RAM-backed isolation.
    Falls back to ./sandbox if /dev/shm is unavailable.
    """
    configured_root = os.getenv("STAGING_ROOT", "/dev/shm/sandbox")
    
    # Check if tmpfs /dev/shm path is requested and actually available/writable
    if configured_root.startswith("/dev/shm"):
        dev_shm = Path("/dev/shm")
        if dev_shm.exists() and os.access(str(dev_shm), os.W_OK):
            return configured_root
        else:
            fallback = str(Path(__file__).resolve().parent.parent / "sandbox")
            logger.warning(
                "Requested STAGING_ROOT '%s' is not available on this host. Falling back to '%s'",
                configured_root,
                fallback,
            )
            return fallback

    return configured_root


class Config:
    """Central configuration class."""
    
    # Hard wall-clock limit per execution in seconds
    EXECUTION_TIMEOUT: int = int(os.getenv("EXECUTION_TIMEOUT", "3"))
    
    # Extra time allowed in seconds for kill/remove before API gives up
    TIMEOUT_GRACE_PERIOD: int = int(os.getenv("TIMEOUT_GRACE_PERIOD", "2"))
    
    # Docker memory limit
    MEMORY_LIMIT: str = os.getenv("MEMORY_LIMIT", "50m")
    
    # Docker memory+swap limit (MUST equal MEMORY_LIMIT to disable swap)
    MEMSWAP_LIMIT: str = os.getenv("MEMSWAP_LIMIT", "50m")
    
    # Docker CPU limit in nano_cpus (500000000 = 0.5 vCPU)
    CPU_NANO: int = int(os.getenv("CPU_NANO", "500000000"))
    
    # Maximum processes/threads inside container
    PIDS_LIMIT: int = int(os.getenv("PIDS_LIMIT", "64"))
    
    # Size cap for container writable /tmp tmpfs mount
    TMPFS_SIZE: str = os.getenv("TMPFS_SIZE", "16m")
    
    # Maximum accepted source code size in bytes (UTF-8 encoded)
    MAX_CODE_SIZE: int = int(os.getenv("MAX_CODE_SIZE", "32768"))
    
    # Maximum accepted source code line count
    MAX_CODE_LINES: int = int(os.getenv("MAX_CODE_LINES", "1000"))
    
    # Combined stdout+stderr output cap in bytes before truncation
    MAX_OUTPUT_SIZE: int = int(os.getenv("MAX_OUTPUT_SIZE", "65536"))
    
    # Maximum concurrent executions allowed before returning 503
    MAX_CONCURRENT_EXECUTIONS: int = int(os.getenv("MAX_CONCURRENT_EXECUTIONS", "10"))
    
    # Per-IP rate limit enforced via Flask-Limiter
    RATE_LIMIT: str = os.getenv("RATE_LIMIT", "20/minute")
    
    # Storage URI for rate limiter (memory:// for dev, redis://... for production)
    RATE_LIMIT_STORAGE_URI: str = os.getenv("RATE_LIMIT_STORAGE_URI", "memory://")
    
    # Tag of the hardened execution sandbox Docker image
    EXECUTION_IMAGE: str = os.getenv("EXECUTION_IMAGE", "python-sandbox-exec:latest")
    
    # Staging root directory for per-execution script.py files
    STAGING_ROOT: str = _get_staging_root()
    
    # Client-level HTTP timeout for Docker SDK calls in seconds
    DOCKER_TIMEOUT: int = int(os.getenv("DOCKER_TIMEOUT", "10"))
    
    # Optional development mock mode for local testing when Docker Desktop is unavailable
    DEV_MOCK_DOCKER: bool = os.getenv("DEV_MOCK_DOCKER", "false").lower() in ("true", "1", "yes")
    
    # Periodic reaper interval in seconds
    REAPER_INTERVAL_SECONDS: int = int(os.getenv("REAPER_INTERVAL_SECONDS", "10"))
    
    # Flask secret key
    SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "dev-insecure-secret-key-change-in-prod")
    
    # Maximum request payload size in bytes (WSGI layer protection)
    MAX_CONTENT_LENGTH: int = MAX_CODE_SIZE + 4096


config = Config()
