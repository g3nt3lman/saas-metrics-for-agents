# Environment Setup + Ledgerly Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reproducible uv + DuckDB + dbt 1.11 project whose `make setup && make generate` produces 10 consistent, deliberately messy Stripe-like raw Parquet tables plus ground-truth files, queryable as dbt sources.

**Architecture:** A seeded month-by-month lifecycle simulation per customer decides *what happened* (subscriptions, item versions, billing periods with payment outcomes, cancellations, reactivations). Billing, events and truth modules derive tables from that history; a writer applies `--as-of` filtering, strips private `_` columns and writes Parquet. dbt reads Parquet through dbt-duckdb `external_location` sources.

**Tech Stack:** Python ≥3.11, uv, numpy, pandas, pyarrow, Faker, PyYAML, pytest, dbt-core ~1.11, dbt-duckdb, dbt-metricflow[duckdb], sqlfluff + dbt templater, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-15-setup-and-generator-design.md`

## Global Constraints

- Window: 2024-11-01 → 2026-10-31 (24 months); price change 2025-11-01 (+15%, v2 plan_ids, v1 grandfathered).
- Default seed 42; ~2,000 customers; same seed ⇒ identical table content.
- No wall clock, no unseeded randomness; one `numpy.random.Generator` threaded through; `Faker.seed_instance(seed)`.
- Outputs: `data/raw/<table>.parquet` (10 tables), `data/truth/customer_month_facts.parquet`, `data/truth/mess_log.parquet`. `data/` git-ignored.
- Columns starting with `_` are internal and never written.
- Generator must not compute a final "MRR"; truth holds pre-definition facts only.
- Never downgrade dbt-core below 1.11 to satisfy dbt-metricflow; stop and report instead.
- Dependency versions are looked up (PyPI / hub.getdbt.com) at install time, never invented.
- SQL style: lowercase. Commits on branch `feat/setup-and-generator`; first commit on `main`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Mess toggled off in config (e.g. `refunds.rate: 0`) → generation still succeeds and that mess is absent. Test in Task 9.
2. `--as-of` inside the window (e.g. 2025-10-31) → no row created after as-of, no subscription `canceled_at > as_of`, v2 plans absent, v1 `active=true`. Test in Task 9.
3. Different seed → different content hash (determinism test isn't vacuous). Test in Task 9.
4. Small config (`n_customers: 50`) runs and integrity holds (used by all tests for speed; mess-presence uses full default size). Tests in Tasks 4–9.
5. dbt invoked from repo root resolves relative `external_location` paths; Makefile always runs from root. Verified in Task 10.

---

## File Map

| File | Responsibility |
|---|---|
| `.gitignore`, `pyproject.toml`, `uv.lock` | env |
| `dbt_project.yml`, `profiles.yml`, `packages.yml`, `.sqlfluff` | dbt config |
| `models/{staging/ledgerly,intermediate,marts,utils}/.gitkeep`, `snapshots/`, `semantic_models/`, `tests/`, `macros/` `.gitkeep` | skeleton (brief §10) |
| `models/staging/ledgerly/_ledgerly__sources.yml` | 10 sources |
| `docs/metric_definitions.md` | stub, 5 headings |
| `Makefile`, `.github/workflows/ci.yml` | automation |
| `generator/ledgerly/config.py` | `Config` dataclasses + `load_config` |
| `generator/ledgerly/messlog.py` | `MessLog` |
| `generator/ledgerly/catalog.py` | plans, fx_rates, price lookup |
| `generator/ledgerly/customers.py` | customers |
| `generator/ledgerly/lifecycle.py` | simulation → subscriptions, items, billing periods |
| `generator/ledgerly/billing.py` | invoices, lines, payments, refunds |
| `generator/ledgerly/events.py` | events + dupes/late |
| `generator/ledgerly/truth.py` | customer_month_facts |
| `generator/ledgerly/writer.py` | as-of, strip, hash, write |
| `generator/generate.py` | `generate()` + CLI |
| `generator/config.yaml`, `generator/README.md` | config, docs |
| `generator/tests/conftest.py`, `test_*.py` | pytest |

`pyproject.toml` sets `[tool.pytest.ini_options] testpaths = ["generator/tests"]`, `pythonpath = ["generator"]` so tests import `ledgerly` and `generate`.

---

### Task 1: Repo bootstrap

**Files:** Create `.gitignore`; delete `PROJECT_BRIEF.md:Zone.Identifier`.

- [ ] **Step 1:** Write `.gitignore` with: `.venv/`, `data/`, `warehouse.duckdb*`, `target/`, `dbt_packages/`, `logs/`, `*:Zone.Identifier`, `.pytest_cache/`, `__pycache__/`, `.user.yml`.
- [ ] **Step 2:** `rm "PROJECT_BRIEF.md:Zone.Identifier"`.
- [ ] **Step 3:** Commit on `main`: `git add .gitignore PROJECT_BRIEF.md docs/superpowers && git commit -m "chore: initial commit with brief and setup design"`.
- [ ] **Step 4:** `git switch -c feat/setup-and-generator`. Expected: `git branch --show-current` → `feat/setup-and-generator`.

### Task 2: Python environment

**Files:** Create `pyproject.toml`, `uv.lock`.

- [ ] **Step 1:** If `uv` missing, install via official installer (`curl -LsSf https://astral.sh/uv/install.sh | sh`) — author approved. Expected: `uv --version` prints a version.
- [ ] **Step 2:** `pyproject.toml`: name `saas-metrics-for-agents`, `requires-python = ">=3.11"`, no build package (`[tool.uv] package = false`), pytest config as in File Map.
- [ ] **Step 3:** `uv add "dbt-core~=1.11" dbt-duckdb duckdb numpy pandas pyarrow faker pyyaml "dbt-metricflow[duckdb]"` and `uv add --dev pytest sqlfluff sqlfluff-templater-dbt`. If resolution forces dbt-core < 1.11 → stop, report the conflict to the author.
- [ ] **Step 4:** Verify: `uv run dbt --version` shows Core 1.11.x and duckdb plugin; `uv run mf --version` prints; `uv run python -c "import duckdb, pandas, pyarrow, numpy, faker, yaml"` exits 0.
- [ ] **Step 5:** Commit `pyproject.toml uv.lock` — `chore: python env with uv`.

