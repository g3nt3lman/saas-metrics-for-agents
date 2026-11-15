# Project brief: `saas-metrics-for-agents`

> A governed SaaS metrics layer (dbt + MetricFlow on DuckDB) exposed to LLM agents through an MCP server — so an agent asked "what was our NDR last quarter?" returns the *right* number instead of a plausible-looking wrong one.

This file is the single source of truth for the project's scope. Keep it in the repo root. Update it when scope changes.

---

## 1. Why this project exists

**The problem.** Companies are putting LLM agents on top of their data ("chat with your warehouse"). Naive text-to-SQL on raw billing tables produces confident, wrong answers about the metrics that matter most: MRR, churn and NDR. The model doesn't know that annual plans must be divided by 12, that trials aren't revenue, that a failed payment isn't churn until dunning ends, that refunds and proration exist, or which FX rate to use.

**The thesis.** The fix isn't a smarter prompt. It's analytics engineering: clean models, tested business definitions, and a semantic layer, with the agent allowed to query **only governed metrics**.

**What this repo proves about the author:**
- I can model messy financial or billing data into trustworthy marts (my day job is a financial DWH in dbt + BigQuery).
- I test numbers the way finance does, with reconciliation and not only `not_null`.
- I understand where AE fits in an AI-first company: the metrics layer is the agent's source of truth.

**Target audience:** founders and heads of data at seed to Series B scale-ups hiring their first or second analytics engineer.

---

## 2. Definition of done (the demo)

A reader of the README can, in under 5 minutes:

1. `make setup && make build` → generates data, runs `dbt build`, validates the semantic layer and runs every test green.
2. Connect the MCP server to Claude Desktop (or any MCP client) and ask business questions in plain English.
3. See an **evaluation table**: 10–15 business questions, ground truth, and the answers from
   - (a) an agent with raw-schema text-to-SQL, and
   - (b) an agent with the metrics MCP tools.

   The table reports accuracy and failure modes for each.

That comparison table is the headline of the README.

---

## 3. Tech stack

| Area | Choice | Notes |
|---|---|---|
| Language / env | Python 3.11+, `uv` | one `pyproject.toml` |
| Warehouse | DuckDB (local file `warehouse.duckdb`) | zero cost, runs in CI |
| Transformations | `dbt-core` 1.11 + `dbt-duckdb` | matches the current dbt AE cert version |
| Packages | `dbt_utils`, optionally `dbt_expectations` | keep dependencies minimal |
| Semantic layer | MetricFlow (`dbt-metricflow[duckdb]`) | verify the current install command in the docs |
| Agent interface | Python MCP SDK (`mcp`, FastMCP) | custom server over MetricFlow |
| Data generation | `numpy`, `Faker`, `pandas`/`pyarrow` | deterministic with a seed |
| Tests (Python) | `pytest` | generator and MCP tools |
| Lint | `sqlfluff` (dbt templater) or `sqlfmt` | pick one, enforce in CI |
| CI | GitHub Actions | build + tests on every PR |

Note: dbt Labs has an official `dbt-mcp` server, but at the time of writing its Semantic Layer tools target dbt Cloud. Check the current state. Building a small custom server over MetricFlow and Core is a deliberate choice here. Mention the official one in the README as the "production" path.

---

## 4. Synthetic data generator (`generator/`)

A fictional B2B SaaS called **"Ledgerly"** (invoicing software for SMBs). It has three tiers × monthly/annual billing and sells in EUR, USD and PLN.

**Scale:** ~2,000 customers, 24 months of history, deterministic (`--seed 42`). It outputs Parquet files to `data/raw/`, and those files are git-ignored.

### Raw tables (mimic a Stripe-like export)

| Table | Grain | Key columns |
|---|---|---|
| `customers` | customer | id, email, company_name, country, created_at, is_deleted |
| `plans` | plan version | plan_id, tier, interval (month/year), currency, unit_amount, valid_from |
| `subscriptions` | subscription | id, customer_id, status, started_at, trial_end, canceled_at, ended_at |
| `subscription_items` | item version | subscription_id, plan_id, quantity (seats), valid_from, valid_to |
| `invoices` | invoice | id, customer_id, subscription_id, period_start, period_end, currency, total, status |
| `invoice_line_items` | line | invoice_id, type (subscription/proration/one_off/discount), amount, period |
| `payments` | attempt | id, invoice_id, amount, status (succeeded/failed), attempted_at |
| `refunds` | refund | id, payment_id, amount, created_at, reason |
| `fx_rates` | currency × day | date, currency, rate_to_eur |
| `events` | event | id, type, object_id, payload_json, created_at, ingested_at |

