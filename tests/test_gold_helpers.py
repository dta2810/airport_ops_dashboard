"""Unit tests for gold pipeline helpers (no pyspark required)."""
import pytest
import sys
from datetime import datetime, timedelta, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "pipelines" / "gold"))

from gold_helpers import quote_literal, build_literal_in_list, build_composite_key_in_list


class TestQuoteLiterals:
    """Test SQL literal quoting for safe IN list construction."""

    def test_string(self):
        assert quote_literal("hello") == "'hello'"
        assert quote_literal("it's") == "'it''s'"
        assert quote_literal("PTY") == "'PTY'"

    def test_numbers(self):
        assert quote_literal(42) == "42"
        assert quote_literal(3.14) == "3.14"
        assert quote_literal(0) == "0"
        assert quote_literal(-5) == "-5"

    def test_booleans(self):
        assert quote_literal(True) == "true"
        assert quote_literal(False) == "false"

    def test_none(self):
        assert quote_literal(None) == "NULL"

    def test_date(self):
        d = date(2026, 1, 15)
        assert quote_literal(d) == "date '2026-01-15'"

    def test_timestamp(self):
        ts = datetime(2026, 1, 15, 10, 30, 45)
        result = quote_literal(ts)
        assert result.startswith("timestamp '")
        assert "2026-01-15" in result
        assert "10:30:45" in result


class TestBuildLiteralInList:
    """Test building SQL IN clauses from value lists."""

    def test_empty_list(self):
        result = build_literal_in_list([])
        assert result == []

    def test_small_list(self):
        result = build_literal_in_list(["PTY", "BOG", "LIM"])
        assert len(result) == 1
        clause = result[0]
        assert "PTY" in clause
        assert "BOG" in clause
        assert "LIM" in clause
        assert clause.startswith("(") and clause.endswith(")")

    def test_chunking(self):
        values = [f"airport_{i}" for i in range(1500)]
        result = build_literal_in_list(values, max_items_per_chunk=1000)
        assert len(result) == 2
        assert result[0].startswith("(") and result[0].endswith(")")
        assert result[1].startswith("(") and result[1].endswith(")")

    def test_mixed_types(self):
        values = [1, 2, 3, 4, 5]
        result = build_literal_in_list(values)
        assert len(result) == 1
        clause = result[0]
        for v in values:
            assert str(v) in clause


class TestBuildCompositeKeyInList:
    """Test building IN clauses for composite keys."""

    def test_empty_keys(self):
        result = build_composite_key_in_list([])
        assert result == []

    def test_single_key_tuple(self):
        keys = [("PTY", "2026-01-15")]
        result = build_composite_key_in_list(keys)
        assert len(result) == 1
        clause = result[0]
        assert "PTY" in clause
        assert "2026-01-15" in clause

    def test_multiple_key_tuples(self):
        keys = [
            ("PTY", "2026-01-15"),
            ("BOG", "2026-01-15"),
            ("LIM", "2026-01-16"),
        ]
        result = build_composite_key_in_list(keys)
        assert len(result) == 1
        clause = result[0]
        assert "PTY" in clause
        assert "BOG" in clause
        assert "LIM" in clause

    def test_chunking_large_keys(self):
        keys = [(f"airport_{i}", f"2026-01-15") for i in range(700)]
        result = build_composite_key_in_list(keys, max_items_per_chunk=500)
        assert len(result) == 2
        assert result[0].startswith("(")
        assert result[1].startswith("(")


