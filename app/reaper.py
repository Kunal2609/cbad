"""
Reaper module for orphan container cleanup.
Provides startup reaper (cleanup left-overs from crash) and periodic background reaper.
Strictly scoped to containers labeled with 'sandbox=true'.
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from app.config import config
from app.docker_manager import DockerManager

logger = logging.getLogger(__name__)


def parse_docker_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse Docker's ISO timestamp string into a timezone-aware datetime."""
    if not ts_str:
        return None
    try:
        # Docker format: 2026-08-30T10:00:00.123456789Z
        # Normalize nanoseconds to microseconds for Python ISO parsing
        if "." in ts_str:
            base, frac = ts_str.split(".", 1)
            frac_clean = frac.rstrip("Z")[:6]
            ts_normalized = f"{base}.{frac_clean}+00:00"
        else:
            ts_normalized = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_normalized)
    except Exception as e:
        logger.warning("Could not parse Docker timestamp '%s': %s", ts_str, str(e))
        return None


def run_startup_reaper(docker_manager: Optional[DockerManager] = None) -> int:
    """
    Run once on Flask application boot.
    Removes any leftover containers labeled sandbox=true from previous crashes.
    Returns the number of orphan containers removed.
    """
    mgr = docker_manager or DockerManager()
    reaped_count = 0
    try:
        containers = mgr.list_sandbox_containers()
        for container in containers:
            try:
                c_id = getattr(container, "id", "unknown")[:12]
                c_name = getattr(container, "name", "unknown")
                logger.warning("Startup reaper cleaning orphan container %s (%s)", c_id, c_name)
                mgr.remove_container(container)
                reaped_count += 1
            except Exception as e:
                logger.error("Startup reaper failed to remove container: %s", str(e))
    except Exception as e:
        logger.warning("Startup reaper could not query Docker containers: %s", str(e))

    if reaped_count > 0:
        logger.info("Startup reaper successfully reaped %d orphan containers.", reaped_count)
    return reaped_count


def run_periodic_reaper_cycle(
    docker_manager: Optional[DockerManager] = None,
    max_age_seconds: Optional[int] = None,
) -> int:
    """
    Check all containers with label sandbox=true and remove any whose age exceeds
    EXECUTION_TIMEOUT + TIMEOUT_GRACE_PERIOD + buffer.
    Returns the number of stale containers removed.
    """
    mgr = docker_manager or DockerManager()
    if max_age_seconds is None:
        # Buffer of 5 seconds beyond timeout + grace period
        max_age_seconds = config.EXECUTION_TIMEOUT + config.TIMEOUT_GRACE_PERIOD + 5

    now = datetime.now(timezone.utc)
    reaped_count = 0

    try:
        containers = mgr.list_sandbox_containers()
        for container in containers:
            try:
                # Reload container to get current attrs
                if hasattr(container, "reload"):
                    container.reload()
                
                attrs = getattr(container, "attrs", {})
                created_str = attrs.get("Created")
                created_dt = parse_docker_timestamp(created_str) if created_str else None

                should_reap = False
                if created_dt:
                    age_seconds = (now - created_dt).total_seconds()
                    if age_seconds > max_age_seconds:
                        should_reap = True
                else:
                    # If timestamp cannot be parsed, check status
                    state = attrs.get("State", {})
                    if state.get("Status") in ("exited", "dead"):
                        should_reap = True

                if should_reap:
                    c_id = getattr(container, "id", "unknown")[:12]
                    logger.warning("Periodic reaper removing stale container %s", c_id)
                    mgr.remove_container(container)
                    reaped_count += 1

            except Exception as item_err:
                logger.warning("Periodic reaper error on container: %s", str(item_err))

    except Exception as list_err:
        logger.warning("Periodic reaper query error: %s", str(list_err))

    return reaped_count


class BackgroundReaper(threading.Thread):
    """Background daemon thread running the periodic reaper cycle."""

    def __init__(
        self,
        docker_manager: Optional[DockerManager] = None,
        interval_seconds: Optional[int] = None,
    ) -> None:
        super().__init__(daemon=True, name="SandboxPeriodicReaper")
        self.docker_manager = docker_manager or DockerManager()
        self.interval_seconds = interval_seconds or config.REAPER_INTERVAL_SECONDS
        self._stop_event = threading.Event()

    def run(self) -> None:
        logger.info("Periodic orphan container reaper thread started (interval=%ds)", self.interval_seconds)
        while not self._stop_event.is_set():
            try:
                run_periodic_reaper_cycle(self.docker_manager)
            except Exception as e:
                logger.error("Exception in periodic reaper cycle: %s", str(e))
            self._stop_event.wait(self.interval_seconds)

    def stop(self) -> None:
        """Signal the reaper thread to stop."""
        self._stop_event.set()


_reaper_thread: Optional[BackgroundReaper] = None


def start_background_reaper(docker_manager: Optional[DockerManager] = None) -> BackgroundReaper:
    """Start background reaper thread if not already running."""
    global _reaper_thread
    if _reaper_thread is None or not _reaper_thread.is_alive():
        _reaper_thread = BackgroundReaper(docker_manager=docker_manager)
        _reaper_thread.start()
    return _reaper_thread
