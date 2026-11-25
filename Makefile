SHELL := /bin/bash
export DBT_PROFILES_DIR := $(CURDIR)

.PHONY: setup debug generate build test lint freshness docs serve clean

## Install Python deps (uv) and dbt packages
setup:
	uv sync
	uv run dbt deps

## Check the dbt <-> DuckDB connection
debug:
	uv run dbt debug

## Generate synthetic raw data: make generate [SEED=42] [AS_OF=YYYY-MM-DD]
generate:
	uv run python generator/generate.py $(if $(SEED),--seed $(SEED)) $(if $(AS_OF),--as-of $(AS_OF))

## Generate data if missing, then dbt build (models + tests + snapshots)
build:
	@test -f data/raw/events.parquet || $(MAKE) generate
	uv run dbt build

## Python tests (generator + MCP server; the MCP tests need `make build` first)
test:
	uv run pytest

## MCP server for the agent (stdio, read-only). Stop it before `make build`: DuckDB allows one writer.
serve:
	uv run python agent/server.py

## Lint dbt SQL
lint:
	uv run sqlfluff lint models tests

## Source freshness. Expected to warn/error on static synthetic data (it ends 2026-10-31).
freshness:
	uv run dbt source freshness

## Generate and serve dbt docs
docs:
	uv run dbt docs generate
	uv run dbt docs serve

## Remove build artefacts, warehouse and generated data
clean:
	rm -rf target dbt_packages logs data warehouse.duckdb warehouse.duckdb.wal
