"""Month-by-month lifecycle simulation: decides *what happened* to every customer.

Output is the source of truth that billing, events and ground-truth modules derive from:
subscriptions, subscription item versions and billing periods (with payment outcomes).
"""

from __future__ import annotations

import calendar
from types import SimpleNamespace
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .catalog import plan_for
from .config import Config
from .customers import new_id
from .messlog import MessLog

TIERS = ("starter", "pro", "business")


@dataclass(frozen=True)
class Proration:
    old_plan_id: str
    old_qty: int
    new_plan_id: str
    new_qty: int
    changed_at: date
    period_start: date  # the period in which the change happened
    period_end: date    # exclusive


@dataclass
class BillingPeriod:
    subscription_id: str
    customer_id: str
    currency: str
    period_start: date
    period_end: date                          # exclusive
    items: list[tuple[str, int]]              # (plan_id, quantity) at period start; empty for kind="proration"
    prorations: list[Proration]               # changes billed on this invoice
    coupon_pct: float                         # 0.0 or the coupon percentage
    one_off_amount: float                     # 0.0 or onboarding fee
    payment_outcome: str                      # "ok" | "recovered" | "failed"
    first_attempt_at: pd.Timestamp            # UTC
    kind: str = "regular"                     # "regular" | "proration" (immediate invoice for annual changes)
    failed_attempts: int = 0                  # failed attempts before success (recovered) or in total (failed)


@dataclass
class Lifecycle:
    subscriptions: pd.DataFrame
    subscription_items: pd.DataFrame
    periods: list[BillingPeriod]
    deleted_customer_ids: set[str] = field(default_factory=set)


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    year, month = d.year + m // 12, m % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def ts(d: date, seconds: int = 0) -> pd.Timestamp:
    return pd.Timestamp(datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(seconds=seconds))


