"""Output formatters for MCP tool responses.

Supports three output formats:
- csv:  Compact CSV output, most token-efficient for LLM agents (default)
- text: Human-readable aligned columns for debugging
- json: Raw JSON, backward-compatible with v1 behavior
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def format_metricsql_result(
    data: dict[str, Any],
    fmt: str = "csv",
    *,
    meta: dict[str, Any] | None = None,
) -> str:
    """Format a single MetricsQL query response."""
    if fmt == "json":
        return _json(data)
    series_list = _extract_metricsql_series(data)
    header_comment = _metricsql_header(meta, len(series_list))
    if fmt == "text":
        return _metricsql_text(series_list, header_comment)
    return _metricsql_csv(series_list, header_comment)


def format_metricsql_batch(
    results: list[dict[str, Any]],
    fmt: str = "csv",
    *,
    meta: dict[str, Any] | None = None,
) -> str:
    """Format a batch of MetricsQL query results."""
    if fmt == "json":
        return _json(results)
    ok = sum(1 for r in results if r.get("status") == "ok")
    fail = len(results) - ok
    header_comment = _batch_header("metricsql_batch", ok, fail, meta)
    if fmt == "text":
        return _metricsql_batch_text(results, header_comment)
    return _metricsql_batch_csv(results, header_comment)


def format_tracing_insights(
    data: Any,
    fmt: str = "csv",
    *,
    meta: dict[str, Any] | None = None,
) -> str:
    """Format a single tracing operation_insights response."""
    if fmt == "json":
        return _json(data)
    ops = extract_tracing_operations(data)
    header_comment = _tracing_header(meta, ops)
    if fmt == "text":
        return _tracing_text(ops, header_comment)
    return _tracing_csv(ops, header_comment)


def format_tracing_batch(
    results: list[dict[str, Any]],
    fmt: str = "csv",
    *,
    meta: dict[str, Any] | None = None,
) -> str:
    """Format a batch of tracing operation_insights results."""
    if fmt == "json":
        return _json(results)
    ok = sum(1 for r in results if r.get("status") == "ok")
    fail = len(results) - ok
    header_comment = _batch_header("tracing_batch", ok, fail, meta)
    if fmt == "text":
        return _tracing_batch_text(results, header_comment)
    return _tracing_batch_csv(results, header_comment)


def format_dashboard_tiles(
    data: dict[str, Any],
    fmt: str = "csv",
    *,
    meta: dict[str, Any] | None = None,
) -> str:
    """Format dashboard tile query results."""
    if fmt == "json":
        return _json(data)
    tiles = data.get("tile_results", [])
    header_comment = _dashboard_header(data, meta)
    if fmt == "text":
        return _dashboard_text(tiles, header_comment)
    return _dashboard_csv(tiles, header_comment)


def format_dashboard_find(matches: list[dict[str, str]], search: str) -> str:
    """Format dashboard search results (always CSV)."""
    buf = io.StringIO()
    buf.write(f"# dashboard_find: {len(matches)} matches for \"{search}\"\n")
    w = csv.writer(buf)
    w.writerow(["collection_id", "collection_title", "dashboard_id", "dashboard_title"])
    for m in matches:
        w.writerow([m.get("collection_id", ""), m.get("collection_title", ""),
                     m.get("dashboard_id", ""), m.get("dashboard_title", "")])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# OTel span classification
# ---------------------------------------------------------------------------

OTEL_SPAN_CATEGORIES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^DB\b|^GET_CONNECTION$"),                                            "db/sql"),
    (re.compile(r"^(GET|SET|DEL|EVALSHA|EVAL|MGET|HGET|HSET|HDEL|EXPIRE|TTL|PING)$"), "db/redis"),
    (re.compile(r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS) /"),                       "http"),
    (re.compile(r"^/[a-z].*\."),                                                       "grpc"),
    (re.compile(r"\.\w+:\w+"),                                                         "internal"),
]
OTEL_SPAN_DEFAULT_CATEGORY = "other"


def classify_otel_span(operation: str) -> str:
    """Classify an operation name into an OTel category.

    Categories: db/sql, db/redis, http, grpc, internal, other.
    Based on OTel auto-instrumentation span naming conventions.
    """
    for pattern, category in OTEL_SPAN_CATEGORIES:
        if pattern.search(operation):
            return category
    return OTEL_SPAN_DEFAULT_CATEGORY


# ---------------------------------------------------------------------------
# OTel label alias rewriting
# ---------------------------------------------------------------------------

OTEL_LABEL_ALIASES: dict[str, str] = {
    "container":  "k8s_container_name",
    "pod":        "k8s_pod_name",
    "namespace":  "k8s_namespace_name",
    "node":       "k8s_node_name",
    "deployment": "k8s_deployment_name",
    "replicaset": "k8s_replicaset_name",
    "daemonset":  "k8s_daemonset_name",
}

# Match label names inside {...} blocks in PromQL.
# Captures: label_name="value" or label_name=~"value"
_PROMQL_LABEL_RE = re.compile(
    r'(?<=[{,])\s*(' + '|'.join(re.escape(k) for k in OTEL_LABEL_ALIASES) + r')\s*([=!~]+)'
)


def rewrite_otel_labels(query: str) -> str:
    """Rewrite short OTel label aliases in PromQL to their full names.

    E.g.: container="enforce" → k8s_container_name="enforce"
    Only rewrites inside {...} blocks. Metric names and function names are untouched.
    """
    def _replace(m: re.Match[str]) -> str:
        alias = m.group(1).strip()
        op = m.group(2)
        full = OTEL_LABEL_ALIASES.get(alias, alias)
        return f"{full}{op}"
    return _PROMQL_LABEL_RE.sub(_replace, query)


# ---------------------------------------------------------------------------
# Time range parsing
# ---------------------------------------------------------------------------

_RANGE_PATTERN = re.compile(r"^(\d+)(m|h|d)$")

_STEP_THRESHOLDS: list[tuple[int, int]] = [
    (30 * 60,      15),      # ≤30m → 15s
    (4 * 3600,     60),      # ≤4h  → 60s
    (24 * 3600,    300),     # ≤24h → 300s
    (7 * 86400,    3600),    # ≤7d  → 3600s
    (30 * 86400,   86400),   # ≤30d → 86400s
]


def parse_time_range(time_range: str) -> int | None:
    """Parse a human time range string to seconds.

    '24h' → 86400, '30m' → 1800, '7d' → 604800.
    Returns None if the string is empty or invalid.
    """
    s = time_range.strip()
    if not s:
        return None
    m = _RANGE_PATTERN.match(s)
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2)
    multiplier = {"m": 60, "h": 3600, "d": 86400}
    return value * multiplier[unit]


def auto_step(duration_seconds: int) -> int:
    """Select an appropriate query step given the duration.

    Returns step in seconds.
    """
    for threshold, step in _STEP_THRESHOLDS:
        if duration_seconds <= threshold:
            return step
    return 86400


# ---------------------------------------------------------------------------
# Tracing helpers
# ---------------------------------------------------------------------------

def build_tracing_query(
    service: str = "",
    kind: str = "",
    operation: str = "",
) -> str:
    """Build an OpsRamp tracing query DSL string.

    Always injects app IN ("default").
    """
    parts = ['app IN ("default")']
    if service:
        parts.append(f'service IN ("{service}")')
    if kind:
        parts.append(f'kind IN ("{kind}")')
    if operation:
        parts.append(f'operation IN ("{operation}")')
    return " AND ".join(parts)


def ensure_nanoseconds(value: str) -> str:
    """Convert epoch seconds to nanoseconds if needed.

    If digit count ≤ 12 → treat as seconds, multiply by 10^9.
    If digit count ≥ 16 → already nanoseconds.
    """
    v = value.strip()
    if not v.isdigit():
        return v
    if len(v) <= 12:
        return str(int(v) * 1_000_000_000)
    return v


# ---------------------------------------------------------------------------
# Internal: Series statistics
# ---------------------------------------------------------------------------

def _series_stats(values: list[list[Any]]) -> dict[str, Any]:
    """Compute summary stats from [[timestamp, value], ...] pairs."""
    if not values:
        return {"pts": 0, "avg": 0, "min": 0, "max": 0, "first": 0, "last": 0}
    floats = []
    for pair in values:
        try:
            floats.append(float(pair[1]))
        except (ValueError, TypeError, IndexError):
            continue
    if not floats:
        return {"pts": 0, "avg": 0, "min": 0, "max": 0, "first": 0, "last": 0}
    return {
        "pts": len(floats),
        "avg": sum(floats) / len(floats),
        "min": min(floats),
        "max": max(floats),
        "first": floats[0],
        "last": floats[-1],
    }


def _humanize_number(n: float) -> str:
    """Format a number for compact display.

    Large numbers get suffixes: 1234567 → '1.18M', 123456 → '120.6K'.
    Small numbers stay as-is with reasonable precision.
    """
    abs_n = abs(n)
    if abs_n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}G"
    if abs_n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs_n >= 10_000:
        return f"{n / 1_000:.1f}K"
    if abs_n >= 100:
        return f"{n:.1f}"
    if abs_n >= 1:
        return f"{n:.2f}"
    if abs_n >= 0.001:
        return f"{n:.4f}"
    return f"{n:.6f}"


def _format_stat(value: float) -> str:
    """Format a stat value for CSV/text output."""
    return _humanize_number(value)


def _labels_str(metric: dict[str, str]) -> str:
    """Convert metric labels dict to compact key=value string."""
    filtered = {k: v for k, v in metric.items() if k != "__name__"}
    if not filtered:
        return ""
    return ",".join(f"{k}={v}" for k, v in filtered.items())


# ---------------------------------------------------------------------------
# Internal: MetricsQL formatters
# ---------------------------------------------------------------------------

def _extract_metricsql_series(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract series from a MetricsQL response."""
    d = data.get("data", {})
    if not isinstance(d, dict):
        return []
    result = d.get("result", [])
    if not isinstance(result, list):
        return []
    return result


