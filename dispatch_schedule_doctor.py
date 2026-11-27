"""Dispatch schedule audit CLI.

Reads a carrier's dispatch schedule CSV export (one row per
load: load id, driver, truck, broker, pickup and delivery
windows, origin and destination, rate, status, and optional
home time and reset windows) and audits it for the
scheduling collisions that cost small fleets real money:
a driver or a truck booked on two loads at once, loads
with no driver assigned, pickup windows already past,
deliveries scheduled before pickups, loads that clash
with a driver's home time or reset window, back-to-back
loads with no travel gap between delivery and the next
pickup, duplicate load ids, and bad data such as malformed
rows, invalid timestamps and rates, and missing times,
plus a per-driver day plan table.

Double-booking is one of the most common dispatch
failures. It happens when a driver books a spot load
while a dispatcher negotiates another, or when two
dispatch relationships book the same truck, and a no-show
triggers automatic negative ratings on load boards and
lost broker relationships. TMS platforms schedule live
inside paid SaaS and small fleets track assignments in
shared spreadsheets, so this tool audits the schedule
export offline, before the truck rolls.

Each problem is a finding with a load id (or a driver
name for driver level findings), a rule name, a severity
(error or warning), and a message. Errors are collisions,
missed windows, or badly recorded data; warnings are
patterns worth investigating. --check exits 1 when any
error is found, and --strict also fails on warnings.

Money uses Decimal cents math, so totals add up exactly.
Times are parsed defensively. A bad timestamp is reported
as a finding and the row is still audited on the fields
that did parse, so one bad row never crashes the run.

Zero dependencies, Python 3.9 or newer, standard library only.

Usage:
    python3 dispatch_schedule_doctor.py examples/schedule.csv
    python3 dispatch_schedule_doctor.py schedule.csv --json --check
    python3 dispatch_schedule_doctor.py schedule.csv --as-of 2026-11-18
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

__version__ = "1.0.0"

DEFAULT_MIN_GAP_HOURS = Decimal("2")
DEFAULT_PAST_GRACE_HOURS = Decimal("0")
CENT = Decimal("0.01")

KNOWN_STATUSES = (
    "booked", "dispatched", "delivered", "cancelled", "pending",
    "assigned",
)
# Statuses that take part in scheduling checks. A cancelled
# load is out of the plan, and a delivered load already
# happened, so neither can collide with a future load.
ACTIVE_STATUSES = ("booked", "dispatched", "pending", "assigned")

TIMESTAMP_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y %H:%M",
    "%d/%m/%Y",
    "%m/%d/%Y",
)


class Finding:
    """One audit finding for one load, or for one driver."""

    def __init__(self, load, rule, severity, message, driver=None):
        self.load = load
        self.rule = rule
        self.severity = severity
        self.message = message
        self.driver = driver

    def as_dict(self):
        """Return the finding as a plain dict."""
        return {"load": self.load, "rule": self.rule,
                "severity": self.severity, "message": self.message}

    def __str__(self):
        return "%s [%s] %s: %s" % (self.severity.upper(), self.rule,
                                   self.load, self.message)


def parse_timestamp(text):
    """Parse a timestamp string into an aware datetime, or None.

    Accepts ISO 8601 with or without a timezone (a trailing Z is
    handled), plus a few common export formats. A naive timestamp
    is read as UTC. Returns None for an empty string or a value
    that matches no known format, so callers can report it
    instead of crashing.
    """
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    candidate = text
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        parsed = None
    if parsed is None:
        for fmt in TIMESTAMP_FORMATS:
            try:
                parsed = datetime.strptime(candidate, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_money(text):
    """Parse a money field, returning (amount, is_valid).

    An empty field means no amount was recorded, which is a
    valid state for a rate. A value that is not a number, or
    a negative amount, is invalid.
    """
    if text is None:
        return None, True
    text = text.strip().replace("$", "").replace(",", "")
    if not text:
        return None, True
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None, False
    if amount < 0:
        return None, False
    return amount, True


def find_column(header, names):
    """Return the first of names present in header, or None."""
    for name in names:
        if name in header:
            return name
    return None


def money(amount):
    """Format a Decimal amount as dollars and cents."""
    return "$%s" % format(amount, ",.2f")


def fmt_hours(hours):
    """Format a Decimal hour count without trailing zeros."""
    return format(hours.normalize(), "f")


def fmt_time(moment):
    """Format a datetime as YYYY-MM-DD HH:MM."""
    return moment.strftime("%Y-%m-%d %H:%M")


def windows_overlap(start_a, end_a, start_b, end_b):
    """Return True when two half-open windows overlap.

    Windows that only touch at an endpoint do not overlap:
    a pickup exactly when the previous delivery ends is a
    clean handover, not a collision.
    """
    return start_a < end_b and start_b < end_a


def parse_csv(path, as_of):
    """Parse a dispatch schedule CSV export.

    Returns (loads, findings). Each load is a dict with the
    id, driver, truck, broker, the parsed pickup and delivery
    times, origin, destination, rate, status, and the home
    time and reset windows. Rows with the wrong number of
    fields are reported as malformed-row errors and skipped,
    never a crash. A UTF-8 BOM header is handled. Extra
    columns are ignored.
    """
    findings = []
    loads = []
    try:
        handle = open(path, newline="", encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError("cannot read %s: %s" % (path, exc))
    with handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError("%s is empty, no header row found" % path)
        except csv.Error as exc:
            raise ValueError("%s is not a readable CSV file: %s"
                             % (path, exc))
        header = [name.strip() for name in header]
        id_column = find_column(header, ("load_id", "load", "id"))
        if id_column is None:
            raise ValueError(
                "%s has no load_id column (header is: %s)"
                % (path, ", ".join(header)))
        driver_column = find_column(
            header, ("driver", "driver_name"))
        truck_column = find_column(header, ("truck", "truck_id",
                                            "unit", "unit_id"))
        broker_column = find_column(header, ("broker", "broker_name"))
        pickup_column = find_column(
            header, ("pickup_at", "pickup", "pickup_time",
                     "pickup_window_start"))
        delivery_column = find_column(
            header, ("delivery_at", "delivery", "delivery_time",
                     "delivery_window_end"))
        origin_column = find_column(header, ("origin", "pickup_city",
                                             "from"))
        destination_column = find_column(
            header, ("destination", "delivery_city", "to"))
        rate_column = find_column(header, ("rate", "load_rate",
                                           "rate_amount"))
        status_column = find_column(header, ("status", "load_status"))
        home_start_column = find_column(
            header, ("home_time_start", "home_start", "home_from"))
        home_end_column = find_column(
            header, ("home_time_end", "home_end", "home_to"))
        reset_column = find_column(
            header, ("reset_until", "reset_end", "reset_available"))
        seen_ids = set()
        for line_number, row in enumerate(reader, start=2):
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) != len(header):
                findings.append(Finding(
                    "row %d" % line_number, "malformed-row", "error",
                    "row has %d fields but the header has %d, "
                    "so the row was skipped" % (len(row), len(header))))
                continue
            cells = dict(zip(header, (cell.strip() for cell in row)))
            load_id = cells[id_column] or "row %d" % line_number
            if load_id in seen_ids:
                findings.append(Finding(
                    load_id, "duplicate-load", "error",
                    "load id appears more than once in the export, "
                    "so the same load is scheduled twice"))
            else:
                seen_ids.add(load_id)
            load = {"id": load_id,
                    "driver": (cells[driver_column]
                               if driver_column else ""),
                    "truck": (cells[truck_column]
                              if truck_column else ""),
                    "broker": (cells[broker_column]
                               if broker_column else ""),
                    "pickup_at": None,
                    "delivery_at": None,
                    "origin": (cells[origin_column]
                               if origin_column else ""),
                    "destination": (cells[destination_column]
                                    if destination_column else ""),
                    "rate": None,
                    "status": (cells[status_column].lower()
                               if status_column
                               and cells[status_column] else ""),
                    "home_time_start": None,
                    "home_time_end": None,
                    "reset_until": None,
                    "rate_invalid": False,
                    "line": line_number}
            for column, key, label in (
                    (pickup_column, "pickup_at", "pickup_at"),
                    (delivery_column, "delivery_at", "delivery_at"),
                    (home_start_column, "home_time_start",
                     "home_time_start"),
                    (home_end_column, "home_time_end",
                     "home_time_end"),
                    (reset_column, "reset_until", "reset_until")):
                if column is None:
                    continue
                raw = cells[column]
                if not raw:
                    continue
                parsed = parse_timestamp(raw)
                if parsed is None:
                    findings.append(Finding(
                        load_id, "invalid-timestamp", "error",
                        "%s %r is not a readable date"
                        % (label, raw)))
                else:
                    load[key] = parsed
            if pickup_column is not None \
                    and not cells[pickup_column]:
                findings.append(Finding(
                    load_id, "missing-times", "error",
                    "pickup_at is empty, so the load cannot be "
                    "placed on the schedule"))
            if delivery_column is not None \
                    and not cells[delivery_column]:
                findings.append(Finding(
                    load_id, "missing-times", "error",
                    "delivery_at is empty, so the load cannot be "
                    "placed on the schedule"))
            if rate_column is not None:
                rate, valid = parse_money(cells[rate_column])
                if not valid:
                    load["rate_invalid"] = True
                    findings.append(Finding(
                        load_id, "invalid-rate", "error",
                        "rate %r is not a valid non-negative "
                        "amount, so it was left out of the rate "
                        "totals" % cells[rate_column]))
                else:
                    load["rate"] = rate
            loads.append(load)
    return loads, findings


def audit(loads, as_of, min_gap_hours=DEFAULT_MIN_GAP_HOURS,
          past_grace_hours=DEFAULT_PAST_GRACE_HOURS):
    """Audit a parsed load list and return a list of findings."""
    findings = []
    # Cancelled loads are out of the plan and delivered loads
    # already happened, so neither can collide with a future
    # load. Every other status, including an unknown one, is
    # treated as active and checked.
    active = [load for load in loads
              if load["status"] not in ("cancelled", "delivered")]
    for load in loads:
        load_id = load["id"]
        pickup = load["pickup_at"]
        delivery = load["delivery_at"]
        status = load["status"]
        if not load["broker"]:
            findings.append(Finding(
                load_id, "missing-broker", "warning",
                "load has no broker name, so it cannot be "
                "counted in the per-broker summary"))
        if not load["truck"]:
            findings.append(Finding(
                load_id, "missing-truck", "warning",
                "load has no truck assigned, so the equipment "
                "for the run is unknown"))
        if status and status not in KNOWN_STATUSES:
            findings.append(Finding(
                load_id, "unknown-status", "warning",
                "status %r is not a known status "
                "(booked, dispatched, delivered, cancelled, "
                "pending, assigned), so the load was treated "
                "as active" % status))
        if load["rate"] is None and not load["rate_invalid"]:
            findings.append(Finding(
                load_id, "missing-rate", "warning",
                "load has no rate recorded, so it was left "
                "out of the rate totals"))
        if status == "cancelled":
            continue
        if not load["driver"]:
            findings.append(Finding(
                load_id, "unassigned-load", "error",
                "load has no driver assigned, so nobody is "
                "booked to run it"))
        if pickup is not None and delivery is not None \
                and delivery < pickup:
            findings.append(Finding(
                load_id, "delivery-before-pickup", "error",
                "delivery_at %s is before pickup_at %s"
                % (fmt_time(delivery), fmt_time(pickup))))
        if pickup is not None and status != "delivered":
            grace = timedelta(
                hours=float(past_grace_hours))
            if pickup < as_of - grace:
                findings.append(Finding(
                    load_id, "past-pickup", "error",
                    "pickup_at %s is already past the as-of "
                    "date %s, so the load should have rolled "
                    "or been rescheduled"
                    % (fmt_time(pickup),
                       fmt_time(as_of))))
        home_start = load["home_time_start"]
        home_end = load["home_time_end"]
        if pickup is not None and delivery is not None \
                and home_start is not None \
                and home_end is not None:
            if windows_overlap(pickup, delivery,
                               home_start, home_end):
                findings.append(Finding(
                    load_id, "home-time-conflict", "error",
                    "load window %s to %s overlaps the "
                    "driver's home time %s to %s"
                    % (fmt_time(pickup), fmt_time(delivery),
                       fmt_time(home_start), fmt_time(home_end))))
        reset_until = load["reset_until"]
        if pickup is not None and reset_until is not None \
                and pickup < reset_until:
            findings.append(Finding(
                load_id, "reset-conflict", "error",
                "pickup_at %s is before the driver's reset "
                "ends at %s"
                % (fmt_time(pickup), fmt_time(reset_until))))
    findings.extend(check_double_booked(active))
    findings.extend(check_travel_gaps(active, min_gap_hours))
    return findings


def check_double_booked(active):
    """Flag drivers and trucks booked on two loads at once.

    Only loads with both pickup and delivery times can be
    placed on the timeline. Each pair is reported once, on
    the later load, so the report reads in schedule order.
    """
    findings = []
    for key, label in (("driver", "driver"),
                       ("truck", "truck")):
        by_value = {}
        for load in active:
            value = load[key]
            if not value:
                continue
            by_value.setdefault(value, []).append(load)
        for value, group in by_value.items():
            placed = [load for load in group
                      if load["pickup_at"] is not None
                      and load["delivery_at"] is not None]
            placed.sort(key=lambda item: (item["pickup_at"],
                                          item["id"]))
            for index, load in enumerate(placed):
                for earlier in placed[:index]:
                    if windows_overlap(
                            earlier["pickup_at"],
                            earlier["delivery_at"],
                            load["pickup_at"],
                            load["delivery_at"]):
                        findings.append(Finding(
                            load["id"],
                            "double-booked-%s" % label, "error",
                            "%s %s is already booked on load %s "
                            "from %s to %s, which overlaps this "
                            "load from %s to %s"
                            % (label, value, earlier["id"],
                               fmt_time(earlier["pickup_at"]),
                               fmt_time(earlier["delivery_at"]),
                               fmt_time(load["pickup_at"]),
                               fmt_time(load["delivery_at"])),
                            driver=load["driver"]))
                        break
    return findings


def check_travel_gaps(active, min_gap_hours):
    """Flag back-to-back loads with too little travel gap.

    For each driver, consecutive loads are compared in
    pickup order: the gap is the next pickup minus the
    previous delivery. A gap below --min-gap-hours is an
    error, because the driver cannot realistically travel
    between the two stops. Overlapping loads are already
    double-booked, so a negative gap is skipped here.
    A gap exactly at the minimum passes.
    """
    findings = []
    by_driver = {}
    for load in active:
        if not load["driver"]:
            continue
        by_driver.setdefault(load["driver"], []).append(load)
    for driver, group in by_driver.items():
        placed = [load for load in group
                  if load["pickup_at"] is not None
                  and load["delivery_at"] is not None]
        placed.sort(key=lambda item: (item["pickup_at"],
                                      item["id"]))
        for previous, load in zip(placed, placed[1:]):
            gap = load["pickup_at"] - previous["delivery_at"]
            gap_hours = Decimal(str(gap.total_seconds())) \
                / Decimal("3600")
            if gap_hours < 0:
                continue
            if gap_hours < min_gap_hours:
                findings.append(Finding(
                    load["id"], "no-travel-gap", "error",
                    "driver %s has only %s hours between "
                    "delivery of load %s at %s and pickup of "
                    "this load at %s, below the minimum gap "
                    "of %s hours"
                    % (driver, fmt_hours(gap_hours),
                       previous["id"],
                       fmt_time(previous["delivery_at"]),
                       fmt_time(load["pickup_at"]),
                       fmt_hours(min_gap_hours))))
    return findings


def summarize_drivers(loads):
    """Return the per-driver day plan rows."""
    per_driver = {}
    for load in loads:
        driver = load["driver"] or "(unassigned)"
        row = per_driver.setdefault(driver, {
            "driver": driver, "loads": 0,
            "first_pickup": None, "last_delivery": None,
            "total_rate": Decimal("0")})
        row["loads"] += 1
        if load["pickup_at"] is not None:
            if row["first_pickup"] is None \
                    or load["pickup_at"] < row["first_pickup"]:
                row["first_pickup"] = load["pickup_at"]
        if load["delivery_at"] is not None:
            if row["last_delivery"] is None \
                    or load["delivery_at"] > row["last_delivery"]:
                row["last_delivery"] = load["delivery_at"]
        if load["rate"] is not None:
            row["total_rate"] += load["rate"]
    rows = []
    for row in sorted(per_driver.values(),
                      key=lambda item: item["driver"]):
        rows.append(row)
    return rows


def summarize(loads, findings):
    """Return the summary counts for a load list and findings."""
    errors = sum(1 for item in findings if item.severity == "error")
    warnings = sum(1 for item in findings
                   if item.severity == "warning")
    total_rate = sum(
        (load["rate"] for load in loads
         if load["rate"] is not None), Decimal("0"))
    double_booked = sum(
        1 for item in findings
        if item.rule.startswith("double-booked"))
    unassigned = sum(
        1 for item in findings if item.rule == "unassigned-load")
    past_pickups = sum(
        1 for item in findings if item.rule == "past-pickup")
    return {"loads": len(loads),
            "findings": len(findings),
            "errors": errors,
            "warnings": warnings,
            "double_booked_loads": double_booked,
            "unassigned_loads": unassigned,
            "past_pickups": past_pickups,
            "total_rate": total_rate,
            "per_driver": summarize_drivers(loads)}


def render_text(loads, findings, summary):
    """Render the text report."""
    lines = [str(item) for item in findings]
    lines.append("")
    lines.append("Loads: %(loads)d, findings: %(findings)d, "
                 "errors: %(errors)d, warnings: %(warnings)d"
                 % summary)
    lines.append("Total rate: %s, double-booked loads: %d, "
                 "unassigned loads: %d, past pickups: %d"
                 % (money(summary["total_rate"]),
                    summary["double_booked_loads"],
                    summary["unassigned_loads"],
                    summary["past_pickups"]))
    # Count collision findings, not distinct load IDs, as in the
    # headline double-booked total. Keep attribution on each finding
    # so duplicate IDs do not accidentally credit a different driver.
    collisions = {}
    for item in findings:
        if item.rule.startswith("double-booked") and item.driver:
            collisions[item.driver] = collisions.get(item.driver, 0) + 1
    if collisions:
        driver = min(collisions, key=lambda name: (-collisions[name], name))
        count = collisions[driver]
        lines.append("Most double-booked driver: %s (%d %s)"
                     % (driver, count, "load" if count == 1 else "loads"))
    lines.append("")
    lines.append("Per-driver day plan:")
    lines.append("%-16s %6s %17s %17s %12s"
                 % ("driver", "loads", "first pickup",
                    "last delivery", "total rate"))
    for row in summary["per_driver"]:
        first = fmt_time(row["first_pickup"]) \
            if row["first_pickup"] is not None else "-"
        last = fmt_time(row["last_delivery"]) \
            if row["last_delivery"] is not None else "-"
        lines.append("%-16s %6d %17s %17s %12s"
                     % (row["driver"], row["loads"], first, last,
                        money(row["total_rate"])))
    if summary["findings"] == 0:
        lines.append("")
        lines.append("All loads clean.")
    return "\n".join(lines) + "\n"


def render_json(loads, findings, summary):
    """Render the JSON report."""
    report = dict(summary)
    report["total_rate"] = str(summary["total_rate"])
    per_driver = []
    for row in summary["per_driver"]:
        item = dict(row)
        item["total_rate"] = str(row["total_rate"])
        item["first_pickup"] = fmt_time(row["first_pickup"]) \
            if row["first_pickup"] is not None else None
        item["last_delivery"] = fmt_time(row["last_delivery"]) \
            if row["last_delivery"] is not None else None
        per_driver.append(item)
    report["per_driver"] = per_driver
    report["findings"] = [item.as_dict() for item in findings]
    return json.dumps(report, indent=2) + "\n"


def parse_decimal_arg(text):
    """Parse a Decimal CLI argument, or raise a usage error."""
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(
            "%r is not a valid number" % text)
    if value < 0:
        raise argparse.ArgumentTypeError(
            "value must be at least 0")
    return value


def build_parser():
    """Build the command line parser."""
    parser = argparse.ArgumentParser(
        prog="dispatch-schedule-doctor",
        description="Audit a dispatch schedule CSV export for "
                    "double-booked drivers and trucks, missed "
                    "pickup windows, and missing travel gaps.")
    parser.add_argument("csv_file",
                        help="dispatch schedule CSV to audit")
    parser.add_argument("--min-gap-hours", type=parse_decimal_arg,
                        default=DEFAULT_MIN_GAP_HOURS,
                        help="a travel gap below this many hours "
                        "between delivery and the next pickup is "
                        "flagged (default: 2)")
    parser.add_argument("--past-grace-hours",
                        type=parse_decimal_arg,
                        default=DEFAULT_PAST_GRACE_HOURS,
                        help="pickups up to this many hours past "
                        "the as-of date are tolerated, for a "
                        "truck already rolling (default: 0)")
    parser.add_argument("--as-of", default=None,
                        help="date to compare pickup windows "
                        "against, for example 2026-11-18 "
                        "(default: now)")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of text")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if any error is found")
    parser.add_argument("--strict", action="store_true",
                        help="with --check, also exit 1 on warnings")
    parser.add_argument("--out", default=None,
                        help="write the report to a file")
    return parser


def main(argv=None):
    """Run the CLI and return the exit code."""
    args = build_parser().parse_args(argv)
    if args.as_of is None:
        as_of = datetime.now(timezone.utc)
    else:
        as_of = parse_timestamp(args.as_of)
        if as_of is None:
            print("error: --as-of %r is not a readable date"
                  % args.as_of, file=sys.stderr)
            return 2
    try:
        loads, findings = parse_csv(args.csv_file, as_of)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    findings.extend(audit(loads, as_of,
                          min_gap_hours=args.min_gap_hours,
                          past_grace_hours=args.past_grace_hours))
    summary = summarize(loads, findings)
    if args.json:
        report = render_json(loads, findings, summary)
    else:
        report = render_text(loads, findings, summary)
    if args.out is not None:
        try:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(report)
        except OSError as exc:
            print("error: cannot write %s: %s" % (args.out, exc),
                  file=sys.stderr)
            return 2
    else:
        sys.stdout.write(report)
    if args.check:
        if summary["errors"] > 0:
            return 1
        if args.strict and summary["warnings"] > 0:
            return 1
    return 0


def main_entry():
    """Console script entry point."""
    sys.exit(main())


if __name__ == "__main__":
    main_entry()
