"""Thin wrapper around the MetricFlow Python API: the only thing the MCP server talks to.

MetricFlow itself guarantees the agent can only reach metrics and dimensions defined in the semantic layer,
and its errors already list valid names. This module adds what MetricFlow does not do:
structured filters instead of SQL, a row limit, a timeout, JSON-friendly results, and stdout hygiene.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[1]
MAX_ROWS = 500
QUERY_TIMEOUT_SECONDS = 30
TIME_GRAINS = ("month", "quarter", "year")  # the finest grain in the marts is month
HIDDEN_DIMENSIONS = {"customer_month__month_start"}  # duplicate of metric_time; would only confuse the agent

log = logging.getLogger("ledgerly")


class SemanticLayerError(Exception):
    """A query the semantic layer refuses or cannot answer. The message is written for the LLM."""


def _to_json(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.date().isoformat()  # month grain at most: the time part is always midnight
    if isinstance(value, (Decimal, float)):
        return round(float(value), 2)
    return value


def _date(value: str | None, field: str) -> dt.datetime | None:
    if value is None:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        raise SemanticLayerError(f"{field} must be a date in YYYY-MM-DD format (e.g. 2025-03-31), got {value!r}.") from None


class SemanticLayer:
    """Loads the dbt semantic manifest once and answers metric queries over a read-only connection."""

    def __init__(self, project_dir: Path = PROJECT_DIR) -> None:
        from dbt_metricflow.cli.cli_configuration import CLIConfiguration

        if not (project_dir / "target" / "semantic_manifest.json").exists():
            raise SemanticLayerError("target/semantic_manifest.json not found. Run `make build` first.")
        os.environ.setdefault("DBT_TARGET", "agent")  # read-only profile, see profiles.yml
        os.environ.setdefault("DUCKDB_PATH", str(project_dir / "warehouse.duckdb"))

        # dbt logs to stdout, but on a stdio MCP server stdout is the protocol channel
        log.debug("loading MetricFlow from %s (target=%s, read-only)", project_dir, os.environ["DBT_TARGET"])
        with contextlib.redirect_stdout(sys.stderr):
            cfg = CLIConfiguration()
            cfg.setup(dbt_profiles_path=project_dir, dbt_project_path=project_dir, configure_file_logging=False)
            self.engine = cfg.mf
            self.sql_client = cfg.sql_client
            self._metrics = self.engine.list_metrics()
        log.debug("loaded %d metrics from target/semantic_manifest.json", len(self._metrics))
        self._executor = ThreadPoolExecutor(max_workers=1)  # one DuckDB connection: run queries one at a time

    def list_metrics(self) -> list[dict[str, Any]]:
        log.debug("describing %d metrics and their dimensions", len(self._metrics))
        with contextlib.redirect_stdout(sys.stderr):
            return [
                {
                    "name": m.name,
                    "label": m.label,
                    "type": m.type.value.lower(),
                    "description": (m.description or "").strip(),
                    "dimensions": sorted(
                        d.granularity_free_dunder_name
                        for d in self.engine.simple_dimensions_for_metrics([m.name])
                        if d.granularity_free_dunder_name not in HIDDEN_DIMENSIONS
                    ),
                }
                for m in sorted(self._metrics, key=lambda m: m.name)
            ]

    def query(
        self,
        metrics: list[str],
        group_by: list[str] | None = None,
        grain: str | None = None,
        start: str | None = None,
        end: str | None = None,
        filters: dict[str, list[str]] | None = None,
        order_by: list[str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        from metricflow.engine.metricflow_engine import MetricFlowQueryRequest

        group_by = list(group_by or [])
        if grain is not None:
            if grain not in TIME_GRAINS:
                raise SemanticLayerError(f"grain must be one of {', '.join(TIME_GRAINS)}, got {grain!r}.")
            group_by.insert(0, f"metric_time__{grain}")
        if not 1 <= limit <= MAX_ROWS:
            raise SemanticLayerError(f"limit must be between 1 and {MAX_ROWS}, got {limit}.")

        # filters are data, never SQL: each becomes `dimension IN ('v1', 'v2')` with quotes escaped
        where = []
        for dimension, values in (filters or {}).items():
            if not values:
                raise SemanticLayerError(f"Filter on {dimension!r} needs at least one value.")
            quoted = ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)
            where.append(f"{{{{ Dimension('{dimension}') }}}} in ({quoted})")
        log.debug("request: metrics=%s group_by=%s where=%s start=%s end=%s limit=%s",
                  metrics, group_by, where, start, end, limit)

        request = MetricFlowQueryRequest.create(
            metric_names=metrics,
            group_by_names=group_by or None,
            where_constraints=where or None,
            time_constraint_start=_date(start, "start"),
            time_constraint_end=_date(end, "end"),
            order_by_names=order_by or [g for g in group_by if g.startswith("metric_time")] or None,
            limit=limit + 1,  # one extra row tells us whether the result was cut off
        )
        result = self._run(request)
        table = result.result_df
        rows = [dict(zip(table.column_names, map(_to_json, row))) for row in table.rows]
        log.debug("MetricFlow returned %d row(s); SQL starts with: %s", len(rows), " ".join(result.sql.split())[:160])
        return {
            "columns": list(table.column_names),
            "rows": rows[:limit],
            "truncated": len(rows) > limit,
            "sql": result.sql,
        }

    def _run(self, request: Any) -> Any:
        def call() -> Any:
            with contextlib.redirect_stdout(sys.stderr):
                return self.engine.query(request)

        try:
            return self._executor.submit(call).result(timeout=QUERY_TIMEOUT_SECONDS)
        except FutureTimeout:
            raise SemanticLayerError(
                f"Query timed out after {QUERY_TIMEOUT_SECONDS}s. Use fewer group_by items, a shorter date range "
                "or a coarser grain."
            ) from None
        except SemanticLayerError:
            raise
        except Exception as e:  # MetricFlow's resolution errors already list the valid names
            log.debug("MetricFlow rejected the query: %s", type(e).__name__)
            raise SemanticLayerError(str(e)) from None
