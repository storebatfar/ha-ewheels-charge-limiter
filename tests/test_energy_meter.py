"""Per-session watt-hour accounting."""

from __future__ import annotations

import pytest

from custom_components.ewheels_charge_limiter.energy_meter import EnergyMeter


def test_energy_sensor_path_counts_the_difference_from_baseline():
    meter = EnergyMeter()
    meter.start(energy_total_wh=1000.0)
    meter.add_energy_reading(1150.0)
    assert meter.delivered_wh == pytest.approx(150.0)


def test_energy_sensor_path_survives_a_meter_reset():
    """total_increasing sensors reset to 0; that must not yield a negative."""
    meter = EnergyMeter()
    meter.start(energy_total_wh=1000.0)
    meter.add_energy_reading(1100.0)
    meter.add_energy_reading(20.0)  # reset, then 20 Wh delivered
    assert meter.delivered_wh == pytest.approx(120.0)
    meter.add_energy_reading(30.0)
    assert meter.delivered_wh == pytest.approx(130.0)


def test_power_path_integrates_trapezoidally():
    meter = EnergyMeter()
    meter.start(energy_total_wh=None)
    # 100 W held for 3600 s => 100 Wh
    meter.add_power_reading(100.0, timestamp=0.0)
    meter.add_power_reading(100.0, timestamp=3600.0)
    assert meter.delivered_wh == pytest.approx(100.0)


def test_power_path_averages_across_a_ramp():
    meter = EnergyMeter()
    meter.start(energy_total_wh=None)
    # 0 W -> 200 W over 3600 s, trapezoid => 100 Wh
    meter.add_power_reading(0.0, timestamp=0.0)
    meter.add_power_reading(200.0, timestamp=3600.0)
    assert meter.delivered_wh == pytest.approx(100.0)


def test_power_path_ignores_a_backwards_timestamp():
    meter = EnergyMeter()
    meter.start(energy_total_wh=None)
    meter.add_power_reading(100.0, timestamp=1000.0)
    meter.add_power_reading(100.0, timestamp=500.0)
    assert meter.delivered_wh == pytest.approx(0.0)


def test_both_paths_agree_on_identical_input():
    energy = EnergyMeter()
    energy.start(energy_total_wh=0.0)
    energy.add_energy_reading(100.0)

    power = EnergyMeter()
    power.start(energy_total_wh=None)
    power.add_power_reading(100.0, timestamp=0.0)
    power.add_power_reading(100.0, timestamp=3600.0)

    assert energy.delivered_wh == pytest.approx(power.delivered_wh)


def test_round_trips_through_a_dict():
    meter = EnergyMeter()
    meter.start(energy_total_wh=1000.0)
    meter.add_energy_reading(1150.0)

    restored = EnergyMeter.from_dict(meter.as_dict())
    restored.add_energy_reading(1200.0)
    assert restored.delivered_wh == pytest.approx(200.0)
