# OpsRamp MCP (FastMCP)

A Python FastMCP server for OpsRamp APIs, focused on:

- **Dashboard APIs (v3)**
- **MetricsQL APIs (v3)**
- **Compatibility helpers for v2 metrics/reporting APIs**

> ✅ Supports **multiple OpsRamp instances (platforms)** and **multiple tenants** via TOML config.
>
> 🚫 **Policy:** TOML-only configuration. Environment variables are intentionally not used.

## What this server can do

### Dashboard (v3)
- List collections: `GET /dashboards/api/v3/collections`
- List dashboards in collection: `GET /dashboards/api/v3/collections/{id}/dashboards`
- Get dashboard details: `GET /dashboards/api/v3/collections/{id}/dashboards/{id}`
- Run dashboard tile queries with direct data output: `opsramp_dashboard_run_tiles_smart`

### MetricsQL (v3)
- Query metrics series: `GET /metricsql/api/v3/tenants/{tenantId}/metrics`
- Smart query mode in MCP (`opsramp_metricsql_query_smart`): auto downsample + sharded fallback for long ranges
- List labels: `GET /metricsql/api/v3/tenants/{tenantId}/metrics/labels`
- List metric names under label: `GET /metricsql/api/v3/tenants/{tenantId}/metrics/labels/{label_name}`
- Push time-series samples: `POST /metricsql/api/v3/tenants/{clientId}/metrics/data`

### v2 compatibility
- List metrics: `GET /api/v2/tenants/{tenantId}/metrics`
- Get metric by name: `GET /api/v2/tenants/{tenantId}/metrics/{metricName}`
- List reporting apps: `GET /api/v2/tenants/{tenantId}/reporting-apps/available/search`

## Setup

1. Install dependencies with your preferred Python toolchain.
2. Copy `config.example.toml` to `config.toml` and fill your platforms/tenants.
3. Run MCP server via project script `opsramp-mcp`.

No environment-variable configuration is required or supported.

For a full installation and onboarding guide, see `docs/MCP_TUTORIAL.md`.

## Multi-platform TOML config (recommended)

In this repository, a **platform** is conceptually the same as a **cluster / OpsRamp instance**.

The server loads config in this order:

1. explicit path from `--config` CLI argument
2. `./opsramp.toml`
3. `./config.toml`
4. `~/.config/opsramp-mcp/config.toml`

Example structure:

- top-level `default_platform`
- `platforms.<platform_alias>` for each OpsRamp instance
- `platforms.<platform_alias>.tenants.<tenant_alias>` for tenant mapping

See `config.example.toml` for a complete template.

Quick sample:

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

[platforms.prod_cluster]
api_base_url = "https://your-prod-instance.api.opsramp.com"
client_id = "..."
client_secret = "..."
default_tenant = "tenant_prod"

[platforms.prod_cluster.tenants.tenant_prod]
id = "tenant_prod_uuid"
```

## Configuration policy

- Only TOML is supported.
- No `.env` configuration path.
- No environment-variable based override path.

If you need a custom config location, start the server with:

- `opsramp-mcp --config /absolute/path/to/config.toml`

## Tool usage pattern with platform/tenant switching

Most tools now support:


Useful helper tool:

- `opsramp_server_info` — debug server metadata (version, active config path, platform summary)

MetricsQL example flow:

1. call `opsramp_metricsql_labels` with `platform="qa_glcp"`, `tenant="hoku"`
2. call `opsramp_metricsql_query` with same platform/tenant and a PromQL query

For long time ranges, prefer `opsramp_metricsql_query_smart`:


For dashboard-level execution, use `opsramp_dashboard_run_tiles_smart`:


At startup, the server writes debug-friendly lines to stderr including:

- server version
- requested config path (if provided)
- resolved active config path
- Renders tile query templates (e.g. `$App`, `$Service`, `$__range`, `$__interval`)
- Executes each tile query through smart MetricsQL
- Returns **tile-level raw data JSON** directly (no visualization step in MCP)

Key inputs:

- `collection_id`, `dashboard_id`, `platform`, `tenant`/`tenant_id`
- `start`, `end`, `step`
- optional `variables_map` override
- optional `execution_options`:
	- `auto_downsample` (default `true`)
	- `enable_sharding` (default `true`)
	- `max_points_per_slice` (default `8000`)
	- `concurrency` (default `4`, bounded to `1..20`)
	- `limit_tiles` (default `0`, means no limit)
- optional `output_options`:
	- `include_rendered_query` (default `true`)
	- `include_dashboard` (default `false`)

## Notes

- OAuth token is obtained from `/tenancy/auth/oauth/token` using client credentials.
- Some dashboard endpoints may require additional tenant/partner headers depending on tenant scope and permissions. The MCP tools expose an `additional_headers` field for this.
- You can define tenant-specific headers in TOML under `additional_headers` and they will be merged automatically.

## PromQL smoke tests

Use `scripts/promql_smoke.py` to validate multiple MetricsQL (PromQL-compatible) queries quickly.

Both smoke scripts are TOML-only and support CLI arguments:

- `--config` TOML file path
- `--platform` platform alias
- `--tenant` tenant alias
- `--tenant-id` explicit tenant/client ID override

It executes a small suite including:

- `system_cpu_utilization`
- label-filtered query
- regex metric-name query
- `topk(...)` query

It reports per-query status and number of returned series.

For targeted long-range benchmarks, use `scripts/query_benchmark.py`:

- normal mode: direct single query
- smart mode: add `--smart`
- optional controls: `--disable-auto-downsample`, `--disable-sharding`, `--max-points-per-slice`
