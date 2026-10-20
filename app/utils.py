"""
Utility functions for logging, ID generation, and output manipulation.
"""

import json
import logging
import secrets
import sys
from typing import Any, Dict, Optional, Tuple


def generate_execution_id() -> str:
    """Generate a cryptographically secure 16-byte hex execution ID."""
    return secrets.token_hex(16)


def truncate_output(text: str, max_bytes: int) -> Tuple[str, bool]:
    """
    Truncate text to max_bytes (UTF-8 encoded) and append a truncation note if needed.
    Returns (truncated_text, is_truncated).
    """
    if not text:
        return "", False

    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text, False

    # Truncate at byte limit, ensuring valid UTF-8
    truncated_bytes = encoded[:max_bytes]
    truncated_text = truncated_bytes.decode("utf-8", errors="ignore")
    truncated_text += "\n[Output truncated: maximum output limit exceeded]"
    return truncated_text, True


class StructuredJsonFormatter(logging.Formatter):
    """
    Structured JSON log formatter for structured audit logging.
    Ensures no source code or secrets are leaked into logs.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_obj: Dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include structured extra fields if present
        for key in (
            "execution_id",
            "status",
            "duration_ms",
            "exit_code",
            "container_name",
            "container_id",
            "truncated",
            "client_ip",
        ):
            if hasattr(record, key):
                log_obj[key] = getattr(record, key)

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            log_obj["exception"] = record.exc_text

        return json.dumps(log_obj)


def setup_structured_logging(level: int = logging.INFO) -> None:
    """Configure root logger to use structured JSON output."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredJsonFormatter())
    
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    # Clear existing handlers to prevent duplicate output
    root_logger.handlers = [handler]
