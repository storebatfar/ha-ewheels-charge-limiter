"""Work out which entities on a smart plug device we need.

Most power-monitoring plugs expose several power-ish sensors, so ambiguity is
an expected outcome rather than an error. The config flow falls through to
asking the user directly when this cannot decide.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er


@dataclass(frozen=True)
class PlugEntities:
    """What we managed to resolve from a plug device."""

    switch: str | None = None
    power: str | None = None
    energy: str | None = None
    switch_ambiguous: bool = False
    power_ambiguous: bool = False
    energy_ambiguous: bool = False

    @property
    def is_complete(self) -> bool:
        """A switch plus at least one meter - the hard requirement."""
        return self.switch is not None and (
            self.power is not None or self.energy is not None
        )


def _pick(candidates: list[str]) -> tuple[str | None, bool]:
    """Return the sole candidate, or None plus an ambiguity flag."""
    if len(candidates) == 1:
        return candidates[0], False
    return None, len(candidates) > 1


def resolve_plug_entities(hass: HomeAssistant, device_id: str) -> PlugEntities:
    """Resolve switch, power and energy entity ids for a plug device."""
    registry = er.async_get(hass)
    entries = er.async_entries_for_device(
        registry, device_id, include_disabled_entities=False
    )

    switches: list[str] = []
    powers: list[str] = []
    energies: list[str] = []

    for entry in entries:
        if entry.domain == Platform.SWITCH:
            switches.append(entry.entity_id)
        elif entry.domain == Platform.SENSOR:
            device_class = entry.device_class or entry.original_device_class
            if device_class == SensorDeviceClass.POWER:
                powers.append(entry.entity_id)
            elif device_class == SensorDeviceClass.ENERGY:
                energies.append(entry.entity_id)

    switch, switch_ambiguous = _pick(switches)
    power, power_ambiguous = _pick(powers)
    energy, energy_ambiguous = _pick(energies)

    return PlugEntities(
        switch=switch,
        power=power,
        energy=energy,
        switch_ambiguous=switch_ambiguous,
        power_ambiguous=power_ambiguous,
        energy_ambiguous=energy_ambiguous,
    )
