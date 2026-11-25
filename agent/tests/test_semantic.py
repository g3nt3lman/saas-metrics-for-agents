import pytest

from semantic import MAX_ROWS, SemanticLayerError

CORE_METRICS = {"mrr", "arr", "active_customers", "mrr_gross", "discount_mrr", "mrr_at_risk"}


def _sql_scalar(layer, sql):
    return layer.sql_client.query(sql).rows[0][0]


def test_list_metrics_exposes_core_metrics_with_definitions(layer):
    metrics = {m["name"]: m for m in layer.list_metrics()}
    assert CORE_METRICS <= set(metrics)
    for name in CORE_METRICS:
        assert len(metrics[name]["description"]) > 30, f"{name} needs a business definition for the agent"
    assert "customer__country_code" in metrics["mrr"]["dimensions"]
    assert "customer_month__month_start" not in metrics["mrr"]["dimensions"]  # hidden duplicate of metric_time


def test_golden_mrr_last_month_equals_mart(layer):
    rows = layer.query(["mrr"], grain="month", order_by=["-metric_time__month"], limit=1)["rows"]
    expected = _sql_scalar(
        layer, "select sum(mrr_eur) from fct_mrr_monthly where month_start = (select max(month_start) from fct_mrr_monthly)"
    )
    assert rows[0]["mrr"] == pytest.approx(expected, abs=0.01)


def test_quarter_is_its_last_month_not_the_sum(layer):
    quarter = layer.query(["mrr"], grain="quarter", start="2025-01-01", end="2025-03-31")["rows"]
    months = layer.query(["mrr"], grain="month", start="2025-01-01", end="2025-03-31")["rows"]
    assert len(quarter) == 1 and len(months) == 3
    assert quarter[0]["mrr"] == pytest.approx(months[-1]["mrr"], abs=0.01)
    assert quarter[0]["mrr"] < sum(r["mrr"] for r in months)


def test_arr_is_twelve_times_mrr(layer):
    for row in layer.query(["mrr", "arr"], grain="quarter")["rows"]:
        assert row["arr"] == pytest.approx(row["mrr"] * 12, abs=0.1)


def test_filter_equals_group_by_slice(layer):
    filtered = layer.query(["mrr"], filters={"customer__country_code": ["PL"]})["rows"]
    grouped = layer.query(["mrr"], group_by=["customer__country_code"])["rows"]
    pl = next(r for r in grouped if r["customer__country_code"] == "PL")
    assert filtered[0]["mrr"] == pytest.approx(pl["mrr"], abs=0.01)


def test_filter_values_are_data_not_sql(layer):
    rows = layer.query(["mrr"], filters={"customer__country_code": ["PL') or (1=1"]})["rows"]
    assert rows == [] or rows[0]["mrr"] in (None, 0)


def test_result_includes_compiled_sql(layer):
    assert "fct_mrr_monthly" in layer.query(["mrr"], grain="year")["sql"]


def test_limit_is_enforced_and_truncation_reported(layer):
    result = layer.query(["mrr"], grain="month", limit=3)
    assert len(result["rows"]) == 3 and result["truncated"] is True
    with pytest.raises(SemanticLayerError, match="between 1 and"):
        layer.query(["mrr"], limit=MAX_ROWS + 1)


@pytest.mark.parametrize(
    ("kwargs", "hint"),
    [
        ({"metrics": ["mrrr"]}, "mrr"),
        ({"metrics": ["mrr"], "group_by": ["customer__country"]}, "customer__country_code"),
        ({"metrics": ["mrr"], "grain": "week"}, "month, quarter, year"),
        ({"metrics": ["mrr"], "start": "Q1 2025"}, "YYYY-MM-DD"),
    ],
)
def test_errors_tell_the_agent_what_is_valid(layer, kwargs, hint):
    with pytest.raises(SemanticLayerError, match=hint):
        layer.query(**kwargs)
