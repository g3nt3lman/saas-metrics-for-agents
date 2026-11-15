# Ledgerly synthetic data generator

Ledgerly is a fictional B2B SaaS (invoicing software for SMBs). This generator simulates 24 months of its
billing history (**2024-11-01 → 2026-10-31**, ~2,000 customers, seed `42`) and writes it as a Stripe-like export.
The data is **deliberately messy** in the ways real billing data is messy, so that naive SQL gets MRR, churn
and NDR wrong, and a governed model gets them right.

```bash
make generate                      # seed 42, as of 2026-10-31 -> data/raw/*.parquet, data/truth/*.parquet
make generate SEED=7               # another universe
make generate AS_OF=2025-10-31     # the source system as it looked on that date
uv run python generator/generate.py --help
```

Same seed and config produce identical content. The CLI prints a `content_hash`; `pytest` checks it.

## How it works

The generator is a **simulation, not a set of independent random tables**. For every customer it walks
month by month through a lifecycle: trial → convert or expire → paid periods. In each period it decides,
in this order, the payment outcome (ok / failed then recovered / failed for good), then voluntary churn,
then at most one plan or seat change. Reactivations start a new subscription later. Every raw table is
derived from that one history, so the data is internally consistent: each proration line corresponds to a
real plan change, each refund to a real successful payment, each dunning churn to a real run of failed attempts.

```
config.yaml ──► catalog (plans, fx) ──► customers ──► lifecycle ──► billing ──► events
                                                          │                       │
                                                          └──► truth ◄────────────┘ ──► as-of ──► parquet
```

| Module | Responsibility |
|---|---|
| `ledgerly/config.py` | typed config, dotted-key overrides (`load_config(**{"mess.refunds.rate": 0})`) |
| `ledgerly/catalog.py` | plan catalogue (price versions), daily FX |
| `ledgerly/customers.py` | customers, internal and junk accounts |
| `ledgerly/lifecycle.py` | the simulation: subscriptions, item versions, billing periods |
| `ledgerly/billing.py` | invoices, line items, payment attempts, refunds |
| `ledgerly/events.py` | webhook event stream with duplicates and late arrivals |
| `ledgerly/truth.py` | ground-truth facts per subscription × month |
| `ledgerly/writer.py` | as-of view, hashing, Parquet output |

## Raw tables (`data/raw/`)

| Table | Grain | Notes |
|---|---|---|
| `customers` | customer | `is_deleted` soft-delete flag; `created_at` UTC |
| `plans` | price version (`plan_id`) | Stripe *price* semantics: a price change creates a new `plan_id`; `unit_amount` per seat, major units |
| `subscriptions` | subscription | `status ∈ trialing, active, past_due, canceled, incomplete_expired`; `cancel_at_period_end` |
| `subscription_items` | item version | one row per plan/seat state; `valid_to` null = current |
| `invoices` | invoice | `total` = Σ lines exactly; `status ∈ paid, open, uncollectible`; local currency |
| `invoice_line_items` | line | `type ∈ subscription, proration, one_off, discount`; signed `amount` |
| `payments` | payment attempt | `status ∈ succeeded, failed`; `attempted_at` is an **ISO-8601 string** (see timezone trap) |
| `refunds` | refund | partial or full; may be in a later month |
| `fx_rates` | currency × business day | `rate_to_eur`; EUR = 1.0; no weekends |
| `events` | delivery | `id` is **not unique** (re-deliveries); `ingested_at` can lag `created_at` by weeks |

## The mess, item by item

Every item is toggled with `mess.<key>.enabled` in `config.yaml`. Its instances are listed in
`data/truth/mess_log.parquet` (`mess_type = <key>`).

### `annual_plans` — annual billing (≈30% of subscriptions)
One invoice with one `subscription` line covers 12 months (`period_start` → `period_end`).
Annual price = 10× monthly.
**Breaks:** MRR if you sum invoices by month. That gives a 12× spike in the billing month and zero for the next 11.

### `trials` — 14-day free trial (35% never convert)
The first subscription of every customer starts with `trial_end = started_at + 14 days`. A trial produces no invoice.
Non-converters end as `status = incomplete_expired`, with `ended_at = trial_end`. Their subscription item still
points at a priced plan.
**Breaks:** MRR and active customers, if you count subscriptions with a plan instead of paid ones.

### `plan_changes` — upgrades and downgrades mid-cycle
The current `subscription_items` version is closed and a new one opens on the new tier's *current* price.
- **Monthly:** the next invoice carries two `proration` lines: a negative credit for the unused time on the old
  plan and a positive charge for the new one.
- **Annual:** the proration is invoiced immediately, on a separate invoice that has only proration lines.
  If the result is a credit (a downgrade), the invoice total is ≤ 0, it is `paid`, and there is no payment.

**Breaks:** MRR if you include prorations (they are revenue, not run-rate). Expansion and contraction if you ignore item versions.

### `seat_changes` — quantity up or down
Same mechanics as plan changes, but only `quantity` changes. These are the main source of expansion and contraction MRR.

### `discounts` — coupon, 20% off for 3 periods (15% of new subscriptions)
A negative `discount` line equal to 20% of the subscription subtotal. Monthly plans get it for 3 invoices,
annual plans for the first annual invoice.
**Breaks:** gross vs net MRR. You have to pick a definition.

### `dunning` — failed payments and retries (5% of invoices fail the first attempt)
Retries happen 3, 5 and 7 days after the first attempt.
- **Recovered (70%):** 1–3 failed attempts, then a success. The invoice is paid late.
- **Not recovered:** 4 failed attempts. The subscription is canceled 14 days after the first attempt
  (`canceled_at = ended_at`), and the invoice becomes `uncollectible`. If those 14 days run past the window end,
  the subscription is still `past_due`.

