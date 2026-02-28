# OpsRamp MCP (FastMCP)

A Python FastMCP server for OpsRamp APIs, focused on:

- **Dashboard APIs (v3)** — search, variable resolution, tile execution
- **MetricsQL APIs (v3)** — single & batch PromQL queries with auto-downsample
- **Tracing APIs (v1)** — operation insights with OTel span classification
- **v2 Metric compatibility** — list & get metric definitions

> ✅ Supports **multiple OpsRamp instances (platforms)** and **multiple tenants** via TOML config.
>
> 🚫 **Policy:** TOML-only configuration. Environment variables are intentionally not used.

## Registered tools (v0.2.0)

11 tools are exposed to the LLM. 7 low-level tools from v0.1.0 have been consolidated into higher-level equivalents.

| # | Tool | Category | Description |
|---|------|----------|-------------|
| 1 | `opsramp_metricsql_query_smart` | MetricsQL | Single PromQL query with auto-downsample, sharding & OTel label rewriting |
| 2 | `opsramp_metricsql_batch_query` | MetricsQL | Parallel execution of multiple PromQL queries (concurrency=4) |
| 3 | `opsramp_metricsql_labels` | MetricsQL | List available label names |
| 4 | `opsramp_metricsql_label_values` | MetricsQL | List values for a specific label |
| 5 | `opsramp_tracing_operation_insights` | Tracing | Operation insights with structured params, auto DSL & OTel classification |
| 6 | `opsramp_tracing_batch_insights` | Tracing | Parallel tracing insights across multiple services/operations |
| 7 | `opsramp_dashboard_run_tiles_smart` | Dashboard | Execute all tile queries with variable resolution |
| 8 | `opsramp_dashboard_find` | Dashboard | Fuzzy search dashboards across all collections |
| 9 | `opsramp_dashboard_get_variables` | Dashboard | Get dashboard variable definitions |
| 10 | `opsramp_v2_list_metrics` | v2 compat | List metric definitions |
| 11 | `opsramp_v2_get_metric` | v2 compat | Get a single metric definition |

### Key features in v0.2.0

- **CSV-first output** — All query tools default to `output_format="csv"` for token-efficient LLM consumption. Also supports `text` and `json`.
- **`time_range` parameter** — Human-readable durations like `"24h"`, `"7d"`, `"30m"` auto-resolve to `(start, end, step)`.
- **OTel label rewriting** — Short label aliases (`container`, `pod`, `namespace`, etc.) are auto-expanded to OTel-compliant names (`k8s_container_name`, `k8s_pod_name`, etc.).
- **OTel span classification** — Tracing operations are auto-tagged with `otel_cat` (db/sql, db/redis, http, grpc, internal).
- **Batch tools** — `metricsql_batch_query` and `tracing_batch_insights` run multiple queries in parallel with `asyncio.Semaphore(4)`.
- **Dashboard search** — `dashboard_find` searches across all collections by keyword.

### Deregistered tools (from v0.1.0)

These tools are still in the codebase but no longer registered with MCP:

- `dashboard_list_collections` / `dashboard_list_dashboards` / `dashboard_get` → replaced by `dashboard_find`
- `metricsql_query` → replaced by `metricsql_query_smart`
- `metricsql_push_data` → write operations removed from LLM scope
- `v2_list_reporting_apps` → rarely used, removed from tool surface
- `tracing_top_operations` → consolidated into `tracing_operation_insights`

## Setup

1. Install dependencies: `pip install -e .` (or use `uv`).
2. Copy `config.example.toml` to `config.toml` and fill your platforms/tenants.
3. Run: `opsramp-mcp` (or `python -m opsramp_mcp`).

For a full installation and onboarding guide, see [docs/MCP_TUTORIAL.md](docs/MCP_TUTORIAL.md).

## Configuration

### Config file resolution order

1. Explicit `--config` CLI argument
2. `./opsramp.toml`
3. `./config.toml`
4. `~/.config/opsramp-mcp/config.toml`

### Multi-platform TOML config

A **platform** = an OpsRamp cluster/instance. Each platform has one or more **tenants**.

```toml
default_platform = "dev_cluster"

[platforms.dev_cluster]
api_base_url = "https://your-dev-instance.api.opsramp.com"
client_id = "..."
client_secret = "..."
default_tenant = "tenant_dev"

[platforms.dev_cluster.tenants.tenant_dev]
id = "tenant_dev_uuid"

[platforms.dev_cluster.tenants.tenant_test]
id = "tenant_test_uuid"
```

See `config.example.toml` for a complete template.

## Usage examples

### Single PromQL query (CSV output)

```
opsramp_metricsql_query_smart(
    query='avg(container_cpu_usage{container="enforce"})',
    time_range="24h",
    output_format="csv"
)
```

### Batch PromQL queries

```
opsramp_metricsql_batch_query(
    queries=[
        {"id": "cpu", "query": 'avg(container_cpu_usage{container="enforce"})'},
        {"id": "mem", "query": 'avg(container_memory_usage{container="enforce"})'},
    ],
    time_range="1h"
)
```

### Tracing operation insights

```
opsramp_tracing_operation_insights(
    service="enforce",
    kind="server",
    time_range="24h"
)
```

### Find a dashboard

```
opsramp_dashboard_find(search="enforce")
```

## Testing

```bash
python -m pytest tests/ -v
```

84 unit tests covering formatters, server utilities, and tool registration.

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/promql_smoke.py` | Validate MetricsQL queries |
| `scripts/query_benchmark.py` | Long-range query benchmarks |
| `scripts/smoke_test.py` | General smoke test |
| `scripts/dashboard_data_test.py` | Dashboard data validation |
| `scripts/dashboard_tiles_smart_smoke.py` | Dashboard tile execution smoke test |

All scripts support `--config`, `--platform`, `--tenant` CLI arguments.

## Architecture

```
src/opsramp_mcp/
├── __init__.py        # Version
├── config.py          # TOML config loader (multi-platform)
├── client.py          # HTTP client (OAuth, retry, API methods)
├── formatters.py      # Output formatting (CSV/text/JSON), OTel helpers
└── server.py          # MCP tool definitions (11 registered)
```

See [docs/DESIGN.md](docs/DESIGN.md) for the full v0.2.0 design document.

## Notes

- OAuth token is obtained from `/tenancy/auth/oauth/token` using client credentials.
- Some dashboard endpoints may require additional tenant/partner headers. Tools expose an `additional_headers` field, and tenant-specific headers defined in TOML are merged automatically.
