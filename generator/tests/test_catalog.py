from datetime import date

import pytest

from ledgerly.catalog import build_fx_rates, build_plans, plan_for
from ledgerly.config import load_config


@pytest.fixture
def plans(small_cfg):
    return build_plans(small_cfg)


def test_plan_catalog_has_36_rows(plans):
    assert len(plans) == 36
    v1 = plans[plans.plan_id.str.endswith("_v1")].set_index(["tier", "interval", "currency"])
    v2 = plans[plans.plan_id.str.endswith("_v2")].set_index(["tier", "interval", "currency"])
    assert len(v1) == len(v2) == 18
    joined = v1.join(v2, suffixes=("", "_v2"))
    assert (joined.unit_amount_v2 == (joined.unit_amount * 1.15).round(2)).all()
    assert (v2.valid_from == date(2025, 11, 1)).all()


def test_annual_price_is_10x_monthly(plans):
    p = plans.assign(version=plans.plan_id.str[-2:]).set_index(["tier", "currency", "version", "interval"]).unit_amount
    for (tier, currency, version, interval), amount in p.items():
        if interval == "year":
            assert amount == pytest.approx(p[(tier, currency, version, "month")] * 10, abs=0.01)


def test_v1_inactive_after_price_change(plans):
    v1 = plans[plans.plan_id.str.endswith("_v1")]
    assert not v1.active.any()
    assert (v1.updated_at.dt.date == date(2025, 11, 1)).all()
    assert plans[plans.plan_id.str.endswith("_v2")].active.all()


def test_fx_eur_is_one_and_no_weekends(small_cfg, rng):
    fx = build_fx_rates(small_cfg, rng)
    assert set(fx.currency) == {"EUR", "USD", "PLN"}
    assert (fx[fx.currency == "EUR"].rate_to_eur == 1.0).all()
    assert not any(d.weekday() >= 5 for d in fx.date)
    assert fx.date.min() == small_cfg.start and fx.date.max() <= small_cfg.end


def test_fx_weekends_present_when_gap_disabled(rng):
    cfg = load_config(n_customers=50, **{"mess.fx_gaps.enabled": False})
    fx = build_fx_rates(cfg, rng)
    assert any(d.weekday() >= 5 for d in fx.date)
    assert len(fx) == 3 * ((cfg.end - cfg.start).days + 1)


def test_plan_for_switches_on_price_change(plans):
    assert plan_for(plans, "pro", "month", "EUR", date(2025, 10, 31)) == "price_pro_month_eur_v1"
    assert plan_for(plans, "pro", "month", "EUR", date(2025, 11, 1)) == "price_pro_month_eur_v2"


def test_config_window(small_cfg):
    assert small_cfg.start == date(2024, 11, 1)
    assert small_cfg.end == date(2026, 10, 31)
    assert small_cfg.seed == 42
    assert small_cfg.n_customers == 50


def test_unknown_override_rejected():
    with pytest.raises(KeyError):
        load_config(**{"mess.nope.rate": 1})