**Breaks:** churn timing. A failed payment is not churn yet. Is churn the first failure, the cancellation, or the month of the last paid period?

### `voluntary_churn` — customer cancels (starter 3%, pro 2%, business 1.5% per month)
- **Monthly:** `canceled_at = ended_at` = end of the current period.
- **Annual:** `cancel_at_period_end = true`, `canceled_at` falls somewhere mid-term, and `ended_at` = term end.
  The customer keeps paying run-rate until then.

**Breaks:** churn month, if you use `canceled_at` instead of `ended_at`.

### `refunds` — 3% of successful payments
60% are partial (10–60% of the payment), the rest are full. 30% are created in a **later calendar month** than the payment.
**Breaks:** net payments per month, and revenue ≠ cash ≠ MRR.

### Multi-currency (always on)
The currency is fixed by country: EUR (DE, FR, NL, ES, IT), USD (US, CA, GB) or PLN (PL). Invoices are in local
currency; reporting is in EUR via `fx_rates`.

### `fx_gaps` — no FX rates on weekends
Like ECB reference rates, there are no rates on Saturday or Sunday. An invoice dated on a weekend has no exact-date rate.
**Breaks:** an inner join on date silently drops revenue. You need an explicit rule (last available rate, month-start, monthly average).

### `reactivations` — churned customers come back (15% of churners)
1–9 months after `ended_at`, the same `customer_id` starts a **new** subscription. There is no trial and the
customer gets the current price.
**Breaks:** new vs reactivation MRR, and cohort assignment.

### `one_off` — onboarding fee (50% of new `business` subscriptions)
A `one_off` line on the first invoice: EUR 500 / USD 550 / PLN 2150.
**Breaks:** MRR if you sum invoice totals. It is revenue, not recurring.

### `price_change` — +15% list price on 2025-11-01
New `_v2` plan ids are created and the `_v1` rows flip to `active = false` (`updated_at = 2025-11-01`).
Existing subscriptions keep `_v1` (grandfathered) until they change plan. Subscriptions started after the change,
and plan changes after it, use `_v2`. Use this for the `plans_snapshot` demo (see as-of below).
**Breaks:** ARR if you price subscriptions at the current list price.

### `duplicate_events` — 2% of events re-delivered
Same `id` and `created_at`, later `ingested_at`.
**Breaks:** event counts, and incremental models without dedup.

### `late_events` — 3% of events arrive 3–40 days late
`ingested_at ≫ created_at`. Events not yet ingested by the as-of date are absent from the export.
**Breaks:** incremental models that filter on `created_at` or have no lookback window.

### `internal_customers` — ~1% `@ledgerly.io` test accounts
They have real subscriptions and invoices, so they leak into every metric unless excluded.

### `soft_deleted` — `is_deleted = true`
Two kinds:
- junk signups with no subscriptions (1%);
- real customers who churned and were later deleted (5% of churners). They **do** have billing history.

Excluding them rewrites history. Including them keeps historical MRR stable. That is a definition you decide.

### `timezone_trap` — `payments.attempted_at` as mixed-offset strings
The column is a string. It ends in `Z` (UTC) for most customers. For PLN customers it carries Warsaw local time with
an offset (`+01:00` in winter, `+02:00` in summer). Five payments sit at 00:00–00:59 local time on the 1st of a month.
Their UTC time is the previous day, so the previous month. Parsing the string naively (dropping the offset) puts
them in the wrong month.
**Breaks:** monthly cash, at month boundaries.

## As-of exports

`--as-of YYYY-MM-DD` emits the world as the source system showed it at the end of that day (UTC):
- rows created later are dropped;
- `canceled_at` and `ended_at` in the future are null, and statuses are recomputed;
- events not yet ingested are dropped;
- plans show the catalogue as it was then.

Uses:
- **Snapshot (SCD2) demo:** `make generate AS_OF=2025-10-31 && dbt snapshot`, then `make generate && dbt snapshot`.
  The v1 plans change `active` / `updated_at`, and v2 plans appear.
- **Incremental + late events:** build `stg_ledgerly__events` on an earlier as-of export, then regenerate fully
  and run incrementally. Late events with an old `created_at` arrive, and only an `ingested_at` lookback window catches them.

## Ground truth (`data/truth/`)

`customer_month_facts.parquet` has one row per subscription × month-end while the subscription exists,
including the month it ended. It records facts **before any business definition**:

| Column | Meaning |
|---|---|
| `state_at_month_end` | `trialing`, `active`, `past_due`, `canceled_pending_end`, or `ended` (in the month it ended) |
| `plan_id`, `tier`, `interval`, `currency`, `quantity` | the item version live at month end (for `ended`: the last one) |
| `run_rate_mrr_local_list` | list run-rate in local currency = unit × seats (÷ 12 for annual). Not FX-converted, not discounted, not zeroed for trials |
| `discount_mrr_local` | run-rate of the coupon active at month end (positive number) |
| `is_internal`, `is_deleted` | flags for exclusion rules |

It deliberately does **not** contain an "MRR" column. The churn, FX, discount, trial, internal and
soft-delete rules belong in `docs/metric_definitions.md`. Your reconciliation tests apply them on top of these facts and
compare the result with `fct_mrr_monthly`.

`mess_log.parquet` has one row per injected mess instance (`mess_type`, `table`, `object_id`, `note`).

Do not expose `data/truth` to the agent. It exists to grade the models, not to answer questions.

## Tests

`uv run pytest` covers:
- determinism (same seed gives the same hash; a different seed gives a different one);
- referential integrity;
- invoice totals equal the sum of their lines;
- refunds never exceed their payments;
- dunning and as-of semantics;
- every mess type occurs at least once at the default size;
- a disabled mess type is absent.
