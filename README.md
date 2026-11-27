# dispatch-schedule-doctor

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/aamirtechs/dispatch-schedule-doctor/blob/main/LICENSE) [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/aamirtechs/dispatch-schedule-doctor/issues)

A zero-dependency Python CLI that audits a carrier's
dispatch schedule CSV export for the scheduling collisions
that cost small fleets real money: a driver or a truck
booked on two loads at once, loads with no driver
assigned, pickup windows already past, deliveries
scheduled before pickups, loads that clash with a
driver's home time or reset window, back-to-back loads
with no travel gap between delivery and the next pickup,
duplicate load ids, and bad data such as malformed rows,
invalid timestamps and rates, and missing times. It also
prints a per-driver day plan table, so a dispatcher can
see each driver's day at a glance before the trucks roll.

Double-booking is one of the most common dispatch
failures. It happens when a driver books a spot load
while a dispatcher negotiates another, or when two
dispatch relationships book the same truck, and a no-show
triggers automatic negative ratings on load boards and
lost broker relationships. TMS platforms schedule live
inside paid SaaS and small fleets track assignments in
shared spreadsheets, so this tool audits the schedule
export offline, before the truck rolls.

## Install

Requires Python 3.9 or newer. No dependencies beyond the
standard library.

```bash
pip install dispatch-schedule-doctor
```

Or run it straight from the source checkout:

```bash
python3 dispatch_schedule_doctor.py examples/schedule.csv
```

## Input format

A CSV export with a header row, one row per load.
Columns used: `load_id` (or `load`, `id`), `driver`,
`truck`, `broker`, `pickup_at`, `delivery_at`, `origin`,
`destination`, `rate`, `status`, `home_time_start`,
`home_time_end`, and `reset_until`. Extra columns are
ignored, so a full schedule export works as-is. A UTF-8
BOM header is handled. Only `load_id` is required; the
other columns are optional, and the checks that need a
missing column are skipped.

`driver` also accepts `driver_name`; `truck` also accepts
`truck_id`, `unit`, or `unit_id`; `broker` also accepts
`broker_name`; `pickup_at` also accepts `pickup`,
`pickup_time`, or `pickup_window_start`; `delivery_at`
also accepts `delivery`, `delivery_time`, or
`delivery_window_end`; `origin` also accepts
`pickup_city` or `from`; `destination` also accepts
`delivery_city` or `to`; `rate` also accepts `load_rate`
or `rate_amount`; `status` also accepts `load_status`;
`home_time_start` also accepts `home_start` or
`home_from`; `home_time_end` also accepts `home_end` or
`home_to`; `reset_until` also accepts `reset_end` or
`reset_available`.

`status` is one of `booked`, `dispatched`, `delivered`,
`cancelled`, `pending`, or `assigned`. Cancelled loads are
out of the plan and delivered loads already happened, so
neither can collide with a future load. An unknown status
is warned about and treated as active. Dates accept ISO
8601 with or without a timezone (`2026-11-19 08:00`,
`2026-11-19T08:00:00Z`, `2026-11-19`), plus
day/month/year and month/day/year forms. A date with no
timezone is read as UTC. Rates accept a plain number,
with an optional dollar sign and thousands commas
(`$1,200.00`).

```csv
load_id,driver,truck,broker,pickup_at,delivery_at,origin,destination,rate,status,home_time_start,home_time_end,reset_until
S-101,Alice Reed,T-101,Acme Logistics,2026-11-18 08:00,2026-11-18 18:00,Dallas TX,Houston TX,1200.00,booked,,,
S-102,Alice Reed,T-101,Acme Logistics,2026-11-18 16:00,2026-11-19 02:00,Houston TX,Austin TX,900.00,booked,,,
S-104,,T-103,Gamma Transport,2026-11-19 08:00,2026-11-19 17:00,Chicago IL,Milwaukee WI,850.00,booked,,,
S-105,Carla Diaz,T-103,Delta Carriers,2026-11-10 08:00,2026-11-10 16:00,Nashville TN,Memphis TN,950.00,booked,,,
S-107,Erin Moss,T-105,Foxtrot Lines,2026-11-19 08:00,2026-11-19 18:00,Denver CO,Kansas City MO,1300.00,booked,2026-11-19 12:00,2026-11-20 08:00,
```

