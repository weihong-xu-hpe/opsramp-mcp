# OpsRamp MCP v2 Design Document

> Date: 2026-02-28 | Status: Draft — Pending Review

## Table of Contents

- [1. Background & Problem](#1-background--problem)
- [2. Design Principles](#2-design-principles)
- [3. Group 1 — opsramp-mcp Changes](#3-group-1--opsramp-mcp-changes)
  - [3.1 Tool Removal](#31-tool-removal)
  - [3.2 output_format Three-Tier Output](#32-output_format-three-tier-output)
  - [3.3 time_range Convenience Parameter](#33-time_range-convenience-parameter)
  - [3.4 OTel Label Alias Transparent Rewriting](#34-otel-label-alias-transparent-rewriting)
  - [3.5 OTel Span Category Auto-Tagging](#35-otel-span-category-auto-tagging)
  - [3.6 Tracing Parameter Structuring + Automatic ns Conversion](#36-tracing-parameter-structuring--automatic-ns-conversion)
  - [3.7 metricsql_batch_query New Tool](#37-metricsql_batch_query-new-tool)
  - [3.8 tracing_batch_insights New Tool](#38-tracing_batch_insights-new-tool)
  - [3.9 dashboard_find Merged Tool](#39-dashboard_find-merged-tool)
  - [3.10 Final Tool Inventory](#310-final-tool-inventory)
- [4. Implementation Phases](#4-implementation-phases)
- [5. Backward Compatibility](#5-backward-compatibility)

---

## 1. Background & Problem

The current MCP exposes 15 tools that are essentially 1:1 proxies for OpsRamp REST APIs. All OpsRamp + OTel platform knowledge is pushed to the consumer side (agent prompt + skill), resulting in:

| Problem | Quantification |
|---------|---------------|
| ~300 lines in agent prompt dedicated to teaching the agent "how to correctly call MCP" | 45% of 671 lines |
| Each daily report consumes ~25,000 tokens of OpsRamp data | 14 tool calls × raw JSON |
| Consumer side forced to invent tmp-first protocol + parse scripts to work around context bloat | 2 extra scripts + ~50 lines of protocol rules |
| Only 2 out of 15 tools are frequently used by the daily report agent | 13 tools wasting tool schema context |

### Core Insight

MCP should encapsulate **OpsRamp + OTel platform complexity** without touching service domain logic:

| Belongs in MCP (Platform Knowledge) | Belongs on Consumer Side (Service Knowledge) |
|--------------------------------------|----------------------------------------------|
| OTel label aliases (`container` → `k8s_container_name`) | Service name → container mapping (`enforce` → `enforce`) |
| OTel span name convention → auto-categorization (`DB SELECT` → `db/sql`) | Application-layer span semantics (`eventbus.go:*` → kafka) |
| Epoch seconds vs nanoseconds conversion | Which PromQL to execute (diagnostic intent) |
| Auto step / downsample / sharding | Severity assessment, report formatting |
| `app IN ("default")` auto-injection | Historical comparison, baseline logic |
| Response → compact output compression | Business meaning of categorization/grouping |

---

## 2. Design Principles

1. **MCP = Platform Execution Engine**: Encapsulate OpsRamp API quirks + OTel conventions, expose clean interfaces
2. **CSV-first Output**: LLM agents are the primary consumers, token efficiency comes first
3. **Batch-first**: Reduce tool call count, leverage MCP-internal concurrency
4. **Backward Compatibility**: All new parameters have defaults, `output_format="json"` preserves original behavior
5. **No Embedded Service Mappings**: PromQL is constructed by the caller, MCP only handles label alias rewriting

---

## 3. Group 1 — opsramp-mcp Changes

### 3.1 Tool Removal

| Tool | Decision | Rationale |
|------|----------|-----------|
| `opsramp_metricsql_query` | **Removed** | `query_smart` is a strict superset (includes auto downsample + sharding) |
| `opsramp_metricsql_push_data` | **Removed** | Write operation, not in the diagnostics/monitoring domain |
| `opsramp_v2_list_reporting_apps` | **Removed** | Zero usage records, no known use cases |
| `opsramp_tracing_top_operations` | **Removed** | `operation_insights` is strictly superior (`top_operations` latency fields are all 0) |

The 3 tools that are commented out but not registered (`auth_test`, `list_platforms`, `server_info`) remain as-is.

The corresponding client.py methods are retained (scripts may use them directly); they are simply no longer registered as MCP tools.

### 3.2 output_format Three-Tier Output

Applies to: `metricsql_query_smart`, `metricsql_batch_query`, `tracing_operation_insights`, `tracing_batch_insights`, `dashboard_run_tiles_smart`

```python
output_format: str = "csv"   # "csv" | "text" | "json"
```

#### csv (default)

MetricsQL single query example:

```csv
# metricsql: ok | tenant=mira-east | range=24h step=300 | 1 series
labels,pts,avg,min,max,first,last
container=enforce,288,0.1520,0.0900,0.4230,0.1400,0.1500
```

MetricsQL batch example:

```csv
# metricsql_batch: 8/8 ok | tenant=mira-east | range=24h step=300
id,labels,pts,avg,min,max,first,last
enforce-cpu,container=enforce,288,0.1520,0.0900,0.4230,0.1400,0.1500
enforce-mem,container=enforce,288,215.3M,210.0M,312.1M,214.0M,228.0M
enforce-mem-avail,container=enforce,288,200.4M,185.2M,220.1M,205.0M,198.3M
enforce-pods,container=enforce,288,6,6,6,6,6
authz-cpu,container=authz-services,288,0.0830,0.0510,0.1200,0.0800,0.0790
authz-mem,container=authz-services,288,180.5M,172.3M,195.8M,178.0M,182.1M
```

Multi-series query (e.g., `sum by (transaction_category, kind) (...)`):

```csv
# metricsql_batch: 2/2 ok | tenant=mira-east | range=24h step=300
id,labels,pts,avg,min,max,first,last
trace-tp,"transaction_category=Database,kind=client",288,8.40,7.10,9.80,8.10,8.20
trace-tp,"transaction_category=HTTP,kind=server",288,0.60,0.40,1.10,0.55,0.50
trace-tp,"transaction_category=HTTP,kind=client",288,0.01,0.00,0.03,0.01,0.01
trace-tp,"transaction_category=RPC Systems,kind=client",288,0.003,0.00,0.01,0.003,0.003
trace-p95,"transaction_category=Database,kind=client",288,12.3ms,8.1ms,18.5ms,11.0ms,11.9ms
trace-p95,"transaction_category=HTTP,kind=server",288,45.2ms,30.1ms,89.3ms,40.0ms,42.1ms
```

Tracing operation insights example:

```csv
# tracing: ok | tenant=mira-east | service=enforce kind=server | range=24h | 10/45 ops sort=throughput
rank,operation,tput_s,avg_ms,p99_ms,err_pct,otel_cat
1,POST /authorization/v2alpha1/enforce,44.60,36.6,119.9,0.11,http
2,POST /authorization/internal/v1/…/{email}/enforce,13.50,264.0,5000.0,1.40,http
3,GET /readyz,50.20,0.5,2.1,0.00,http
4,GET /authorization/v2alpha1/current-user-permissions,5.00,105.0,2140.0,0.49,http
```

Tracing batch example:

```csv
# tracing_batch: 4/4 ok | tenant=mira-east | range=24h
id,rank,operation,tput_s,avg_ms,p99_ms,err_pct,otel_cat
enforce-server,1,POST /authorization/v2alpha1/enforce,44.60,36.6,119.9,0.11,http
enforce-server,2,GET /readyz,50.20,0.5,2.1,0.00,http
enforce-client,1,DB SELECT,304.90,1.2,4.1,0.00,db/sql
enforce-client,2,DB,90.30,1.5,4.1,0.00,db/sql
enforce-client,3,GET,58.60,0.7,1.4,0.00,db/redis
enforce-client,4,GET /accounts/internal/v1/customers/platform/{id},31.10,56.7,2000.0,1.52,http
authz-server,1,POST /authorization/v2alpha1/scope-groups,8.20,12.3,45.0,0.02,http
authz-client,1,DB SELECT,120.30,0.8,3.2,0.00,db/sql
```

Dashboard tiles example:

```csv
# dashboard_tiles: ok | tenant=mira-east | dashboard="Enforce Overview" | range=1h step=60 | 6 tiles 6/6 ok
tile_id,tile_title,series_count,status
t1,CPU Usage,6,ok
t2,Memory Working Set,6,ok
t3,Request Rate,1,ok
---
tile_id,labels,pts,avg,min,max,first,last
t1,k8s_pod_name=enforce-7b4f9c-x2k8p,60,0.152,0.09,0.42,0.14,0.15
t1,k8s_pod_name=enforce-7b4f9c-abc12,60,0.110,0.08,0.19,0.10,0.11
t2,k8s_pod_name=enforce-7b4f9c-x2k8p,60,215.3M,210.0M,220.1M,214.0M,215.3M
t3,,60,44.60,38.20,52.10,42.00,44.60
```

#### text (debugging / human-readable)

Aligned column format, following the proven style of parse_opsramp_response.py:

```
=== metricsql_batch: 8/8 ok | tenant=mira-east | range=24h step=300

[enforce-cpu] 1 series
  container=enforce | pts=288 | avg=0.152 min=0.09 max=0.42 last=0.15

[enforce-mem] 1 series
  container=enforce | pts=288 | avg=215.3M min=210.0M max=312.1M last=228.0M

[trace-tp] 4 series
  transaction_category=Database, kind=client | pts=288 | avg=8.40 min=7.10 max=9.80 last=8.20
  transaction_category=HTTP, kind=server     | pts=288 | avg=0.60 min=0.40 max=1.10 last=0.50
```

#### json (backward compatible)

Full raw JSON, identical to current behavior.

#### Implementation Location

New `src/opsramp_mcp/formatters.py` module:

```python
# formatters.py — Output formatting

def format_metricsql_result(data: dict, fmt: str = "csv") -> str: ...
def format_metricsql_batch(results: list, fmt: str = "csv") -> str: ...
def format_tracing_insights(data: dict, fmt: str = "csv") -> str: ...
def format_tracing_batch(results: list, fmt: str = "csv") -> str: ...
def format_dashboard_tiles(data: dict, fmt: str = "csv") -> str: ...

def _series_stats(values: list) -> dict:
    """Compute avg/min/max/first/last from [[ts, val], ...]."""
    floats = [float(v) for _, v in values]
    return {
        "avg": sum(floats) / len(floats),
        "min": min(floats),
        "max": max(floats),
        "first": floats[0],
        "last": floats[-1],
        "pts": len(floats),
    }

def _humanize_bytes(n: float) -> str:
    """225800000 → '215.3M'"""
    ...

def _humanize_duration(us: float) -> str:
    """36600 → '36.6ms', 663 → '0.7ms'"""
    ...
```

No pandas dependency introduced. Generated using Python stdlib `csv.writer` + `io.StringIO`.

### 3.3 time_range Convenience Parameter

Applies to: `metricsql_query_smart`, `metricsql_batch_query`, `tracing_operation_insights`, `tracing_batch_insights`, `dashboard_run_tiles_smart`

```python
time_range: str = ""   # "1h" | "4h" | "24h" | "7d" | "30d" — mutually exclusive with start/end
```

**Behavior**:
- If `time_range` is non-empty and `start`/`end` are at default values → MCP internally computes `end = now_epoch`, `start = now_epoch - duration_seconds`
- If `start`/`end` are explicitly specified → `time_range` is ignored (backward compatible)
- Automatic step selection:

  | time_range | step (seconds) |
  |------------|:-:|
  | ≤30m | 15 |
  | ≤4h | 60 |
  | ≤24h | 300 |
  | ≤7d | 3600 |
  | ≤30d | 86400 |

- For tracing tools, internally auto-multiplied by `× 1_000_000_000` to convert to nanoseconds

**Parsing function** (added to server.py or a standalone utils module):

```python
_RANGE_PATTERN = re.compile(r"^(\d+)(m|h|d)$")

def _parse_time_range(time_range: str) -> int | None:
    """'24h' → 86400 seconds, invalid → None"""
    m = _RANGE_PATTERN.match(time_range.strip())
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2)
    multiplier = {"m": 60, "h": 3600, "d": 86400}
    return value * multiplier[unit]
```

### 3.4 OTel Label Alias Transparent Rewriting

Applies to: `metricsql_query_smart`, `metricsql_batch_query`

```python
rewrite_otel_labels: bool = True   # enabled by default
```

**Alias mapping table** (embedded in server.py, configurable and extensible):

```python
OTEL_LABEL_ALIASES: dict[str, str] = {
    "container":  "k8s_container_name",
    "pod":        "k8s_pod_name",
    "namespace":  "k8s_namespace_name",
    "node":       "k8s_node_name",
    "deployment": "k8s_deployment_name",
    "replicaset": "k8s_replicaset_name",
    "daemonset":  "k8s_daemonset_name",
}
```

**Rewriting logic**: Before submitting the query to the OpsRamp API, perform find-replace on label names inside `{...}` in the PromQL string.

```python
def _rewrite_otel_labels(query: str, aliases: dict[str, str]) -> str:
    """Replace label aliases in PromQL.

    container="enforce" → k8s_container_name="enforce"
    Leaves content outside {} (metric names, function names) unchanged.
    """
    ...
```

**Caller-side usage**:

```promql
avg(container_cpu_usage{container="enforce"})
```

MCP automatically rewrites to the actual query sent to OpsRamp:

```promql
avg(container_cpu_usage{k8s_container_name="enforce"})
```

**Opt-out**: Set `rewrite_otel_labels=False` to pass through as-is (for debugging / advanced users).

### 3.5 OTel Span Category Auto-Tagging

Applies to: `tracing_operation_insights`, `tracing_batch_insights`

For each operation in the returned results, MCP auto-tags an `otel_cat` column based on OTel auto-instrumentation span naming conventions:

```python
OTEL_SPAN_CATEGORIES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^DB\b|^GET_CONNECTION$"),                                        "db/sql"),
    (re.compile(r"^(GET|SET|DEL|EVALSHA|EVAL|MGET|HGET|HSET|HDEL|EXPIRE|TTL|PING)$"), "db/redis"),
    (re.compile(r"^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS) /"),                   "http"),
    (re.compile(r"^/[a-z].*\."),                                                   "grpc"),
    (re.compile(r"\.\w+:\w+"),                                                     "internal"),
]
OTEL_SPAN_DEFAULT_CATEGORY = "other"
```

**Matching rules**:
- Matched in list order; the first hit is returned
- No match → `"other"`
- These patterns are all derived from OTel auto-instrumentation standard naming, not specific to any particular service:
  - `database/sql` instrumentation → `DB SELECT`, `DB INSERT`, etc.
  - `go-redis` instrumentation → uppercase single-word commands `GET`, `SET`, `DEL`
  - `net/http` instrumentation → `METHOD /path`
  - gRPC instrumentation → `/package.Service/Method`
  - Go code span → `file.go:FuncName`

**Consumer-side override**: The agent/skill can further reclassify on top of the `otel_cat` returned by MCP using its own patterns (e.g., `internal` entries matching `eventbus.go:*` → `messaging/kafka`). MCP provides an ~80% accurate baseline.

### 3.6 Tracing Parameter Structuring + Automatic ns Conversion

Parameter redesign for `opsramp_tracing_operation_insights`:

```python
# === New structured parameters ===
service: str = ""              # "enforce" — if specified, auto-constructs query DSL
kind: str = ""                 # "server" | "client" | "internal" — optional
operation: str = ""            # exact operation name — optional
time_range: str = ""           # "24h" — mutually exclusive with start/end

# === Retained original parameters (backward compatible) ===
query: str = ""                # raw DSL — if specified, takes priority
start: str = ""                # epoch seconds (MCP auto-appends ×10^9) or raw ns
end: str = ""
```

**Automatic DSL construction logic**:

```python
def _build_tracing_query(service: str, kind: str = "", operation: str = "") -> str:
    """Construct OpsRamp tracing query DSL.

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
```

**Automatic time conversion logic**:

```python
def _ensure_nanoseconds(value: str) -> str:
    """Automatically convert epoch seconds to nanoseconds.

    Heuristic: if the number has ≤ 12 digits → treat as seconds, × 10^9.
    If ≥ 16 digits → treat as already nanoseconds.
    """
    v = value.strip()
    if not v.isdigit():
        return v
    if len(v) <= 12:  # epoch seconds
        return str(int(v) * 1_000_000_000)
    return v  # already nanoseconds
```

### 3.7 metricsql_batch_query New Tool

```python
@mcp.tool()
async def opsramp_metricsql_batch_query(
    queries: list[dict[str, str]],
    ctx: Context,
    platform: str = "",
    tenant: str = "",
    tenant_id: str = "",
    time_range: str = "",
    start: str = "0",
    end: str = "0",
    step: int = 0,                    # 0 = auto from range
    output_format: str = "csv",       # "csv" | "text" | "json"
    rewrite_otel_labels: bool = True,
    additional_headers: dict[str, str] | None = None,
) -> str:
    """
    Execute multiple PromQL queries in one call with parallel execution.
    Returns compact results keyed by query ID.
    
    Args:
        queries: List of {"id": "unique-name", "query": "promql..."}.
                 Label aliases (container, pod, namespace) are auto-rewritten
                 to OTel names (k8s_container_name, etc.) unless disabled.
        time_range: Human time range like "24h", "1h", "7d" — auto-computes
                    start/end/step. Ignored if start/end are explicitly set.
        output_format: "csv" (default, most token-efficient), "text" (human-readable),
                       or "json" (full raw response, backward-compatible).
    """
```

**Internal execution**:
- Each query is executed concurrently via `client.query_metricsql_v3_smart()` using `asyncio.gather`
- Concurrency is limited by a semaphore (default 4)
- Each query succeeds or fails independently without affecting others

**Failure handling**: In csv/text mode, failed queries are marked as errors in the output:

```csv
# metricsql_batch: 7/8 ok 1 fail | tenant=mira-east | range=24h step=300
id,labels,pts,avg,min,max,first,last
enforce-cpu,container=enforce,288,0.1520,0.0900,0.4230,0.1400,0.1500
bad-query,ERROR: 400 "parse error at char 25",,,,,
```

### 3.8 tracing_batch_insights New Tool

```python
@mcp.tool()
async def opsramp_tracing_batch_insights(
    queries: list[dict[str, Any]],
    ctx: Context,
    platform: str = "",
    tenant: str = "",
    tenant_id: str = "",
    time_range: str = "",
    start: str = "",
    end: str = "",
    output_format: str = "csv",
    additional_headers: dict[str, str] | None = None,
) -> str:
    """
    Execute multiple tracing operation_insights queries in one call.
    
    Args:
        queries: List of structured query dicts, each with:
            - "id": unique name (e.g., "enforce-server")
            - "service": service name (e.g., "enforce")
            - "kind": optional, "server" | "client" | "internal"
            - "operation": optional, exact operation name filter
            - "sort_by": optional, default "throughput"
            - "limit": optional, default 10
        time_range: "24h" etc. Auto-converts to nanoseconds internally.
        output_format: "csv" | "text" | "json"
    
    OpsRamp platform details handled automatically:
        - app IN ("default") is always injected
        - Time is always converted to epoch nanoseconds
        - Each operation is auto-tagged with otel_cat (db/sql, db/redis, http, grpc, internal)
    """
```

**Internal execution**:
- For each query item, construct the DSL and call `client.get_tracing_operation_insights()`
- Execute concurrently via `asyncio.gather`
- Auto-match `otel_cat` for each operation in the results

### 3.9 dashboard_find Merged Tool

Merges `dashboard_list_collections` + `dashboard_list_dashboards` into a single search tool:

```python
@mcp.tool()
async def opsramp_dashboard_find(
    search: str,
    ctx: Context,
    platform: str = "",
    tenant: str = "",
    additional_headers: dict[str, str] | None = None,
) -> str:
    """
    Search for dashboards by name (fuzzy match across all collections).
    Returns matching collection_id + dashboard_id pairs ready for use
    with dashboard_run_tiles_smart or dashboard_get_variables.
    
    Args:
        search: Dashboard name to search for (case-insensitive substring match).
                E.g., "enforce", "AuthZ Overview", "CPU"
    """
```

**Internal execution**:
1. `list_dashboard_collections_v3()` → retrieve all collections
2. Concurrently call `list_collection_dashboards_v3()` for each collection
3. Perform case-insensitive substring match on dashboard titles
4. Return a compact list

**Output example**:

```csv
# dashboard_find: 3 matches for "enforce"
collection_id,collection_title,dashboard_id,dashboard_title
5,enforce,108,Enforce Overview
5,enforce,109,Enforce Latency
5,enforce,110,Enforce Dependencies
```

The original `dashboard_list_collections` and `dashboard_list_dashboards` are retained but **no longer registered as MCP tools** (client.py methods kept for script usage).
`dashboard_get` is also no longer registered as a tool (dashboard_run_tiles_smart already contains complete data; use dashboard_get_variables if only definitions are needed).

### 3.10 Final Tool Inventory

| # | Tool Name | Change | Default output_format |
|---|-----------|--------|:---:|
| 1 | `opsramp_metricsql_query_smart` | Enhanced: +output_format, +time_range, +rewrite_otel_labels | csv |
| 2 | `opsramp_metricsql_batch_query` | **New** | csv |
| 3 | `opsramp_tracing_operation_insights` | Enhanced: +output_format, +time_range, +structured params, +otel_cat | csv |
| 4 | `opsramp_tracing_batch_insights` | **New** | csv |
| 5 | `opsramp_dashboard_run_tiles_smart` | Enhanced: +output_format, +time_range | csv |
| 6 | `opsramp_dashboard_find` | **New (merged)** | csv |
| 7 | `opsramp_dashboard_get_variables` | Unchanged | json |
| 8 | `opsramp_metricsql_labels` | Unchanged | json |
| 9 | `opsramp_metricsql_label_values` | Unchanged | json |
| 10 | `opsramp_v2_list_metrics` | Unchanged | json |
| 11 | `opsramp_v2_get_metric` | Unchanged | json |

**From 15 → 11 tools** (broader functionality coverage, less context usage)

**Removal details**:
- ❌ `opsramp_metricsql_query` — superseded by query_smart
- ❌ `opsramp_metricsql_push_data` — write operation, out of scope
- ❌ `opsramp_v2_list_reporting_apps` — zero usage
- ❌ `opsramp_tracing_top_operations` — superseded by operation_insights
- ❌ `opsramp_dashboard_list_collections` — superseded by dashboard_find
- ❌ `opsramp_dashboard_list_dashboards` — superseded by dashboard_find
- ❌ `opsramp_dashboard_get` — covered by run_tiles_smart / get_variables

---

> **Group 2 (authz-svc consumer-side changes)** has been extracted to MCP memory.
> Query memory entities: `MCP-v2-Group2-Overview`, `MCP-v2-Group2-SKILL-Slimming`,
> `MCP-v2-Group2-ParseScript-Deprecation`, `MCP-v2-Group2-MetricPatterns-Adjustment`,
> `MCP-v2-Group2-AgentMD-Rewrite`.

---

## 4. Implementation Phases

### Phase 1 — Foundational Enhancements (opsramp-mcp)

**Scope**: Minimal changes to existing tools, immediately effective

1. Add `formatters.py` (csv/text/json three-tier output functions)
2. Add `output_format`, `time_range` parameters to `metricsql_query_smart`
3. Add `output_format`, `time_range`, structured parameters, and `otel_cat` to `tracing_operation_insights`
4. Add `output_format`, `time_range` to `dashboard_run_tiles_smart`
5. Remove 4 unused tools (unregister `@mcp.tool()` decorators)

**Backward Compatibility**: All new parameters have defaults; omitting them = original behavior (output_format defaulting to csv is a breaking change, discussed below).

### Phase 2 — New Batch Tools + OTel Knowledge (opsramp-mcp)

**Scope**: Adding new capabilities

1. `metricsql_batch_query` new tool
2. `tracing_batch_insights` new tool
3. `dashboard_find` merged tool
4. OTel label alias rewriting (`rewrite_otel_labels`)
5. OTel span category tagging (`otel_cat`)

### Phase 3 — Consumer-Side Refactoring (authz-svc)

**Scope**: Leverage MCP v2 capabilities to simplify the consumer side

1. Slim down SKILL.md (remove knowledge migrated into MCP)
2. Simplify metric-patterns.json (use standard label aliases)
3. Refactor daily-health-report.agent.md (batch calls + remove tmp protocol)
4. Mark parse_opsramp_response.py as deprecated

### Phase Dependencies

```
Phase 1 ──→ Phase 2 ──→ Phase 3 (see MCP memory: MCP-v2-Group2-*)
                          ↑
                     (Phase 2 batch tools required before agent refactoring)
```

Phase 1 and Phase 2 can be completed in the same PR (both in the opsramp-mcp repo).
Phase 3 is a separate PR (in the authz-svc repo); see MCP memory Group 2 entities for details.

---

## 5. Backward Compatibility

### output_format Default Value

**Option A**: Default to `csv` (breaking change for JSON consumers)
**Option B**: Default to `json` (backward compatible, but agent must explicitly pass `output_format="csv"` every time)

**Recommendation**: Option A. Rationale:
- The only current consumer is the daily-health-report agent, which doesn't directly use JSON anyway (it runs a parser)
- The new default directly satisfies consumer needs
- `json` is still available via explicit parameter

### rewrite_otel_labels Default Value

Defaults to `True`. Existing PromQL using `k8s_container_name` is unaffected (aliases only replace short names; long names are passed through as-is).

### Tracing Structured Parameters

The raw `query` DSL is still accepted. The new `service`/`kind`/`operation` parameters are optional — if `query` string is also provided, `query` takes priority.
