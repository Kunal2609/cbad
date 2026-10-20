"""
Unit tests for security.py validation logic.
"""

import pytest
from app.config import config
from app.security import ValidationError, validate_execute_request


def test_validate_valid_code():
    code = 'print("Hello, world!")\nx = 10 + 20\n'
    payload = {"code": code}
    result = validate_execute_request(payload)
    assert result == code


def test_validate_non_dict_payload():
    with pytest.raises(ValidationError) as exc:
        validate_execute_request("print('hi')")
    assert "must be a valid JSON object" in exc.value.message


def test_validate_missing_code_key():
    with pytest.raises(ValidationError) as exc:
        validate_execute_request({"source": "print(1)"})
    assert "Missing required 'code' field" in exc.value.message


def test_validate_extra_keys_rejected():
    with pytest.raises(ValidationError) as exc:
        validate_execute_request({
            "code": "print(1)",
            "image": "ubuntu:latest",
            "privileged": True,
        })
    assert "Disallowed top-level keys" in exc.value.message


def test_validate_non_string_code():
    with pytest.raises(ValidationError) as exc:
        validate_execute_request({"code": 12345})
    assert "must be a string" in exc.value.message


def test_validate_empty_and_whitespace_code():
    with pytest.raises(ValidationError) as exc:
        validate_execute_request({"code": ""})
    assert "cannot be empty" in exc.value.message

    with pytest.raises(ValidationError) as exc:
        validate_execute_request({"code": "   \n\t  \n  "})
    assert "cannot be empty" in exc.value.message


def test_validate_nul_bytes_rejected():
    with pytest.raises(ValidationError) as exc:
        validate_execute_request({"code": "print('hello')\x00malicious"})
    assert "NUL bytes" in exc.value.message


def test_validate_oversized_code_bytes():
    oversized = "a = 1\n" * (config.MAX_CODE_SIZE // 6 + 10)
    with pytest.raises(ValidationError) as exc:
        validate_execute_request({"code": oversized})
    assert "maximum size" in exc.value.message


def test_validate_oversized_code_lines():
    too_many_lines = "\n" * (config.MAX_CODE_LINES + 5)
    too_many_lines_with_code = "x = 1" + too_many_lines
    with pytest.raises(ValidationError) as exc:
        validate_execute_request({"code": too_many_lines_with_code})
    assert "maximum allowed lines" in exc.value.message