### Mess to inject on purpose

Every item below should be **configurable** and **documented** in `generator/README.md`. The README is part of the story.

- [ ] **Annual plans:** a single invoice covers 12 months, so MRR must spread it across them.
- [ ] **Trials:** a 14-day free period that isn't revenue; some trials never convert.
- [ ] **Upgrades/downgrades mid-cycle** with **proration** line items (positive and negative).
- [ ] **Seat changes** (quantity) creating expansion and contraction.
- [ ] **Discounts/coupons**, e.g. 20% off for 3 months.
- [ ] **Failed payments + dunning retries.** Some recover, some end in cancellation after N days.
- [ ] **Refunds**, both partial and full, sometimes in a later month than the payment.
- [ ] **Multi-currency.** Invoices are in local currency and reporting is in EUR.
- [ ] **Reactivations:** a churned customer comes back months later.
- [ ] **One-off charges** (onboarding fee), which are revenue but **not** MRR.
- [ ] **Price change** of a plan in month 12; old customers stay grandfathered.
- [ ] **Duplicate events** and **late-arriving events** (`ingested_at` ≫ `created_at`).
- [ ] **Internal/test customers** (`@ledgerly.io` emails) that must be excluded.
- [ ] **Soft-deleted customers.**
- [ ] **Timezone trap:** some timestamps are UTC and some are local.

Generator tests (`pytest`): determinism (same seed gives the same hash), basic referential integrity, and that each injected mess type actually occurs at least once.

---

## 5. dbt project (`models/`)

### Conventions
- Prefixes: `stg_`, `int_`, `dim_`, `fct_`. One staging model per source table.
- Staging handles renaming, casting, timezone normalization (everything to UTC), dedup and test-data flags. It contains no business logic.
- SQL style: lowercase, CTEs at the top (import → logical → final), and a doc block for every model.
- Business definitions live in **`docs/metric_definitions.md`** and are referenced from model and metric descriptions.

### Layers

**Staging:** `stg_ledgerly__customers`, `__plans`, `__subscriptions`, `__subscription_items`, `__invoices`, `__invoice_line_items`, `__payments`, `__refunds`, `__fx_rates`, `__events`.
- `stg_ledgerly__events` is **incremental** (merge on `event_id`, with a lookback window on `ingested_at` for late data). This mirrors my real 1.6 TB → 30 GB story, so describe it in the README.

**Snapshots:** `plans_snapshot` (SCD2) for the plan price change.

**Intermediate:**
- `int_subscription_periods`: clean validity periods per subscription item, with trials flagged.
- `int_invoice_lines_eur`: lines converted to EUR at the documented FX rule.
- `int_customer_months`: a spine of customer × month (use `dbt_utils.date_spine`).
- `int_mrr_by_customer_month`: normalized MRR per customer per month. It applies the annual/12 rule, excludes trials and one-offs, applies discounts, and converts FX.

**Marts:**
- `dim_customers`: one row per real customer, with first paid month (cohort), current status and country.
- `dim_plans`
- `fct_invoices`, `fct_payments` (with refunds netted, and both gross and net columns)
- `fct_mrr_monthly`: grain is customer × month, with `mrr_eur` and `previous_mrr_eur`.
- `fct_mrr_movements`: each customer-month is classified as `new | expansion | contraction | churn | reactivation | unchanged`, with `movement_amount_eur`.
- `fct_cohort_retention`: cohort month × months since start, giving logo and revenue retention.
- `fct_ndr_monthly`: NDR at month *m* = current MRR of customers active at *m−12* ÷ their MRR at *m−12*.

**Model contracts** (`contract: enforced: true`) go on all marts.