### Task 3: dbt project skeleton

**Files:** Create `dbt_project.yml`, `profiles.yml`, `packages.yml`, `.sqlfluff`, skeleton `.gitkeep`s, `docs/metric_definitions.md`, minimal `Makefile` (targets `setup`, `debug` only for now).

- [ ] **Step 1:** `profiles.yml`: profile `ledgerly`, target `dev`, `type: duckdb`, `path: "{{ env_var('DUCKDB_PATH', 'warehouse.duckdb') }}"`, `threads: 4`.
- [ ] **Step 2:** `dbt_project.yml`: `name: saas_metrics`, `profile: ledgerly`, `require-dbt-version: [">=1.11.0", "<2.0.0"]`, model configs: `staging: +materialized: view`, `intermediate: +materialized: view`, `marts: +materialized: table`; `snapshot-paths: [snapshots]`; `model-paths: [models, semantic_models]`.
- [ ] **Step 3:** `packages.yml`: `dbt-labs/dbt_utils` with the version range listed on hub.getdbt.com as compatible with 1.11.
- [ ] **Step 4:** `.sqlfluff`: `dialect = duckdb`, `templater = dbt`, `[sqlfluff:templater:dbt] project_dir = .  profiles_dir = .`, capitalisation rules lower for keywords/identifiers/functions.
- [ ] **Step 5:** `docs/metric_definitions.md`: title + five `##` headings verbatim from brief §5 (churn timing, FX rate, discounts net/gross, reactivation window, proration) each with `_Decision: TODO (author)_`.
- [ ] **Step 6:** Makefile: `export DBT_PROFILES_DIR := $(CURDIR)`; `setup: uv sync && uv run dbt deps`; `debug: uv run dbt debug`.
- [ ] **Step 7:** Verify `make setup && make debug` → "All checks passed!"; `uv run dbt parse` exit 0.
- [ ] **Step 8:** Commit — `chore: dbt project skeleton on duckdb`.

### Task 4: Config, MessLog, catalog

