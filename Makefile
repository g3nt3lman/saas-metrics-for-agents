SHELL := /bin/bash
export DBT_PROFILES_DIR := $(CURDIR)

.PHONY: setup debug

setup:
	uv sync
	uv run dbt deps

debug:
	uv run dbt debug
