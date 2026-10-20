"""
Security and request validation module.
Enforces strict server-side validation on user-submitted requests.
"""

import logging
from typing import Any, Dict, Optional, Tuple

from app.config import config

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Custom exception raised when request validation fails."""

    def __init__(self, message: str, status: str = "invalid_request") -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def validate_execute_request(data: Any) -> str:
    """
    Validate the incoming POST /api/execute request payload.
    
    Rules:
    - Must be a JSON dict.
    - Must contain only the 'code' key (disallow extra keys to prevent parameter injection).
    - 'code' must be a non-empty string with non-whitespace content.
    - 'code' must not contain NUL bytes.
    - 'code' UTF-8 byte length must not exceed MAX_CODE_SIZE.
    - 'code' line count must not exceed MAX_CODE_LINES.
    
    Returns the sanitized code string if valid, otherwise raises ValidationError.
    """
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a valid JSON object.")

    if "code" not in data:
        raise ValidationError("Missing required 'code' field.")

    # Reject unexpected keys
    allowed_keys = {"code"}
    extra_keys = set(data.keys()) - allowed_keys
    if extra_keys:
        raise ValidationError(f"Disallowed top-level keys in request: {', '.join(sorted(extra_keys))}.")

    code = data.get("code")
    if not isinstance(code, str):
        raise ValidationError("'code' field must be a string.")

    if not code.strip():
        raise ValidationError("'code' cannot be empty or whitespace-only.")

    # Check for NUL bytes
    if "\x00" in code or "\0" in code:
        raise ValidationError("Source code cannot contain NUL bytes.")

    # Check UTF-8 byte length
    try:
        encoded_bytes = code.encode("utf-8")
    except UnicodeEncodeError as e:
        raise ValidationError(f"Invalid UTF-8 character encoding: {str(e)}")

    byte_len = len(encoded_bytes)
    if byte_len > config.MAX_CODE_SIZE:
        raise ValidationError(
            f"Code exceeds maximum size of {config.MAX_CODE_SIZE} bytes (received {byte_len} bytes)."
        )

    # Check line count
    line_count = len(code.splitlines())
    if line_count > config.MAX_CODE_LINES:
        raise ValidationError(
            f"Code exceeds maximum allowed lines of {config.MAX_CODE_LINES} (received {line_count} lines)."
        )

    return code