**Files:** Create `generator/config.yaml`, `generator/ledgerly/{__init__,config,messlog,catalog}.py`, `generator/tests/{conftest,test_catalog}.py`.

**Interfaces — Produces:**
- `load_config(path: Path | None = None, **overrides) -> Config` (default path `generator/config.yaml`; overrides use dotted keys, e.g. `load_config(n_customers=50, **{"mess.refunds.rate": 0})`).
- `Config` frozen dataclass: `seed:int, start: date, months:int, n_customers:int, country_currency: dict[str,str], country_weights: dict[str,float], tiers: dict[str, dict[str, float]]` (tier → currency → monthly price per seat, v1), `annual_multiplier: float = 10`, `price_change: PriceChange(date, pct)`, `mess: dict[str, dict]` (one entry per mess key below, each with `enabled: bool` + params). `cfg.end` property = last day of window.
- Mess keys (exact): `annual_plans, trials, plan_changes, seat_changes, discounts, dunning, voluntary_churn, refunds, fx_gaps, reactivations, one_off, price_change, duplicate_events, late_events, internal_customers, soft_deleted, timezone_trap`.
- `MessLog.add(mess_type: str, table: str, object_id: str, note: str = "") -> None`; `MessLog.to_frame() -> pd.DataFrame` (columns `mess_type, table, object_id, note`).
- `build_plans(cfg) -> pd.DataFrame` columns `plan_id, product, tier, interval, currency, unit_amount, valid_from, active, updated_at`; ids `price_{tier}_{interval}_{currency}_v{n}` lowercase.
- `build_fx_rates(cfg, rng) -> pd.DataFrame` columns `date, currency, rate_to_eur`; EUR always 1.0; USD/PLN seeded random walk (USD≈0.92, PLN≈0.23 EUR); weekends omitted when `fx_gaps.enabled`.
- `plan_for(plans, tier, interval, currency, on: date) -> str` → plan_id of the version valid on that date.

- [ ] **Step 1: Failing tests** in `test_catalog.py`:
  - `test_plan_catalog_has_36_rows` — 18 v1 + 18 v2; v2 `unit_amount == round(v1 * 1.15, 2)`; v2 `valid_from == date(2025,11,1)`.
  - `test_annual_price_is_10x_monthly` — for each tier/currency/version.
  - `test_v1_inactive_after_price_change` — v1 `active` False, `updated_at` date 2025-11-01.
  - `test_fx_eur_is_one_and_no_weekends` — all EUR rates 1.0; no `date.weekday() >= 5`.
  - `test_fx_weekends_present_when_gap_disabled` — `load_config(**{"mess.fx_gaps.enabled": False})`.
  - `test_plan_for_switches_on_price_change` — 2025-10-31 → `_v1`, 2025-11-01 → `_v2`.
  - `conftest.py` fixtures: `small_cfg` = `load_config(n_customers=50)`, `rng` = `np.random.default_rng(small_cfg.seed)`.
- [ ] **Step 2:** `uv run pytest generator/tests/test_catalog.py -v` → FAIL (import error).
- [ ] **Step 3:** Implement `config.yaml` (defaults = rates table in spec §5) and the modules above. Prices v1 EUR/mo per seat: starter 9, pro 19, business 39; USD ×1.1, PLN ×4.3, rounded to whole units.
- [ ] **Step 4:** Tests PASS.
- [ ] **Step 5:** Commit — `feat(generator): config, plan catalog, fx rates`.

### Task 5: Customers

**Files:** Create `generator/ledgerly/customers.py`, `generator/tests/test_customers.py`.

**Interfaces — Produces:** `build_customers(cfg, rng, faker, log: MessLog) -> pd.DataFrame` columns `id` (`cus_` + 14 base36 chars from rng), `email, company_name, country, created_at` (UTC tz-aware timestamp), `is_deleted`, private `_kind ∈ {real, internal, junk}`, `_currency`. Signup dates spread across the window with weight decaying linearly from 1.5 (first month) to 0.5 (last month). `internal` emails end `@ledgerly.io`; `junk` customers are soft-deleted with no subscriptions; additional `is_deleted=True` among `real` is set later by lifecycle (churned real customers) — this module only creates junk.

