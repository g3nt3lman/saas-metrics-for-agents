import json
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from ledgerly.billing import build_billing
from ledgerly.catalog import build_plans
from ledgerly.config import load_config
from ledgerly.customers import build_customers, make_faker
from ledgerly.events import build_events
from ledgerly.lifecycle import simulate
from ledgerly.messlog import MessLog
from ledgerly.truth import build_customer_month_facts


@pytest.fixture(scope="module")
def world():
    cfg = load_config(n_customers=600)
    rng = np.random.default_rng(cfg.seed)
    log = MessLog()
    customers = build_customers(cfg, rng, make_faker(cfg.seed), log)
    plans = build_plans(cfg)
    lc = simulate(cfg, rng, customers, plans, log)
    tables = {"customers": customers, "plans": plans, "subscriptions": lc.subscriptions,
              "subscription_items": lc.subscription_items, **build_billing(cfg, rng, lc, plans, customers, log)}
    events = build_events(cfg, rng, tables, log)
    facts = build_customer_month_facts(cfg, customers, lc, plans)
    return cfg, tables, lc, events, facts, log


def test_event_types_cover_all_objects(world):
    _, tables, _, events, _, _ = world
    assert {"customer.created", "subscription.created", "subscription.updated", "subscription.deleted",
            "invoice.created", "invoice.paid", "payment.succeeded", "payment.failed",
            "refund.created"} <= set(events.type)
    created = set(events[events.type == "customer.created"].object_id)
    assert created == set(tables["customers"].id)
    assert all(isinstance(json.loads(p), dict) for p in events.payload_json.head(50))


def test_duplicates_share_id_later_ingested(world):
    events = world[3]
    dup = events[events.id.duplicated(keep=False)].sort_values(["id", "ingested_at"])
    assert len(dup) > 0
    for _, g in dup.groupby("id"):
        assert g.created_at.nunique() == 1 and g.ingested_at.is_monotonic_increasing
        assert g.ingested_at.nunique() == len(g)


def test_late_events_lag_at_least_3_days(world):
    events, log = world[3], world[5]
    late = set(log.to_frame().query("mess_type == 'late_events'").object_id)
    assert late
    e = events[events.id.isin(late)].drop_duplicates("id")
    assert ((e.ingested_at - e.created_at) >= timedelta(days=3)).all()
    normal = events[~events.id.isin(late)].drop_duplicates("id")
    assert ((normal.ingested_at - normal.created_at) < timedelta(minutes=10)).all()


def test_ingested_not_before_created(world):
    e = world[3]
    assert (e.ingested_at > e.created_at).all()


def test_truth_columns(world):
    assert list(world[4].columns) == [
        "customer_id", "month", "state_at_month_end", "subscription_id", "plan_id", "tier", "interval",
        "currency", "quantity", "run_rate_mrr_local_list", "discount_mrr_local", "is_internal", "is_deleted"]


def test_truth_annual_divided_by_12(world):
    _, tables, _, _, facts, _ = world
    price = tables["plans"].set_index("plan_id").unit_amount
    annual = facts[facts.interval == "year"]
    assert len(annual) > 0
    expected = (annual.plan_id.map(price) * annual.quantity / 12).round(4)
    assert (annual.run_rate_mrr_local_list == expected).all()


def test_truth_trial_month_state_is_trialing(world):
    _, _, lc, _, facts, _ = world
    s = lc.subscriptions
    spans = s[s.trial_end.notna() & (s.trial_end.dt.strftime("%Y-%m") != s.started_at.dt.strftime("%Y-%m"))]
    assert len(spans) > 0
    row = spans.iloc[0]
    month = pd.Timestamp(row.started_at.strftime("%Y-%m-01")).date()
    f = facts[(facts.subscription_id == row.id) & (facts.month == month)]
    assert f.state_at_month_end.tolist() == ["trialing"]


def test_truth_month_end_state_after_dunning_cancel_is_ended(world):
    _, _, lc, _, facts, _ = world
    s = lc.subscriptions
    dun = s[s._churn_reason == "dunning"].iloc[0]
    month = pd.Timestamp(dun.ended_at.strftime("%Y-%m-01")).date()
    f = facts[(facts.subscription_id == dun.id)]
    assert f[f.month == month].state_at_month_end.tolist() == ["ended"]
    assert f.month.max() == month


def test_truth_one_row_per_subscription_month(world):
    f = world[4]
    assert not f.duplicated(["subscription_id", "month"]).any()


def test_truth_discount_matches_coupon(world):
    f = world[4]
    d = f[f.discount_mrr_local > 0]
    assert len(d) > 0
    assert (d.discount_mrr_local <= d.run_rate_mrr_local_list * 0.2 + 1e-3).all()


def test_truth_covers_every_subscription_through_window(world):
    cfg, _, lc, _, facts, _ = world
    assert set(facts.subscription_id) == set(lc.subscriptions.id)
    live = lc.subscriptions[lc.subscriptions.ended_at.isna()]
    last = facts[facts.month == cfg.end.replace(day=1)]
    assert set(live.id) <= set(last.subscription_id)