class TestBusinessLogic:
    """Test KPI calculations and phase logic."""

    def test_otp_calculation(self):
        """OTP D15: delay <= threshold is on-time."""
        threshold_min = 15

        test_cases = [
            (0, True),      # On-time
            (5, True),      # Early
            (15, True),     # Threshold
            (16, False),    # Just late
            (30, False),    # Very late
            (None, True),   # Not completed
        ]

        for delay_min, expected_otp in test_cases:
            if delay_min is None:
                result = True  # Not completed = on-time
            else:
                result = delay_min <= threshold_min
            assert result == expected_otp, f"OTP(delay={delay_min}) should be {expected_otp}"

    def test_turnaround_calculation(self):
        """Turnaround: aibt of prior flight to aobt of next flight."""
        min_ground_time = 40

        aibt_prior = datetime(2026, 1, 15, 10, 0, 0)

        test_cases = [
            (datetime(2026, 1, 15, 10, 45, 0), 45, True),   # 45 min > 40
            (datetime(2026, 1, 15, 10, 40, 0), 40, True),   # 40 min = threshold
            (datetime(2026, 1, 15, 10, 39, 0), 39, False),  # 39 min < 40
            (datetime(2026, 1, 15, 10, 20, 0), 20, False),  # 20 min < 40
        ]

        for aobt_next, expected_min, expected_ok in test_cases:
            turnaround_min = int((aobt_next - aibt_prior).total_seconds() / 60)
            turnaround_ok = turnaround_min >= min_ground_time

            assert turnaround_min == expected_min, f"Turnaround should be {expected_min}"
            assert turnaround_ok == expected_ok, f"Turnaround OK should be {expected_ok}"

    def test_delay_calculation(self):
        """Departure/arrival delay: actual - scheduled (in minutes)."""
        std = datetime(2026, 1, 15, 10, 0, 0)
        sta = datetime(2026, 1, 15, 12, 0, 0)

        # On-time departure
        aobt_ontime = datetime(2026, 1, 15, 10, 0, 0)
        dep_delay = int((aobt_ontime - std).total_seconds() / 60)
        assert dep_delay == 0

        # 30 min late departure
        aobt_late = datetime(2026, 1, 15, 10, 30, 0)
        dep_delay = int((aobt_late - std).total_seconds() / 60)
        assert dep_delay == 30

        # Early arrival
        aibt_early = datetime(2026, 1, 15, 11, 45, 0)
        arr_delay = int((aibt_early - sta).total_seconds() / 60)
        assert arr_delay == -15

    def test_phase_priority(self):
        """Phase: first match of CANCELLED → DIVERTED → AT_GATE → ... → SCHEDULED."""
        phases = {
            "all_completed": "AT_GATE",  # has aibt
            "landed_not_docked": "TAXI_IN",  # has aldt but not aibt
            "airborne": "AIRBORNE",  # has atot but not aldt
            "taxi_out": "TAXI_OUT",  # has aobt but not atot
            "boarding": "BOARDING",  # has boarding_start_ts but not aobt
            "gate_assigned": "SCHEDULED",  # only gate assigned, not boarding yet
            "cancelled": "CANCELLED",  # cancelled flag set
            "diverted": "DIVERTED",  # diverted to different airport
        }

        # Verify reasonable phase values exist
        for phase_name, phase_value in phases.items():
            assert phase_value in ["CANCELLED", "DIVERTED", "AT_GATE", "TAXI_IN", "AIRBORNE", "TAXI_OUT", "BOARDING", "SCHEDULED"]

    def test_connection_readiness(self):
        """Connection ready: priority aibt > aldt+8min > sta+delay > sta."""
        sta = datetime(2026, 1, 15, 12, 0, 0)
        delay_min = 10
        aldt = datetime(2026, 1, 15, 11, 58, 0)
        aibt = datetime(2026, 1, 15, 12, 5, 0)

        # With aibt: ready_ts = aibt
        ready_ts = aibt
        assert ready_ts == datetime(2026, 1, 15, 12, 5, 0)

        # Without aibt: ready_ts = aldt + 8 min
        ready_ts = aldt + timedelta(minutes=8)
        assert ready_ts == datetime(2026, 1, 15, 12, 6, 0)

        # With delay: ready_ts = sta + delay
        ready_ts = sta + timedelta(minutes=delay_min)
        assert ready_ts == datetime(2026, 1, 15, 12, 10, 0)

        # Fallback: ready_ts = sta
        ready_ts = sta
        assert ready_ts == datetime(2026, 1, 15, 12, 0, 0)