class _Simulation:
    def __init__(self, cfg: Config, rng: np.random.Generator, plans: pd.DataFrame, log: MessLog):
        self.cfg, self.rng, self.plans, self.log = cfg, rng, plans, log
        self.m = cfg.mess
        self.subs: list[dict] = []
        self.items: list[dict] = []
        self.periods: list[BillingPeriod] = []
        self.taken: set[str] = set()

    # -- helpers -----------------------------------------------------------------------------
    def _rand(self) -> float:
        return float(self.rng.random())

    def _open_item(self, sub_id: str, plan_id: str, qty: int, at: pd.Timestamp) -> None:
        self.items.append({"id": new_id(self.rng, "si", self.taken), "subscription_id": sub_id,
                           "plan_id": plan_id, "quantity": int(qty), "valid_from": at, "valid_to": None})

    def _close_item(self, at: pd.Timestamp) -> None:
        self.items[-1]["valid_to"] = at

    def _initial_choice(self) -> tuple[str, str, int]:
        tw = np.array([self.cfg.tier_weights[t] for t in TIERS])
        tier = TIERS[int(self.rng.choice(len(TIERS), p=tw / tw.sum()))]
        annual = self.cfg.on("annual_plans") and self._rand() < self.m["annual_plans"]["share"]
        lo, hi = self.cfg.seats[tier]
        return tier, "year" if annual else "month", int(self.rng.integers(lo, hi + 1))

    # -- one subscription ----------------------------------------------------------------------
    def run_subscription(self, customer, tier: str, interval: str, qty: int,
                         start: pd.Timestamp, first: bool) -> tuple[str | None, pd.Timestamp | None, str]:
        """Simulate one subscription. Returns (churn_reason or None, ended_at or None, last tier)."""
        cfg, m, end = self.cfg, self.m, self.cfg.end
        sub_id = new_id(self.rng, "sub", self.taken)
        currency = customer._currency
        sub = {"id": sub_id, "customer_id": customer.id, "status": "active", "started_at": start,
               "trial_end": None, "canceled_at": None, "ended_at": None, "cancel_at_period_end": False,
               "_churn_reason": None, "_currency": currency}
        self.subs.append(sub)
        plan = plan_for(self.plans, tier, interval, currency, start.date())
        self._open_item(sub_id, plan, qty, start)
        if interval == "year":
            self.log.add("annual_plans", "subscriptions", sub_id)

        trial_end = None
        if first and cfg.on("trials"):
            trial_end = start + timedelta(days=int(m["trials"]["days"]))
            sub["trial_end"] = trial_end
            converts = self._rand() >= m["trials"]["non_convert_rate"]
            if trial_end.date() > end:
                sub["status"] = "trialing"
                return None, None, tier
            if not converts:
                sub.update(status="incomplete_expired", ended_at=trial_end, _churn_reason="trial_expired")
                self._close_item(trial_end)
                self.log.add("trials", "subscriptions", sub_id, "trial never converted")
                return "trial_expired", trial_end, tier

        has_coupon = cfg.on("discounts") and self._rand() < m["discounts"]["rate"]
        coupon_periods = (int(m["discounts"]["periods"]) if interval == "month" else 1) if has_coupon else 0
        if has_coupon:
            self.log.add("discounts", "subscriptions", sub_id, f"{m['discounts']['pct']:.0%} x {coupon_periods}")
        one_off = 0.0
        if tier == "business" and cfg.on("one_off") and self._rand() < m["one_off"]["rate"]:
            one_off = float(m["one_off"]["amount"][currency])
            self.log.add("one_off", "subscriptions", sub_id, "onboarding fee")

        anchor = (trial_end or start).date()
        months_per = 12 if interval == "year" else 1
        pending: list[Proration] = []
        past_due = False
        idx = 0
        period_start = anchor
        while period_start <= end:
            period_end = add_months(anchor, months_per * (idx + 1))
            if idx == 0:
                attempt_at = trial_end if trial_end is not None else start
            else:
                attempt_at = ts(period_start, int(self.rng.integers(0, 86_400)))

            outcome, failed = "ok", 0
            if cfg.on("dunning") and self._rand() < m["dunning"]["fail_rate"]:
                retries = m["dunning"]["retry_days"]
                if self._rand() < m["dunning"]["recover_rate"]:
                    outcome, failed = "recovered", int(self.rng.integers(1, len(retries) + 1))
                else:
                    outcome, failed = "failed", len(retries) + 1
            self.periods.append(BillingPeriod(
                subscription_id=sub_id, customer_id=customer.id, currency=currency,
                period_start=period_start, period_end=period_end, items=[(plan, qty)], prorations=pending,
                coupon_pct=float(m["discounts"]["pct"]) if idx < coupon_periods else 0.0,
                one_off_amount=one_off if idx == 0 else 0.0, payment_outcome=outcome,
                first_attempt_at=attempt_at, kind="regular", failed_attempts=failed))
            pending = []

            if outcome == "failed":
                cancel_at = attempt_at + timedelta(days=int(m["dunning"]["cancel_after_days"]))
                self.log.add("dunning", "subscriptions", sub_id, "failed: canceled after dunning")
                if cancel_at.date() <= end:
                    sub.update(status="canceled", canceled_at=cancel_at, ended_at=cancel_at, _churn_reason="dunning")
                    self._close_item(cancel_at)
                    return "dunning", cancel_at, tier
                sub["status"] = "past_due"
                return None, None, tier
            if outcome == "recovered":
                self.log.add("dunning", "subscriptions", sub_id, f"recovered after {failed} failed attempts")
                recovered_at = attempt_at + timedelta(days=int(m["dunning"]["retry_days"][failed - 1]))
                past_due = recovered_at.date() > end
            else:
                past_due = False

            # voluntary churn
            if cfg.on("voluntary_churn"):
                p = 1 - (1 - m["voluntary_churn"]["monthly_rate"][tier]) ** months_per
                if self._rand() < p:
                    if interval == "month" and period_end <= end:
                        ended = ts(period_end)
                        sub.update(status="canceled", canceled_at=ended, ended_at=ended, _churn_reason="voluntary")
                        self._close_item(ended)
                        self.log.add("voluntary_churn", "subscriptions", sub_id, "monthly")
                        return "voluntary", ended, tier
                    if interval == "year":
                        span = (period_end - period_start).days
                        canceled_at = ts(period_start, int(self.rng.integers(86_400, (span - 1) * 86_400)))
                        if canceled_at.date() <= end:
                            sub.update(canceled_at=canceled_at, cancel_at_period_end=True)
                            self.log.add("voluntary_churn", "subscriptions", sub_id, "annual, cancel at period end")
                            if period_end <= end:
                                ended = ts(period_end)
                                sub.update(status="canceled", ended_at=ended, _churn_reason="voluntary")
                                self._close_item(ended)
                                return "voluntary", ended, tier
                            return None, None, tier

            # at most one plan or seat change per period
            change = self._draw_change(tier, qty, months_per)
            if change is not None:
                span = (period_end - period_start).days
                changed_at = ts(period_start, int(self.rng.integers(86_400, (span - 1) * 86_400)))
                if changed_at.date() <= end:
                    kind, new_tier, new_qty = change
                    new_plan = (plan_for(self.plans, new_tier, interval, currency, changed_at.date())
                                if new_tier != tier else plan)
                    self._close_item(changed_at)
                    self._open_item(sub_id, new_plan, new_qty, changed_at)
                    pr = Proration(plan, qty, new_plan, new_qty, changed_at.date(), period_start, period_end)
                    self.log.add(kind, "subscription_items", self.items[-1]["id"],
                                 f"{plan} x{qty} -> {new_plan} x{new_qty}")
                    if interval == "month":
                        pending = [pr]
                    else:
                        self.periods.append(BillingPeriod(
                            subscription_id=sub_id, customer_id=customer.id, currency=currency,
                            period_start=changed_at.date(), period_end=period_end, items=[], prorations=[pr],
                            coupon_pct=0.0, one_off_amount=0.0, payment_outcome="ok",
                            first_attempt_at=changed_at, kind="proration", failed_attempts=0))
                    plan, qty, tier = new_plan, new_qty, new_tier

            period_start = period_end
            idx += 1

        sub["status"] = "past_due" if past_due else "active"
        return None, None, tier

    def _draw_change(self, tier: str, qty: int, months: int) -> tuple[str, str, int] | None:
        m, cfg = self.m, self.cfg
        t = TIERS.index(tier)
        options: list[tuple[float, tuple[str, str, int]]] = []
        if cfg.on("plan_changes"):
            if t < len(TIERS) - 1:
                options.append((m["plan_changes"]["upgrade_rate"], ("plan_changes", TIERS[t + 1], qty)))
            if t > 0:
                options.append((m["plan_changes"]["downgrade_rate"], ("plan_changes", TIERS[t - 1], qty)))
        if cfg.on("seat_changes"):
            options.append((m["seat_changes"]["increase_rate"],
                            ("seat_changes", tier, qty + int(self.rng.integers(1, 4)))))
            if qty > 1:
                options.append((m["seat_changes"]["decrease_rate"],
                                ("seat_changes", tier, max(1, qty - int(self.rng.integers(1, 3))))))
        u = self._rand()
        acc = 0.0
        for rate, change in options:
            acc += min(rate * months, 0.25)
            if u < acc:
                return change
        return None


