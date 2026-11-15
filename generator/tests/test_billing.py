from datetime import date

import numpy as np
import pandas as pd
import pytest

from ledgerly.billing import build_billing
from ledgerly.catalog import build_plans
from ledgerly.config import load_config
from ledgerly.customers import build_customers, make_faker
from ledgerly.lifecycle import simulate
from ledgerly.messlog import MessLog


@pytest.fixture(scope="module")
def full():
    cfg = load_config()
    rng = np.random.default_rng(cfg.seed)
    log = MessLog()
    customers = build_customers(cfg, rng, make_faker(cfg.seed), log)
    plans = build_plans(cfg)
    lc = simulate(cfg, rng, customers, plans, log)
    return build_billing(cfg, rng, lc, plans, customers, log), lc, log


def test_invoice_total_equals_sum_of_lines(full):
    b = full[0]
    sums = b["invoice_line_items"].groupby("invoice_id").amount.sum().round(2)
    inv = b["invoices"].set_index("id").total
    assert (sums.reindex(inv.index) == inv).all()


def test_one_invoice_per_billing_period(full):
    b, lc, _ = full
    assert len(b["invoices"]) == len(lc.periods)
    assert b["invoices"].id.is_unique


def test_succeeded_payment_amount_equals_total(full):
    b = full[0]
    p = b["payments"][b["payments"].status == "succeeded"].merge(
        b["invoices"][["id", "total"]], left_on="invoice_id", right_on="id")
    assert len(p) > 0 and (p.amount == p.total).all()


def test_refund_not_exceeding_payment(full):
    b = full[0]
    r = b["refunds"].groupby("payment_id").amount.sum()
    pay = b["payments"].set_index("id").amount
    assert len(r) > 0 and (r <= pay.reindex(r.index) + 1e-9).all()
    assert set(b["refunds"].payment_id) <= set(b["payments"][b["payments"].status == "succeeded"].id)


def test_refund_after_payment(full):
    b = full[0]
    j = b["refunds"].merge(b["payments"][["id", "_attempted_at_utc"]], left_on="payment_id", right_on="id")
    assert (j.created_at > j._attempted_at_utc).all()
    later = j.created_at.dt.strftime("%Y-%m") > j._attempted_at_utc.dt.strftime("%Y-%m")
    assert later.any() and (~later).any()
    partial = j.amount < b["payments"].set_index("id").amount.reindex(j.payment_id).values
    assert partial.any() and (~partial).any()


def test_uncollectible_has_no_succeeded_payment(full):
    b = full[0]
    unc = set(b["invoices"][b["invoices"].status == "uncollectible"].id)
    assert unc
    succ = set(b["payments"][b["payments"].status == "succeeded"].invoice_id)
    assert not unc & succ
    fails = b["payments"][b["payments"].invoice_id.isin(unc)].groupby("invoice_id").size()
    assert (fails == 4).all()


def test_recovered_invoices_have_failed_then_succeeded(full):
    b, lc, _ = full
    p = b["payments"].sort_values("_attempted_at_utc")
    multi = p.groupby("invoice_id").filter(lambda g: len(g) > 1 and (g.status == "succeeded").any())
    assert len(multi) > 0
    for _, g in multi.groupby("invoice_id"):
        assert g.status.tolist()[-1] == "succeeded" and set(g.status.tolist()[:-1]) == {"failed"}


def test_attempted_at_mixed_offsets(full):
    a = full[0]["payments"].attempted_at
    assert a.str.endswith("Z").any()
    assert a.str.endswith("+01:00").any() and a.str.endswith("+02:00").any()
    assert (pd.to_datetime(a, utc=True, format="ISO8601") == full[0]["payments"]._attempted_at_utc).all()


def test_timezone_boundary_cases_shift_month(full):
    b, _, log = full
    ids = set(log.to_frame().query("mess_type == 'timezone_trap'").object_id)
    assert len(ids) == 5
    p = b["payments"][b["payments"].id.isin(ids)]
    naive_month = p.attempted_at.str[:7]
    utc_month = p._attempted_at_utc.dt.strftime("%Y-%m")
    assert (naive_month != utc_month).all()


def test_annual_invoice_one_subscription_line_spanning_12_months(full):
    b = full[0]
    lines = b["invoice_line_items"]
    annual = lines[(lines.type == "subscription") & lines.plan_id.str.contains("_year_")]
    assert len(annual) > 0
    months = (pd.to_datetime(annual.period_end).dt.to_period("M") - pd.to_datetime(annual.period_start).dt.to_period("M"))
    assert (months.apply(lambda x: x.n) == 12).all()
    assert annual.groupby("invoice_id").size().max() == 1


def test_line_types_present(full):
    lines = full[0]["invoice_line_items"]
    assert set(lines.type) == {"subscription", "proration", "one_off", "discount"}
    pr = lines[lines.type == "proration"].amount
    assert (pr < 0).any() and (pr > 0).any()
    assert (lines[lines.type == "discount"].amount < 0).all()


def test_no_refunds_when_disabled():
    cfg = load_config(n_customers=80, **{"mess.refunds.rate": 0})
    rng = np.random.default_rng(cfg.seed)
    log = MessLog()
    customers = build_customers(cfg, rng, make_faker(cfg.seed), log)
    plans = build_plans(cfg)
    b = build_billing(cfg, rng, simulate(cfg, rng, customers, plans, log), plans, customers, log)
    assert len(b["refunds"]) == 0
    assert list(b["refunds"].columns) == ["id", "payment_id", "amount", "created_at", "reason"]
