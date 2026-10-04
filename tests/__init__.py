"""Shared test helpers."""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(items):
    """Fail fast on tests that are accidentally marked only."""
    for item in items:
        if "security" in item.keywords and item.get_closest_marker("integration"):
            pytest.fail(f"{item.nodeid} is marked both integration and security")