An empty `driver` means nobody is booked to run the load.
An empty `rate` means no rate was recorded, and the load
is left out of the rate totals. `home_time_start` and
`home_time_end` together describe a home time window the
load must not overlap; `reset_until` is the time the
driver's reset ends, and a pickup before it is a conflict.

## What it checks

Errors, the ones that mean a collision, a missed window,
or badly recorded data:

* `double-booked-driver`: the driver is already booked on
  another load whose pickup to delivery window overlaps
  this one. Each pair is reported once, on the later load.
* `double-booked-truck`: the truck is already booked on
  another load whose window overlaps this one, even when
  the driver is different.
* `unassigned-load`: the load has no driver assigned, so
  nobody is booked to run it.
* `past-pickup`: the pickup window is already past the
  `--as-of` date (default now), so the load should have
  rolled or been rescheduled. `--past-grace-hours` (default
  0) tolerates pickups up to that many hours past, for a
  truck already rolling. Delivered loads are exempt.
* `delivery-before-pickup`: the delivery time is before
  the pickup time.
* `home-time-conflict`: the load window overlaps the
  driver's home time window.
* `reset-conflict`: the pickup is before the driver's
  reset ends.
* `no-travel-gap`: for one driver, consecutive loads in
  pickup order have less than `--min-gap-hours` (default
  2) between delivery and the next pickup. A gap exactly
  at the minimum passes, and overlapping loads are
  already double-booked, so a negative gap is skipped
  here.
* `duplicate-load`: the same load id twice in the export,
  so the same load is scheduled twice.
* `malformed-row`: a row with the wrong number of fields.
  The row is skipped and reported, never a crash.
* `invalid-timestamp`: a date field that matches no known
  format. The row is still audited on the fields that did
  parse.
* `invalid-rate`: a rate that is not a valid non-negative
  number. It is left out of the rate totals.
* `missing-times`: a pickup or delivery time left empty,
  so the load cannot be placed on the schedule.

Warnings, patterns worth investigating:

* `missing-broker`: a load with no broker name.
* `missing-truck`: a load with no truck assigned, so the
  equipment for the run is unknown.
* `missing-rate`: a load with no rate recorded, so it was
  left out of the rate totals. A rate that is present but
  invalid is an `invalid-rate` error instead, not this
  warning.
* `unknown-status`: a status that is not one of the known
  statuses. The load is treated as active.

The summary reports the total rate with Decimal cents
math, the counts of double-booked loads, unassigned
loads, and past pickups. If a named driver has collision
findings, the text report also names the driver with the most
findings (driver and truck collisions count separately; ties
break by driver name). It includes a per-driver day plan table
with load count, first pickup, last delivery, and total
rate. Loads with no driver are grouped under
`(unassigned)`.

## Usage

```bash
python3 dispatch_schedule_doctor.py examples/schedule.csv --as-of 2026-11-18
```

Captured output:

