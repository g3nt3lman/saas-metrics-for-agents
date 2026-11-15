"""Ground truth: per customer x subscription x month-end facts *before* any business definition.

No FX, no churn timing, no gross/net choice: the author applies those in their own tests.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import pandas as pd

from .config import Config
from .lifecycle import Lifecycle, add_months, ts

FACT_COLS = ["customer_id", "month", "state_at_month_end", "subscription_id", "plan_id", "tier", "interval",
             "currency", "quantity", "run_rate_mrr_local_list", "discount_mrr_local", "is_internal", "is_deleted"]


def _months(cfg: Config) -> list[date]:
    return [add_months(cfg.start, i) for i in range(cfg.months)]


def build_customer_month_facts(cfg: Config, customers: pd.DataFrame, lifecycle: Lifecycle,
                               plans: pd.DataFrame) -> pd.DataFrame:
    plan = plans.set_index("plan_id")
    cust = customers.set_index("id")
    deleted = set(cust.index[cust.is_deleted]) | lifecycle.deleted_customer_ids
    items_by_sub = {k: g.sort_values("valid_from") for k, g in lifecycle.subscription_items.groupby("subscription_id")}
    periods_by_sub = defaultdict(list)
    for p in lifecycle.periods:
        periods_by_sub[p.subscription_id].append(p)
    dunning = cfg.mess["dunning"]

    rows = []
    for s in lifecycle.subscriptions.to_dict("records"):
        items = items_by_sub[s["id"]]
        periods = periods_by_sub[s["id"]]
        for month in _months(cfg):
            at = ts(add_months(month, 1))  # month end = instant just before the next month starts
            if s["started_at"] >= at:
                continue  # month ends before the subscription starts
            ended = pd.notna(s["ended_at"]) and s["ended_at"] < at
            if ended and s["ended_at"] < ts(month):
                break
            if ended:
                state = "ended"
                item = items.iloc[-1]
            else:
                live = items[(items.valid_from < at) & (items.valid_to.isna() | (items.valid_to >= at))]
                item = live.iloc[-1]
                state = "active"
                if pd.notna(s["trial_end"]) and s["trial_end"] >= at:
                    state = "trialing"
                elif pd.notna(s["canceled_at"]) and s["canceled_at"] < at:
                    state = "canceled_pending_end"
                else:
                    for p in periods:
                        if p.payment_outcome == "ok" or p.first_attempt_at >= at:
                            continue
                        days = (dunning["retry_days"][p.failed_attempts - 1] if p.payment_outcome == "recovered"
                                else dunning["cancel_after_days"])
                        if p.first_attempt_at + timedelta(days=int(days)) >= at:
                            state = "past_due"
            pl = plan.loc[item.plan_id]
            run_rate = round(pl.unit_amount * item.quantity / (12 if pl.interval == "year" else 1), 4)
            coupon = 0.0
            for p in periods:
                if p.kind == "regular" and p.coupon_pct and ts(p.period_start) < at <= ts(p.period_end):
                    coupon = p.coupon_pct
            rows.append({
                "customer_id": s["customer_id"], "month": month, "state_at_month_end": state,
                "subscription_id": s["id"], "plan_id": item.plan_id, "tier": pl.tier, "interval": pl.interval,
                "currency": pl.currency, "quantity": int(item.quantity), "run_rate_mrr_local_list": run_rate,
                "discount_mrr_local": round(run_rate * coupon, 4) if state != "ended" else 0.0,
                "is_internal": bool(cust.at[s["customer_id"], "_kind"] == "internal"),
                "is_deleted": s["customer_id"] in deleted,
            })
            if ended:
                break
    return pd.DataFrame(rows, columns=FACT_COLS)
