"""Manifest sanity checks."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.ewheels_charge_limiter.const import DOMAIN

MANIFEST = Path("custom_components/ewheels_charge_limiter/manifest.json")


def test_manifest_domain_matches_const():
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["domain"] == DOMAIN


def test_manifest_declares_config_flow():
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["config_flow"] is True


def test_hacs_json_has_no_filename_key():
    """filename is for frontend repos; an integration repo must not set it."""
    hacs = json.loads(Path("hacs.json").read_text())
    assert "filename" not in hacs
    # The floor is what we actually test against: the newest
    # pytest-homeassistant-custom-component pins HA 2026.2.3.
    assert hacs["homeassistant"] == "2026.2.0"