def _metricsql_header(meta: dict[str, Any] | None, series_count: int) -> str:
    meta = meta or {}
    parts = ["# metricsql: ok"]
    tenant = meta.get("tenant", "")
    if tenant:
        parts.append(f"tenant={tenant}")
    tr = meta.get("time_range", "")
    step = meta.get("effective_step") or meta.get("step", "")
    if tr:
        parts.append(f"range={tr}")
    if step:
        parts.append(f"step={step}")
    parts.append(f"{series_count} series")
    return " | ".join(parts)


def _metricsql_csv(series_list: list[dict[str, Any]], header: str) -> str:
    buf = io.StringIO()
    buf.write(header + "\n")
    w = csv.writer(buf)
    w.writerow(["labels", "pts", "avg", "min", "max", "first", "last"])
    for s in series_list:
        stats = _series_stats(s.get("values", []))
        labels = _labels_str(s.get("metric", {}))
        w.writerow([
            labels, stats["pts"],
            _format_stat(stats["avg"]), _format_stat(stats["min"]),
            _format_stat(stats["max"]), _format_stat(stats["first"]),
            _format_stat(stats["last"]),
        ])
    return buf.getvalue()


def _metricsql_text(series_list: list[dict[str, Any]], header: str) -> str:
    lines = [f"=== {header}", ""]
    for s in series_list:
        stats = _series_stats(s.get("values", []))
        labels = _labels_str(s.get("metric", {}))
        lines.append(
            f"  {labels} | pts={stats['pts']} | "
            f"avg={_format_stat(stats['avg'])} min={_format_stat(stats['min'])} "
            f"max={_format_stat(stats['max'])} last={_format_stat(stats['last'])}"
        )
    lines.append("")
    return "\n".join(lines)


