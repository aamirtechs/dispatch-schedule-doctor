"""Tests for dispatch_schedule_doctor."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import dispatch_schedule_doctor as dsd

AS_OF = datetime(2026, 11, 18, tzinfo=timezone.utc)

HEADER = ("load_id,driver,truck,broker,pickup_at,delivery_at,"
          "origin,destination,rate,status,home_time_start,"
          "home_time_end,reset_until\n")


def make_load(**overrides):
    """Build one load dict with sane defaults."""
    load = {"id": "S-1",
            "driver": "Alice Reed",
            "truck": "T-101",
            "broker": "Acme Logistics",
            "pickup_at": datetime(2026, 11, 19, 8, 0,
                                  tzinfo=timezone.utc),
            "delivery_at": datetime(2026, 11, 19, 18, 0,
                                    tzinfo=timezone.utc),
            "origin": "Dallas TX",
            "destination": "Houston TX",
            "rate": Decimal("1200.00"),
            "status": "booked",
            "home_time_start": None,
            "home_time_end": None,
            "reset_until": None,
            "rate_invalid": False,
            "line": 2}
    load.update(overrides)
    return load


def write_csv(text):
    """Write CSV text to a temp file and return its path."""
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, encoding="utf-8")
    handle.write(text)
    handle.close()
    return handle.name


def run_cli(*argv):
    """Run main() capturing stdout and stderr, return (code, out)."""
    buffer = io.StringIO()
    errors = io.StringIO()
    with redirect_stdout(buffer), redirect_stderr(errors):
        try:
            code = dsd.main(list(argv))
        except SystemExit as exc:
            code = exc.code
    return code, buffer.getvalue(), errors.getvalue()


def rules(findings):
    """Return the set of rule names in a findings list."""
    return {item.rule for item in findings}


class TestParseTimestamp(unittest.TestCase):
    def test_iso_with_space(self):
        parsed = dsd.parse_timestamp("2026-11-19 08:00")
        self.assertEqual(parsed, datetime(2026, 11, 19, 8, 0,
                                          tzinfo=timezone.utc))

    def test_iso_with_t_and_z(self):
        parsed = dsd.parse_timestamp("2026-11-19T08:00:00Z")
        self.assertEqual(parsed, datetime(2026, 11, 19, 8, 0,
                                          tzinfo=timezone.utc))

    def test_date_only(self):
        parsed = dsd.parse_timestamp("2026-11-19")
        self.assertEqual(parsed, datetime(2026, 11, 19,
                                          tzinfo=timezone.utc))

    def test_day_month_year(self):
        parsed = dsd.parse_timestamp("19/11/2026 08:00")
        self.assertEqual(parsed, datetime(2026, 11, 19, 8, 0,
                                          tzinfo=timezone.utc))

    def test_ambiguous_slash_is_day_first(self):
        parsed = dsd.parse_timestamp("01/02/2026")
        self.assertEqual(parsed, datetime(2026, 2, 1,
                                          tzinfo=timezone.utc))

    def test_invalid_returns_none(self):
        self.assertIsNone(dsd.parse_timestamp("not-a-time"))

    def test_empty_returns_none(self):
        self.assertIsNone(dsd.parse_timestamp(""))
        self.assertIsNone(dsd.parse_timestamp(None))

    def test_naive_is_utc(self):
        parsed = dsd.parse_timestamp("2026-11-19 08:00")
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_offset_kept(self):
        parsed = dsd.parse_timestamp("2026-11-19T08:00:00+02:00")
        self.assertEqual(parsed.hour, 8)
        self.assertEqual(parsed.utcoffset().total_seconds(), 7200)


class TestParseMoney(unittest.TestCase):
    def test_plain(self):
        amount, valid = dsd.parse_money("1200.00")
        self.assertTrue(valid)
        self.assertEqual(amount, Decimal("1200.00"))

    def test_dollar_sign_and_commas(self):
        amount, valid = dsd.parse_money("$1,200.00")
        self.assertTrue(valid)
        self.assertEqual(amount, Decimal("1200.00"))

    def test_empty_is_none_and_valid(self):
        amount, valid = dsd.parse_money("")
        self.assertTrue(valid)
        self.assertIsNone(amount)

    def test_invalid(self):
        _, valid = dsd.parse_money("abc")
        self.assertFalse(valid)

    def test_negative_invalid(self):
        _, valid = dsd.parse_money("-50")
        self.assertFalse(valid)

    def test_zero_valid(self):
        amount, valid = dsd.parse_money("0")
        self.assertTrue(valid)
        self.assertEqual(amount, Decimal("0"))


class TestWindowsOverlap(unittest.TestCase):
    def test_overlap(self):
        self.assertTrue(dsd.windows_overlap(
            datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 18, 18, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 18, 16, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 19, 2, 0, tzinfo=timezone.utc)))

    def test_touching_endpoints_do_not_overlap(self):
        self.assertFalse(dsd.windows_overlap(
            datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 18, 18, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 18, 18, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 19, 2, 0, tzinfo=timezone.utc)))

    def test_disjoint(self):
        self.assertFalse(dsd.windows_overlap(
            datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 18, 14, 0, tzinfo=timezone.utc)))

    def test_contained(self):
        self.assertTrue(dsd.windows_overlap(
            datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 18, 18, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)))


class TestParseCsv(unittest.TestCase):
    def test_header_aliases(self):
        text = ("load,driver_name,unit,broker_name,pickup,"
                "delivery,from,to,rate,status\n"
                "S-1,Alice Reed,T-101,Acme,2026-11-19 08:00,"
                "2026-11-19 18:00,Dallas,Houston,1200,booked\n")
        path = write_csv(text)
        try:
            loads, findings = dsd.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)
        self.assertEqual(len(loads), 1)
        self.assertEqual(loads[0]["driver"], "Alice Reed")
        self.assertEqual(loads[0]["truck"], "T-101")
        self.assertEqual(loads[0]["broker"], "Acme")
        self.assertEqual(findings, [])

    def test_bom_header(self):
        text = "\ufeff" + HEADER + (
            "S-1,Alice Reed,T-101,Acme,2026-11-19 08:00,"
            "2026-11-19 18:00,Dallas TX,Houston TX,1200,booked,,,\n")
        path = write_csv(text)
        try:
            loads, findings = dsd.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)
        self.assertEqual(len(loads), 1)
        self.assertEqual(findings, [])

    def test_extra_columns_ignored(self):
        text = HEADER.rstrip("\n") + ",notes\n" + (
            "S-1,Alice Reed,T-101,Acme,2026-11-19 08:00,"
            "2026-11-19 18:00,Dallas TX,Houston TX,1200,booked,"
            ",,,extra note\n")
        path = write_csv(text)
        try:
            loads, findings = dsd.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)
        self.assertEqual(len(loads), 1)
        self.assertEqual(findings, [])

    def test_missing_load_id_column_raises(self):
        path = write_csv("driver,truck\nAlice,T-101\n")
        try:
            with self.assertRaises(ValueError) as ctx:
                dsd.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)
        self.assertIn("no load_id column", str(ctx.exception))

    def test_empty_file_raises(self):
        path = write_csv("")
        try:
            with self.assertRaises(ValueError) as ctx:
                dsd.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)
        self.assertIn("empty", str(ctx.exception))

    def test_missing_file_raises(self):
        with self.assertRaises(ValueError) as ctx:
            dsd.parse_csv("/tmp/no-such-dispatch-file.csv", AS_OF)
        self.assertIn("cannot read", str(ctx.exception))

    def test_malformed_row_skipped(self):
        text = HEADER + (
            "S-1,Alice Reed,T-101,Acme,2026-11-19 08:00,"
            "2026-11-19 18:00,Dallas TX,Houston TX,1200,booked,,,\n"
            "S-2,Bad Row,only,three,fields\n"
            "S-3,Bob Chen,T-102,Beta,2026-11-19 08:00,"
            "2026-11-19 18:00,Atlanta GA,Charlotte NC,1100,"
            "booked,,,\n")
        path = write_csv(text)
        try:
            loads, findings = dsd.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)
        self.assertEqual(len(loads), 2)
        self.assertEqual([item["id"] for item in loads],
                         ["S-1", "S-3"])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "malformed-row")
        self.assertEqual(findings[0].load, "row 3")

    def test_duplicate_load_id(self):
        text = HEADER + (
            "S-1,Alice Reed,T-101,Acme,2026-11-19 08:00,"
            "2026-11-19 18:00,Dallas TX,Houston TX,1200,booked,,,\n"
            "S-1,Alice Reed,T-101,Acme,2026-11-20 08:00,"
            "2026-11-20 18:00,Dallas TX,Houston TX,1200,booked,,,\n")
        path = write_csv(text)
        try:
            loads, findings = dsd.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)
        self.assertEqual(len(loads), 2)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "duplicate-load")

    def test_invalid_timestamp_row_still_audited(self):
        text = HEADER + (
            "S-1,Alice Reed,T-101,Acme,not-a-time,"
            "2026-11-19 18:00,Dallas TX,Houston TX,1200,booked,,,\n")
        path = write_csv(text)
        try:
            loads, findings = dsd.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)
        self.assertEqual(len(loads), 1)
        self.assertIsNone(loads[0]["pickup_at"])
        self.assertIsNotNone(loads[0]["delivery_at"])
        self.assertEqual(findings[0].rule, "invalid-timestamp")

    def test_invalid_rate_flagged(self):
        text = HEADER + (
            "S-1,Alice Reed,T-101,Acme,2026-11-19 08:00,"
            "2026-11-19 18:00,Dallas TX,Houston TX,abc,booked,,,\n")
        path = write_csv(text)
        try:
            loads, findings = dsd.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)
        self.assertIsNone(loads[0]["rate"])
        self.assertTrue(loads[0]["rate_invalid"])
        self.assertEqual(findings[0].rule, "invalid-rate")

    def test_missing_times(self):
        text = HEADER + (
            "S-1,Alice Reed,T-101,Acme,,,"
            "Dallas TX,Houston TX,1200,booked,,,\n")
        path = write_csv(text)
        try:
            loads, findings = dsd.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)
        self.assertEqual(
            sorted(item.rule for item in findings),
            ["missing-times", "missing-times"])

    def test_blank_lines_skipped(self):
        text = HEADER + "\n" + (
            "S-1,Alice Reed,T-101,Acme,2026-11-19 08:00,"
            "2026-11-19 18:00,Dallas TX,Houston TX,1200,booked,,,\n"
            "\n")
        path = write_csv(text)
        try:
            loads, findings = dsd.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)
        self.assertEqual(len(loads), 1)
        self.assertEqual(findings, [])

    def test_status_lowercased(self):
        text = HEADER + (
            "S-1,Alice Reed,T-101,Acme,2026-11-19 08:00,"
            "2026-11-19 18:00,Dallas TX,Houston TX,1200,"
            "BOOKED,,,\n")
        path = write_csv(text)
        try:
            loads, _ = dsd.parse_csv(path, AS_OF)
        finally:
            os.unlink(path)
        self.assertEqual(loads[0]["status"], "booked")


class TestAuditRules(unittest.TestCase):
    def test_double_booked_driver(self):
        loads = [make_load(id="S-1"),
                 make_load(id="S-2",
                           pickup_at=datetime(2026, 11, 19, 16, 0,
                                              tzinfo=timezone.utc),
                           delivery_at=datetime(2026, 11, 20, 2, 0,
                                                tzinfo=timezone.utc))]
        findings = dsd.audit(loads, AS_OF)
        flagged = [item for item in findings
                   if item.rule == "double-booked-driver"]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0].load, "S-2")
        self.assertEqual(flagged[0].severity, "error")

    def test_double_booked_truck_different_drivers(self):
        loads = [make_load(id="S-1"),
                 make_load(id="S-2", driver="Grace Kim",
                           pickup_at=datetime(2026, 11, 19, 10, 0,
                                              tzinfo=timezone.utc),
                           delivery_at=datetime(2026, 11, 19, 15, 0,
                                                tzinfo=timezone.utc))]
        findings = dsd.audit(loads, AS_OF)
        self.assertIn("double-booked-truck", rules(findings))
        self.assertNotIn("double-booked-driver", rules(findings))

    def test_touching_windows_not_double_booked(self):
        loads = [make_load(id="S-1"),
                 make_load(id="S-2",
                           pickup_at=datetime(2026, 11, 19, 18, 0,
                                              tzinfo=timezone.utc),
                           delivery_at=datetime(2026, 11, 20, 2, 0,
                                                tzinfo=timezone.utc))]
        findings = dsd.audit(loads, AS_OF)
        self.assertNotIn("double-booked-driver", rules(findings))
        self.assertNotIn("double-booked-truck", rules(findings))

    def test_unassigned_load(self):
        findings = dsd.audit([make_load(driver="")], AS_OF)
        flagged = [item for item in findings
                   if item.rule == "unassigned-load"]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0].severity, "error")

    def test_past_pickup(self):
        load = make_load(
            pickup_at=datetime(2026, 11, 10, 8, 0,
                               tzinfo=timezone.utc),
            delivery_at=datetime(2026, 11, 10, 16, 0,
                                 tzinfo=timezone.utc))
        findings = dsd.audit([load], AS_OF)
        self.assertIn("past-pickup", rules(findings))

    def test_pickup_exactly_at_as_of_is_not_past(self):
        load = make_load(
            pickup_at=datetime(2026, 11, 18, 0, 0,
                               tzinfo=timezone.utc),
            delivery_at=datetime(2026, 11, 18, 10, 0,
                                 tzinfo=timezone.utc))
        findings = dsd.audit([load], AS_OF)
        self.assertNotIn("past-pickup", rules(findings))

    def test_past_grace_hours_tolerates_recent_pickup(self):
        load = make_load(
            pickup_at=datetime(2026, 11, 17, 12, 0,
                               tzinfo=timezone.utc),
            delivery_at=datetime(2026, 11, 17, 20, 0,
                                 tzinfo=timezone.utc))
        findings = dsd.audit([load], AS_OF,
                             past_grace_hours=Decimal("24"))
        self.assertNotIn("past-pickup", rules(findings))

    def test_past_grace_hours_too_small_still_flags(self):
        load = make_load(
            pickup_at=datetime(2026, 11, 17, 12, 0,
                               tzinfo=timezone.utc),
            delivery_at=datetime(2026, 11, 17, 20, 0,
                                 tzinfo=timezone.utc))
        findings = dsd.audit([load], AS_OF,
                             past_grace_hours=Decimal("6"))
        self.assertIn("past-pickup", rules(findings))

    def test_delivered_load_past_pickup_not_flagged(self):
        load = make_load(
            status="delivered",
            pickup_at=datetime(2026, 11, 10, 8, 0,
                               tzinfo=timezone.utc),
            delivery_at=datetime(2026, 11, 10, 16, 0,
                                 tzinfo=timezone.utc))
        findings = dsd.audit([load], AS_OF)
        self.assertNotIn("past-pickup", rules(findings))

    def test_delivery_before_pickup(self):
        load = make_load(
            pickup_at=datetime(2026, 11, 19, 14, 0,
                               tzinfo=timezone.utc),
            delivery_at=datetime(2026, 11, 19, 8, 0,
                                 tzinfo=timezone.utc))
        findings = dsd.audit([load], AS_OF)
        flagged = [item for item in findings
                   if item.rule == "delivery-before-pickup"]
        self.assertEqual(len(flagged), 1)

    def test_delivery_equal_pickup_is_not_before(self):
        load = make_load(
            delivery_at=datetime(2026, 11, 19, 8, 0,
                                 tzinfo=timezone.utc))
        findings = dsd.audit([load], AS_OF)
        self.assertNotIn("delivery-before-pickup", rules(findings))

    def test_home_time_conflict(self):
        load = make_load(
            home_time_start=datetime(2026, 11, 19, 12, 0,
                                     tzinfo=timezone.utc),
            home_time_end=datetime(2026, 11, 20, 8, 0,
                                   tzinfo=timezone.utc))
        findings = dsd.audit([load], AS_OF)
        self.assertIn("home-time-conflict", rules(findings))

    def test_home_time_touching_is_not_conflict(self):
        load = make_load(
            home_time_start=datetime(2026, 11, 19, 18, 0,
                                     tzinfo=timezone.utc),
            home_time_end=datetime(2026, 11, 20, 8, 0,
                                   tzinfo=timezone.utc))
        findings = dsd.audit([load], AS_OF)
        self.assertNotIn("home-time-conflict", rules(findings))

    def test_home_time_after_delivery_is_not_conflict(self):
        load = make_load(
            home_time_start=datetime(2026, 11, 20, 8, 0,
                                     tzinfo=timezone.utc),
            home_time_end=datetime(2026, 11, 21, 8, 0,
                                   tzinfo=timezone.utc))
        findings = dsd.audit([load], AS_OF)
        self.assertNotIn("home-time-conflict", rules(findings))

    def test_reset_conflict(self):
        load = make_load(
            reset_until=datetime(2026, 11, 19, 12, 0,
                                 tzinfo=timezone.utc))
        findings = dsd.audit([load], AS_OF)
        self.assertIn("reset-conflict", rules(findings))

    def test_pickup_exactly_at_reset_end_passes(self):
        load = make_load(
            reset_until=datetime(2026, 11, 19, 8, 0,
                                 tzinfo=timezone.utc))
        findings = dsd.audit([load], AS_OF)
        self.assertNotIn("reset-conflict", rules(findings))

    def test_no_travel_gap(self):
        first = make_load(id="S-1")
        second = make_load(
            id="S-2",
            pickup_at=datetime(2026, 11, 19, 19, 0,
                               tzinfo=timezone.utc),
            delivery_at=datetime(2026, 11, 20, 3, 0,
                                 tzinfo=timezone.utc))
        findings = dsd.audit([first, second], AS_OF)
        flagged = [item for item in findings
                   if item.rule == "no-travel-gap"]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0].load, "S-2")
        self.assertIn("1 hours", flagged[0].message)

    def test_gap_exactly_at_minimum_passes(self):
        first = make_load(id="S-1")
        second = make_load(
            id="S-2",
            pickup_at=datetime(2026, 11, 19, 20, 0,
                               tzinfo=timezone.utc),
            delivery_at=datetime(2026, 11, 20, 4, 0,
                                 tzinfo=timezone.utc))
        findings = dsd.audit([first, second], AS_OF)
        self.assertNotIn("no-travel-gap", rules(findings))

    def test_gap_just_under_minimum_fails(self):
        first = make_load(id="S-1")
        second = make_load(
            id="S-2",
            pickup_at=datetime(2026, 11, 19, 19, 59,
                               tzinfo=timezone.utc),
            delivery_at=datetime(2026, 11, 20, 3, 0,
                                 tzinfo=timezone.utc))
        findings = dsd.audit([first, second], AS_OF)
        self.assertIn("no-travel-gap", rules(findings))

    def test_min_gap_hours_zero_allows_any_gap(self):
        first = make_load(id="S-1")
        second = make_load(
            id="S-2",
            pickup_at=datetime(2026, 11, 19, 18, 30,
                               tzinfo=timezone.utc),
            delivery_at=datetime(2026, 11, 20, 3, 0,
                                 tzinfo=timezone.utc))
        findings = dsd.audit([first, second], AS_OF,
                             min_gap_hours=Decimal("0"))
        self.assertNotIn("no-travel-gap", rules(findings))

    def test_gap_uses_pickup_order_not_file_order(self):
        first = make_load(
            id="S-2",
            pickup_at=datetime(2026, 11, 19, 19, 0,
                               tzinfo=timezone.utc),
            delivery_at=datetime(2026, 11, 20, 3, 0,
                                 tzinfo=timezone.utc))
        second = make_load(id="S-1")
        findings = dsd.audit([first, second], AS_OF)
        flagged = [item for item in findings
                   if item.rule == "no-travel-gap"]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0].load, "S-2")

    def test_cancelled_load_skips_scheduling_checks(self):
        loads = [make_load(id="S-1"),
                 make_load(id="S-2", status="cancelled",
                           pickup_at=datetime(2026, 11, 19, 10, 0,
                                              tzinfo=timezone.utc),
                           delivery_at=datetime(2026, 11, 19, 15, 0,
                                                tzinfo=timezone.utc))]
        findings = dsd.audit(loads, AS_OF)
        self.assertNotIn("double-booked-driver", rules(findings))
        self.assertNotIn("double-booked-truck", rules(findings))
        self.assertNotIn("no-travel-gap", rules(findings))

    def test_delivered_load_skips_double_booking(self):
        loads = [make_load(id="S-1", status="delivered",
                           pickup_at=datetime(2026, 11, 10, 8, 0,
                                              tzinfo=timezone.utc),
                           delivery_at=datetime(2026, 11, 10, 16, 0,
                                                tzinfo=timezone.utc)),
                 make_load(id="S-2",
                           pickup_at=datetime(2026, 11, 10, 10, 0,
                                              tzinfo=timezone.utc),
                           delivery_at=datetime(2026, 11, 10, 15, 0,
                                                tzinfo=timezone.utc))]
        findings = dsd.audit(loads, AS_OF)
        self.assertNotIn("double-booked-driver", rules(findings))

    def test_missing_broker_warning(self):
        findings = dsd.audit([make_load(broker="")], AS_OF)
        flagged = [item for item in findings
                   if item.rule == "missing-broker"]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0].severity, "warning")

    def test_missing_truck_warning(self):
        findings = dsd.audit([make_load(truck="")], AS_OF)
        flagged = [item for item in findings
                   if item.rule == "missing-truck"]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0].severity, "warning")

    def test_missing_rate_warning(self):
        findings = dsd.audit([make_load(rate=None)], AS_OF)
        flagged = [item for item in findings
                   if item.rule == "missing-rate"]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0].severity, "warning")

    def test_invalid_rate_has_no_missing_rate_warning(self):
        findings = dsd.audit(
            [make_load(rate=None, rate_invalid=True)], AS_OF)
        self.assertNotIn("missing-rate", rules(findings))

    def test_unknown_status_warning_and_treated_active(self):
        loads = [make_load(id="S-1"),
                 make_load(id="S-2", status="holding",
                           pickup_at=datetime(2026, 11, 19, 10, 0,
                                              tzinfo=timezone.utc),
                           delivery_at=datetime(2026, 11, 19, 15, 0,
                                                tzinfo=timezone.utc))]
        findings = dsd.audit(loads, AS_OF)
        self.assertIn("unknown-status", rules(findings))
        self.assertIn("double-booked-driver", rules(findings))

    def test_clean_load_has_no_findings(self):
        self.assertEqual(dsd.audit([make_load()], AS_OF), [])

    def test_unassigned_load_not_in_gap_check(self):
        loads = [make_load(id="S-1", driver=""),
                 make_load(id="S-2", driver="",
                           pickup_at=datetime(2026, 11, 19, 19, 0,
                                              tzinfo=timezone.utc),
                           delivery_at=datetime(2026, 11, 20, 3, 0,
                                                tzinfo=timezone.utc))]
        findings = dsd.audit(loads, AS_OF)
        self.assertNotIn("no-travel-gap", rules(findings))


class TestSummarize(unittest.TestCase):
    def test_summary_counts(self):
        loads = [make_load(id="S-1"),
                 make_load(id="S-2", driver="",
                           pickup_at=datetime(2026, 11, 19, 10, 0,
                                              tzinfo=timezone.utc),
                           delivery_at=datetime(2026, 11, 19, 15, 0,
                                                tzinfo=timezone.utc))]
        findings = dsd.audit(loads, AS_OF)
        summary = dsd.summarize(loads, findings)
        self.assertEqual(summary["loads"], 2)
        self.assertEqual(summary["unassigned_loads"], 1)
        self.assertEqual(summary["total_rate"], Decimal("2400.00"))
        self.assertEqual(summary["errors"],
                         sum(1 for item in findings
                             if item.severity == "error"))

    def test_per_driver_table(self):
        loads = [make_load(id="S-1"),
                 make_load(id="S-2", driver="Bob Chen",
                           truck="T-102", rate=Decimal("500.00"),
                           pickup_at=datetime(2026, 11, 20, 8, 0,
                                              tzinfo=timezone.utc),
                           delivery_at=datetime(2026, 11, 20, 16, 0,
                                                tzinfo=timezone.utc))]
        summary = dsd.summarize(loads, [])
        rows = {row["driver"]: row
                for row in summary["per_driver"]}
        self.assertEqual(rows["Alice Reed"]["loads"], 1)
        self.assertEqual(rows["Alice Reed"]["total_rate"],
                         Decimal("1200.00"))
        self.assertEqual(rows["Bob Chen"]["total_rate"],
                         Decimal("500.00"))
        self.assertEqual(
            rows["Alice Reed"]["first_pickup"],
            datetime(2026, 11, 19, 8, 0, tzinfo=timezone.utc))
        self.assertEqual(
            rows["Alice Reed"]["last_delivery"],
            datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc))

    def test_unassigned_grouped_in_day_plan(self):
        loads = [make_load(driver="")]
        summary = dsd.summarize(loads, [])
        self.assertEqual(summary["per_driver"][0]["driver"],
                         "(unassigned)")

    def test_per_driver_sorted_by_name(self):
        loads = [make_load(driver="Zoe Fox"),
                 make_load(driver="Amy Pond")]
        summary = dsd.summarize(loads, [])
        names = [row["driver"] for row in summary["per_driver"]]
        self.assertEqual(names, ["Amy Pond", "Zoe Fox"])

    def test_invalid_rate_left_out_of_total(self):
        loads = [make_load(rate=None, rate_invalid=True),
                 make_load(id="S-2", rate=Decimal("500.00"))]
        summary = dsd.summarize(loads, [])
        self.assertEqual(summary["total_rate"], Decimal("500.00"))


class TestRender(unittest.TestCase):
    def test_text_report_clean(self):
        summary = dsd.summarize([make_load()], [])
        text = dsd.render_text([make_load()], [], summary)
        self.assertIn("Loads: 1, findings: 0", text)
        self.assertIn("All loads clean.", text)
        self.assertIn("Per-driver day plan:", text)

    def test_json_money_is_string(self):
        summary = dsd.summarize([make_load()], [])
        parsed = json.loads(
            dsd.render_json([make_load()], [], summary))
        self.assertEqual(parsed["total_rate"], "1200.00")
        self.assertEqual(parsed["per_driver"][0]["total_rate"],
                         "1200.00")
        self.assertEqual(parsed["per_driver"][0]["first_pickup"],
                         "2026-11-19 08:00")

    def test_json_findings_included(self):
        findings = dsd.audit([make_load(driver="")], AS_OF)
        summary = dsd.summarize([make_load(driver="")], findings)
        parsed = json.loads(
            dsd.render_json([make_load(driver="")], findings,
                            summary))
        self.assertEqual(len(parsed["findings"]), 1)
        self.assertEqual(parsed["findings"][0]["rule"],
                         "unassigned-load")


class TestCli(unittest.TestCase):
    def setUp(self):
        self.base = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        self.sample = os.path.join(
            self.base, "examples", "schedule.csv")
        self.clean = os.path.join(
            self.base, "examples", "clean.csv")
        self.warnings = os.path.join(
            self.base, "examples", "warnings-only.csv")

    def test_sample_counts(self):
        code, out, _ = run_cli(self.sample, "--as-of", "2026-11-18")
        self.assertEqual(code, 0)
        self.assertIn("Loads: 25, findings: 17, errors: 15, "
                      "warnings: 2", out)
        self.assertIn("Total rate: $18,350.00", out)
        self.assertIn("double-booked loads: 4", out)
        self.assertIn("unassigned loads: 1", out)
        self.assertIn("past pickups: 1", out)

    def test_sample_headline_cases(self):
        _, out, _ = run_cli(self.sample, "--as-of", "2026-11-18")
        self.assertIn("[double-booked-driver] S-102", out)
        self.assertIn("[double-booked-truck] S-124", out)
        self.assertIn("[unassigned-load] S-104", out)
        self.assertIn("[past-pickup] S-105", out)
        self.assertIn("[delivery-before-pickup] S-106", out)
        self.assertIn("[home-time-conflict] S-107", out)
        self.assertIn("[reset-conflict] S-110", out)
        self.assertIn("[no-travel-gap] S-108", out)
        self.assertIn("[no-travel-gap] S-112", out)
        self.assertIn("[duplicate-load] S-101", out)

    def test_clean_passes_check(self):
        code, out, _ = run_cli(self.clean, "--as-of", "2026-11-18",
                               "--check")
        self.assertEqual(code, 0)
        self.assertIn("Loads: 4, findings: 0", out)
        self.assertIn("Total rate: $4,000.00", out)

    def test_warnings_only_check_and_strict(self):
        code, out, _ = run_cli(self.warnings, "--as-of",
                               "2026-11-18", "--check")
        self.assertEqual(code, 0)
        self.assertIn("findings: 2, errors: 0, warnings: 2", out)
        code, _, _ = run_cli(self.warnings, "--as-of",
                             "2026-11-18", "--check", "--strict")
        self.assertEqual(code, 1)

    def test_sample_check_exits_1(self):
        code, _, _ = run_cli(self.sample, "--as-of", "2026-11-18",
                             "--check")
        self.assertEqual(code, 1)

    def test_strict_without_check_exits_0(self):
        code, _, _ = run_cli(self.sample, "--as-of", "2026-11-18",
                             "--strict")
        self.assertEqual(code, 0)

    def test_min_gap_hours_zero_drops_gap_errors(self):
        _, out, _ = run_cli(self.sample, "--as-of", "2026-11-18",
                            "--min-gap-hours", "0")
        self.assertIn("findings: 15, errors: 13", out)
        self.assertNotIn("no-travel-gap", out)

    def test_past_grace_hours_drops_past_pickup(self):
        _, out, _ = run_cli(self.sample, "--as-of", "2026-11-18",
                            "--past-grace-hours", "200")
        self.assertIn("findings: 16, errors: 14", out)
        self.assertIn("past pickups: 0", out)

    def test_json_output_parses(self):
        code, out, _ = run_cli(self.sample, "--as-of", "2026-11-18",
                               "--json")
        self.assertEqual(code, 0)
        parsed = json.loads(out)
        self.assertEqual(parsed["loads"], 25)
        self.assertEqual(len(parsed["findings"]), 17)
        self.assertEqual(parsed["total_rate"], "18350.00")

    def test_out_writes_file(self):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False)
        handle.close()
        try:
            code, out, _ = run_cli(
                self.sample, "--as-of", "2026-11-18",
                "--out", handle.name)
            self.assertEqual(code, 0)
            self.assertEqual(out, "")
            with open(handle.name, encoding="utf-8") as read:
                content = read.read()
            self.assertIn("Loads: 25, findings: 17", content)
        finally:
            os.unlink(handle.name)

    def test_bad_as_of_exits_2(self):
        code, _, err = run_cli(self.sample, "--as-of", "not-a-date")
        self.assertEqual(code, 2)
        self.assertIn("not a readable date", err)

    def test_missing_file_exits_2(self):
        code, _, err = run_cli("/tmp/no-such-dispatch-file.csv")
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)

    def test_negative_gap_exits_2(self):
        code, _, err = run_cli(self.sample, "--min-gap-hours", "-1")
        self.assertEqual(code, 2)

    def test_missing_load_id_column_exits_2(self):
        path = write_csv("driver,truck\nAlice,T-101\n")
        try:
            code, _, err = run_cli(path)
        finally:
            os.unlink(path)
        self.assertEqual(code, 2)
        self.assertIn("no load_id column", err)

    def test_version(self):
        self.assertEqual(dsd.__version__, "1.0.0")


if __name__ == "__main__":
    unittest.main()