### Business definitions to decide and write down (`docs/metric_definitions.md`)
Write these down **before** coding the intermediate layer. The point is that I make the calls a finance team would make.
- When does churn happen: at cancellation, at `ended_at`, or after dunning fails?
- Which FX rate: the month-start rate, the invoice-date rate, or the monthly average?
- Are discounts in MRR (net) or excluded (gross)? Pick one and expose the other.
- Reactivation window: after how many months does a returning customer count as "new" again?
- How do proration lines affect MRR (they don't, since MRR reflects the run-rate), and how do they affect revenue (they do)?

---

## 6. Testing strategy (the part that sells me)

| Type | Examples |
|---|---|
| Generic | `unique`, `not_null`, `relationships`, `accepted_values` on movement types and statuses |
| Package | `dbt_utils.unique_combination_of_columns` (customer_id, month), `expression_is_true` (mrr_eur >= 0) |
| **Unit tests** (dbt ≥ 1.8) | movement classification: hand-crafted inputs for new, expansion, churn, reactivation, annual plan, trial → expected outputs |
| **Reconciliation (singular)** | (1) Σ movements in month *m* = MRR(*m*) − MRR(*m−1*), exactly. (2) Per currency: invoices paid − refunds = net payments, within a rounding tolerance. (3) Customers at month end = start + new + reactivated − churned. (4) Σ `fct_mrr_monthly` for one month = Σ `int_mrr_by_customer_month`. |
| Source freshness | on `events` (`loaded_at_field: ingested_at`) |
| Data-quality canaries | test customers excluded from marts; no MRR during trial months |

Use `severity: warn` vs `error` on purpose, and explain why in the YAML descriptions.

---

## 7. Semantic layer (`semantic_models/`, MetricFlow)

- A time spine model (required by MetricFlow).
- Semantic models on `fct_mrr_monthly`, `fct_mrr_movements`, `fct_invoices`, `dim_customers`, `fct_ndr_monthly`.
- **Important detail:** MRR is a *snapshot* and must not be summed over time. Use `non_additive_dimension` on `metric_time` with `window_choice: max` or `min` (end of period), and test that quarterly MRR is the last month's value, not the sum of three months.

**Metrics** (every description states the business definition in plain English, because the agent will read it):

| Metric | Type |
|---|---|
| `mrr` | simple (semi-additive) |
| `arr` | derived = `mrr * 12` |
| `new_mrr`, `expansion_mrr`, `contraction_mrr`, `churned_mrr`, `reactivation_mrr` | simple, with a filter on movement type |
| `net_new_mrr` | derived |
| `active_customers` | simple (semi-additive count) |
| `logo_churn_rate` | ratio |
| `gross_mrr_churn_rate` | ratio (churned + contraction ÷ starting MRR) |
| `ndr` | from `fct_ndr_monthly` |
| `revenue_recognized`, `net_payments` | simple, so the agent can show that revenue ≠ MRR |

Dimensions: `metric_time`, `customer__country`, `customer__cohort_month`, `plan__tier`, `plan__interval`, `currency`.

Add 3–5 **saved queries**, e.g. a monthly MRR bridge or NDR by cohort.

`mf validate-configs` runs in CI.

---

## 8. MCP server (`agent/`)

A small Python MCP server with a **read-only** connection. The agent can only reach governed metrics.

| Tool | Input | Output |
|---|---|---|
| `list_metrics` | none | name, type, description and available dimensions for every metric |
| `get_dimensions` | `metrics[]` | valid group-by dimensions and time grains |
| `query_metric` | `metrics[]`, `group_by[]`, `start`, `end`, `grain`, `limit` | rows, plus the **compiled SQL** for transparency |
| `explain_metric` | `metric` | business definition (from `metric_definitions.md`), upstream dbt models (from `manifest.json`), and the tests protecting it |

Guardrails:
- whitelist metrics and dimensions;
- a hard row limit;
- timeouts;
- clear error messages the LLM can recover from ("dimension X not valid for metric Y; valid: …").

Implementation note: call MetricFlow via its Python API or the `mf query --explain` / `mf query` CLI. Parse `target/manifest.json` and `semantic_manifest.json` for metadata.

Tests:
- `pytest` per tool, plus one "golden" test: `query_metric(mrr, month=last)` equals the value computed straight from `fct_mrr_monthly`.

---

## 9. Evaluation (`eval/`)

- `eval/questions.yaml` holds 10–15 questions with ground truth computed by SQL against marts. Examples:
  - "What was MRR at the end of Q2 2026?" (semi-additivity trap)
  - "What was our NDR for the 2025-01 cohort after 12 months?"
  - "How much MRR did we lose to churn in March?" (dunning trap)
  - "What's ARR by tier?" (annual plan trap)
  - "Revenue vs MRR last month — why do they differ?" (proration, one-offs, refunds)
  - "MRR in PLN customers converted to EUR" (FX trap)
- **Baseline A:** the LLM gets the raw-schema DDL and writes SQL against `data/raw`.
- **Baseline B:** the LLM uses only the MCP tools.
- `eval/run_eval.py` produces `eval/results.md`, with each answer marked correct or wrong and the reason for each failure. You can run it manually through Claude Desktop if API cost is a concern; just document the procedure honestly.

---

## 10. Repository structure

```
saas-metrics-for-agents/
├── PROJECT_BRIEF.md
├── README.md
├── Makefile                  # setup, generate, build, test, eval, serve
├── pyproject.toml
├── dbt_project.yml
├── profiles.yml              # DuckDB, path via env var
├── packages.yml
├── generator/
│   ├── generate.py
│   ├── config.yaml           # sizes, mess toggles, seed
│   └── README.md
├── models/
│   ├── staging/ledgerly/
│   ├── intermediate/
│   ├── marts/
│   └── utils/metricflow_time_spine.sql
├── snapshots/
├── semantic_models/
├── tests/                    # singular reconciliation tests
├── macros/
├── docs/
│   ├── metric_definitions.md
│   └── architecture.png      # lineage + agent flow diagram
├── agent/
│   ├── server.py
│   └── tests/
├── eval/
│   ├── questions.yaml
│   ├── run_eval.py
│   └── results.md
└── .github/workflows/ci.yml
```

---

## 11. Milestones

**Weekend 1: trustworthy numbers**
1. Repo skeleton, `uv`, `Makefile`, DuckDB profile, CI running `dbt debug`.
2. Generator with the mess list (start with 5 types, add the rest incrementally).
3. `docs/metric_definitions.md`, with decisions written down **before** the SQL.
4. Staging + snapshot + incremental events.
5. Intermediate + marts up to `fct_mrr_movements`.
6. Unit tests + reconciliation tests green.

**Weekend 2: agent-ready**
7. Semantic models + metrics + saved queries, with `mf validate-configs` green.
8. MCP server with four tools + pytest.
9. Eval set + baseline comparison → `results.md`.
10. README, architecture diagram, `dbt docs` screenshots, and a 2-minute Loom demo.

**Stretch (only if time allows):** `fct_cohort_retention` heatmap; a Slim CI job (`state:modified+` with a deferred manifest); swap DuckDB for BigQuery via a second target; a small LLM-generated "weekly MRR commentary" that uses only MCP tools.

---

## 12. README outline

1. One-paragraph pitch and the eval results table, above the fold.
2. "The problem: why text-to-SQL gets MRR wrong," with 3 concrete examples from the eval.
3. Architecture diagram: raw → staging → marts → semantic layer → MCP → agent.
4. Business definitions and the decisions I made, linking to `metric_definitions.md`.
5. How numbers are tested (reconciliation section).
6. Quickstart (`make setup && make build && make serve`).
7. What I'd do in production: dbt Cloud Semantic Layer and the official dbt-mcp, BigQuery, orchestration, access control.
8. About me, in 2 lines, with links.

---

## 13. Mapping to the dbt Analytics Engineering certification

This project doubles as exam prep. Make sure I've touched each of these:
- model materializations (view, table, incremental, ephemeral) and when to use them;
- sources, freshness, `ref`/`source`, DAG selection syntax (`+model`, `tag:`, `state:`);
- generic, singular and unit tests, plus severity, `store_failures` and `where` configs;
- snapshots (timestamp vs check strategy);
- Jinja and macros (one useful macro, e.g. `to_eur(amount, currency, date)`);
- docs blocks, `dbt docs generate`, exposures (declare the MCP server as an exposure);
- model contracts, versions, access (`public`/`protected`), groups;
- packages, `dbt deps`, `dbt build` vs `run`/`test`;
- Git workflow: feature branches, PRs, CI.

---

## 14. Working agreement for the AI assistant in this repo

I'm the author, and I'm using this project to learn and to prepare for the dbt AE certification. So:
- **Default mode is mentor and reviewer, not ghostwriter.** Explain, propose, review my code, point out bugs and edge cases. Write full models or files only when I explicitly ask.
- When I ask "how should I…", give options with trade-offs, then a recommendation.
- Challenge my business definitions the way a CFO would.
- Ask before adding any new dependency.
- Keep to the conventions in section 5. Flag drift.
- When something touches a cert topic (section 13), mention it in one line.
- Don't invent dbt or MetricFlow syntax. If unsure about the current version's spec, say so and point me to the docs.

## 15. Out of scope

Dashboards and BI tools, a real Stripe integration, auth on the MCP server beyond read-only, fine-tuning, multi-tenant setups, and perfect realism of the synthetic data. It only has to be realistic where it matters for the metrics.
