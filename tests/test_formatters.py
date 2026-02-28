"""Tests for opsramp_mcp.formatters module."""

from __future__ import annotations

import json
from typing import Any

import pytest

from opsramp_mcp.formatters import (
    OTEL_LABEL_ALIASES,
    auto_step,
    build_tracing_query,
    classify_otel_span,
    ensure_nanoseconds,
    format_dashboard_find,
    format_dashboard_tiles,
    format_metricsql_batch,
    format_metricsql_result,
    format_tracing_batch,
    format_tracing_insights,
    parse_time_range,
    rewrite_otel_labels,
)


# ======================================================================
# parse_time_range
# ======================================================================

class TestParseTimeRange:
    def test_hours(self):
        assert parse_time_range("24h") == 86400

    def test_minutes(self):
        assert parse_time_range("30m") == 1800

    def test_days(self):
        assert parse_time_range("7d") == 604800

    def test_single_hour(self):
        assert parse_time_range("1h") == 3600

    def test_empty(self):
        assert parse_time_range("") is None

    def test_whitespace(self):
        assert parse_time_range("  24h  ") == 86400

    def test_invalid(self):
        assert parse_time_range("abc") is None

    def test_no_unit(self):
        assert parse_time_range("100") is None

    def test_seconds_not_supported(self):
        assert parse_time_range("60s") is None


# ======================================================================
# auto_step
# ======================================================================

class TestAutoStep:
    def test_short_range(self):
        assert auto_step(600) == 15       # 10m → 15s

    def test_medium_range(self):
        assert auto_step(3600) == 60      # 1h → 60s

    def test_day_range(self):
        assert auto_step(86400) == 300    # 24h → 300s

    def test_week_range(self):
        assert auto_step(604800) == 3600  # 7d → 3600s

    def test_month_range(self):
        assert auto_step(86400 * 30) == 86400  # 30d → 86400s

    def test_very_long(self):
        assert auto_step(86400 * 365) == 86400


# ======================================================================
# rewrite_otel_labels
# ======================================================================

class TestRewriteOtelLabels:
    def test_basic_rewrite(self):
        q = 'avg(container_cpu_usage{container="enforce"})'
        result = rewrite_otel_labels(q)
        assert 'k8s_container_name="enforce"' in result

    def test_pod_rewrite(self):
        q = 'sum(rate(http_requests{pod="web-1"}[5m]))'
        result = rewrite_otel_labels(q)
        assert 'k8s_pod_name="web-1"' in result

    def test_multiple_labels(self):
        q = 'metric{container="app",namespace="prod"}'
        result = rewrite_otel_labels(q)
        assert "k8s_container_name" in result
        assert "k8s_namespace_name" in result

    def test_already_long_name_untouched(self):
        q = 'metric{k8s_container_name="app"}'
        result = rewrite_otel_labels(q)
        assert result == q

    def test_no_labels_untouched(self):
        q = "sum(rate(container_cpu_usage[5m]))"
        result = rewrite_otel_labels(q)
        assert result == q

    def test_regex_operator(self):
        q = 'metric{container=~"enforce.*"}'
        result = rewrite_otel_labels(q)
        assert 'k8s_container_name=~"enforce.*"' in result

    def test_negation_operator(self):
        q = 'metric{container!="enforce"}'
        result = rewrite_otel_labels(q)
        assert 'k8s_container_name!="enforce"' in result


# ======================================================================
# classify_otel_span
# ======================================================================

class TestClassifyOtelSpan:
    def test_db_select(self):
        assert classify_otel_span("DB SELECT") == "db/sql"

    def test_db_insert(self):
        assert classify_otel_span("DB INSERT") == "db/sql"

    def test_db_plain(self):
        assert classify_otel_span("DB") == "db/sql"

    def test_get_connection(self):
        assert classify_otel_span("GET_CONNECTION") == "db/sql"

    def test_redis_get(self):
        assert classify_otel_span("GET") == "db/redis"

    def test_redis_set(self):
        assert classify_otel_span("SET") == "db/redis"

    def test_redis_evalsha(self):
        assert classify_otel_span("EVALSHA") == "db/redis"

    def test_redis_ping(self):
        assert classify_otel_span("PING") == "db/redis"

    def test_http_get(self):
        assert classify_otel_span("GET /authorization/v2alpha1/enforce") == "http"

    def test_http_post(self):
        assert classify_otel_span("POST /api/v1/users") == "http"

    def test_grpc(self):
        assert classify_otel_span("/authz.v1.AuthzService/Enforce") == "grpc"

    def test_internal(self):
        assert classify_otel_span("eventbus.go:PublishEvent") == "internal"

    def test_unknown(self):
        assert classify_otel_span("something random") == "other"

    def test_empty(self):
        assert classify_otel_span("") == "other"