- [ ] **Step 1: Failing tests:** `test_customer_count` (len == n_customers), `test_ids_unique`, `test_internal_have_ledgerly_domain` (every `_kind=="internal"` email endswith `@ledgerly.io`, none of `real`), `test_currency_matches_country`, `test_created_within_window`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit — `feat(generator): customers`.

### Task 6: Lifecycle simulation

**Files:** Create `generator/ledgerly/lifecycle.py`, `generator/tests/test_lifecycle.py`.

**Interfaces — Consumes:** `Config`, `build_plans`, `plan_for`, customers frame, `MessLog`.
**Produces:** `simulate(cfg, rng, customers, plans, log) -> Lifecycle` where

```python
@dataclass
class BillingPeriod:
    subscription_id: str; customer_id: str; currency: str
    period_start: date; period_end: date          # end exclusive
    items: list[tuple[str, int]]                  # (plan_id, quantity) at period start
    prorations: list[Proration]                   # changes in the *previous* period, billed now
    coupon_pct: float                             # 0.0 or 0.2
    one_off_amount: float                         # 0.0 or onboarding fee
    payment_outcome: str                          # "ok" | "recovered" | "failed"
    first_attempt_at: datetime                    # UTC

@dataclass
class Proration:
    old_plan_id: str; old_qty: int; new_plan_id: str; new_qty: int
    changed_at: date; period_start: date; period_end: date

@dataclass
class Lifecycle:
    subscriptions: pd.DataFrame   # id, customer_id, status, started_at, trial_end, canceled_at, ended_at, cancel_at_period_end, _churn_reason ∈ {voluntary, dunning, trial_expired, None}
    subscription_items: pd.DataFrame  # subscription_id, plan_id, quantity, valid_from, valid_to
    periods: list[BillingPeriod]
    deleted_customer_ids: set[str]    # churned real customers marked soft-deleted
```

Rules (from spec §5; rates from `cfg.mess`):
- Each non-junk customer starts a subscription at `created_at`; with trials enabled, trial 14 days, then convert or end `incomplete_expired` (`_churn_reason="trial_expired"`).
- Billing anchored at trial end (or start), monthly or annual; period after a plan/seat change carries a `Proration` for the remainder of the period in which it happened.
- Each period: draw first-attempt failure (`dunning.fail_rate`); failed → `recovered` with `dunning.recover_rate`, else `failed` ⇒ subscription `canceled_at = ended_at = first_attempt + 14 days`, `_churn_reason="dunning"`, status `canceled`.
- Voluntary churn per month by tier; monthly ⇒ `canceled_at = ended_at` at period end; annual ⇒ `cancel_at_period_end=True`, `canceled_at` mid-term, `ended_at` = term end.
- Reactivation: churned (voluntary/dunning) customer with `reactivations.rate` starts a new subscription 1–9 months after `ended_at` (if inside window).
- Subscription status at window end: `trialing|active|past_due|canceled|incomplete_expired` (past_due if last period is mid-dunning at `cfg.end`).
- Coupon: 20% for first 3 billing periods on `discounts.rate` of new subscriptions (monthly only; annual gets 20% on first period). One-off: business onboarding fee (EUR 500 / USD 550 / PLN 2150) on first paid period with `one_off.rate`.
- Every injected instance → `log.add(...)` with matching mess key.

- [ ] **Step 1: Failing tests** (use `small_cfg` unless noted):
  - `test_items_versions_do_not_overlap` — per subscription, sorted `valid_from` == previous `valid_to`.
  - `test_trial_periods_not_billed` — no `BillingPeriod.period_start < trial_end`.
  - `test_dunning_failed_cancels_14_days_after_first_attempt`.
  - `test_annual_cancel_at_period_end_ends_at_term_end`.
  - `test_reactivation_is_new_subscription_same_customer` — at full default size: ≥1 customer with 2+ subscriptions where second `started_at > first ended_at`.
  - `test_grandfathered_keep_v1` — subscriptions started before 2025-11-01 and never plan-changed only have `_v1` plan_ids.
  - `test_deterministic` — two `simulate` runs with same seed → equal frames.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit — `feat(generator): lifecycle simulation`.

### Task 7: Billing

**Files:** Create `generator/ledgerly/billing.py`, `generator/tests/test_billing.py`.

