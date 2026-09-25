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

Changing the target mid-charge works: the requirement is re-measured against
the new target straight away, and if more has already been delivered than the
new target calls for, the plug is cut on the spot.

## Self-calibration: the vase

A pack's reported percent is not linear in energy. On the scooter this was
written for, a point near the top costs about three times the wall energy of
one in the middle. Picture a vase that is narrow at the bottom and wide at the
top: the same cup of water raises the level a lot low down and very little near
the brim.

So instead of one Wh-per-percent, the integration keeps **ten 10-point bands**,
each with its own wall watt-hours per reported point. The energy needed for a
charge is the sum across the bands it passes through, which is why it lands on
target whether it starts at 30 % or 75 %.

Each band starts from a **starting point**: a value you typed for it under
*Vase values*, or otherwise the default seeded from the configured capacity.
After every completed charge, the integration remembers where it started, where
it settled and how much energy it took, keeping the **last ten charges**. It
then refits all ten bands to explain them together, gently pulled toward the
starting points and toward neighbouring bands. Charges from different start
points are what reveal the vase's shape.

It only learns from a reading it can trust:

- **Real news only.** A value replayed after a reconnect or a restart is ignored.
- **Settled.** The pack reads high straight after charging, so readings sooner
  than *Wait before learning* (30 minutes by default) are shown but not learned
  from.
- **Enough signal.** A charge must rise at least 10 points.
- **Not used since.** A reading more than 15 points below where the charge
  should have ended, or taken after *Treat a reading as stale after* (12 hours
  by default), means the scooter has been ridden since. That charge is
  abandoned rather than learned wrongly.

A reading that is too soon or too small a rise leaves the charge waiting for a
better one, and re-reading the same value later counts. A new charge starting throws away any charge still waiting.

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

## Starting a charge is yours; ending one is the integration's

**The integration never switches the plug on.** Its authority runs one way: it
cuts power at the target, at the session cap, and if every meter dies. Closing
the relay is always a deliberate act by you.

So `armed` means "watching, ready to limit the next charge" — not "powered up
and waiting". Home Assistant restarting, an option being edited, or the battery
falling below the re-arm threshold will all arm the limiter, and none of them
will start a charge.

The trade-off is worth stating plainly: after a charge completes the plug stays
off, so plugging the scooter in overnight does nothing until you switch the plug
on.

## The manual plug switch

The `Plug` switch is an override, and it is authoritative. Turning it **on**
starts a session regardless of the current charge — it deliberately bypasses
the target, because "turn the plug on" should mean exactly that. Turning it
**off** ends any open session. It mirrors the real plug, so it reads `off`
while the limiter is merely armed.

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
| `sensor` Charge power | What the plug is drawing right now; only when a power sensor is configured |
| `sensor` Projected charge | Estimated charge right now |
| `sensor` Wh per percent | Average wall Wh per point from the latest reading to the target; attributes list all ten bands, which are typed, and how many charges are remembered |

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
| Wait before learning | 30 min |
| Vase values (per 10-point band) | blank: the default starting point |

## Actions

`ewheels_charge_limiter.record_charge` remembers a charge measured some other
way: start %, settled end %, and wall watt-hours. It uses the same rules as an
automatic charge, so the end must be at least 10 points above the start. The
bands are refitted straight away. It is useful for seeding the vase from
charges you already know, or for putting back one whose learning was lost.

## Not included

Price-based scheduling and solar-surplus charging are out of scope. For a
720 Wh pack a 30→80% charge moves about 0.4 kWh at the wall, so even perfect
price timing saves a fraction of a currency unit per charge. The longevity
limit is the point of this, not the tariff.

## A note on the icon

The icon ships inside the integration at
`custom_components/ewheels_charge_limiter/brand/`, which is the supported
mechanism from Home Assistant 2026.3 onwards — Home Assistant serves it via its
brands proxy, and the `home-assistant/brands` repository no longer accepts
submissions for custom integrations.

It therefore appears on the Integrations page, device pages and elsewhere in
Home Assistant, but **not** in the HACS store listing. That is a known HACS bug
([#5171](https://github.com/hacs/integration/issues/5171),
[#5223](https://github.com/hacs/integration/issues/5223)): the HACS frontend
still fetches icons from the brands CDN, which has no entry for inline-shipped
icons. Nothing in this repository can change that.

## Licence

MIT.

The icon in `custom_components/ewheels_charge_limiter/brand/` is a third-party
asset supplied by the repository owner and is not covered by the MIT licence
above. If it came from a stock library such as Flaticon, attribution is
probably required — replace this paragraph with the correct credit.
