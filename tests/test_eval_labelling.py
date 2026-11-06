"""Tests for the /eval relevance-labelling tooling.

Runs without a database: db access is monkeypatched at the app boundary.
"""

import re

import pytest

import app as app_module


@pytest.fixture
def client(monkeypatch):
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


@pytest.fixture
def admin(client, monkeypatch):
    monkeypatch.setattr(app_module, "current_user",
                        lambda: {"id": 7, "username": "tester", "is_admin": True})
    return client


def _row(cid=1, name="Test Camp"):
    return {
        "id": cid, "name": name, "forest_name": "inyo", "num_sites": 12,
        "is_free": True, "reservation_type": "first-come", "elevation_ft": 5000,
        "toilet_type": "vault", "water_feature": "lake", "activities": ["fishing"],
        "relevance": 100.0, "name_score": 0.0, "unknown_attrs": (),
    }


# ========== access control ==========

def test_eval_index_is_404_for_anonymous(client, monkeypatch):
    """Non-admins get 404, not 403: a 403 confirms the page exists."""
    monkeypatch.setattr(app_module, "current_user", lambda: None)
    assert client.get("/eval").status_code == 404


def test_eval_index_is_404_for_non_admin(client, monkeypatch):
    monkeypatch.setattr(app_module, "current_user",
                        lambda: {"id": 2, "username": "nobody", "is_admin": False})
    assert client.get("/eval").status_code == 404
    assert client.get("/eval/1").status_code == 404


def test_judge_endpoint_is_404_for_non_admin(client, monkeypatch):
    monkeypatch.setattr(app_module, "current_user",
                        lambda: {"id": 2, "is_admin": False})
    r = client.post("/api/eval/judge", json={"query_id": 1, "campsite_id": 1, "grade": 2})
    assert r.status_code == 404


# ========== CSRF ==========

def test_judge_rejects_a_missing_csrf_token(admin, monkeypatch):
    """The blanket /api/ CSRF exemption exists for sendBeacon, which cannot
    set headers. This endpoint is a fetch, so it must not inherit it.
    """
    monkeypatch.setattr(app_module, "get_eval_query", lambda qid: {"id": qid})
    r = admin.post("/api/eval/judge",
                   json={"query_id": 1, "campsite_id": 1, "grade": 2})
    assert r.status_code == 400
    assert "csrf" in r.get_json()["error"].lower()


def test_judge_rejects_a_wrong_csrf_token(admin, monkeypatch):
    monkeypatch.setattr(app_module, "get_eval_query", lambda qid: {"id": qid})
    with admin.session_transaction() as sess:
        sess["_csrf"] = "the-real-token"
    r = admin.post("/api/eval/judge",
                   json={"query_id": 1, "campsite_id": 1, "grade": 2},
                   headers={"X-CSRFToken": "not-the-token"})
    assert r.status_code == 400


# ========== judging ==========

def test_judge_records_a_grade(admin, monkeypatch):
    saved = {}
    monkeypatch.setattr(app_module, "get_eval_query", lambda qid: {"id": qid})
    monkeypatch.setattr(app_module, "upsert_judgment",
                        lambda q, c, g, judged_by=None: saved.update(
                            query=q, campsite=c, grade=g, by=judged_by))
    with admin.session_transaction() as sess:
        sess["_csrf"] = "tok"

    r = admin.post("/api/eval/judge",
                   json={"query_id": 3, "campsite_id": 42, "grade": 2},
                   headers={"X-CSRFToken": "tok"})
    assert r.status_code == 200
    assert saved == {"query": 3, "campsite": 42, "grade": 2, "by": 7}


def test_null_grade_deletes_rather_than_scoring_zero(admin, monkeypatch):
    """Unjudged and 'wrong' are different claims.

    An unjudged pair is excluded from the metrics; a 0 asserts the result is
    wrong. Conflating them would let "I have not looked at this" count as
    evidence against the ranking.
    """
    calls = []
    monkeypatch.setattr(app_module, "get_eval_query", lambda qid: {"id": qid})
    monkeypatch.setattr(app_module, "delete_judgment",
                        lambda q, c: calls.append(("delete", q, c)))
    monkeypatch.setattr(app_module, "upsert_judgment",
                        lambda *a, **k: calls.append(("upsert",)))
    with admin.session_transaction() as sess:
        sess["_csrf"] = "tok"

    r = admin.post("/api/eval/judge",
                   json={"query_id": 3, "campsite_id": 42, "grade": None},
                   headers={"X-CSRFToken": "tok"})
    assert r.status_code == 200
    assert calls == [("delete", 3, 42)]


