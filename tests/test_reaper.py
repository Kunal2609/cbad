"""
Unit tests for orphan container reaper (app/reaper.py).
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
import pytest

from app.docker_manager import DockerManager
from app.reaper import (
    parse_docker_timestamp,
    run_startup_reaper,
    run_periodic_reaper_cycle,
)


def test_parse_docker_timestamp():
    ts1 = "2026-08-30T10:15:30.123456789Z"
    dt1 = parse_docker_timestamp(ts1)
    assert dt1 is not None
    assert dt1.year == 2026
    assert dt1.month == 8
    assert dt1.day == 30
    assert dt1.hour == 10

    ts2 = "2026-08-30T10:15:30Z"
    dt2 = parse_docker_timestamp(ts2)
    assert dt2 is not None
    assert dt2.second == 30

    assert parse_docker_timestamp("") is None
    assert parse_docker_timestamp("invalid-date") is None


def test_startup_reaper_cleans_orphan_containers():
    mock_container_1 = MagicMock()
    mock_container_1.id = "container-1"
    mock_container_1.name = "sandbox-exec-1"

    mock_container_2 = MagicMock()
    mock_container_2.id = "container-2"
    mock_container_2.name = "sandbox-exec-2"

    mock_mgr = MagicMock(spec=DockerManager)
    mock_mgr.list_sandbox_containers.return_value = [mock_container_1, mock_container_2]

    reaped = run_startup_reaper(docker_manager=mock_mgr)
    assert reaped == 2
    mock_mgr.remove_container.assert_any_call(mock_container_1)
    mock_mgr.remove_container.assert_any_call(mock_container_2)


def test_periodic_reaper_removes_only_stale_containers():
    now = datetime.now(timezone.utc)
    
    # Container 1: created 2 seconds ago (active, fresh)
    fresh_time = (now - timedelta(seconds=2)).isoformat().replace("+00:00", "Z")
    mock_fresh = MagicMock()
    mock_fresh.id = "fresh-1"
    mock_fresh.attrs = {"Created": fresh_time, "State": {"Status": "running"}}

    # Container 2: created 60 seconds ago (stale)
    stale_time = (now - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    mock_stale = MagicMock()
    mock_stale.id = "stale-1"
    mock_stale.attrs = {"Created": stale_time, "State": {"Status": "running"}}

    mock_mgr = MagicMock(spec=DockerManager)
    mock_mgr.list_sandbox_containers.return_value = [mock_fresh, mock_stale]

    reaped = run_periodic_reaper_cycle(docker_manager=mock_mgr, max_age_seconds=10)
    assert reaped == 1
    mock_mgr.remove_container.assert_called_once_with(mock_stale)
