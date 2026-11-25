"""Ledgerly metrics MCP server (stdio).

The agent gets governed metrics only: no SQL tool, no raw tables, a read-only database connection.

    uv run python agent/server.py          # or: make serve
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from semantic import SemanticLayer, SemanticLayerError

INSTRUCTIONS = """\
Answer questions about Ledgerly's SaaS metrics (MRR, ARR, active customers, discounts, MRR at risk) using only
these tools. Call list_metrics first to see the metrics, their business definitions and their dimensions.

Rules:
- MRR-type metrics and active_customers are month-end snapshots. For a quarter or a year the tools already
  return the value of its last month; never add up months yourself.
- All amounts are in EUR unless a metric says otherwise.
- Data covers {first_month} to {last_month} (month starts). A period after that has no data; a period that is only
  partly covered returns the latest month available, so say so in the answer.
- Internal test accounts are already excluded. Do not try to compute metrics from raw data.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

log = logging.getLogger("ledgerly")


def build_server(layer: SemanticLayer) -> MCPServer:
    with contextlib.redirect_stdout(sys.stderr):
        first, last = layer.sql_client.query("select min(month_start), max(month_start) from fct_mrr_monthly").rows[0]
    server = MCPServer(
        name="ledgerly-metrics",
        instructions=INSTRUCTIONS.format(first_month=first.date(), last_month=last.date()),
    )

    @server.tool(annotations=READ_ONLY)
    def list_metrics() -> list[dict[str, Any]]:
        """List every available metric: name, label, type, business definition and valid dimensions.

        Time dimensions (metric_time, customer__cohort_month, customer__signup_month) can be grouped at
        month, quarter or year grain by adding a suffix, e.g. customer__cohort_month__quarter.
        """
        log.debug("tool call: list_metrics()")
        return layer.list_metrics()

    @server.tool(annotations=READ_ONLY)
    def query_metric(
        metrics: list[str],
        group_by: list[str] | None = None,
        grain: Literal["month", "quarter", "year"] | None = None,
        start: str | None = None,
        end: str | None = None,
        filters: dict[str, list[str]] | None = None,
        order_by: list[str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Query one or more metrics from the semantic layer and return rows plus the SQL that was run.

        Args:
            metrics: metric names from list_metrics, e.g. ["mrr", "arr"].
            group_by: dimension names from list_metrics, e.g. ["customer__country_code", "plan__tier"].
            grain: adds metric_time at this grain, e.g. "quarter" for one row per quarter.
                Without grain or a metric_time group_by, snapshot metrics return the latest month.
            start: first date to include, YYYY-MM-DD (filters on metric_time).
            end: last date to include, YYYY-MM-DD (filters on metric_time).
            filters: keep only these values of a categorical dimension, e.g. {"customer__country_code": ["PL"]}.
            order_by: metric or group_by names; prefix with "-" for descending. Defaults to time ascending.
            limit: maximum rows to return (1-500). "truncated" is true when more rows exist.
        """
        log.debug("tool call: query_metric(metrics=%s, group_by=%s, grain=%s, start=%s, end=%s, filters=%s)",
                  metrics, group_by, grain, start, end, filters)
        try:
            return layer.query(metrics, group_by, grain, start, end, filters, order_by, limit)
        except SemanticLayerError as e:
            log.debug("returning the error to the agent as a tool error (is_error=true)")
            raise ToolError(str(e)) from None

    return server


def main() -> None:
    # LEDGERLY_TRACE=1 logs every step with file:line to stderr (stdout is the MCP channel)
    logging.basicConfig(stream=sys.stderr, format="  [%(filename)s:%(lineno)d %(funcName)s] %(message)s")
    log.setLevel(logging.DEBUG if os.environ.get("LEDGERLY_TRACE") else logging.WARNING)
    log.debug("server starting")
    try:
        layer = SemanticLayer()
    except SemanticLayerError as e:
        sys.exit(f"ledgerly-metrics: {e}")
    log.debug("listening for MCP messages on stdin")
    build_server(layer).run("stdio")


if __name__ == "__main__":
    main()