def test_judge_rejects_non_integer_ids(admin, monkeypatch):
    monkeypatch.setattr(app_module, "get_eval_query", lambda qid: {"id": qid})
    with admin.session_transaction() as sess:
        sess["_csrf"] = "tok"
    r = admin.post("/api/eval/judge",
                   json={"query_id": "3", "campsite_id": 42, "grade": 2},
                   headers={"X-CSRFToken": "tok"})
    assert r.status_code == 400


def test_judge_404s_on_an_unknown_query(admin, monkeypatch):
    monkeypatch.setattr(app_module, "get_eval_query", lambda qid: None)
    with admin.session_transaction() as sess:
        sess["_csrf"] = "tok"
    r = admin.post("/api/eval/judge",
                   json={"query_id": 999, "campsite_id": 1, "grade": 2},
                   headers={"X-CSRFToken": "tok"})
    assert r.status_code == 404


# ========== the labelling page ==========

def _patch_label_page(monkeypatch, results, graded=None):
    monkeypatch.setattr(app_module, "get_eval_query", lambda qid: {
        "id": qid, "query_text": "lake fishing", "intent": None,
        "kind": "graded", "expect_campsite_id": None, "notes": None,
        "created_at": None, "retired_at": None,
    })
    monkeypatch.setattr(app_module, "search_campsites",
                        lambda **kw: results)
    monkeypatch.setattr(app_module, "judgments_for_query",
                        lambda qid: dict(graded or {}))
    monkeypatch.setattr(app_module, "list_eval_queries",
                        lambda include_retired=False: [{"id": 1}])
    monkeypatch.setattr(app_module, "get_campsite_by_id", lambda cid: _row(cid))


def test_label_page_hides_scores_by_default(admin, monkeypatch):
    """Anchoring bias: showing "rank 1" makes a human assume it is good, and
    that assumption would be baked into the judgments.
    """
    _patch_label_page(monkeypatch, [_row(i, "Camp %d" % i) for i in range(1, 11)])
    body = admin.get("/eval/1").data.decode()
    assert 'class="jq-rank"' not in body
    assert not re.search(r"relevance \d", body)


def test_label_page_shows_scores_when_asked(admin, monkeypatch):
    _patch_label_page(monkeypatch, [_row(i, "Camp %d" % i) for i in range(1, 11)])
    body = admin.get("/eval/1?reveal=1").data.decode()
    assert body.count('class="jq-rank"') == 10


def test_label_page_order_is_stable_across_reloads(admin, monkeypatch):
    """The shuffle is seeded on the query id, so the page must not reshuffle
    under someone who is part-way through a pass.
    """
    _patch_label_page(monkeypatch, [_row(i, "Camp %d" % i) for i in range(1, 11)])
    first = re.findall(r'data-campsite-id="(\d+)"', admin.get("/eval/1").data.decode())
    second = re.findall(r'data-campsite-id="(\d+)"', admin.get("/eval/1").data.decode())
    assert first == second
    assert len(first) == 10


def test_label_page_shuffles_away_from_the_true_ranking(admin, monkeypatch):
    _patch_label_page(monkeypatch, [_row(i, "Camp %d" % i) for i in range(1, 11)])
    shown = re.findall(r'data-campsite-id="(\d+)"', admin.get("/eval/1").data.decode())
    assert shown != [str(i) for i in range(1, 11)]
    assert sorted(shown, key=int) == list(range(1, 11)) or sorted(map(int, shown)) == list(range(1, 11))


def test_existing_grades_are_rendered(admin, monkeypatch):
    _patch_label_page(monkeypatch,
                      [_row(i, "Camp %d" % i) for i in range(1, 4)],
                      graded={2: 2})
    body = admin.get("/eval/1").data.decode()
    assert 'data-campsite-id="2"' in body
    assert 'data-grade="2"' in body


def test_previously_judged_but_no_longer_returned_is_surfaced(admin, monkeypatch):
    """A campsite you called ideal falling out of the results is a regression
    signal, so it must not just vanish from the page.
    """
    _patch_label_page(monkeypatch, [_row(1, "Still here")], graded={1: 1, 77: 2})
    body = admin.get("/eval/1").data.decode()
    assert "Judged before, not returned now" in body


def test_label_page_survives_a_search_failure(admin, monkeypatch):
    """A broken retriever must not make the labelling page unreachable."""
    _patch_label_page(monkeypatch, [])
    def _explode(**kw):
        raise RuntimeError("retriever down")
    monkeypatch.setattr(app_module, "search_campsites", _explode)
    assert admin.get("/eval/1").status_code == 200


def test_unknown_query_404s(admin, monkeypatch):
    monkeypatch.setattr(app_module, "get_eval_query", lambda qid: None)
    assert admin.get("/eval/4242").status_code == 404