**Interfaces — Consumes:** `Lifecycle`, plans, customers, `MessLog`. **Produces:** `build_billing(cfg, rng, lifecycle, plans, customers, log) -> dict[str, pd.DataFrame]` with keys `invoices, invoice_line_items, payments, refunds`.
- One invoice per `BillingPeriod`; lines: one `subscription` per item (`unit_amount × qty`), `proration` pair (negative credit old, positive new, day-based on the old period, rounded to 2dp), `discount` = −coupon_pct × subscription subtotal, `one_off`. `total` = Σ lines exactly (round each line first).
- Payments: `ok` → 1 succeeded; `recovered` → 1–3 failed then succeeded at +3/+5/+7 days; `failed` → 4 failed attempts, invoice `uncollectible`. Payment `_attempted_at_utc` (tz-aware) kept private for as-of filtering; `attempted_at` is formatted here as an ISO string, `Z` suffix except PLN customers when `timezone_trap.enabled` (Europe/Warsaw offset via `zoneinfo`). Force `timezone_trap.boundary_cases` (default 5) PLN attempts to 00:00–00:59 local on the 1st of a month (logged).
- Refunds: `refunds.rate` of succeeded payments; partial (`partial_share`=0.6, 10–60% of amount) else full; `later_month_share`=0.3 created next calendar month, else same month after payment.

- [ ] **Step 1: Failing tests:** `test_invoice_total_equals_sum_of_lines`, `test_refund_not_exceeding_payment`, `test_refund_after_payment`, `test_uncollectible_has_no_succeeded_payment`, `test_attempted_at_mixed_offsets` (full size: some end `Z`, some `+01:00`/`+02:00`), `test_annual_invoice_one_subscription_line_spanning_12_months`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit — `feat(generator): invoices, payments, dunning, refunds`.

### Task 8: Events and truth

**Files:** Create `generator/ledgerly/{events,truth}.py`, `generator/tests/test_events_truth.py`.

**Interfaces — Produces:**
- `build_events(cfg, rng, tables: dict[str, pd.DataFrame], log) -> pd.DataFrame` columns `id` (`evt_…`), `type, object_id, payload_json` (JSON with object snapshot), `created_at, ingested_at` (UTC). Base `ingested_at = created_at + U(1s, 5min)`; late: +3–40 days; duplicates: copy row, same `id`, `ingested_at` later.
- `build_customer_month_facts(cfg, customers, lifecycle, plans) -> pd.DataFrame` columns exactly as spec §5 (`customer_id, month` (first day), `state_at_month_end, subscription_id, plan_id, tier, interval, currency, quantity, run_rate_mrr_local_list, discount_mrr_local, is_internal, is_deleted`). Multi-item subs: one row per item; `run_rate` = unit × qty / (12 if year else 1).

- [ ] **Step 1: Failing tests:** `test_event_types_cover_all_objects`, `test_duplicates_share_id_later_ingested`, `test_late_events_lag_at_least_3_days`, `test_truth_annual_divided_by_12`, `test_truth_trial_month_state_is_trialing` (a customer whose trial spans a month end has `state_at_month_end == "trialing"` for that month), `test_truth_month_end_state_after_dunning_cancel_is_ended`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit — `feat(generator): events and ground truth`.

### Task 9: Writer, CLI, end-to-end tests, README

**Files:** Create `generator/ledgerly/writer.py`, `generator/generate.py`, `generator/README.md`, `generator/tests/{test_determinism,test_integrity,test_mess_present,test_as_of}.py`; Modify `Makefile` (add `generate`).

**Interfaces — Produces:**
- `generate(cfg: Config, as_of: date | None = None) -> dict[str, pd.DataFrame]` keys: the 10 raw table names + `truth/customer_month_facts`, `truth/mess_log`.
- `generate()` order: catalog → customers → `simulate` → set `customers.is_deleted=True` for `lifecycle.deleted_customer_ids` → billing → events → truth → `apply_as_of` (if given) → `strip_private`.
- `apply_as_of(tables, as_of: date) -> dict` — drop rows created after as-of; null `canceled_at/ended_at > as_of`; recompute subscription `status` and invoice `status` as of the date; plans: drop v2 if `valid_from > as_of`, v1 `active`/`updated_at` reverted; events with `ingested_at > as_of` dropped.
- `strip_private(df) -> df`; `content_hash(tables) -> str` (sha256 over `pd.util.hash_pandas_object(df, index=False)` per table in sorted key order); `write_tables(tables, out_dir: Path)`.
- CLI: `python generator/generate.py [--seed N] [--as-of YYYY-MM-DD] [--config PATH] [--out data]`, prints row counts + content hash.

