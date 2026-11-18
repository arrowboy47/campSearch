"""Tests for admin metadata editing and the eval-page weather lookup.

No database: db access is monkeypatched at the app boundary, and the
coercion rules are tested directly.
"""

import pytest

import app as app_module
import db as db_module


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


@pytest.fixture
def admin(client, monkeypatch):
    monkeypatch.setattr(app_module, "current_user",
                        lambda: {"id": 7, "username": "tester", "is_admin": True})
    with client.session_transaction() as sess:
        sess["_csrf"] = "tok"
    return client


H = {"X-CSRFToken": "tok"}


# ========== access ==========

def test_metadata_is_404_for_non_admin(client, monkeypatch):
    monkeypatch.setattr(app_module, "current_user", lambda: {"id": 2, "is_admin": False})
    assert client.get("/api/eval/campsite/1/metadata").status_code == 404
    assert client.post("/api/eval/campsite/1/metadata", json={"name": "x"},
                       headers=H).status_code == 404


def test_weather_is_404_for_non_admin(client, monkeypatch):
    monkeypatch.setattr(app_module, "current_user", lambda: None)
    assert client.get("/api/eval/weather?ids=1").status_code == 404


def test_metadata_post_requires_csrf(admin, monkeypatch):
    called = []
    monkeypatch.setattr(app_module, "update_campsite_metadata",
                        lambda *a, **k: called.append(1) or {})
    r = admin.post("/api/eval/campsite/1/metadata", json={"name": "x"})
    assert r.status_code == 400
    r = admin.post("/api/eval/campsite/1/metadata", json={"name": "x"},
                   headers={"X-CSRFToken": "wrong"})
    assert r.status_code == 400
    assert called == []


# ========== endpoint behaviour ==========

def test_post_returns_changed_and_fresh_fields(admin, monkeypatch):
    monkeypatch.setattr(app_module, "update_campsite_metadata",
                        lambda cid, ch, admin_id: {"num_sites": 12})
    monkeypatch.setattr(app_module, "get_editable_metadata",
                        lambda cid: {"num_sites": 12, "name": "A"})
    r = admin.post("/api/eval/campsite/1/metadata", json={"num_sites": "12"}, headers=H)
    assert r.status_code == 200
    assert r.get_json()["changed"] == {"num_sites": 12}


def test_post_maps_validation_error_to_400_and_missing_to_404(admin, monkeypatch):
    def bad(cid, ch, admin_id): raise ValueError("num_sites must be a number")
    monkeypatch.setattr(app_module, "update_campsite_metadata", bad)
    r = admin.post("/api/eval/campsite/1/metadata", json={"num_sites": "x"}, headers=H)
    assert r.status_code == 400 and "number" in r.get_json()["error"]

    def gone(cid, ch, admin_id): raise LookupError("no such campsite")
    monkeypatch.setattr(app_module, "update_campsite_metadata", gone)
    assert admin.post("/api/eval/campsite/9/metadata", json={"name": "x"},
                      headers=H).status_code == 404


def test_post_rejects_non_object_body(admin):
    assert admin.post("/api/eval/campsite/1/metadata", json=[1], headers=H).status_code == 400
    assert admin.post("/api/eval/campsite/1/metadata", json={}, headers=H).status_code == 400


# ========== coercion and whitelist ==========

@pytest.mark.parametrize("field,kind,raw,expected", [
    ("n", "text", "  hi ", "hi"),
    ("n", "text", "", None),
    ("n", "int", "12", 12),
    ("n", "int", "", None),
    ("f", "num", "7.5", 7.5),
    ("b", "bool", "true", True),
    ("b", "bool", False, False),
    ("b", "bool", "", None),
    ("a", "list", "Fishing, hiking ,", ["fishing", "hiking"]),
    ("a", "list", "", None),
])
def test_coerce_edit_accepts(field, kind, raw, expected):
    assert db_module._coerce_edit(field, kind, raw) == expected


@pytest.mark.parametrize("kind,raw", [
    ("int", "abc"), ("int", "1.5"), ("int", "-3"), ("num", "-1"), ("bool", "maybe"),
])
def test_coerce_edit_rejects(kind, raw):
    with pytest.raises(ValueError):
        db_module._coerce_edit("f", kind, raw)


def test_update_rejects_unknown_and_blank_name_before_touching_db(monkeypatch):
    monkeypatch.setattr(db_module, "get_connection",
                        lambda: pytest.fail("must not open a connection"))
    with pytest.raises(ValueError):
        db_module.update_campsite_metadata(1, {"id": 5}, admin_id=1)
    with pytest.raises(ValueError):
        db_module.update_campsite_metadata(1, {"name; DROP TABLE users": "x"}, admin_id=1)
    with pytest.raises(ValueError):
        db_module.update_campsite_metadata(1, {"name": "  "}, admin_id=1)


def test_unchanged_values_are_not_written(monkeypatch):
    monkeypatch.setattr(db_module, "get_editable_metadata",
                        lambda cid: {"name": "A", "num_sites": 5})
    monkeypatch.setattr(db_module, "get_connection",
                        lambda: pytest.fail("no write expected"))
    assert db_module.update_campsite_metadata(1, {"name": "A", "num_sites": "5"}, 1) == {}


# ========== weather ==========

def test_weather_uses_cache_and_skips_live_call(admin, monkeypatch):
    monkeypatch.setattr(app_module, "cached_forecasts", lambda ids, d: {
        1: {"date": "2026-11-15", "temp_max": 70, "temp_min": 40,
            "precipitation_total": 0, "cloud_cover_afternoon": 10}})
    monkeypatch.setattr(app_module, "get_forecast",
                        lambda *a: pytest.fail("cache hit must not call the API"))
    r = admin.get("/api/eval/weather?ids=1&date=2026-11-15")
    w = r.get_json()["weather"]["1"]
    assert w["high"] == 70 and w["source"] == "cache" and w["sky"] == "mostly clear"


def test_weather_live_calls_are_capped(admin, monkeypatch):
    monkeypatch.setattr(app_module, "cached_forecasts", lambda ids, d: {})
    monkeypatch.setattr(app_module, "get_campsite_by_id",
                        lambda cid: {"id": cid, "latitude": 1, "longitude": 1})
    monkeypatch.setattr(app_module.geo, "effective_coords", lambda c: (1.0, 1.0, False))
    calls = []
    monkeypatch.setattr(app_module, "get_forecast", lambda *a: calls.append(1) or {
        "date": "d", "temp_max": 1, "temp_min": 0,
        "precipitation_total": 0, "cloud_cover_afternoon": 0})
    ids = ",".join(str(i) for i in range(1, 31))
    out = admin.get("/api/eval/weather?ids=%s&date=2026-11-15" % ids).get_json()["weather"]
    assert len(calls) == app_module._EVAL_WEATHER_MAX_LIVE
    assert out["30"]["error"] == "not cached"


def test_weather_rejects_bad_input(admin):
    assert admin.get("/api/eval/weather?ids=a,b").status_code == 400
    assert admin.get("/api/eval/weather?ids=1&date=nope").status_code == 400
