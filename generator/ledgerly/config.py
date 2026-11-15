"""Typed loading of generator/config.yaml with dotted-key overrides."""

from __future__ import annotations

import copy
import calendar
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"

MESS_KEYS = (
    "annual_plans", "trials", "plan_changes", "seat_changes", "discounts", "dunning",
    "voluntary_churn", "refunds", "fx_gaps", "reactivations", "one_off", "price_change",
    "duplicate_events", "late_events", "internal_customers", "soft_deleted", "timezone_trap",
)


@dataclass(frozen=True)
class PriceChange:
    date: date
    pct: float


@dataclass(frozen=True)
class Config:
    seed: int
    start: date
    months: int
    n_customers: int
    country_currency: dict[str, str]
    country_weights: dict[str, float]
    tiers: dict[str, dict[str, float]]
    tier_weights: dict[str, float]
    seats: dict[str, tuple[int, int]]
    fx: dict[str, dict[str, float]]
    price_change: PriceChange
    annual_multiplier: float = 10
    mess: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def end(self) -> date:
        """Last day of the simulated window (inclusive)."""
        m = self.start.month - 1 + self.months - 1
        year, month = self.start.year + m // 12, m % 12 + 1
        return date(year, month, calendar.monthrange(year, month)[1])

    def on(self, key: str) -> bool:
        """Is the given mess type enabled?"""
        return bool(self.mess[key]["enabled"])


def _set_dotted(raw: dict, dotted: str, value: Any) -> None:
    *parents, leaf = dotted.split(".")
    node = raw
    for p in parents:
        if p not in node:
            raise KeyError(f"unknown config key: {dotted}")
        node = node[p]
    if leaf not in node:
        raise KeyError(f"unknown config key: {dotted}")
    node[leaf] = value


def load_config(path: Path | None = None, **overrides: Any) -> Config:
    """Load the YAML config; keyword overrides use dotted keys, e.g. ``**{"mess.refunds.rate": 0}``."""
    raw = yaml.safe_load(Path(path or DEFAULT_CONFIG).read_text())
    raw = copy.deepcopy(raw)
    for key, value in overrides.items():
        _set_dotted(raw, key, value)
    missing = set(MESS_KEYS) - set(raw["mess"])
    if missing:
        raise KeyError(f"config is missing mess keys: {sorted(missing)}")
    return Config(
        seed=int(raw["seed"]),
        start=raw["start"],
        months=int(raw["months"]),
        n_customers=int(raw["n_customers"]),
        country_currency={c: v[0] for c, v in raw["countries"].items()},
        country_weights={c: float(v[1]) for c, v in raw["countries"].items()},
        tiers={t: {cur: float(p) for cur, p in prices.items()} for t, prices in raw["tiers"].items()},
        tier_weights={t: float(w) for t, w in raw["tier_weights"].items()},
        seats={t: (int(r[0]), int(r[1])) for t, r in raw["seats"].items()},
        fx={cur: {k: float(v) for k, v in p.items()} for cur, p in raw["fx"].items()},
        price_change=PriceChange(date=raw["price_change"]["date"], pct=float(raw["price_change"]["pct"])),
        annual_multiplier=float(raw["annual_multiplier"]),
        mess=raw["mess"],
    )
