"""Tests for decide(). Run from repo root:  python -m pytest partB -v"""
from eta_mlflow import decide

PROD = dict(mae=10.0, mae_rush=20.0, mae_rain=12.0, p95_ms=3.0)


def cand(**kw):
    c = dict(mae=6.0, mae_rush=15.0, mae_rain=8.0, p95_ms=4.0)     # clearly better everywhere
    c.update(kw)
    return c


def test_promotes_when_everything_ok():
    r = decide(PROD, cand())
    assert r["promote"] is True and all(r["criteria"].values())


def test_gain_just_inside_threshold_promotes():
    # 3.1% better than 10.0 -> 9.69
    assert decide(PROD, cand(mae=9.69))["promote"] is True


def test_gain_just_outside_threshold_blocks():
    # 2.9% better -> 9.71
    r = decide(PROD, cand(mae=9.71))
    assert r["promote"] is False and r["criteria"]["overall_mae_gain"] is False


def test_worse_overall_mae_blocks():
    assert decide(PROD, cand(mae=10.5))["promote"] is False


def test_latency_boundary():
    assert decide(PROD, cand(p95_ms=10.0))["promote"] is True       # exactly at limit: ok
    r = decide(PROD, cand(p95_ms=10.01))                            # just over: blocked
    assert r["promote"] is False and r["criteria"]["latency_p95"] is False


def test_rush_slice_regression_just_inside_and_outside():
    # prod rush 20.0 -> limit is 21.0 (5% relative)
    assert decide(PROD, cand(mae_rush=20.99))["promote"] is True
    r = decide(PROD, cand(mae_rush=21.01))
    assert r["promote"] is False and r["criteria"]["no_rush_regression"] is False


def test_rain_slice_regression_blocks_even_if_overall_great():
    # overall MAE improves hugely, but rain rows get worse by > 5% (12.0 -> 12.7)
    r = decide(PROD, cand(mae=3.0, mae_rain=12.7))
    assert r["promote"] is False and r["criteria"]["no_rain_regression"] is False
    assert r["criteria"]["overall_mae_gain"] is True


def test_custom_thresholds_change_outcome():
    c = cand(mae=6.0)                                               # 40% gain
    assert decide(PROD, c)["promote"] is True
    assert decide(PROD, c, min_rel_gain=0.60)["promote"] is False
    assert decide(PROD, cand(p95_ms=4.0), max_p95_ms=3.5)["promote"] is False


def test_decide_is_pure():
    p, c = dict(PROD), cand()
    decide(p, c)
    assert p == PROD and c == cand()
