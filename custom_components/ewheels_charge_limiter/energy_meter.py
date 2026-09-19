"""Accumulates the watt-hours delivered during one charging session.

Prefers a cumulative energy sensor when the plug has one, because it is exact.
Falls back to integrating a power sensor over time, which is accurate enough
given plugs report every few seconds and a charge runs for hours.
"""

from __future__ import annotations

from typing import Any


class EnergyMeter:
    """Counts watt-hours for a single session. All values are in Wh."""

    def __init__(self) -> None:
        self.delivered_wh: float = 0.0
        # Watt-hours from segments that ended at a meter reset. The live
        # segment is measured from _energy_baseline and added on top.
        self._banked_wh: float = 0.0
        self._energy_baseline: float | None = None
        self._last_energy_total: float | None = None
        self._last_power_w: float | None = None
        self._last_timestamp: float | None = None

    def start(self, energy_total_wh: float | None) -> None:
        """Open a session. Pass the energy sensor's current total, or None."""
        self.delivered_wh = 0.0
        self._banked_wh = 0.0
        self._energy_baseline = energy_total_wh
        self._last_energy_total = energy_total_wh
        self._last_power_w = None
        self._last_timestamp = None

    def add_energy_reading(self, energy_total_wh: float) -> None:
        """Feed a cumulative energy reading."""
        if self._energy_baseline is None or self._last_energy_total is None:
            self._energy_baseline = energy_total_wh
            self._last_energy_total = energy_total_wh
            return

        if energy_total_wh < self._last_energy_total:
            # A total_increasing sensor restarted from zero. Bank the segment
            # that just ended and measure the new one from zero. Without this
            # the session would silently lose everything delivered before the
            # reset and undercharge.
            self._banked_wh += self._last_energy_total - self._energy_baseline
            self._energy_baseline = 0.0

        self._last_energy_total = energy_total_wh
        self.delivered_wh = self._banked_wh + (energy_total_wh - self._energy_baseline)

    def add_power_reading(self, power_w: float, timestamp: float) -> None:
        """Feed an instantaneous power reading, integrating trapezoidally."""
        if self._last_power_w is not None and self._last_timestamp is not None:
            elapsed = timestamp - self._last_timestamp
            if elapsed > 0:
                average_w = (self._last_power_w + power_w) / 2.0
                self.delivered_wh += average_w * elapsed / 3600.0

        self._last_power_w = power_w
        self._last_timestamp = timestamp

    def as_dict(self) -> dict[str, Any]:
        """Serialise for persistence across a restart."""
        return {
            "delivered_wh": self.delivered_wh,
            "banked_wh": self._banked_wh,
            "energy_baseline": self._energy_baseline,
            "last_energy_total": self._last_energy_total,
            "last_power_w": self._last_power_w,
            "last_timestamp": self._last_timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnergyMeter:
        """Restore a session that was in flight when Home Assistant stopped."""
        meter = cls()
        meter.delivered_wh = data.get("delivered_wh", 0.0)
        meter._banked_wh = data.get("banked_wh", 0.0)
        meter._energy_baseline = data.get("energy_baseline")
        meter._last_energy_total = data.get("last_energy_total")
        meter._last_power_w = data.get("last_power_w")
        meter._last_timestamp = data.get("last_timestamp")
        return meter
