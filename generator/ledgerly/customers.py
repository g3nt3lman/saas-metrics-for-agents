"""Customers: real SMBs, internal/test accounts (@ledgerly.io) and junk soft-deleted signups."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from faker import Faker

from .config import Config
from .messlog import MessLog

COUNTRY_LOCALE = {
    "DE": "de_DE", "FR": "fr_FR", "NL": "nl_NL", "ES": "es_ES", "IT": "it_IT",
    "US": "en_US", "CA": "en_CA", "GB": "en_GB", "PL": "pl_PL",
}
_B36 = np.array(list("0123456789abcdefghijklmnopqrstuvwxyz"))


def make_faker(seed: int) -> Faker:
    fake = Faker(sorted(set(COUNTRY_LOCALE.values())))
    fake.seed_instance(seed)
    return fake


def new_id(rng: np.random.Generator, prefix: str, taken: set[str], length: int = 14) -> str:
    """Stripe-like id (``cus_…``) drawn from rng; unique within ``taken`` (which is updated)."""
    while True:
        candidate = f"{prefix}_{''.join(rng.choice(_B36, length))}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate


def _signup_days(cfg: Config, rng: np.random.Generator, n: int) -> np.ndarray:
    span = (cfg.end - cfg.start).days + 1
    weights = np.linspace(1.5, 0.5, span)
    return rng.choice(span, size=n, p=weights / weights.sum())


def build_customers(cfg: Config, rng: np.random.Generator, faker: Faker, log: MessLog) -> pd.DataFrame:
    countries = list(cfg.country_weights)
    cw = np.array([cfg.country_weights[c] for c in countries])
    n = cfg.n_customers
    country_idx = rng.choice(len(countries), size=n, p=cw / cw.sum())
    days = _signup_days(cfg, rng, n)
    seconds = rng.integers(0, 86_400, size=n)
    kind_draw = rng.random(n)
    internal_rate = cfg.mess["internal_customers"]["rate"] if cfg.on("internal_customers") else 0.0
    junk_rate = cfg.mess["soft_deleted"]["junk_rate"] if cfg.on("soft_deleted") else 0.0

    taken: set[str] = set()
    rows = []
    start = datetime(cfg.start.year, cfg.start.month, cfg.start.day, tzinfo=timezone.utc)
    for i in range(n):
        country = countries[country_idx[i]]
        kind = "internal" if kind_draw[i] < internal_rate else (
            "junk" if kind_draw[i] < internal_rate + junk_rate else "real")
        fk = faker[COUNTRY_LOCALE[country]]
        company = fk.company()
        if kind == "internal":
            email = f"{fk.user_name()}+test{i}@ledgerly.io"
            company = f"Ledgerly QA {i}"
        else:
            email = fk.company_email()
        cid = new_id(rng, "cus", taken)
        rows.append({
            "id": cid, "email": email, "company_name": company, "country": country,
            "created_at": pd.Timestamp(start + timedelta(days=int(days[i]), seconds=int(seconds[i]))),
            "is_deleted": kind == "junk",
            "_kind": kind, "_currency": cfg.country_currency[country],
        })
        if kind == "internal":
            log.add("internal_customers", "customers", cid, email)
        elif kind == "junk":
            log.add("soft_deleted", "customers", cid, "junk signup, no paid history")
    return pd.DataFrame(rows).sort_values("created_at", kind="stable").reset_index(drop=True)
