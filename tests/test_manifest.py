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


STRINGS = Path("custom_components/ewheels_charge_limiter/strings.json")
EN = Path("custom_components/ewheels_charge_limiter/translations/en.json")


def test_english_translation_is_the_strings_file():
    assert json.loads(EN.read_text()) == json.loads(STRINGS.read_text())


def test_every_form_field_has_help_text():
    """Each field on every screen explains itself in the integration view."""
    strings = json.loads(STRINGS.read_text())
    for flow in ("config", "options"):
        for step_id, step in strings[flow]["step"].items():
            fields = set(step.get("data", {}))
            described = set(step.get("data_description", {}))
            assert fields <= described, f"{flow}.{step_id}: {fields - described}"


def test_every_screen_has_a_description():
    strings = json.loads(STRINGS.read_text())
    for flow in ("config", "options"):
        for step_id, step in strings[flow]["step"].items():
            assert step.get("description"), f"{flow}.{step_id} has no description"
