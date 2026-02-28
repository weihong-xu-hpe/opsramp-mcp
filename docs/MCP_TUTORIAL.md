# OpsRamp MCP Installation and Quick Start

This tutorial is a practical installation and onboarding guide with:

- clear quick-start options,
- copy/paste MCP client config,
- explicit TOML examples,
- local development workflow.

---

## 1) What this server provides

`opsramp-mcp` is a FastMCP server that encapsulates OpsRamp + OTel platform complexity, with:

- **11 focused tools** (down from 15) — broader coverage, less context overhead,
- **CSV-first output** (`output_format="csv"`) — optimized for LLM token efficiency,
- **Batch tools** — `metricsql_batch_query` and `tracing_batch_insights` for parallel multi-query execution,
- **`time_range` convenience** — `"1h"`, `"24h"`, `"7d"` instead of manually computing epoch timestamps,
- **OTel label alias rewriting** — write `container="enforce"`, MCP rewrites to `k8s_container_name="enforce"`,
- **OTel span auto-categorization** — each tracing operation tagged with `otel_cat` (`http`, `db/sql`, `db/redis`, `grpc`, etc.),
- **Dashboard search** — `dashboard_find` replaces separate list_collections + list_dashboards calls,
- Smart long-range querying (auto-downsample + sharding fallback),
- v2 metric compatibility helpers.

The server is **TOML-only** for configuration.

See [docs/design/context-saving-rewrite.md](design/context-saving-rewrite.md) for the full design rationale.

---

## 2) Prerequisite

Install `uv` (recommended for MCP Python projects).

Typical macOS option:

- `brew install uv`

Alternative installer option:

- `curl -LsSf https://astral.sh/uv/install.sh | sh`

---

## 3) Quick Start (for users)

### Option A: One-click via VS Code MCP config

Add this to your VS Code MCP configuration (`settings.json` or `.vscode/mcp.json`):

```json
{
  "servers": {
    "opsramp-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/weihong-xu-hpe/opsramp-mcp.git",
        "opsramp-mcp",
        "--config",
        "/absolute/path/to/your/config.toml"
      ]
    }
  }
}
```

Then create config at:

- `~/.config/opsramp-mcp/config.toml`

Example:

```toml
default_platform = "dev_cluster"

[platforms.dev_cluster]
api_base_url = "https://your-dev-instance.api.opsramp.com"
client_id = "your_client_id"
client_secret = "your_client_secret"
verify_tls = true
timeout_seconds = 30
default_tenant = "tenant_dev"

[platforms.dev_cluster.tenants.tenant_dev]
id = "your_tenant_uuid"
```

### Option B: Clone and run locally

```bash
git clone https://github.com/weihong-xu-hpe/opsramp-mcp.git
cd opsramp-mcp
uv sync
cp config.example.toml config.toml
# edit config.toml
```

Run in stdio mode:

```bash
uv run mcp run src/opsramp_mcp/server.py
```

Run with an explicit config path:

```bash
opsramp-mcp --config /absolute/path/to/config.toml
```

Debug output at startup includes:

- server version
- requested config path (if `--config` is provided)
- resolved active config path

### Option C: Claude Desktop

Add to Claude Desktop MCP config:

```json
{
  "mcpServers": {
    "opsramp-mcp": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/weihong-xu-hpe/opsramp-mcp.git",
        "opsramp-mcp",
        "--config",
        "/absolute/path/to/your/config.toml"
      ]
    }
  }
}
```

---

## 4) Configuration model (TOML)

### Search order

1. explicit path from CLI/tool argument,
2. `./opsramp.toml`,
3. `./config.toml`,
4. `~/.config/opsramp-mcp/config.toml`.

### Schema shape

- `default_platform` (string)
- `[platforms.<platform>]`
  - `api_base_url`, `client_id`, `client_secret`
  - optional `verify_tls`, `timeout_seconds`
  - `default_tenant`
- `[platforms.<platform>.tenants.<tenant>]`
  - `id`
  - optional `[additional_headers]`

This uses a top-level selector and named endpoint blocks, extended for
tenant-aware OpsRamp routing.

---

## 5) Tool inventory

| # | Tool | Purpose |
|---|------|--------|
| 1 | `opsramp_metricsql_query_smart` | Execute a single PromQL query with auto-downsample/sharding |
| 2 | `opsramp_metricsql_batch_query` | Execute multiple PromQL queries in parallel |
| 3 | `opsramp_metricsql_labels` | List available metric label names |
| 4 | `opsramp_metricsql_label_values` | List values for a specific label |
| 5 | `opsramp_v2_list_metrics` | List available metrics (v2 API) |
| 6 | `opsramp_v2_get_metric` | Get metric metadata (v2 API) |
| 7 | `opsramp_tracing_operation_insights` | Tracing operation analytics with structured params + otel_cat |
| 8 | `opsramp_tracing_batch_insights` | Multiple tracing queries in parallel |
| 9 | `opsramp_dashboard_find` | Search dashboards by name across all collections |
| 10 | `opsramp_dashboard_get_variables` | Get dashboard template variables |
| 11 | `opsramp_dashboard_run_tiles_smart` | Execute all tiles in a dashboard |

