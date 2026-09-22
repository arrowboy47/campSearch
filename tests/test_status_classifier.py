"""Tests for the open/closed classifier in scripts/refresh_status.py.

Why this file exists: this is the one job that *infers* a fact rather than
copying one. The predecessor (`dynamic.py`) wrote a hardcoded is_open and made
the search facet actively misleading, which is why the rewrite carries an
explicit instruction not to guess. These tests pin the abstentions as hard as
the verdicts -- returning None for ambiguous evidence is the feature, and a
future "improvement" that turns a shrug into a closure should fail here.
"""

import datetime

import pytest

from refresh_status import (
    HORIZON_DAYS,
    MIN_EVIDENCE,
    classify,
    month_starts,
    window_statuses,
)

TODAY = datetime.date(2026, 9, 21)


def days(status, n, start=TODAY):
    """n consecutive site-days all carrying the same status."""
    return [(start + datetime.timedelta(days=i), status) for i in range(n)]


class TestOpenVerdicts:
    def test_available_days_mean_open(self):
        is_open, detail = classify(days("Available", 5), reservable=True)
        assert is_open is True
        assert "bookable" in detail

    def test_fully_booked_still_means_open(self):
        # every site reserved is a popular campground, not a shut one
        assert classify(days("Reserved", 30), reservable=True)[0] is True

    def test_one_bookable_day_outranks_many_closed_ones(self):
        # partial closures are normal: a loop shuts, the campground runs on
        mixed = days("Closed", 40) + days("Available", 2)
        assert classify(mixed, reservable=True)[0] is True

    def test_open_verdict_does_not_need_the_reservable_flag(self):
        # a bookable day is direct evidence; the flag only matters for the
        # ambiguous path
        assert classify(days("Available", 3), reservable=False)[0] is True


class TestClosedVerdicts:
    def test_explicit_closed_with_nothing_bookable(self):
        is_open, detail = classify(days("Closed", 20), reservable=True)
        assert is_open is False
        assert "explicitly closed" in detail

    def test_explicit_closed_counts_even_for_a_first_come_facility(self):
        assert classify(days("Closed", 20), reservable=False)[0] is False

    def test_reservable_facility_with_nothing_bookable_is_closed(self):
        # the seasonal-closure shape: a campground that takes bookings, with
        # nothing bookable for the whole window
        is_open, detail = classify(days("Not Reservable", 40), reservable=True)
        assert is_open is False
        assert "nothing bookable" in detail


class TestAbstentions:
    def test_no_data_is_unknown(self):
        is_open, detail = classify([], reservable=True)
        assert is_open is None
        assert "no availability data" in detail

    def test_first_come_facility_is_never_called_closed_on_not_reservable(self):
        # THE important one: "Not Reservable" is the permanent, normal state of
        # a first-come campground. Reading it as closed would wrongly shut
        # hundreds of perfectly open sites out of the facet.
        assert classify(days("Not Reservable", 40), reservable=False)[0] is None

    def test_nyr_alone_is_unknown_not_closed(self):
        # the booking window simply has not opened yet
        is_open, detail = classify(days("NYR", 40), reservable=True)
        assert is_open is None
        assert "not open yet" in detail

    def test_thin_evidence_does_not_support_a_closure(self):
        thin = days("Not Reservable", MIN_EVIDENCE - 1)
        assert classify(thin, reservable=True)[0] is None

    def test_evidence_threshold_is_inclusive(self):
        enough = days("Not Reservable", MIN_EVIDENCE)
        assert classify(enough, reservable=True)[0] is False

    def test_an_unrecognised_status_never_becomes_a_verdict(self):
        # the vocabulary is undocumented and can grow; a new word must not be
        # silently read as either open or closed
        assert classify(days("Pending Maintenance", 40), reservable=True)[0] is None

    def test_unknown_status_mixed_with_not_reservable_abstains(self):
        mixed = days("Not Reservable", 20) + days("Brand New State", 5)
        assert classify(mixed, reservable=True)[0] is None

    def test_detail_is_always_populated(self):
        for statuses, reservable in [
            ([], True),
            (days("NYR", 3), True),
            (days("Available", 3), True),
            (days("Closed", 20), True),
        ]:
            _, detail = classify(statuses, reservable=reservable)
            assert detail and isinstance(detail, str)


