SHELL := /bin/bash
export DBT_PROFILES_DIR := $(CURDIR)

.PHONY: setup debug generate

setup:
	uv sync
	uv run dbt deps

debug:
	uv run dbt debug

generate:
	uv run python generator/generate.py $(if $(SEED),--seed $(SEED)) $(if $(AS_OF),--as-of $(AS_OF))
