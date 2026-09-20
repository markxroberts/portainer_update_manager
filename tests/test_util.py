"""Tests for maintenance window helpers."""

from datetime import time
import importlib.util
from pathlib import Path

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "portainer_update_manager"
    / "util.py"
)
spec = importlib.util.spec_from_file_location("pum_util", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def test_all_day_window() -> None:
    assert module.is_time_in_window(time(12), time(0), time(0))


def test_day_window() -> None:
    assert module.is_time_in_window(time(12), time(9), time(17))
    assert not module.is_time_in_window(time(18), time(9), time(17))


def test_overnight_window() -> None:
    assert module.is_time_in_window(time(23), time(22), time(5))
    assert module.is_time_in_window(time(4), time(22), time(5))
    assert not module.is_time_in_window(time(12), time(22), time(5))