class TestMonthStarts:
    def test_covers_every_month_the_window_touches(self):
        out = month_starts(datetime.date(2026, 9, 21), horizon_days=30)
        assert out == [datetime.date(2026, 9, 1), datetime.date(2026, 10, 1)]

    def test_single_month_window(self):
        out = month_starts(datetime.date(2026, 9, 1), horizon_days=5)
        assert out == [datetime.date(2026, 9, 1)]

    def test_rolls_over_a_year_boundary(self):
        out = month_starts(datetime.date(2026, 12, 20), horizon_days=30)
        assert out == [datetime.date(2026, 12, 1), datetime.date(2027, 1, 1)]

    def test_spans_three_months_for_a_long_horizon(self):
        out = month_starts(datetime.date(2026, 9, 21), horizon_days=60)
        assert out == [
            datetime.date(2026, 9, 1),
            datetime.date(2026, 10, 1),
            datetime.date(2026, 11, 1),
        ]

    def test_february_does_not_trip_the_month_increment(self):
        out = month_starts(datetime.date(2027, 2, 15), horizon_days=30)
        assert out == [datetime.date(2027, 2, 1), datetime.date(2027, 3, 1)]


class TestWindowStatuses:
    def _payload(self, *day_status):
        return {
            "campsites": {
                "1": {"availabilities": {d: s for d, s in day_status}},
            }
        }

    def test_days_inside_the_window_are_kept(self):
        p = self._payload(("2026-09-25T00:00:00Z", "Available"))
        assert list(window_statuses(p, TODAY, 30)) == [
            (datetime.date(2026, 9, 25), "Available")
        ]

    def test_past_days_are_dropped(self):
        # old rows are never pruned upstream; counting yesterday's bookings as
        # evidence of today's status would keep a closed campground "open"
        p = self._payload(("2026-09-01T00:00:00Z", "Available"))
        assert list(window_statuses(p, TODAY, 30)) == []

    def test_days_past_the_horizon_are_dropped(self):
        p = self._payload(("2026-12-01T00:00:00Z", "Available"))
        assert list(window_statuses(p, TODAY, 30)) == []

    def test_today_is_included_and_the_end_is_exclusive(self):
        p = self._payload(
            ("2026-09-21T00:00:00Z", "Available"),
            ("2026-10-21T00:00:00Z", "Available"),
        )
        got = [d for d, _ in window_statuses(p, TODAY, 30)]
        assert datetime.date(2026, 9, 21) in got
        assert datetime.date(2026, 10, 21) not in got

    def test_every_site_contributes(self):
        p = {
            "campsites": {
                "1": {"availabilities": {"2026-09-25T00:00:00Z": "Available"}},
                "2": {"availabilities": {"2026-09-25T00:00:00Z": "Reserved"}},
            }
        }
        assert len(list(window_statuses(p, TODAY, 30))) == 2

    def test_empty_and_missing_keys_are_survivable(self):
        assert list(window_statuses({}, TODAY, 30)) == []
        assert list(window_statuses({"campsites": {}}, TODAY, 30)) == []
        assert list(window_statuses({"campsites": {"1": {}}}, TODAY, 30)) == []

    def test_null_campsites_does_not_raise(self):
        # the endpoint returns null rather than {} for some facilities
        assert list(window_statuses({"campsites": None}, TODAY, 30)) == []

    def test_a_malformed_date_is_skipped_not_fatal(self):
        p = self._payload(("not-a-date", "Available"),
                          ("2026-09-25T00:00:00Z", "Available"))
        assert len(list(window_statuses(p, TODAY, 30))) == 1


def test_horizon_default_is_sane():
    assert 7 <= HORIZON_DAYS <= 90
