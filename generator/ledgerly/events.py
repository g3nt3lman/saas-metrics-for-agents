"""Webhook-style event stream derived from the raw tables, with duplicate and late deliveries."""

from __future__ import annotations

import json
from datetime import timedelta

import numpy as np
import pandas as pd

from .config import Config
from .customers import new_id
from .messlog import MessLog

EVENT_COLS = ["id", "type", "object_id", "payload_json", "created_at", "ingested_at"]


def _payload(**fields) -> str:
    return json.dumps(fields, sort_keys=True, default=str)


def _collect(tables: dict[str, pd.DataFrame]) -> list[tuple[pd.Timestamp, str, str, str]]:
    """(created_at, type, object_id, payload_json) for every lifecycle change."""
    out = []
    for r in tables["customers"].itertuples(index=False):
        out.append((r.created_at, "customer.created", r.id, _payload(id=r.id, email=r.email, country=r.country)))
    subs = tables["subscriptions"]
    for r in subs.itertuples(index=False):
        out.append((r.started_at, "subscription.created", r.id,
                    _payload(id=r.id, customer=r.customer_id, trial_end=r.trial_end)))
        if r.cancel_at_period_end and pd.notna(r.canceled_at):
            out.append((r.canceled_at, "subscription.updated", r.id,
                        _payload(id=r.id, cancel_at_period_end=True)))
        if pd.notna(r.ended_at):
            out.append((r.ended_at, "subscription.deleted", r.id, _payload(id=r.id, ended_at=r.ended_at)))
    items = tables["subscription_items"]
    changes = items[items.duplicated("subscription_id", keep="first")]
    for r in changes.itertuples(index=False):
        out.append((r.valid_from, "subscription.updated", r.subscription_id,
                    _payload(id=r.subscription_id, item=r.id, plan=r.plan_id, quantity=r.quantity)))
    for r in tables["invoices"].itertuples(index=False):
        out.append((r.created_at, "invoice.created", r.id, _payload(id=r.id, total=r.total, currency=r.currency)))
    pay = tables["payments"]
    for pid, inv, amount, status, at in zip(pay.id, pay.invoice_id, pay.amount, pay.status, pay._attempted_at_utc):
        out.append((at, f"payment.{status}", pid, _payload(id=pid, invoice=inv, amount=amount)))
        if status == "succeeded":
            out.append((at, "invoice.paid", inv, _payload(id=inv, payment=pid)))
    for r in tables["refunds"].itertuples(index=False):
        out.append((r.created_at, "refund.created", r.id, _payload(id=r.id, payment=r.payment_id, amount=r.amount)))
    return out


def build_events(cfg: Config, rng: np.random.Generator, tables: dict[str, pd.DataFrame],
                 log: MessLog) -> pd.DataFrame:
    raw = sorted(_collect(tables), key=lambda e: (e[0], e[1], e[2]))
    taken: set[str] = set()
    late_on, dup_on = cfg.on("late_events"), cfg.on("duplicate_events")
    lm, dm = cfg.mess["late_events"], cfg.mess["duplicate_events"]
    rows = []
    for created, etype, obj, payload in raw:
        eid = new_id(rng, "evt", taken)
        ingested = created + timedelta(seconds=int(rng.integers(1, 300)))
        if late_on and rng.random() < lm["rate"]:
            ingested = created + timedelta(days=int(rng.integers(lm["min_days"], lm["max_days"] + 1)),
                                           seconds=int(rng.integers(0, 86_400)))
            log.add("late_events", "events", eid, f"lag {(ingested - created).days}d")
        rows.append({"id": eid, "type": etype, "object_id": obj, "payload_json": payload,
                     "created_at": created, "ingested_at": ingested})
        if dup_on and rng.random() < dm["rate"]:
            rows.append({**rows[-1], "ingested_at": ingested + timedelta(seconds=int(rng.integers(1, 2 * 86_400)))})
            log.add("duplicate_events", "events", eid, "re-delivered")
    df = pd.DataFrame(rows, columns=EVENT_COLS)
    for c in ("created_at", "ingested_at"):
        df[c] = pd.to_datetime(df[c], utc=True)
    return df
