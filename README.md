# E-Wheels Charge Limiter

A Home Assistant integration that stops charging at a target state of charge by
cutting mains at a power-monitoring smart plug.

Built for an E-wheels E2S V3 GT Pro, but nothing about it is scooter-specific:
it needs a state-of-charge sensor, a switchable plug that measures power, a
capacity in watt-hours, and a target. An e-bike, a power station or a tool
battery would work equally well.

> Not affiliated with, endorsed by, or supported by E-Wheels Norge AS.

## Hardware requirements

Read this before buying anything. Your smart plug **must** expose:

- a `switch` entity — without it there is nothing to cut, and
- a power sensor **or** an energy sensor (both is better).

Switch-only plugs and non-switchable energy monitors will not work, and the
config flow refuses them rather than creating a half-working entry.

You also need a state-of-charge sensor for the battery, with
`device_class: battery`.

## How it works, and its main limitation

The integration is **open-loop**. It reads the state of charge *before* a
session, works out how many watt-hours are needed to reach the target, counts
them at the plug, and cuts power when they have been delivered.

It works this way because the scooter it was written for drops its Bluetooth
connection the moment the charger is inserted, so there is no live state of
charge to watch. If your device keeps reporting while charging, this still
works — it just does not take advantage of it yet.

The practical consequence: **accuracy depends on a recent state-of-charge
reading.** Power the device on before you plug it in and you will land close to
the target.

## Self-calibration

Rather than assuming a charger efficiency, the integration learns **wall
watt-hours per percent of charge** from completed sessions. That single number
absorbs both charger losses and any non-linearity in how the device reports
percent, which on cheap BMS firmware is usually the larger error.

It is seeded from the configured capacity and corrected after each session by
comparing the watt-hours delivered against the actual change in state of
charge. Expect it to converge within two or three charges, and to keep tracking
as the pack ages. The current value is exposed as a sensor, and can be reset
from the options if it ever goes wrong.

## Fail toward charged

If something goes wrong, the battery's own BMS terminates the charge when full.
The worst outcome of a failure here is a 100% charge — a lost longevity
benefit, not a hazard. A flat battery is the outcome actually worth avoiding.

Every ambiguous case therefore resolves toward delivering more energy:

- A **stale** state-of-charge reading disables the limit for that session
  rather than guessing. A stale reading usually means the device has been used
  since, so the real charge is *lower*; applying the limit anyway would cut
  early and leave you short. You may occasionally get a 100% charge as a
  result.
- Losing the state-of-charge sensor mid-session does not abort — the value is
  only needed at the start and is already recorded.

There is exactly one deliberate exception. If **every** meter becomes
unavailable mid-session, the count can never advance and the plug would stay
live indefinitely, so the integration cuts power and reports `stalled`.

## The manual plug switch

The `Plug` switch is an override, and it is authoritative. Turning it **on**
starts a session regardless of the current charge — it deliberately bypasses
the target, because "turn the plug on" should mean exactly that. Turning it
**off** ends any open session.

Changes made anywhere else — the plug's own entity, its physical button, the
vendor app — are detected and treated identically.

## Entities

| Entity | Purpose |
|---|---|
| `number` Target charge | Target state of charge |
| `switch` Enabled | Master enable; when off, the plug is left alone entirely |
| `switch` Plug | Manual override, mirrors the real plug |
| `sensor` Status | `idle`, `armed`, `charging`, `complete`, `stopped`, `uncalibrated`, `stalled` |
| `sensor` Session energy | Watt-hours delivered this session |
| `sensor` Projected charge | Estimated charge right now |
| `sensor` Wh per percent | The learned calibration |

## Installation

HACS → three-dot menu → **Custom repositories** → add this repository with
category **Integration**. Install, restart Home Assistant, then
**Settings → Devices & Services → Add Integration → E-Wheels Charge Limiter**.

Setup is a single screen asking for the plug's switch entity, its power and/or
energy sensor, the battery's state-of-charge sensor, and the capacity in
watt-hours. You name the entities outright rather than picking a device: it is
one screen either way, and nothing is inferred behind your back about which
switch gets to cut mains.

## Options

| Option | Default |
|---|---|
| Target state of charge | 80% |
| Re-arm hysteresis | 5% |
| Charging power threshold | 5 W |
| Close session after idle | 10 min |
| Maximum session length | 8 h |
| Treat a reading as stale after | 12 h |
| Learned Wh per percent | learned; blank keeps learning |

## Not included

Price-based scheduling and solar-surplus charging are out of scope. For a
720 Wh pack a 30→80% charge moves about 0.4 kWh at the wall, so even perfect
price timing saves a fraction of a currency unit per charge. The longevity
limit is the point of this, not the tariff.

## Licence

MIT.

The icon in `custom_components/ewheels_charge_limiter/brand/` is a third-party
asset supplied by the repository owner and is not covered by the MIT licence
above. If it came from a stock library such as Flaticon, attribution is
probably required — replace this paragraph with the correct credit.
