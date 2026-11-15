"""Invoices, line items, payments (with dunning retries) and refunds, derived from the lifecycle."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from .config import Config
from .customers import new_id
from .lifecycle import BillingPeriod, Lifecycle, add_months, ts
from .messlog import MessLog

WARSAW = "Europe/Warsaw"
REFUND_REASONS = ("requested_by_customer", "duplicate", "fraudulent")
INVOICE_COLS = ["id", "customer_id", "subscription_id", "period_start", "period_end", "currency", "total",
                "status", "created_at"]
LINE_COLS = ["id", "invoice_id", "type", "amount", "period_start", "period_end", "plan_id", "quantity"]
PAYMENT_COLS = ["id", "invoice_id", "amount", "currency", "status", "attempted_at", "_attempted_at_utc"]
REFUND_COLS = ["id", "payment_id", "amount", "created_at", "reason"]


def _lines_for(period: BillingPeriod, price: dict[str, float]) -> list[dict]:
    lines = []
    subtotal = 0.0
    for plan_id, qty in period.items:
        amount = round(price[plan_id] * qty, 2)
        subtotal += amount
        lines.append({"type": "subscription", "amount": amount, "period_start": period.period_start,
                      "period_end": period.period_end, "plan_id": plan_id, "quantity": qty})
    for pr in period.prorations:
        share = (pr.period_end - pr.changed_at).days / (pr.period_end - pr.period_start).days
        lines.append({"type": "proration", "amount": -round(price[pr.old_plan_id] * pr.old_qty * share, 2),
                      "period_start": pr.changed_at, "period_end": pr.period_end,
                      "plan_id": pr.old_plan_id, "quantity": pr.old_qty})
        lines.append({"type": "proration", "amount": round(price[pr.new_plan_id] * pr.new_qty * share, 2),
                      "period_start": pr.changed_at, "period_end": pr.period_end,
                      "plan_id": pr.new_plan_id, "quantity": pr.new_qty})
    if period.coupon_pct:
        lines.append({"type": "discount", "amount": -round(subtotal * period.coupon_pct, 2),
                      "period_start": period.period_start, "period_end": period.period_end,
                      "plan_id": None, "quantity": None})
    if period.one_off_amount:
        lines.append({"type": "one_off", "amount": round(period.one_off_amount, 2),
                      "period_start": period.period_start, "period_end": period.period_start,
                      "plan_id": None, "quantity": None})
    return lines


def _attempt_offsets(period: BillingPeriod, retry_days: list[int]) -> list[tuple[int, str]]:
    """(days after first attempt, status) for every payment attempt of the invoice."""
    offsets = [0, *retry_days]
    if period.payment_outcome == "ok":
        return [(0, "succeeded")]
    if period.payment_outcome == "recovered":
        k = period.failed_attempts
        return [(offsets[i], "failed") for i in range(k)] + [(offsets[k], "succeeded")]
    return [(o, "failed") for o in offsets]


def format_attempted_at(t: pd.Timestamp, local: bool) -> str:
    """ISO-8601 string: UTC with 'Z', or Europe/Warsaw local time with its offset (the timezone trap)."""
    if local:
        return t.tz_convert(WARSAW).isoformat()
    return t.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def build_billing(cfg: Config, rng: np.random.Generator, lifecycle: Lifecycle, plans: pd.DataFrame,
                  customers: pd.DataFrame, log: MessLog) -> dict[str, pd.DataFrame]:
    price = dict(zip(plans.plan_id, plans.unit_amount))
    retry_days = list(cfg.mess["dunning"]["retry_days"])
    tz_trap = cfg.on("timezone_trap")
    taken: set[str] = set()
    invoices, lines, payments = [], [], []

    for period in lifecycle.periods:
        inv_id = new_id(rng, "in", taken)
        inv_lines = _lines_for(period, price)
        for line in inv_lines:
            lines.append({"id": new_id(rng, "il", taken), "invoice_id": inv_id, **line})
        total = round(sum(line["amount"] for line in inv_lines), 2)
        local = tz_trap and period.currency == "PLN"

        if total <= 0:
            status = "paid"  # credit-only invoice (e.g. annual downgrade): nothing to collect
        else:
            attempts = _attempt_offsets(period, retry_days)
            for offset, pstatus in attempts:
                t = period.first_attempt_at + timedelta(days=offset)
                payments.append({"id": new_id(rng, "py", taken), "invoice_id": inv_id, "amount": total,
                                 "currency": period.currency, "status": pstatus,
                                 "attempted_at": format_attempted_at(t, local), "_attempted_at_utc": t})
            if period.payment_outcome != "failed":
                status = "paid"
            else:
                cancel_at = period.first_attempt_at + timedelta(days=int(cfg.mess["dunning"]["cancel_after_days"]))
                status = "uncollectible" if cancel_at.date() <= cfg.end else "open"
        invoices.append({"id": inv_id, "customer_id": period.customer_id, "subscription_id": period.subscription_id,
                         "period_start": period.period_start, "period_end": period.period_end,
                         "currency": period.currency, "total": total, "status": status,
                         "created_at": period.first_attempt_at})

    pay = pd.DataFrame(payments, columns=PAYMENT_COLS)
    if tz_trap:
        _inject_boundary_cases(cfg, rng, pay, log)
    refunds = _build_refunds(cfg, rng, pay, taken, log)
    inv = pd.DataFrame(invoices, columns=INVOICE_COLS)
    inv["created_at"] = pd.to_datetime(inv.created_at, utc=True)
    pay["_attempted_at_utc"] = pd.to_datetime(pay._attempted_at_utc, utc=True)
    return {"invoices": inv, "invoice_line_items": pd.DataFrame(lines, columns=LINE_COLS),
            "payments": pay, "refunds": refunds}


def _inject_boundary_cases(cfg: Config, rng: np.random.Generator, pay: pd.DataFrame, log: MessLog) -> None:
    """Move a few PLN payments made in the last two days of a month to 00:00-00:59 Warsaw time on the 1st.

    Their UTC time is the previous day (previous month); parsing the string naively shifts the month.
    """
    n = int(cfg.mess["timezone_trap"]["boundary_cases"])
    last_month = (cfg.end.year, cfg.end.month)
    attempts_per_invoice = pay.invoice_id.value_counts()
    candidates = []
    for i, row in pay.iterrows():
        if row.currency != "PLN" or row.status != "succeeded" or attempts_per_invoice[row.invoice_id] != 1:
            continue
        local = row._attempted_at_utc.tz_convert(WARSAW)
        days_in_month = (add_months(date(local.year, local.month, 1), 1) - timedelta(days=1)).day
        if local.day >= days_in_month - 1 and (local.year, local.month) < last_month:
            candidates.append(i)
    if not candidates:
        return
    for i in rng.choice(candidates, size=min(n, len(candidates)), replace=False):
        t = pay.at[i, "_attempted_at_utc"].tz_convert(WARSAW)
        first = add_months(date(t.year, t.month, 1), 1)
        local = pd.Timestamp(first.year, first.month, 1, 0, int(rng.integers(0, 60)), tz=WARSAW)
        utc = local.tz_convert("UTC")
        pay.at[i, "_attempted_at_utc"] = utc
        pay.at[i, "attempted_at"] = format_attempted_at(utc, local=True)
        log.add("timezone_trap", "payments", pay.at[i, "id"], "local midnight on the 1st = previous month in UTC")


def _build_refunds(cfg: Config, rng: np.random.Generator, pay: pd.DataFrame, taken: set[str],
                   log: MessLog) -> pd.DataFrame:
    m = cfg.mess["refunds"]
    rows = []
    if not cfg.on("refunds") or m["rate"] <= 0:
        return pd.DataFrame(rows, columns=REFUND_COLS)
    end_ts = ts(cfg.end + timedelta(days=1))
    for row in pay[pay.status == "succeeded"].itertuples(index=False):
        if rng.random() >= m["rate"]:
            continue
        paid_at = pd.Timestamp(row[PAYMENT_COLS.index("_attempted_at_utc")])
        partial = rng.random() < m["partial_share"]
        amount = round(row.amount * float(rng.uniform(0.1, 0.6)), 2) if partial else row.amount
        if rng.random() < m["later_month_share"]:
            nxt = add_months(date(paid_at.year, paid_at.month, 1), 1)
            created = ts(nxt, int(rng.integers(0, 27 * 86_400)))
        else:
            month_end = ts(add_months(date(paid_at.year, paid_at.month, 1), 1))
            room = int((month_end - paid_at).total_seconds()) - 1
            if room < 60:
                continue
            created = paid_at + timedelta(seconds=int(rng.integers(60, room + 1)))
        if created >= end_ts:
            continue
        rid = new_id(rng, "re", taken)
        rows.append({"id": rid, "payment_id": row.id, "amount": amount, "created_at": created,
                     "reason": REFUND_REASONS[int(rng.integers(0, len(REFUND_REASONS)))]})
        log.add("refunds", "refunds", rid, "partial" if partial else "full")
    df = pd.DataFrame(rows, columns=REFUND_COLS)
    df["created_at"] = pd.to_datetime(df.created_at, utc=True)
    return df
