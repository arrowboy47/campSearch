"""A dry run of refresh_status must write nothing, including its audit row.

Background. A dry run was observed creating scrape_runs row 175 and marking it
`failed`, despite --dry-run being documented as "classify and print, write
nothing". Both halves of that are explained by the same thing:

  * the row existed at all, which the flag says it should not, and
  * _pipeline.scrape_run marks a run failed when
    `counters.errors and not counters.upserted`. A dry run upserts nothing by
    definition, so a single dead facility flips it. In a real weekly run over
    ~1800 facilities the upsert count is large, so one 404 does not fail it.

So the shared convention was already sensible and needed no per-script error
threshold. The fix is simply that a dry run writes no row.

These tests stub the database and the inter-facility sleep. An earlier version
called main() with only requests.Session mocked, which left both live, so it
queried real Postgres and slept per facility and hung for minutes.
"""

import sys
import types
from unittest import mock

import pytest

sys.path.insert(0, "scripts")
import refresh_status  # noqa: E402


class _Cur:
    def __init__(self, rows): self._rows = rows
    def execute(self, *a, **k): pass
    def fetchall(self): return self._rows
    def fetchone(self): return (1,)
    def close(self): pass


class _Conn:
    def __init__(self, rows): self._rows = rows; self.commits = 0
    def cursor(self, *a, **k): return _Cur(self._rows)
    def commit(self): self.commits += 1
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    """No database, no network, no sleeping."""
    rows = [(i, "Camp %d" % i, 1000 + i, True) for i in range(1, 4)]
    monkeypatch.setattr(refresh_status, "get_conn", lambda: _Conn(rows))
    monkeypatch.setattr(refresh_status.time, "sleep", lambda *_: None)
    monkeypatch.setattr(refresh_status, "requests", mock.MagicMock())
    return rows


def test_dry_run_creates_no_scrape_runs_row(monkeypatch):
    called = []
    monkeypatch.setattr(refresh_status, "scrape_run",
                        lambda src: called.append(src) or _unreachable())
    refresh_status.main(argv=["--dry-run", "--limit", "3"])
    assert called == [], (
        "--dry-run must not open a scrape_run; it documents that it writes "
        "nothing, and a dry run always has upserted=0 so any single error "
        "would mark the row failed and pollute the job history")


def _unreachable():
    raise AssertionError("scrape_run should not have been called")


def test_real_run_does_create_one_scrape_runs_row(monkeypatch):
    import contextlib
    opened = []

    @contextlib.contextmanager
    def fake_scrape_run(source):
        opened.append(source)
        yield refresh_status.RunCounters() if hasattr(
            refresh_status, "RunCounters") else types.SimpleNamespace(
                seen=0, upserted=0, errors=0, note=None)

    monkeypatch.setattr(refresh_status, "scrape_run", fake_scrape_run)
    refresh_status.main(argv=["--limit", "3"])
    assert opened == ["recreation_gov_status"], (
        "a non-dry run must open exactly one scrape_run")


def test_no_per_script_error_threshold_remains():
    """The shared convention in _pipeline decides status, not this script.

    A threshold here previously zeroed run.errors whenever it was not
    exceeded, which hid dead facilities entirely: the audit row reported 0
    errors while facilities were quietly failing.
    """
    src = open("scripts/refresh_status.py", encoding="utf-8").read()
    assert "ERROR_THRESHOLD_ABSOLUTE" not in src
    assert "ERROR_THRESHOLD_FRACTION" not in src


def test_every_facility_error_is_counted():
    """run.errors must be incremented per failure, never conditionally."""
    src = open("scripts/refresh_status.py", encoding="utf-8").read()
    assert "run.errors += 1" in src, (
        "facility errors must always increment the counter so the audit row "
        "reflects how many facilities failed")