def _batch_header(prefix: str, ok: int, fail: int, meta: dict[str, Any] | None) -> str:
    meta = meta or {}
    status = f"{ok}/{ok + fail} ok"
    if fail:
        status += f" {fail} fail"
    parts = [f"# {prefix}: {status}"]
    tenant = meta.get("tenant", "")
    if tenant:
        parts.append(f"tenant={tenant}")
    tr = meta.get("time_range", "")
    step = meta.get("effective_step") or meta.get("step", "")
    if tr:
        parts.append(f"range={tr}")
    if step:
        parts.append(f"step={step}")
    return " | ".join(parts)


def _metricsql_batch_csv(results: list[dict[str, Any]], header: str) -> str:
    buf = io.StringIO()
    buf.write(header + "\n")
    w = csv.writer(buf)
    w.writerow(["id", "labels", "pts", "avg", "min", "max", "first", "last"])
    for r in results:
        qid = r.get("id", "")
        if r.get("status") != "ok":
            error_msg = r.get("error", "unknown error")
            w.writerow([qid, f"ERROR: {error_msg}", "", "", "", "", ""])
            continue
        for s in r.get("series", []):
            stats = _series_stats(s.get("values", []))
            labels = _labels_str(s.get("metric", {}))
            w.writerow([
                qid, labels, stats["pts"],
                _format_stat(stats["avg"]), _format_stat(stats["min"]),
                _format_stat(stats["max"]), _format_stat(stats["first"]),
                _format_stat(stats["last"]),
            ])
    return buf.getvalue()