# ======================================================================
# build_tracing_query
# ======================================================================

class TestBuildTracingQuery:
    def test_service_only(self):
        q = build_tracing_query(service="enforce")
        assert 'app IN ("default")' in q
        assert 'service IN ("enforce")' in q

    def test_service_and_kind(self):
        q = build_tracing_query(service="enforce", kind="server")
        assert 'kind IN ("server")' in q

    def test_all_params(self):
        q = build_tracing_query(service="enforce", kind="client", operation="DB SELECT")
        assert 'service IN ("enforce")' in q
        assert 'kind IN ("client")' in q
        assert 'operation IN ("DB SELECT")' in q

    def test_empty_params(self):
        q = build_tracing_query()
        assert q == 'app IN ("default")'


# ======================================================================
# ensure_nanoseconds
# ======================================================================

class TestEnsureNanoseconds:
    def test_epoch_seconds(self):
        result = ensure_nanoseconds("1709000000")
        assert result == "1709000000000000000"

    def test_already_nanoseconds(self):
        ns = "1709000000000000000"
        assert ensure_nanoseconds(ns) == ns

    def test_non_numeric(self):
        assert ensure_nanoseconds("now-1h") == "now-1h"

    def test_whitespace(self):
        assert ensure_nanoseconds("  1709000000  ") == "1709000000000000000"


# ======================================================================
# format_metricsql_result
# ======================================================================

def _make_metricsql_response(series: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": series,
        },
    }


def _make_series(labels: dict[str, str], values: list[list[Any]]) -> dict[str, Any]:
    return {"metric": labels, "values": values}


class TestFormatMetricsqlResult:
    def test_csv_output(self):
        data = _make_metricsql_response([
            _make_series(
                {"k8s_container_name": "enforce"},
                [[1709000000, "0.15"], [1709000300, "0.18"]],
            ),
        ])
        result = format_metricsql_result(data, "csv")
        assert "# metricsql:" in result
        assert "labels,pts,avg,min,max,first,last" in result
        assert "k8s_container_name=enforce" in result
        assert "2" in result  # 2 points

    def test_json_output(self):
        data = _make_metricsql_response([])
        result = format_metricsql_result(data, "json")
        parsed = json.loads(result)
        assert parsed["status"] == "success"

    def test_text_output(self):
        data = _make_metricsql_response([
            _make_series({"container": "app"}, [[1, "100"]]),
        ])
        result = format_metricsql_result(data, "text")
        assert "===" in result
        assert "container=app" in result

    def test_empty_series(self):
        data = _make_metricsql_response([])
        result = format_metricsql_result(data, "csv")
        assert "0 series" in result


# ======================================================================
# format_metricsql_batch
# ======================================================================

class TestFormatMetricsqlBatch:
    def test_csv_batch_ok(self):
        results = [
            {
                "id": "cpu",
                "status": "ok",
                "series": [_make_series({"container": "app"}, [[1, "0.5"], [2, "0.6"]])],
            },
            {
                "id": "mem",
                "status": "ok",
                "series": [_make_series({"container": "app"}, [[1, "200000000"]])],
            },
        ]
        result = format_metricsql_batch(results, "csv")
        assert "2/2 ok" in result
        assert "cpu" in result
        assert "mem" in result

    def test_csv_batch_with_error(self):
        results = [
            {"id": "good", "status": "ok", "series": []},
            {"id": "bad", "status": "error", "error": "400 parse error"},
        ]
        result = format_metricsql_batch(results, "csv")
        assert "1/2 ok" in result
        assert "1 fail" in result
        assert "ERROR" in result

    def test_json_batch(self):
        results = [{"id": "q1", "status": "ok", "series": []}]
        result = format_metricsql_batch(results, "json")
        parsed = json.loads(result)
        assert isinstance(parsed, list)


