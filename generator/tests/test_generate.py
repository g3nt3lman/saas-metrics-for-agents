from datetime import date

import pandas as pd
import pyarrow.parquet as pq
import pytest

from generate import build_world, generate
from ledgerly.config import MESS_KEYS, load_config
from ledgerly.writer import RAW_TABLES, apply_as_of, content_hash, write_tables


@pytest.fixture(scope="module")
def full():
    return generate(load_config())


@pytest.fixture(scope="module")
def medium():
    return generate(load_config(n_customers=300))


def test_outputs(full):
    assert set(full) == set(RAW_TABLES) | {"truth/customer_month_facts", "truth/mess_log"}
    assert len(RAW_TABLES) == 10
    assert all(len(df) > 0 for df in full.values())


def test_same_seed_same_hash():
    cfg = load_config(n_customers=50)
    assert content_hash(generate(cfg)) == content_hash(generate(cfg))


def test_different_seed_different_hash():
    assert content_hash(generate(load_config(n_customers=50))) != content_hash(
        generate(load_config(n_customers=50, seed=43)))


def test_integrity_foreign_keys(medium):
    t = medium
    assert t["customers"].id.is_unique and t["subscriptions"].id.is_unique
    assert set(t["subscriptions"].customer_id) <= set(t["customers"].id)
    assert set(t["subscription_items"].subscription_id) <= set(t["subscriptions"].id)
    assert set(t["subscription_items"].plan_id) <= set(t["plans"].plan_id)
    assert set(t["invoices"].customer_id) <= set(t["customers"].id)
    assert set(t["invoices"].subscription_id) <= set(t["subscriptions"].id)
    assert set(t["invoice_line_items"].invoice_id) <= set(t["invoices"].id)
    assert set(t["payments"].invoice_id) <= set(t["invoices"].id)
    assert set(t["refunds"].payment_id) <= set(t["payments"].id)
    assert set(t["invoices"].currency) <= set(t["fx_rates"].currency)
    ids = pd.concat([t[k].id for k in ("customers", "subscriptions", "subscription_items", "invoices",
                                       "invoice_line_items", "payments", "refunds")])
    assert set(t["events"].object_id) <= set(ids)


def test_no_private_columns_written(medium, tmp_path):
    write_tables(medium, tmp_path)
    files = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.parquet"))
    assert files == sorted([f"raw/{n}.parquet" for n in RAW_TABLES]
                           + ["truth/customer_month_facts.parquet", "truth/mess_log.parquet"])
    for f in tmp_path.rglob("*.parquet"):
        assert not any(c.startswith("_") for c in pq.read_schema(f).names), f


def test_each_mess_type_present(full):
    t = full
    logged = set(t["truth/mess_log"].mess_type)
    assert set(MESS_KEYS) <= logged, set(MESS_KEYS) - logged
    c = t["customers"]
    assert c.email.str.endswith("@ledgerly.io").any()
    deleted = c[c.is_deleted]
    with_subs = deleted.id.isin(t["subscriptions"].customer_id)
    assert with_subs.any() and (~with_subs).any()
    assert t["events"].id.duplicated().any()
    lines = t["invoice_line_items"]
    assert (lines[lines.type == "proration"].amount < 0).any() and (lines[lines.type == "proration"].amount > 0).any()
    assert (lines.type == "one_off").any() and (lines.type == "discount").any()
    items = t["subscription_items"].merge(t["plans"], on="plan_id")
    assert (items.interval == "year").any()
    live = items[items.valid_to.isna() | (items.valid_to >= pd.Timestamp("2025-11-01", tz="UTC"))]
    assert live.plan_id.str.endswith("_v1").any() and live.plan_id.str.endswith("_v2").any()
    assert not any(d.weekday() >= 5 for d in t["fx_rates"].date)


def test_mess_disabled_absent():
    t = generate(load_config(n_customers=80, **{"mess.refunds.rate": 0, "mess.duplicate_events.enabled": False}))
    assert len(t["refunds"]) == 0
    assert t["events"].id.is_unique


def test_as_of_mid_window():
    cut = date(2025, 10, 31)
    t = generate(load_config(n_customers=300), as_of=cut)
    end = pd.Timestamp("2025-11-01", tz="UTC")
    assert (t["customers"].created_at < end).all()
    assert (t["subscriptions"].started_at < end).all()
    assert (t["subscriptions"].canceled_at.dropna() < end).all()
    assert (t["subscriptions"].ended_at.dropna() < end).all()
    assert (pd.to_datetime(t["payments"].attempted_at, utc=True, format="ISO8601") < end).all()
    assert (t["events"].ingested_at < end).all()
    assert (t["fx_rates"].date <= cut).all()
    assert not t["plans"].plan_id.str.endswith("_v2").any() and t["plans"].active.all()
    assert (t["truth/customer_month_facts"].month <= date(2025, 10, 1)).all()
    assert (t["invoices"].created_at < end).all()
    assert set(t["invoices"].status) <= {"paid", "open", "uncollectible"}


def test_status_at_window_end_matches_lifecycle():
    cfg = load_config(n_customers=400)
    world = build_world(cfg)
    at_end = apply_as_of(world, cfg.end)
    pd.testing.assert_series_equal(world["subscriptions"].status, at_end["subscriptions"].status)
    pd.testing.assert_series_equal(world["invoices"].status, at_end["invoices"].status)


def test_late_events_beyond_window_dropped(full):
    assert (full["events"].ingested_at < pd.Timestamp("2026-11-01", tz="UTC")).all()