- [ ] **Step 1: Failing tests:**
  - `test_same_seed_same_hash`, `test_different_seed_different_hash` (small cfg).
  - `test_integrity_foreign_keys` — every FK (`subscriptions.customer_id`, items→subs, items→plans, invoices→customers/subs, lines→invoices, payments→invoices, refunds→payments, fx covers every invoice currency) resolves.
  - `test_no_private_columns_written`.
  - `test_each_mess_type_present` — full default cfg (module-scoped fixture): every mess key in `mess_log.mess_type`, plus direct checks: ≥1 `@ledgerly.io`, ≥1 `is_deleted` with and without subscriptions, ≥1 duplicate event id, ≥1 `proration` line <0 and >0, ≥1 `one_off`, ≥1 `discount`, ≥1 `annual` plan item, ≥1 v2 and v1 plan in use after 2025-11-01.
  - `test_mess_disabled_absent` — `mess.refunds.rate=0`, `mess.duplicate_events.enabled=False` → refunds empty, event ids unique.
  - `test_as_of_mid_window` — as-of 2025-10-31: no `created_at`/`started_at`/`attempted_at` after, no v2 plans, v1 `active` all True, no `canceled_at > as_of`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement writer + CLI; `Makefile: generate: uv run python generator/generate.py $(if $(SEED),--seed $(SEED)) $(if $(AS_OF),--as-of $(AS_OF))`.
- [ ] **Step 4:** `uv run pytest -v` → all PASS; `make generate` prints counts for 12 outputs.
- [ ] **Step 5:** Write `generator/README.md`: world overview, table list with grain, one subsection per mess key (what, config key, where in raw, which metric it breaks), regenerate/as-of usage, truth files.
- [ ] **Step 6:** Commit — `feat(generator): writer, cli, e2e tests, readme`.

### Task 10: dbt sources + full Makefile

**Files:** Create `models/staging/ledgerly/_ledgerly__sources.yml`; Modify `Makefile`.

- [ ] **Step 1:** Sources yml: `sources: - name: ledgerly`, `meta: {external_location: "data/raw/{name}.parquet"}` at source level, 10 tables with one-line descriptions; `events`: `loaded_at_field: ingested_at`, `freshness: {warn_after: {count: 2, period: day}, error_after: {count: 7, period: day}}`. Check current dbt 1.11 docs for whether `freshness`/`loaded_at_field` belong under `config:`; use the documented form.
- [ ] **Step 2:** Makefile adds `build` (generate if `data/raw` missing, then `uv run dbt build`), `test` (`uv run pytest`), `lint` (`uv run sqlfluff lint models`), `freshness`, `docs`, `clean`; `.PHONY` all.
- [ ] **Step 3:** Verify from repo root: for each of the 10 tables, `uv run dbt show --inline "select count(*) as n from {{ source('ledgerly','<t>') }}"` returns n > 0. `make build` exits 0 (nothing to build yet is fine). `make freshness` runs (warn/error expected; documented).
- [ ] **Step 4:** Commit — `feat(dbt): ledgerly sources over parquet`.

### Task 11: CI

**Files:** Create `.github/workflows/ci.yml`.

- [ ] **Step 1:** Workflow on `pull_request` and `push: branches: [main]`: `actions/checkout@v4`, `astral-sh/setup-uv` (latest major tag per its README, with `python-version: "3.12"`), steps `make setup`, `make generate`, `make debug`, `uv run dbt parse`, `make test`, `make lint` with `continue-on-error: true`.
- [ ] **Step 2:** Verify locally the same sequence passes: `make clean && make setup && make generate && make debug && uv run dbt parse && make test`.
- [ ] **Step 3:** Commit — `ci: build and test on every pr`.