# ======================================================================
# format_tracing_insights
# ======================================================================

class TestFormatTracingInsights:
    def test_csv_output(self):
        data = [
            {
                "operationName": "POST /api/enforce",
                "throughput": 44.6,
                "averageLatency": 36600,
                "p99Latency": 119900,
                "errorPercentage": 0.11,
            },
            {
                "operationName": "DB SELECT",
                "throughput": 304.9,
                "averageLatency": 1200,
                "p99Latency": 4100,
                "errorPercentage": 0.0,
            },
        ]
        result = format_tracing_insights(data, "csv")
        assert "# tracing:" in result
        assert "rank,operation" in result
        assert "POST /api/enforce" in result
        assert "http" in result
        assert "db/sql" in result

    def test_text_output(self):
        data = [{"operationName": "GET /health", "throughput": 10}]
        result = format_tracing_insights(data, "text")
        assert "===" in result
        assert "GET /health" in result

    def test_json_output(self):
        data = [{"operationName": "test"}]
        result = format_tracing_insights(data, "json")
        parsed = json.loads(result)
        assert isinstance(parsed, list)


# ======================================================================
# format_tracing_batch
# ======================================================================

class TestFormatTracingBatch:
    def test_csv_batch(self):
        results = [
            {
                "id": "enforce-server",
                "status": "ok",
                "operations": [
                    {"operationName": "POST /enforce", "throughput": 44.6,
                     "averageLatency": 36600, "p99Latency": 119900, "errorPercentage": 0.11},
                ],
            },
            {
                "id": "enforce-client",
                "status": "ok",
                "operations": [
                    {"operationName": "DB SELECT", "throughput": 304.9,
                     "averageLatency": 1200, "p99Latency": 4100, "errorPercentage": 0.0},
                ],
            },
        ]
        result = format_tracing_batch(results, "csv")
        assert "2/2 ok" in result
        assert "enforce-server" in result
        assert "enforce-client" in result
        assert "http" in result
        assert "db/sql" in result

    def test_batch_with_error(self):
        results = [
            {"id": "ok-q", "status": "ok", "operations": []},
            {"id": "bad-q", "status": "error", "error": "timeout"},
        ]
        result = format_tracing_batch(results, "csv")
        assert "1 fail" in result
        assert "ERROR" in result


# ======================================================================
# format_dashboard_tiles
# ======================================================================

class TestFormatDashboardTiles:
    def test_csv_output(self):
        data = {
            "dashboard_title": "Enforce Overview",
            "tile_results": [
                {
                    "tile_id": "t1",
                    "tile_title": "CPU Usage",
                    "status": "success",
                    "data": _make_metricsql_response([
                        _make_series({"pod": "enforce-abc"}, [[1, "0.15"], [2, "0.18"]]),
                    ]),
                },
            ],
        }
        result = format_dashboard_tiles(data, "csv")
        assert "# dashboard_tiles:" in result
        assert "Enforce Overview" in result
        assert "CPU Usage" in result
        assert "pod=enforce-abc" in result

    def test_json_output(self):
        data = {"dashboard_title": "Test", "tile_results": []}
        result = format_dashboard_tiles(data, "json")
        parsed = json.loads(result)
        assert parsed["dashboard_title"] == "Test"


# ======================================================================
# format_dashboard_find
# ======================================================================

class TestFormatDashboardFind:
    def test_matches(self):
        matches = [
            {"collection_id": "5", "collection_title": "enforce",
             "dashboard_id": "108", "dashboard_title": "Enforce Overview"},
        ]
        result = format_dashboard_find(matches, "enforce")
        assert '1 matches for "enforce"' in result
        assert "Enforce Overview" in result

    def test_no_matches(self):
        result = format_dashboard_find([], "nonexistent")
        assert "0 matches" in result
