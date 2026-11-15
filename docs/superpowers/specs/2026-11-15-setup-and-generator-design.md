# Design: environment setup + Ledgerly data generator

Date: 2026-11-15 · Scope: milestones 1–2 of `PROJECT_BRIEF.md` (Weekend 1)

## 1. Intent

Give the author a working, reproducible foundation so they can spend their own time on the
analytics-engineering part (staging → marts → semantic layer), which they will write themselves.

Two deliverables:

1. **Environment:** `uv` project, DuckDB, dbt-core 1.11 + dbt-duckdb, profile, packages,
   sources pointing at the raw Parquet files, Makefile, CI.
2. **Synthetic data generator** for "Ledgerly" that writes 10 Stripe-like raw tables to
   `data/raw/*.parquet`, with every "mess" item from brief §4 injected on purpose and
   documented, plus a ground-truth file the author's reconciliation tests can compare against.

Per the author's choice (option A), Claude writes the generator in full, with tests and README;
the author reviews it.

## 2. Goals, non-goals, anti-goals

**Goals**
- `make setup && make generate` works on a clean machine (and in CI) with one seed producing
  byte-identical data.
- Data is *internally consistent*: every proration line matches a real plan/seat change, every
  refund refers to a real successful payment, every dunning churn has a real sequence of failed
  attempts. Red reconciliation tests should mean a bug in the models, never in the generator.
- Every mess type is configurable (`generator/config.yaml`), documented in `generator/README.md`
  (what it is, config key, where it shows up in raw data, which metric it breaks), and proven
  to occur by a pytest.
- `dbt debug`, `dbt deps`, `dbt parse` green; every source is queryable from dbt.

**Non-goals (author writes these)**
- Staging, snapshots, intermediate, marts, unit/reconciliation tests, semantic layer, MCP, eval.
- `docs/metric_definitions.md` content. Only a stub with the five open questions as headings
  is created.

**Anti-goals**
- The generator must not pre-decide the business definitions the author owns (churn timing,
  FX rule, gross vs net of discounts, reactivation window, soft-delete handling). The ground
  truth therefore holds *facts before definitions*, not a final "MRR" number (see §6).
- No non-determinism (no wall clock, no unseeded RNG, no set/dict iteration order dependence).
- No cleverness the author couldn't explain in an interview: a plain, readable, monthly
  simulation over modest pure-Python loops.

## 3. Approaches considered

| | Approach | Trade-off | Decision |
|---|---|---|---|
| 1 | Customer lifecycle simulation → derive all tables + ground-truth file | Most code (~600–900 lines) but consistent data and an independent oracle | **Chosen** |
| 2 | Same simulation, no ground-truth file | Simpler; but eval/reconciliation ground truth can only come from the marts themselves | Rejected |
| 3 | Each table generated independently, then stitched | Fast, but inconsistent; tests go red because of the generator | Rejected early |

Raw → dbt loading: Parquet files registered as dbt sources via dbt-duckdb
`meta.external_location` (no separate load step). Chosen by Claude, agreed in conversation.

## 4. Environment

