from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from ledgerly.catalog import build_plans
from ledgerly.config import load_config
from ledgerly.customers import build_customers, make_faker
from ledgerly.lifecycle import simulate
from ledgerly.messlog import MessLog


def _run(cfg):
    rng = np.random.default_rng(cfg.seed)
    log = MessLog()
    customers = build_customers(cfg, rng, make_faker(cfg.seed), log)
    plans = build_plans(cfg)
    return simulate(cfg, rng, customers, plans, log), customers, plans, log


@pytest.fixture(scope="module")
def small():
    return _run(load_config(n_customers=150))


@pytest.fixture(scope="module")
def full():
    return _run(load_config())


def test_items_versions_do_not_overlap(small):
    lc = small[0]
    for _, g in lc.subscription_items.sort_values("valid_from").groupby("subscription_id"):
        assert g.valid_from.iloc[1:].tolist() == g.valid_to.iloc[:-1].tolist()
        assert g.valid_to.iloc[:-1].notna().all()


def test_every_item_belongs_to_a_subscription(small):
    lc = small[0]
    assert set(lc.subscription_items.subscription_id) <= set(lc.subscriptions.id)


def test_trial_periods_not_billed(small):
    lc = small[0]
    trial_end = lc.subscriptions.set_index("id").trial_end
    for p in lc.periods:
        te = trial_end[p.subscription_id]
        if pd.notna(te):
            assert p.period_start >= te.date()


def test_non_converted_trials_have_no_periods(small):
    lc = small[0]
    expired = set(lc.subscriptions[lc.subscriptions.status == "incomplete_expired"].id)
    assert expired
    assert not expired & {p.subscription_id for p in lc.periods}


def test_dunning_failed_cancels_14_days_after_first_attempt(full):
    lc = full[0]
    subs = lc.subscriptions.set_index("id")
    failed = [p for p in lc.periods if p.payment_outcome == "failed"]
    assert failed
    for p in failed:
        s = subs.loc[p.subscription_id]
        expected = p.first_attempt_at + timedelta(days=14)
        if expected.date() <= date(2026, 10, 31):
            assert s.canceled_at == expected and s.ended_at == expected
            assert s._churn_reason == "dunning" and s.status == "canceled"
        else:
            assert s.status == "past_due" and pd.isna(s.canceled_at)


def test_annual_cancel_at_period_end_ends_at_term_end(full):
    lc = full[0]
    s = lc.subscriptions[lc.subscriptions.cancel_at_period_end & lc.subscriptions.ended_at.notna()]
    assert len(s) > 0
    last_period_end = {}
    for p in lc.periods:
        if p.kind == "regular":
            last_period_end[p.subscription_id] = p.period_end
    for row in s.itertuples():
        assert row.canceled_at < row.ended_at
        assert row.ended_at.date() == last_period_end[row.id]


def test_reactivation_is_new_subscription_same_customer(full):
    subs = full[0].subscriptions.sort_values("started_at")
    found = False
    for _, g in subs.groupby("customer_id"):
        if len(g) >= 2:
            first, second = g.iloc[0], g.iloc[1]
            assert pd.notna(first.ended_at) and second.started_at > first.ended_at
            found = True
    assert found


def test_grandfathered_keep_v1(full):
    lc, _, _, log = full
    changed_items = set(log.to_frame().query("mess_type == 'plan_changes'").object_id)
    items_all = lc.subscription_items
    changed = set(items_all[items_all.id.isin(changed_items)].subscription_id)
    subs = lc.subscriptions
    early = set(subs[subs.started_at < pd.Timestamp("2025-11-01", tz="UTC")].id) - changed
    items = lc.subscription_items[lc.subscription_items.subscription_id.isin(early)]
    assert len(items) > 0
    assert items.plan_id.str.endswith("_v1").all()


def test_new_subscriptions_after_price_change_on_v2(full):
    lc = full[0]
    subs = lc.subscriptions
    late = set(subs[subs.started_at >= pd.Timestamp("2025-11-01", tz="UTC")].id)
    first_items = lc.subscription_items[lc.subscription_items.subscription_id.isin(late)].groupby("subscription_id").head(1)
    assert len(first_items) > 0
    assert first_items.plan_id.str.endswith("_v2").all()


def test_annual_change_creates_proration_period(full):
    lc = full[0]
    pro = [p for p in lc.periods if p.kind == "proration"]
    assert pro and all(not p.items and len(p.prorations) == 1 for p in pro)


def test_status_values(full):
    assert set(full[0].subscriptions.status) <= {"trialing", "active", "past_due", "canceled", "incomplete_expired"}
    assert {"active", "canceled", "incomplete_expired"} <= set(full[0].subscriptions.status)


def test_junk_customers_have_no_subscriptions(full):
    lc, customers = full[0], full[1]
    junk = set(customers[customers._kind == "junk"].id)
    assert junk and not junk & set(lc.subscriptions.customer_id)


def test_deleted_churned_are_real_and_not_active(full):
    lc, customers = full[0], full[1]
    assert lc.deleted_customer_ids
    kinds = customers.set_index("id")._kind
    active = set(lc.subscriptions[lc.subscriptions.status.isin(["active", "past_due", "trialing"])].customer_id)
    for cid in lc.deleted_customer_ids:
        assert kinds[cid] == "real" and cid not in active


def test_deterministic():
    cfg = load_config(n_customers=60)
    a, b = _run(cfg)[0], _run(cfg)[0]
    pd.testing.assert_frame_equal(a.subscriptions, b.subscriptions)
    pd.testing.assert_frame_equal(a.subscription_items, b.subscription_items)
    assert a.periods == b.periods