### Removed tools (still available as client.py methods for scripts)

- `opsramp_metricsql_query` — superseded by `query_smart`
- `opsramp_metricsql_push_data` — write operation, out of scope
- `opsramp_v2_list_reporting_apps` — zero usage
- `opsramp_tracing_top_operations` — superseded by `operation_insights`
- `opsramp_dashboard_list_collections` — superseded by `dashboard_find`
- `opsramp_dashboard_list_dashboards` — superseded by `dashboard_find`
- `opsramp_dashboard_get` — covered by `run_tiles_smart` / `get_variables`

---

## 6) Key features

### output_format (csv / text / json)

Applies to: `metricsql_query_smart`, `metricsql_batch_query`, `tracing_operation_insights`, `tracing_batch_insights`, `dashboard_run_tiles_smart`.

- **`csv`** (default) — most token-efficient for LLM agents
- **`text`** — human-readable aligned columns for debugging
- **`json`** — full raw response, backward compatible

Example CSV output from `metricsql_query_smart`:

```csv
# metricsql: ok | tenant=mira-east | range=24h step=300 | 1 series
labels,pts,avg,min,max,first,last
container=enforce,288,0.1520,0.0900,0.4230,0.1400,0.1500
```

### time_range convenience parameter

Instead of computing epoch timestamps manually:

```
time_range="1h"    # last 1 hour
time_range="24h"   # last 24 hours
time_range="7d"    # last 7 days
```

MCP auto-computes `start`/`end` and selects an appropriate `step`. If `start`/`end` are explicitly set, `time_range` is ignored.

### OTel label alias rewriting

Enabled by default (`rewrite_otel_labels=True`). Write concise PromQL:

```promql
avg(container_cpu_usage{container="enforce"})
```

MCP rewrites to the actual OTel label before sending to OpsRamp:

```promql
avg(container_cpu_usage{k8s_container_name="enforce"})
```

Supported aliases: `container`, `pod`, `namespace`, `node`, `deployment`, `replicaset`, `daemonset`.

### Batch queries

`metricsql_batch_query` — execute multiple PromQL queries in one call:

```json
{
  "queries": [
    {"id": "enforce-cpu", "query": "avg(container_cpu_usage{container=\"enforce\"})"},
    {"id": "enforce-mem", "query": "avg(container_memory_working_set{container=\"enforce\"})"}
  ],
  "time_range": "24h"
}
```

`tracing_batch_insights` — execute multiple tracing queries in one call:

```json
{
  "queries": [
    {"id": "enforce-server", "service": "enforce", "kind": "server", "limit": 10},
    {"id": "enforce-client", "service": "enforce", "kind": "client", "limit": 10}
  ],
  "time_range": "1h"
}
```

### Dashboard search

`dashboard_find` — search by name across all collections:

```json
{"search": "enforce"}
```

Returns matching `collection_id` + `dashboard_id` pairs ready for `dashboard_run_tiles_smart`.

---

## 7) First tool calls to test

Recommended onboarding flow:

1. `opsramp_metricsql_labels` — verify connectivity and see available labels
2. `opsramp_metricsql_query_smart` with `time_range="1h"` — run a simple PromQL query
3. `opsramp_dashboard_find` with `search="overview"` — discover dashboards
4. `opsramp_dashboard_run_tiles_smart` with `time_range="1h"` — execute a dashboard
5. `opsramp_tracing_operation_insights` with `service="your-service"`, `time_range="1h"` — get tracing data

---

## 8) Development workflow

Run MCP Inspector:

```bash
uv run mcp dev src/opsramp_mcp/server.py
```

Run server directly:

```bash
uv run mcp run src/opsramp_mcp/server.py
```

Optional smoke scripts:

- `scripts/promql_smoke.py`
- `scripts/query_benchmark.py`
- `scripts/dashboard_tiles_smart_smoke.py`

---

## 9) Configuration and behavior notes

- This project is **strictly TOML-only** (no env-var override path).
- Naming uses `platform`/`tenant` because OpsRamp requires tenant scoping.
- MetricsQL smart mode includes downsampling and slice-merge behavior.
- Default `output_format` is `csv` (breaking change from v1 JSON default). Use `output_format="json"` for raw responses.
- OTel label rewriting is on by default. Set `rewrite_otel_labels=False` to disable.
- Tracing tools auto-inject `app IN ("default")` and convert epoch seconds to nanoseconds.
