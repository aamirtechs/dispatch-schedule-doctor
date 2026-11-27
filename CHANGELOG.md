# Changelog

## 1.0.0 - 2026-11-27

Initial release.

* Dispatch schedule audit over a carrier's dispatch
  schedule CSV export: double-booked drivers and trucks
  (overlapping pickup to delivery windows on two loads),
  loads with no driver assigned, pickup windows already
  past, deliveries scheduled before pickups, loads that
  clash with a driver's home time or reset window,
  back-to-back loads with no travel gap between delivery
  and the next pickup, and duplicate load ids.
* A per-driver day plan table with load count, first
  pickup, last delivery, and total rate, plus summary
  counts for double-booked loads, unassigned loads, and
  past pickups, with Decimal cents math on the rate
  totals.
* Travel gap rule: for each driver, consecutive loads in
  pickup order need at least --min-gap-hours (default 2)
  between delivery and the next pickup; a gap exactly at
  the minimum passes, and windows that only touch at an
  endpoint are a clean handover, not a collision.
* Pickup windows are compared against --as-of (default
  now); --past-grace-hours (default 0) tolerates pickups
  up to that many hours past the as-of date, for a truck
  already rolling.
* Cancelled loads are out of the plan and delivered loads
  already happened, so neither can collide with a future
  load; unknown statuses are warned about and treated as
  active.
* Data quality checks: duplicate load ids, malformed
  rows, invalid or missing timestamps and rates, missing
  pickup or delivery times, missing broker, truck, or
  rate, and unknown statuses.
* Defensive timestamp parsing (ISO 8601 with or without a
  timezone, plus common day/month/year forms); a bad row
  is reported as a finding and never crashes the run.
  UTF-8 BOM headers and extra columns handled.
* Findings carry a load id, a rule name, a severity (error
  or warning), and a message; errors and warnings are
  counted in the summary.
* Text and --json output, --out file output, --min-gap-hours,
  --past-grace-hours, and --as-of thresholds, and a
  --check CI gate that exits 1 on errors (and on warnings
  with --strict).
* Zero dependencies, Python 3.9 or newer.