def _to_utc(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for c in cols:
        df[c] = pd.to_datetime(df[c], utc=True)
    return df


def simulate(cfg: Config, rng: np.random.Generator, customers: pd.DataFrame, plans: pd.DataFrame,
             log: MessLog) -> Lifecycle:
    sim = _Simulation(cfg, rng, plans, log)
    m = cfg.mess
    for pid in plans[plans.plan_id.str.endswith("_v2")].plan_id:
        log.add("price_change", "plans", pid, "new list price; v1 subscriptions grandfathered")
    deleted: set[str] = set()

    for c in (SimpleNamespace(**r) for r in customers.to_dict("records")):
        if c._kind == "junk":
            continue
        tier, interval, qty = sim._initial_choice()
        reason, ended_at, tier = sim.run_subscription(c, tier, interval, qty, c.created_at, first=True)
        while (reason in ("voluntary", "dunning") and cfg.on("reactivations")
               and sim._rand() < m["reactivations"]["rate"]):
            gap = int(rng.integers(m["reactivations"]["min_months"], m["reactivations"]["max_months"] + 1))
            back = add_months(ended_at.date(), gap) + timedelta(days=int(rng.integers(0, 28)))
            if back > cfg.end:
                break
            restart = ts(back, int(rng.integers(0, 86_400)))
            _, interval, qty = sim._initial_choice()
            reason, ended_at, tier = sim.run_subscription(c, tier, interval, qty, restart, first=False)
            log.add("reactivations", "subscriptions", sim.subs[-1]["id"], f"customer {c.id} returned")
        if (c._kind == "real" and reason in ("voluntary", "dunning") and cfg.on("soft_deleted")
                and sim._rand() < m["soft_deleted"]["churned_rate"]):
            deleted.add(c.id)
            log.add("soft_deleted", "customers", c.id, "churned real customer, later soft-deleted")

    subs = _to_utc(pd.DataFrame(sim.subs), ["started_at", "trial_end", "canceled_at", "ended_at"])
    items = _to_utc(pd.DataFrame(sim.items), ["valid_from", "valid_to"])
    return Lifecycle(subscriptions=subs, subscription_items=items, periods=sim.periods,
                     deleted_customer_ids=deleted)
