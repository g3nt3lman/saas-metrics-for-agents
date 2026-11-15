"""Generate the Ledgerly raw dataset.

    uv run python generator/generate.py [--seed 42] [--as-of 2026-10-31] [--config generator/config.yaml] [--out data]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledgerly.billing import build_billing  # noqa: E402
from ledgerly.catalog import build_fx_rates, build_plans  # noqa: E402
from ledgerly.config import Config, load_config  # noqa: E402
from ledgerly.customers import build_customers, make_faker  # noqa: E402
from ledgerly.events import build_events  # noqa: E402
from ledgerly.lifecycle import simulate  # noqa: E402
from ledgerly.messlog import MessLog  # noqa: E402
from ledgerly.truth import build_customer_month_facts  # noqa: E402
from ledgerly.writer import apply_as_of, content_hash, strip_private, write_tables  # noqa: E402


def build_world(cfg: Config) -> dict[str, pd.DataFrame]:
    """Full simulated history (private columns kept, no as-of applied)."""
    rng = np.random.default_rng(cfg.seed)
    log = MessLog()
    plans = build_plans(cfg)
    fx = build_fx_rates(cfg, rng)
    if cfg.on("fx_gaps"):
        for i in range((cfg.end - cfg.start).days + 1):
            d = date.fromordinal(cfg.start.toordinal() + i)
            if d.weekday() >= 5:
                log.add("fx_gaps", "fx_rates", d.isoformat(), "no rate published")
    customers = build_customers(cfg, rng, make_faker(cfg.seed), log)
    lc = simulate(cfg, rng, customers, plans, log)
    customers.loc[customers.id.isin(lc.deleted_customer_ids), "is_deleted"] = True
    tables = {"customers": customers, "plans": plans, "subscriptions": lc.subscriptions,
              "subscription_items": lc.subscription_items,
              **build_billing(cfg, rng, lc, plans, customers, log), "fx_rates": fx}
    tables["events"] = build_events(cfg, rng, tables, log)
    tables["truth/customer_month_facts"] = build_customer_month_facts(cfg, customers, lc, plans)
    tables["truth/mess_log"] = log.to_frame()
    return tables


def generate(cfg: Config, as_of: date | None = None) -> dict[str, pd.DataFrame]:
    """The dataset as the source system shows it at ``as_of`` (default: end of window), private columns removed."""
    as_of = as_of or cfg.end
    if not cfg.start <= as_of <= cfg.end:
        raise ValueError(f"--as-of {as_of} is outside the simulated window {cfg.start}..{cfg.end}")
    world = apply_as_of(build_world(cfg), as_of)
    return {k: strip_private(v).reset_index(drop=True) for k, v in world.items()}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--as-of", type=date.fromisoformat)
    ap.add_argument("--config", type=Path)
    ap.add_argument("--out", type=Path, default=Path("data"))
    args = ap.parse_args(argv)
    cfg = load_config(args.config, **({"seed": args.seed} if args.seed is not None else {}))
    tables = generate(cfg, args.as_of)
    write_tables(tables, args.out)
    for name, df in tables.items():
        print(f"{name:32s} {len(df):>8,d} rows")
    print(f"seed={cfg.seed} as_of={args.as_of or cfg.end} content_hash={content_hash(tables)}")


if __name__ == "__main__":
    main()
