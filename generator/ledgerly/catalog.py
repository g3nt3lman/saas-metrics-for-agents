"""Plan catalogue (Stripe *price* semantics) and daily FX rates."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .config import Config

CATALOG_CREATED = date(2024, 1, 1)
INTERVALS = ("month", "year")


def _ts(d: date) -> pd.Timestamp:
    return pd.Timestamp(datetime(d.year, d.month, d.day, tzinfo=timezone.utc))


def build_plans(cfg: Config) -> pd.DataFrame:
    """One row per plan_id. A price change creates v2 plan_ids; v1 rows become inactive."""
    change = cfg.on("price_change")
    rows = []
    for tier, prices in cfg.tiers.items():
        for interval in INTERVALS:
            for currency, monthly in prices.items():
                v1 = round(monthly * (cfg.annual_multiplier if interval == "year" else 1), 2)
                base = f"price_{tier}_{interval}_{currency.lower()}"
                rows.append({
                    "plan_id": f"{base}_v1", "product": f"prod_{tier}", "tier": tier, "interval": interval,
                    "currency": currency, "unit_amount": v1, "valid_from": CATALOG_CREATED,
                    "active": not change,
                    "updated_at": _ts(cfg.price_change.date if change else CATALOG_CREATED),
                })
                if change:
                    rows.append({
                        "plan_id": f"{base}_v2", "product": f"prod_{tier}", "tier": tier, "interval": interval,
                        "currency": currency, "unit_amount": round(v1 * (1 + cfg.price_change.pct), 2),
                        "valid_from": cfg.price_change.date, "active": True,
                        "updated_at": _ts(cfg.price_change.date),
                    })
    return pd.DataFrame(rows)


def build_fx_rates(cfg: Config, rng: np.random.Generator) -> pd.DataFrame:
    """Daily rate_to_eur per currency (EUR = 1). Weekends omitted when fx_gaps is on (ECB-like)."""
    days = [cfg.start + timedelta(d) for d in range((cfg.end - cfg.start).days + 1)]
    frames = [pd.DataFrame({"date": days, "currency": "EUR", "rate_to_eur": 1.0})]
    for currency, p in cfg.fx.items():
        walk = p["start"] * np.exp(np.cumsum(rng.normal(0.0, p["vol"], len(days))))
        frames.append(pd.DataFrame({"date": days, "currency": currency, "rate_to_eur": np.round(walk, 6)}))
    fx = pd.concat(frames, ignore_index=True)
    if cfg.on("fx_gaps"):
        fx = fx[[d.weekday() < 5 for d in fx.date]].reset_index(drop=True)
    return fx


def plan_for(plans: pd.DataFrame, tier: str, interval: str, currency: str, on: date) -> str:
    """plan_id of the list price valid on the given date."""
    c = plans[(plans.tier == tier) & (plans.interval == interval) & (plans.currency == currency)
              & (plans.valid_from <= on)]
    return c.sort_values("valid_from").plan_id.iloc[-1]
