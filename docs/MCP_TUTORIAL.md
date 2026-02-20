# OpsRamp MCP Installation and Quick Start

This tutorial is a practical installation and onboarding guide with:

- clear quick-start options,
- copy/paste MCP client config,
- explicit TOML examples,
- local development workflow.

---

## 1) What this server provides

`opsramp-mcp` is a FastMCP server for OpsRamp APIs, with:

- Dashboard v3 tools,
- MetricsQL v3 tools,
- smart long-range querying (auto-downsample + sharding fallback),
- v2 compatibility helpers.

The server is **TOML-only** for configuration.

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
        "git+https://github.com/weihong-xu-hpe/OPSRampMCP.git",
        "opsramp-mcp"
      ]
    }
  }
}
```

Then create config at:

- `~/.config/opsramp-mcp/config.toml`

Example:

```toml
default_platform = "qa_glcp"

[platforms.qa_glcp]
api_base_url = "https://glcp-ccs-qa.api.opsramp.com"
client_id = "your_client_id"
client_secret = "your_client_secret"
verify_tls = true
timeout_seconds = 30
default_tenant = "hoku"

[platforms.qa_glcp.tenants.hoku]
id = "your_tenant_uuid"
```

### Option B: Clone and run locally

```bash
git clone https://github.com/weihong-xu-hpe/OPSRampMCP.git
cd OPSRampMCP
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
        "git+https://github.com/weihong-xu-hpe/OPSRampMCP.git",
        "opsramp-mcp"
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

## 5) First tool calls to test

Recommended onboarding flow:

1. `opsramp_server_info`
2. `opsramp_auth_test_on_platform`
3. `opsramp_list_platforms`
4. `opsramp_dashboard_list_collections`
5. `opsramp_metricsql_labels`
6. `opsramp_metricsql_query_smart`

For dashboard-wide execution:

- use `opsramp_dashboard_run_tiles_smart`
- it returns tile-level data JSON directly.

---

## 6) Development workflow

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

## 7) Configuration and behavior notes

- This project is **strictly TOML-only** (no env-var override path).
- Naming uses `platform`/`tenant` because OpsRamp requires tenant scoping.
- MetricsQL smart mode includes downsampling and slice-merge behavior.