```
ERROR [invalid-rate] S-119: rate 'abc' is not a valid non-negative amount, so it was left out of the rate totals
ERROR [invalid-timestamp] S-120: delivery_at 'not-a-time' is not a readable date
ERROR [duplicate-load] S-101: load id appears more than once in the export, so the same load is scheduled twice
ERROR [malformed-row] row 23: row has 12 fields but the header has 13, so the row was skipped
ERROR [unassigned-load] S-104: load has no driver assigned, so nobody is booked to run it
ERROR [past-pickup] S-105: pickup_at 2026-11-10 08:00 is already past the as-of date 2026-11-18 00:00, so the load should have rolled or been rescheduled
ERROR [delivery-before-pickup] S-106: delivery_at 2026-11-19 08:00 is before pickup_at 2026-11-19 14:00
ERROR [home-time-conflict] S-107: load window 2026-11-19 08:00 to 2026-11-19 18:00 overlaps the driver's home time 2026-11-19 12:00 to 2026-11-20 08:00
ERROR [reset-conflict] S-110: pickup_at 2026-11-20 08:00 is before the driver's reset ends at 2026-11-20 12:00
WARNING [missing-truck] S-122: load has no truck assigned, so the equipment for the run is unknown
WARNING [unknown-status] S-123: status 'holding' is not a known status (booked, dispatched, delivered, cancelled, pending, assigned), so the load was treated as active
ERROR [double-booked-driver] S-102: driver Alice Reed is already booked on load S-101 from 2026-11-18 08:00 to 2026-11-18 18:00, which overlaps this load from 2026-11-18 16:00 to 2026-11-19 02:00
ERROR [double-booked-truck] S-124: truck T-101 is already booked on load S-101 from 2026-11-18 08:00 to 2026-11-18 18:00, which overlaps this load from 2026-11-18 10:00 to 2026-11-18 15:00
ERROR [double-booked-truck] S-102: truck T-101 is already booked on load S-101 from 2026-11-18 08:00 to 2026-11-18 18:00, which overlaps this load from 2026-11-18 16:00 to 2026-11-19 02:00
ERROR [double-booked-truck] S-109: truck T-103 is already booked on load S-104 from 2026-11-19 08:00 to 2026-11-19 17:00, which overlaps this load from 2026-11-19 08:00 to 2026-11-19 15:00
ERROR [no-travel-gap] S-108: driver Bob Chen has only 1 hours between delivery of load S-103 at 2026-11-18 20:00 and pickup of this load at 2026-11-18 21:00, below the minimum gap of 2 hours
ERROR [no-travel-gap] S-112: driver Frank Hale has only 0.5 hours between delivery of load S-111 at 2026-11-19 14:00 and pickup of this load at 2026-11-19 14:30, below the minimum gap of 2 hours

Loads: 25, findings: 17, errors: 15, warnings: 2
Total rate: $18,350.00, double-booked loads: 4, unassigned loads: 1, past pickups: 1
Most double-booked driver: Alice Reed (2 loads)

Per-driver day plan:
driver            loads      first pickup     last delivery   total rate
(unassigned)          1  2026-11-19 08:00  2026-11-19 17:00      $850.00
Alice Reed            5  2026-11-18 08:00  2026-11-22 16:00    $3,450.00
Bob Chen              4  2026-11-18 09:00  2026-11-20 18:00    $2,700.00
Carla Diaz            4  2026-11-10 08:00  2026-11-22 16:00    $2,850.00
Dan Okafor            4  2026-11-19 14:00  2026-11-22 15:00    $3,300.00
Erin Moss             3  2026-11-19 08:00  2026-11-22 17:00    $2,850.00
Frank Hale            3  2026-11-19 07:00  2026-11-21 15:00    $1,850.00
Grace Kim             1  2026-11-18 10:00  2026-11-18 15:00      $500.00
```

The sample has 26 rows; the malformed row 23 is skipped, so
25 loads are audited. The headline numbers are 17 findings,
15 errors, 2 warnings, and a total rate of $18,350.00.
Alice Reed is double-booked on S-102 (her 16:00 pickup
overlaps S-101, which runs to 18:00), and truck T-101 is
double-booked twice over, by S-102 and by S-124, which Grace
Kim runs on the same truck with a different driver. S-104
has no driver at all, and its truck T-103 is also booked on
S-109 at the same time. S-105 picked up on 2026-11-10 and
never rolled. S-107 runs straight through Erin Moss's home
time, S-110 picks up four hours before Dan Okafor's reset
ends, and Bob Chen and Frank Hale each have a back-to-back
turn with under two hours to travel. The cancelled load
S-115 is correctly skipped from every scheduling check, and
Dan Okafor's S-116 pickup exactly two hours after his
previous delivery passes the gap rule on the boundary.

Use it as a CI gate: `--check` exits 1 when any error is
found, and 0 otherwise. On the sample above it exits 1:

```bash
python3 dispatch_schedule_doctor.py examples/schedule.csv --as-of 2026-11-18 --check
```

On `examples/clean.csv`, four clean loads, it exits 0:

```
Loads: 4, findings: 0, errors: 0, warnings: 0
Total rate: $4,000.00, double-booked loads: 0, unassigned loads: 0, past pickups: 0

Per-driver day plan:
driver            loads      first pickup     last delivery   total rate
Alice Reed            1  2026-11-18 08:00  2026-11-18 18:00    $1,200.00
Bob Chen              1  2026-11-18 09:00  2026-11-18 19:00    $1,100.00
Carla Diaz            1  2026-11-19 08:00  2026-11-19 16:00      $900.00
Dan Okafor            1  2026-11-19 08:00  2026-11-19 15:00      $800.00

All loads clean.
```

With `--strict`, `--check` also fails on warnings.
`examples/warnings-only.csv` has one load with no broker
and one with no truck, so it passes `--check` (exit 0)
and fails `--check --strict` (exit 1). With no minimum
travel gap the two gap errors drop out of the sample
report:

```bash
python3 dispatch_schedule_doctor.py examples/schedule.csv --as-of 2026-11-18 --min-gap-hours 0 --check
```

That run leaves 15 findings and 13 errors, with the
`no-travel-gap` errors for S-108 and S-112 gone. With a
200-hour past-pickup grace the stale pickup drops out
instead:

```bash
python3 dispatch_schedule_doctor.py examples/schedule.csv --as-of 2026-11-18 --past-grace-hours 200 --check
```

That run leaves 16 findings and 14 errors, with the
`past-pickup` error for S-105 gone and past pickups at 0.

`--json` emits the summary, the per-driver day plan, and
the findings as JSON (money amounts as strings, to keep
the cents exact), and `--out` writes the report to a file.

## Options

| Flag | Meaning |
| --- | --- |
| `--min-gap-hours` | A travel gap below this many hours between delivery and the next pickup is flagged (default: 2) |
| `--past-grace-hours` | Pickups up to this many hours past the as-of date are tolerated, for a truck already rolling (default: 0) |
| `--as-of` | Date to compare pickup windows against, for example 2026-11-18 (default: now) |
| `--json` | Emit JSON instead of text |
| `--check` | Exit 1 if any error is found |
| `--strict` | With --check, also exit 1 on warnings |
| `--out` | Write the report to a file |

## Edge cases

* A row with too many or too few fields is a malformed-row
  error and is skipped; the rest of the file is still
  audited.
* A bad date is an invalid-timestamp error, and the row is
  still audited on the fields that did parse, so one bad
  date never hides the other problems on the load.
* Windows that only touch at an endpoint do not overlap:
  a pickup exactly when the previous delivery ends is a
  clean handover, not a collision. A travel gap exactly
  at the minimum passes.
* A cancelled load is out of the plan and a delivered load
  already happened, so neither is checked for collisions,
  gaps, or past pickups. An unknown status is warned about
  and treated as active.
* A load whose rate is present but invalid is an
  `invalid-rate` error and is left out of the rate totals;
  it does not also get a `missing-rate` warning.
* A blank line in the file is skipped silently. A load
  with no driver is grouped under `(unassigned)` in the
  per-driver day plan.
* `--as-of` makes runs reproducible: without it, the
  past-pickup check compares against the current time.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

85 tests, all passing: timestamp, money, and window
parsing, header aliases and BOM handling, malformed rows,
duplicate load ids, invalid and missing timestamps and
rates, double-booked drivers and trucks, touching windows
that are not collisions, unassigned loads, past pickups
and the grace window, delivery before pickup, home time
and reset conflicts, travel gaps at and under the
minimum, cancelled and delivered loads skipped from
scheduling checks, unknown statuses, summary counts and
rate totals, per-driver day plan tables, text and JSON
output, and `--check` and `--strict` exit codes. The same
85 tests pass under pytest.

## License

MIT. See [LICENSE](LICENSE).
