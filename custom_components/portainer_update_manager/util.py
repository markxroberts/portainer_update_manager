"""Utility helpers for Portainer Update Manager."""

from datetime import time


def parse_time(value: str) -> time:
    """Parse a Home Assistant time-selector value."""
    return time.fromisoformat(value)


def is_time_in_window(current: time, start: time, end: time) -> bool:
    """Return whether current is within a maintenance window.

    Equal start and end values represent an unrestricted 24-hour window.
    Windows that cross midnight are supported.
    """
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end