### Tooling
- `uv` installed per its official installer (needs the author's OK; it installs into `~/.local/bin`).
  The global `~/.local/bin/dbt` is ignored; everything runs via `uv run` from the project `.venv`.
- Python: `requires-python = ">=3.11"`; local dev on the machine's 3.12.
- **Dependencies** (all from brief §3; versions checked against PyPI/docs at implementation time,
  not invented):
  - runtime: `dbt-core~=1.11`, `dbt-duckdb`, `duckdb`, `numpy`, `pandas`, `pyarrow`, `faker`, `pyyaml`
  - semantic layer: `dbt-metricflow[duckdb]` — installed now so dependency resolution with
    dbt-core 1.11 is proven on day one. If it cannot resolve with 1.11, stop and report to the author
    rather than downgrading dbt.
  - dev: `pytest`, `sqlfluff` + `sqlfluff-templater-dbt`
  - dbt package: `dbt_utils` (version per hub.getdbt.com compatible with 1.11). `dbt_expectations`
    not added.
  - `mcp` deferred to milestone 8.

### Files
- `pyproject.toml`, `uv.lock` (committed).
- `profiles.yml` in repo root: profile `ledgerly`, target `dev`, type duckdb,
  `path: "{{ env_var('DUCKDB_PATH', 'warehouse.duckdb') }}"`, threads 4. Makefile exports
  `DBT_PROFILES_DIR=.`.
- `dbt_project.yml`: name `saas_metrics`, folder-level defaults: staging `view`,
  intermediate `view` (author may switch to ephemeral), marts `table`, snapshots in `snapshots/`.
  Empty model folders kept with `.gitkeep` matching brief §10.
- `packages.yml`: `dbt_utils`.
- `models/staging/ledgerly/_ledgerly__sources.yml`: source `ledgerly`, 10 tables, each with
  `meta.external_location: "data/raw/{name}.parquet"` and a one-line description; `events` gets
  `loaded_at_field: ingested_at` + freshness thresholds (warn 2 days / error 7 days relative to
  the generator's "as-of" date — see note below). Column-level docs/tests are left to the author.
- `docs/metric_definitions.md`: stub with the five questions from brief §5 as headings, empty.
- `.gitignore`: `.venv/`, `data/`, `warehouse.duckdb*`, `target/`, `dbt_packages/`, `logs/`,
  `*:Zone.Identifier`, `.pytest_cache/`. The stray `PROJECT_BRIEF.md:Zone.Identifier` is deleted.
- `.sqlfluff`: dialect duckdb, templater dbt, lowercase keywords/identifiers (brief §5 style).

**Freshness note:** generated data ends in the past (Oct 2026), so freshness against "now" will
always fail. Decision (Claude's call): freshness thresholds are set and the source is configured,
but the Makefile's `build` target does not run `dbt source freshness`; a separate `make freshness`
does, and the README explains that it is expected to warn on static synthetic data.

### Makefile targets
`setup` (uv sync, dbt deps), `generate` (seed from config, override with `SEED=`), `build`
(generate if missing, then `dbt build`), `test` (pytest), `lint` (sqlfluff lint models),
`debug` (dbt debug), `freshness`, `docs` (dbt docs generate/serve), `clean`
(target, dbt_packages, warehouse, data). `eval`/`serve` added in later milestones.

### CI (`.github/workflows/ci.yml`)
On PR and push to main: checkout → `astral-sh/setup-uv` (pinned version) → `make setup` →
`make generate` → `dbt debug` → `dbt parse` → `make test` → `make lint` (non-blocking until
models exist). Later milestones extend it with `dbt build` and `mf validate-configs`.

### Git
Repo has no commits. First commit on `main`: brief + this spec + `.gitignore`. All setup and
generator work on a feature branch (`feat/setup-and-generator`) merged via PR (cert §13 Git
workflow). No remote exists yet; the author adds it.

## 5. Generator

### Time frame and scale
- 24 full months: **2024-11-01 → 2026-10-31** (so "Q2 2026" and the "2025-01 cohort after 12 months"
  eval questions from brief §9 have data).
- ~2,000 customers acquired over the window (front-loaded slightly so early cohorts have 12+ months).
- Seed 42 default; `--seed` CLI override.
- `--as-of YYYY-MM-DD` option (default = end of window): emits the world as the source system
  would have shown it on that date — rows created after it are dropped, mutable fields
  (statuses, `canceled_at`, plan `active`) show their value at that date, and events not yet
  ingested are dropped. This lets the author demo the **snapshot** (run before/after the price
  change) and **incremental + late-arriving events** (generate as-of an earlier date, build, then
  regenerate full and rebuild incrementally).

### Business world
- Tiers: `starter`, `pro`, `business`; per-seat pricing; intervals `month` and `year`
  (annual = 10× monthly, i.e. two months free). ~30% of subscriptions annual.
- Currencies by customer country: EUR (DE, FR, NL, ES…), USD (US, CA, GB), PLN (PL).
  Approx. mix 50/30/20. Currency fixed per customer.
- Plan catalogue: tier × interval × currency = 18 prices, each a `plan_id` (Stripe *price*
  semantics: a price change creates a new `plan_id`).

### Raw tables (columns per brief §4, details below)
- `customers`: id, email, company_name, country, created_at, is_deleted.
- `plans`: plan_id, product (`tier`), tier, interval, currency, unit_amount (decimal major
  units, per seat), valid_from, `active`, `updated_at`. The month-12
  price change (2025-11-01) adds v2 plan_ids with higher prices; v1 rows flip `active=false`
  with a new `updated_at`. Grandfathered subscriptions keep v1 plan_ids.
- `subscriptions`: id, customer_id, status (`trialing|active|past_due|canceled|incomplete_expired`),
  started_at, trial_end, canceled_at, ended_at, `cancel_at_period_end`.
- `subscription_items`: subscription_id, plan_id, quantity, valid_from, valid_to (null = current).
  A new version per plan change or seat change.
- `invoices`: id, customer_id, subscription_id, period_start, period_end, currency, total,
  status (`paid|open|uncollectible|void`), `created_at`.
- `invoice_line_items`: id, invoice_id, type (`subscription|proration|one_off|discount`),
  amount (signed), period_start, period_end, plan_id (nullable), quantity (nullable).
- `payments`: id, invoice_id, amount, currency, status (`succeeded|failed`), attempted_at
  (**ISO-8601 string**, see timezone trap).
- `refunds`: id, payment_id, amount, created_at, reason.
- `fx_rates`: date, currency, rate_to_eur (EUR row = 1.0). Seeded random walk around realistic
  levels.
- `events`: id, type, object_id, payload_json, created_at, ingested_at. One event per lifecycle
  change (customer.created, subscription.created/updated/deleted, invoice.created/paid,
  payment.succeeded/failed, refund.created).

### Billing mechanics
- Billing in advance at period start; annual invoices cover 12 months in one subscription line.
- Proration: Stripe-style, on the **next** invoice: a negative line crediting unused time on
  the old item and a positive line charging remaining time on the new item (day-based).
- Invoice `total` = sum of its lines; reconciles exactly.

### Mess list (all toggled/parameterised in `config.yaml`; default rates are Claude's calls)

| Mess | Mechanics | Default |
|---|---|---|
| Annual plans | one invoice covers 12 months | ~30% of subs |
| Trials | 14-day trial, no invoice; non-converters end `incomplete_expired` | 35% never convert |
| Upgrades/downgrades | mid-cycle plan change + proration lines (±) | 2% / 1% per active month |
| Seat changes | quantity change, new item version + proration | +5% / −3% per month |
| Discounts | coupon 20% off for 3 months as negative `discount` line | 15% of new subs |
| Failed payments + dunning | retries at +3/+5/+7 days; 60% recover; else canceled 14 days after first failure, invoice `uncollectible` | 6% of invoices fail first attempt |
| Voluntary churn | monthly: canceled_at = ended_at; annual: `cancel_at_period_end`, canceled_at mid-term, ended_at at term end | 2–4%/month by tier |
| Refunds | partial (60%) and full; 30% land in a later month than the payment | 3% of payments |
| Multi-currency | EUR/USD/PLN invoices, EUR reporting | per country |
| FX gaps (extra) | no rates on weekends (ECB-like); forces an explicit fill rule | on |
| Reactivations | churned customer returns 1–9 months later with a *new* subscription, same customer_id | 15% of churned |
| One-off charges | onboarding fee line (`one_off`) on first invoice | 50% of new `business` |
| Price change | 2025-11-01 new v2 prices (+15%); existing subs grandfathered on v1 | on |
| Duplicate events | same event id re-delivered with later `ingested_at` | 2% |
| Late events | `ingested_at` = created_at + 3–40 days | 3% |
| Internal/test customers | `@ledgerly.io` emails with real paid subscriptions (so they leak MRR if not excluded) | ~1% |
| Soft-deleted | `is_deleted=true`: both junk signups with no paid history *and* some churned real customers (exclusion choice is the author's to make and document) | ~2% |
| Timezone trap | `payments.attempted_at` is an ISO string with mixed offsets (`Z` vs `+01:00/+02:00` Europe/Warsaw for PLN customers); attempts near midnight on month-end shift month if parsed naively | on |

### Ground truth (`data/truth/`, git-ignored, never registered as a dbt source for the agent)
- `customer_month_facts.parquet` — one row per customer × month-end (only months where the
  customer has any subscription), facts **before** the author's definitions:
  customer_id, month, state_at_month_end (`trialing|active|past_due|canceled_pending_end|ended|none`),
  subscription_id, plan_id, tier, interval, currency, quantity,
  `run_rate_mrr_local_list` (annual/12, seats × unit price), `discount_mrr_local` (run-rate
  of active coupon), is_internal, is_deleted.
  The author's reconciliation test applies their own churn/FX/discount rules on top and
  compares with `fct_mrr_monthly`.
- `mess_log.parquet` — (mess_type, table, object_id, note): every injected mess instance.
  Used by the "each mess occurs" pytest and for debugging.

The author may choose to register `data/truth` as a separate dbt source (e.g. `ledgerly_truth`)
for singular tests; that is their call when writing tests.

### Code layout
```
generator/
├── generate.py        # CLI: --seed, --as-of, --config, --out
├── config.yaml
├── README.md          # world, tables, every mess item, how to regenerate
├── ledgerly/
│   ├── config.py      # typed config loading
│   ├── catalog.py     # plans, fx_rates
│   ├── customers.py   # customers incl. internal / soft-deleted
│   ├── lifecycle.py   # month-by-month state machine per customer
│   ├── billing.py     # invoices, line items, payments, dunning, refunds
│   ├── events.py      # events + duplicates/late
│   ├── truth.py       # customer_month_facts, mess_log
│   └── writer.py      # as-of filtering, parquet writing
└── tests/
    ├── test_determinism.py   # same seed → same content hash (hash_pandas_object per table)
    ├── test_integrity.py     # FKs, invoice total = Σ lines, refunds ≤ payment, item validity non-overlapping
    └── test_mess_present.py  # every mess type occurs ≥1 (via mess_log + direct checks)
```

## 6. Done when

- `make setup && make generate` on a fresh clone produces 10 raw Parquet files + 2 truth files.
- `uv run pytest` green; determinism test proves byte-stable content for seed 42.
- `dbt debug`, `dbt deps`, `dbt parse` green; `dbt show --inline "select count(*) from {{ source('ledgerly','invoices') }}"`
  returns rows for every source.
- `generator/README.md` documents every mess item; `docs/metric_definitions.md` stub exists.
- CI workflow file present and passing locally-equivalent steps.

## 7. Left to the builder

Exact default probabilities, Faker locales, FX starting levels and volatility, event payload
shapes, ID formats (`cus_…`, `sub_…`, `in_…`, Stripe-like prefixes), exact sqlfluff rules.
