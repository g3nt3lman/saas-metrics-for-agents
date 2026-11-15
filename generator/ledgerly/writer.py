"""As-of filtering, private-column stripping, content hashing and Parquet output."""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .catalog import CATALOG_CREATED, _ts
from .lifecycle import add_months, ts

RAW_TABLES = ("customers", "plans", "subscriptions", "subscription_items", "invoices", "invoice_line_items",
              "payments", "refunds", "fx_rates", "events")


def _null_after(s: pd.Series, cutoff: pd.Timestamp) -> pd.Series:
    return s.where(s.isna() | (s < cutoff))


def apply_as_of(tables: dict[str, pd.DataFrame], as_of: date) -> dict[str, pd.DataFrame]:
    """The world as the source system showed it at the end of ``as_of`` (UTC).

    Expects unstripped tables (private ``_`` columns present).
    """
    cutoff = ts(as_of + timedelta(days=1))
    t = {k: v.copy() for k, v in tables.items()}

    plans = t["plans"][t["plans"].valid_from <= as_of].copy()
    future = plans.updated_at >= cutoff
    plans.loc[future, "active"] = True
    plans.loc[future, "updated_at"] = _ts(CATALOG_CREATED)
    t["plans"] = plans.reset_index(drop=True)

    t["fx_rates"] = t["fx_rates"][t["fx_rates"].date <= as_of].reset_index(drop=True)

    pay = t["payments"][t["payments"]._attempted_at_utc < cutoff].reset_index(drop=True)
    t["payments"] = pay
    t["refunds"] = t["refunds"][t["refunds"].created_at < cutoff].reset_index(drop=True)

    subs = t["subscriptions"][t["subscriptions"].started_at < cutoff].copy()
    canceled_later = subs.canceled_at.notna() & (subs.canceled_at >= cutoff)
    subs.loc[canceled_later, "cancel_at_period_end"] = False
    subs["canceled_at"] = _null_after(subs.canceled_at, cutoff)
    subs["ended_at"] = _null_after(subs.ended_at, cutoff)
    subs.loc[subs.ended_at.isna(), "_churn_reason"] = None

    inv = t["invoices"][t["invoices"].created_at < cutoff].copy()
    succeeded = set(pay[pay.status == "succeeded"].invoice_id)
    failed = set(pay[pay.status == "failed"].invoice_id)
    dunning_ended = set(subs[subs._churn_reason == "dunning"].id)
    paid = inv.id.isin(succeeded) | (inv.total <= 0)
    unc = ~paid & inv.subscription_id.isin(dunning_ended) & inv.id.isin(failed)
    inv["status"] = "open"
    inv.loc[unc, "status"] = "uncollectible"
    inv.loc[paid, "status"] = "paid"
    t["invoices"] = inv.reset_index(drop=True)
    t["invoice_line_items"] = t["invoice_line_items"][
        t["invoice_line_items"].invoice_id.isin(set(inv.id))].reset_index(drop=True)

    past_due_subs = set(inv[(inv.status == "open") & inv.id.isin(failed)].subscription_id)
    status = pd.Series("active", index=subs.index, dtype=object)
    status[subs.id.isin(past_due_subs)] = "past_due"
    status[subs.trial_end.notna() & (subs.trial_end >= cutoff)] = "trialing"
    status[subs.ended_at.notna()] = "canceled"
    status[subs.ended_at.notna() & (subs._churn_reason == "trial_expired")] = "incomplete_expired"
    subs["status"] = status.astype(t["subscriptions"].status.dtype)
    t["subscriptions"] = subs.reset_index(drop=True)

    items = t["subscription_items"][t["subscription_items"].subscription_id.isin(set(subs.id))
                                    & (t["subscription_items"].valid_from < cutoff)].copy()
    items["valid_to"] = _null_after(items.valid_to, cutoff)
    t["subscription_items"] = items.reset_index(drop=True)

    cust = t["customers"][t["customers"].created_at < cutoff].copy()
    still_live = set(subs[subs.ended_at.isna()].customer_id)
    revived = (cust._kind == "real") & cust.id.isin(still_live)
    cust.loc[revived, "is_deleted"] = False
    t["customers"] = cust.reset_index(drop=True)

    t["events"] = t["events"][t["events"].ingested_at < cutoff].reset_index(drop=True)

    if "truth/customer_month_facts" in t:
        f = t["truth/customer_month_facts"]
        keep = [add_months(m, 1) <= as_of + timedelta(days=1) for m in f.month]
        f = f[keep]
        f = f[f.subscription_id.isin(set(subs.id))].copy()
        f["is_deleted"] = f.customer_id.map(cust.set_index("id").is_deleted).astype(bool)
        t["truth/customer_month_facts"] = f.reset_index(drop=True)
    if "truth/mess_log" in t:
        log = t["truth/mess_log"]
        survivors = {name: set(t[name].id) for name in ("customers", "subscriptions", "subscription_items",
                                                         "payments", "refunds", "events")}
        survivors["plans"] = set(t["plans"].plan_id)
        # fx_gaps points at dates that are missing by design: keep those up to as-of
        survivors["fx_rates"] = {(as_of - timedelta(days=i)).isoformat()
                                 for i in range((as_of - min(t["fx_rates"].date)).days + 1)}
        keep = [oid in survivors.get(tbl, set()) for tbl, oid in zip(log.table, log.object_id)]
        t["truth/mess_log"] = log[keep].reset_index(drop=True)
    return t


def strip_private(df: pd.DataFrame) -> pd.DataFrame:
    return df[[c for c in df.columns if not c.startswith("_")]]


def content_hash(tables: dict[str, pd.DataFrame]) -> str:
    h = hashlib.sha256()
    for name in sorted(tables):
        df = tables[name]
        h.update(name.encode())
        h.update(",".join(map(str, df.columns)).encode())
        h.update(pd.util.hash_pandas_object(df, index=False).values.tobytes())
    return h.hexdigest()


def write_tables(tables: dict[str, pd.DataFrame], out_dir: Path) -> list[Path]:
    """raw tables -> <out>/raw/<name>.parquet; 'truth/<name>' -> <out>/truth/<name>.parquet."""
    written = []
    for name, df in tables.items():
        path = Path(out_dir) / (f"{name}.parquet" if "/" in name else f"raw/{name}.parquet")
        path.parent.mkdir(parents=True, exist_ok=True)
        strip_private(df).to_parquet(path, index=False)
        written.append(path)
    return written