def _metricsql_batch_text(results: list[dict[str, Any]], header: str) -> str:
    lines = [f"=== {header}", ""]
    for r in results:
        qid = r.get("id", "")
        if r.get("status") != "ok":
            lines.append(f"[{qid}] ERROR: {r.get('error', 'unknown')}")
            continue
        series = r.get("series", [])
        lines.append(f"[{qid}] {len(series)} series")
        for s in series:
            stats = _series_stats(s.get("values", []))
            labels = _labels_str(s.get("metric", {}))
            lines.append(
                f"  {labels} | pts={stats['pts']} | "
                f"avg={_format_stat(stats['avg'])} min={_format_stat(stats['min'])} "
                f"max={_format_stat(stats['max'])} last={_format_stat(stats['last'])}"
            )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal: Tracing formatters
# ---------------------------------------------------------------------------

def extract_tracing_operations(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract operations list from tracing insights response.

    The API returns a nested structure:
        {"apps": {"default": {"services": {"<svc>": {"operations": [...]}}}}}
    We also handle flat formats for forward compatibility.
    """
    if isinstance(data, list):
        return data
    # Try nested apps.*.services.*.operations (actual API format)
    apps = data.get("apps", {})
    if isinstance(apps, dict):
        for app_val in apps.values():
            services = app_val.get("services", {}) if isinstance(app_val, dict) else {}
            for svc_val in services.values():
                ops = svc_val.get("operations", []) if isinstance(svc_val, dict) else []
                if isinstance(ops, list) and ops:
                    return ops
    # Try flat formats
    ops = data.get("data", data.get("operations", []))
    if isinstance(ops, list):
        return ops
    return []


def _tracing_header(meta: dict[str, Any] | None, ops: list[dict[str, Any]]) -> str:
    meta = meta or {}
    parts = ["# tracing: ok"]
    tenant = meta.get("tenant", "")
    if tenant:
        parts.append(f"tenant={tenant}")
    service = meta.get("service", "")
    kind = meta.get("kind", "")
    svc_parts = []
    if service:
        svc_parts.append(f"service={service}")
    if kind:
        svc_parts.append(f"kind={kind}")
    if svc_parts:
        parts.append(" ".join(svc_parts))
    tr = meta.get("time_range", "")
    if tr:
        parts.append(f"range={tr}")
    total = meta.get("total_operations", len(ops))
    sort_by = meta.get("sort_by", "throughput")
    parts.append(f"{len(ops)}/{total} ops sort={sort_by}")
    return " | ".join(parts)


def _tracing_op_row(op: dict[str, Any], rank: int) -> list[str]:
    """Build a CSV row from a single tracing operation."""
    name = op.get("operationName", op.get("operation", ""))
    tput = op.get("throughput", 0)
    avg_us = op.get("averageLatency", 0)
    p99_us = op.get("p99Latency", op.get("p99", 0))
    err_pct = op.get("errorPercentage", op.get("errorRate", 0))
    otel_cat = op.get("otel_cat", classify_otel_span(name))
    return [
        str(rank),
        name,
        _format_stat(tput),
        _format_stat(avg_us / 1000) if avg_us > 1000 else _format_stat(avg_us),
        _format_stat(p99_us / 1000) if p99_us > 1000 else _format_stat(p99_us),
        _format_stat(err_pct),
        otel_cat,
    ]


def _tracing_csv(ops: list[dict[str, Any]], header: str) -> str:
    buf = io.StringIO()
    buf.write(header + "\n")
    w = csv.writer(buf)
    w.writerow(["rank", "operation", "tput_s", "avg_ms", "p99_ms", "err_pct", "otel_cat"])
    for i, op in enumerate(ops, 1):
        w.writerow(_tracing_op_row(op, i))
    return buf.getvalue()


def _tracing_text(ops: list[dict[str, Any]], header: str) -> str:
    lines = [f"=== {header}", ""]
    for i, op in enumerate(ops, 1):
        row = _tracing_op_row(op, i)
        lines.append(
            f"  {row[0]:>3}. {row[1]:<60} tput={row[2]:>8} avg={row[3]:>8} "
            f"p99={row[4]:>8} err={row[5]:>6} [{row[6]}]"
        )
    lines.append("")
    return "\n".join(lines)


def _tracing_batch_csv(results: list[dict[str, Any]], header: str) -> str:
    buf = io.StringIO()
    buf.write(header + "\n")
    w = csv.writer(buf)
    w.writerow(["id", "rank", "operation", "tput_s", "avg_ms", "p99_ms", "err_pct", "otel_cat"])
    for r in results:
        qid = r.get("id", "")
        if r.get("status") != "ok":
            w.writerow([qid, "", f"ERROR: {r.get('error', 'unknown')}", "", "", "", "", ""])
            continue
        for i, op in enumerate(r.get("operations", []), 1):
            row = _tracing_op_row(op, i)
            w.writerow([qid] + row)
    return buf.getvalue()


def _tracing_batch_text(results: list[dict[str, Any]], header: str) -> str:
    lines = [f"=== {header}", ""]
    for r in results:
        qid = r.get("id", "")
        if r.get("status") != "ok":
            lines.append(f"[{qid}] ERROR: {r.get('error', 'unknown')}")
            continue
        ops = r.get("operations", [])
        lines.append(f"[{qid}] {len(ops)} operations")
        for i, op in enumerate(ops, 1):
            row = _tracing_op_row(op, i)
            lines.append(
                f"  {row[0]:>3}. {row[1]:<60} tput={row[2]:>8} avg={row[3]:>8} "
                f"p99={row[4]:>8} err={row[5]:>6} [{row[6]}]"
            )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal: Dashboard formatters
# ---------------------------------------------------------------------------

def _dashboard_header(data: dict[str, Any], meta: dict[str, Any] | None) -> str:
    meta = meta or {}
    title = data.get("dashboard_title", "")
    tiles = data.get("tile_results", [])
    ok = sum(1 for t in tiles if t.get("status") == "success")
    parts = ["# dashboard_tiles: ok"]
    tenant = meta.get("tenant", "")
    if tenant:
        parts.append(f"tenant={tenant}")
    if title:
        parts.append(f'dashboard="{title}"')
    tr = meta.get("time_range", "")
    step = meta.get("step", "")
    if tr:
        parts.append(f"range={tr}")
    if step:
        parts.append(f"step={step}")
    parts.append(f"{len(tiles)} tiles {ok}/{len(tiles)} ok")
    return " | ".join(parts)


def _dashboard_csv(tiles: list[dict[str, Any]], header: str) -> str:
    buf = io.StringIO()
    buf.write(header + "\n")
    w = csv.writer(buf)
    # Summary section
    w.writerow(["tile_id", "tile_title", "series_count", "status"])
    for t in tiles:
        series_count = 0
        if t.get("status") == "success":
            d = t.get("data", {})
            if isinstance(d, dict):
                result = d.get("data", {})
                if isinstance(result, dict):
                    series_count = len(result.get("result", []))
        w.writerow([
            t.get("tile_id", ""), t.get("tile_title", ""),
            series_count, t.get("status", "unknown"),
        ])
    buf.write("---\n")
    # Detail section
    w.writerow(["tile_id", "labels", "pts", "avg", "min", "max", "first", "last"])
    for t in tiles:
        if t.get("status") != "success":
            continue
        tid = t.get("tile_id", "")
        series = _extract_metricsql_series(t.get("data", {}))
        for s in series:
            stats = _series_stats(s.get("values", []))
            labels = _labels_str(s.get("metric", {}))
            w.writerow([
                tid, labels, stats["pts"],
                _format_stat(stats["avg"]), _format_stat(stats["min"]),
                _format_stat(stats["max"]), _format_stat(stats["first"]),
                _format_stat(stats["last"]),
            ])
    return buf.getvalue()


def _dashboard_text(tiles: list[dict[str, Any]], header: str) -> str:
    lines = [f"=== {header}", ""]
    for t in tiles:
        tid = t.get("tile_id", "")
        title = t.get("tile_title", "")
        status = t.get("status", "unknown")
        if status != "success":
            lines.append(f"[{tid}] {title} — {status}: {t.get('error', '')}")
            continue
        series = _extract_metricsql_series(t.get("data", {}))
        lines.append(f"[{tid}] {title} — {len(series)} series")
        for s in series:
            stats = _series_stats(s.get("values", []))
            labels = _labels_str(s.get("metric", {}))
            lines.append(
                f"  {labels} | pts={stats['pts']} | "
                f"avg={_format_stat(stats['avg'])} min={_format_stat(stats['min'])} "
                f"max={_format_stat(stats['max'])} last={_format_stat(stats['last'])}"
            )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal: JSON helper
# ---------------------------------------------------------------------------

def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)
