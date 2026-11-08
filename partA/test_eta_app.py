"""Part A tests. Run from repo root:  python -m pytest partA -v"""
import re
import uuid

import pytest
from fastapi.testclient import TestClient

import eta_app

client = TestClient(eta_app.app)
URL = "/v1/models/eta:predict"
ROW = dict(distance_km=30, n_stops=5, hour=12, weekday=2, traffic_index=0.5, rain=0, weight_kg=10)


def row(**kw):
    r = dict(ROW)
    r.update(kw)
    return r


def post(rows, **kw):
    return client.post(URL, json={"instances": rows}, **kw)


def counter(text, path, status):
    m = re.search(r'eta_requests_total\{path="%s",status="%s"\} (\d+)' % (re.escape(path), status), text)
    return int(m.group(1)) if m else 0


def test_valid_request():
    r = post([ROW])
    assert r.status_code == 200
    j = r.json()
    assert len(j["predictions"]) == 1 and isinstance(j["predictions"][0], float)
    assert j["predictions"][0] == round(j["predictions"][0], 1)
    assert j["model_version"] == eta_app.VERSION
    assert j["request_id"] == r.headers["X-Request-ID"]


def test_batch_order_preserved():
    rows = [row(distance_km=5), row(distance_km=60), row(distance_km=120, n_stops=3)]
    batch = post(rows).json()["predictions"]
    singles = [post([x]).json()["predictions"][0] for x in rows]
    assert batch == singles
    assert batch[0] < batch[1] < batch[2]          # order really is the input order


@pytest.mark.parametrize("bad", [
    row(distance_km=0),            # below range
    row(distance_km=501),          # above range
    row(rain=2),                   # not 0/1
    row(weight_kg=0),              # must be > 0
    row(hour=24),
    row(traffic_index=1.5),
])
def test_out_of_range_rejected(bad):
    r = post([bad])
    assert r.status_code == 422
    assert r.json()["error"] == "validation_failed"


def test_wrong_type_rejected():
    assert post([row(distance_km="far")]).status_code == 422


def test_missing_field_rejected():
    bad = dict(ROW)
    del bad["weight_kg"]
    r = post([bad])
    assert r.status_code == 422
    assert any("weight_kg" in d["field"] for d in r.json()["details"])


def test_validation_error_body_shape():
    r = post([row(hour=99)])
    j = r.json()
    assert set(j) == {"error", "details", "request_id"}
    assert j["details"] and {"field", "msg"} <= set(j["details"][0])
    assert j["request_id"] == r.headers["X-Request-ID"]


def test_cross_field_infeasible():
    r = post([row(distance_km=151, n_stops=21)])
    assert r.status_code == 422
    assert "infeasible" in r.text
    # boundaries that are still feasible
    assert post([row(distance_km=150, n_stops=21)]).status_code == 200
    assert post([row(distance_km=151, n_stops=20)]).status_code == 200


def test_batch_size_limits():
    assert post([]).status_code == 422                 # 0 rows: too small
    assert post([ROW] * 501).status_code == 422        # 501 rows: too big
    assert post([ROW] * 500).status_code == 200        # exactly the max is fine
    assert post([ROW] * 1).status_code == 200          # exactly the min is fine


def test_request_id_echoed():
    r = post([ROW], headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"
    assert r.json()["request_id"] == "abc-123"


def test_request_id_generated_is_uuid():
    r = post([ROW])
    uuid.UUID(r.headers["X-Request-ID"])               # raises if not a UUID


def test_request_id_in_error_response():
    r = post([row(hour=99)], headers={"X-Request-ID": "err-1"})
    assert r.status_code == 422
    assert r.headers["X-Request-ID"] == "err-1" and r.json()["request_id"] == "err-1"


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "model_version": eta_app.VERSION}


def test_metrics_output():
    before = counter(client.get("/metrics").text, URL, "200")
    post([ROW])
    post([ROW])
    text = client.get("/metrics").text
    assert counter(text, URL, "200") == before + 2
    assert "eta_request_latency_seconds_bucket" in text
    assert 'le="+Inf"' in text
    assert "eta_request_latency_seconds_sum" in text
    assert "eta_request_latency_seconds_count" in text
    assert 'path="/metrics"' not in text               # /metrics is not counted


def test_generic_500_hides_internals(monkeypatch):
    def boom(X):
        raise RuntimeError("secret internal detail")
    monkeypatch.setattr(eta_app.model, "predict", boom)
    r = post([ROW])
    assert r.status_code == 500
    assert "secret" not in r.text and "Traceback" not in r.text
    assert r.headers["X-Request-ID"]
