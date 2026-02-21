"""OpsRamp MCP server (FastMCP)."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from . import __version__
from .client import OpsRampAPIError, OpsRampClient
from .config import AppConfig, PlatformConfig, load_config


@dataclass
class AppContext:
    config: AppConfig
    clients: dict[str, OpsRampClient] = field(default_factory=dict)


_RUNTIME_STATE: dict[str, str | None] = {"config_path": None}


def set_config_path(config_path: str | None) -> None:
    _RUNTIME_STATE["config_path"] = config_path


@asynccontextmanager
async def app_lifespan(_server: FastMCP) -> AsyncIterator[AppContext]:
    config = load_config(_RUNTIME_STATE["config_path"])
    print(
        f"OpsRamp MCP v{__version__} loaded config: {config.config_path}",
        file=sys.stderr,
    )
    app_ctx = AppContext(config=config)
    try:
        yield app_ctx
    finally:
        for client in app_ctx.clients.values():
            await client.aclose()


mcp = FastMCP(
    "opsramp-mcp",
    instructions=(
        "OpsRamp MCP server supporting v2/v3 APIs. "
        "Primary tools include Dashboard retrieval and MetricsQL query operations."
    ),
    lifespan=app_lifespan,
)


def _client(ctx: Context[ServerSession, AppContext]) -> OpsRampClient:
    return _client_for_platform(ctx, platform="")


def _platform_config(ctx: Context[ServerSession, AppContext], platform: str = "") -> PlatformConfig:
    app_ctx = ctx.request_context.lifespan_context
    return app_ctx.config.get_platform(platform or None)


def _client_for_platform(ctx: Context[ServerSession, AppContext], platform: str = "") -> OpsRampClient:
    app_ctx = ctx.request_context.lifespan_context
    platform_cfg = app_ctx.config.get_platform(platform or None)
    if platform_cfg.name not in app_ctx.clients:
        app_ctx.clients[platform_cfg.name] = OpsRampClient(platform_cfg)
    return app_ctx.clients[platform_cfg.name]


def _resolve_tenant_id(
    platform_cfg: PlatformConfig,
    tenant: str = "",
    tenant_id: str = "",
) -> str:
    explicit_tenant_id = tenant_id.strip()
    if explicit_tenant_id:
        return explicit_tenant_id
    return platform_cfg.get_tenant(tenant or None).id


def _resolve_headers(
    platform_cfg: PlatformConfig,
    tenant: str = "",
    additional_headers: dict[str, str] | None = None,
) -> dict[str, str] | None:
    merged: dict[str, str] = {}

    tenant_name = (tenant or platform_cfg.default_tenant or "").strip()
    if tenant_name:
        merged.update(platform_cfg.get_tenant(tenant_name).additional_headers)

    if additional_headers:
        merged.update(additional_headers)

    return merged or None


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


_VAR_PATTERN = re.compile(r"\$([A-Za-z_]\w*)|\$\{([^}]+)\}")


def _parse_epoch_seconds(value: str) -> int | None:
    v = value.strip()
    if not v or not v.isdigit():
        return None
    return int(v)


def _duration_seconds(start: str, end: str) -> int | None:
    s = _parse_epoch_seconds(start)
    e = _parse_epoch_seconds(end)
    if s is None or e is None or e <= s:
        return None
    return e - s


def _format_range(duration_seconds: int | None) -> str:
    if duration_seconds is None or duration_seconds <= 0:
        return "0s"
    if duration_seconds % 86400 == 0:
        return f"{duration_seconds // 86400}d"
    if duration_seconds % 3600 == 0:
        return f"{duration_seconds // 3600}h"
    if duration_seconds % 60 == 0:
        return f"{duration_seconds // 60}m"
    return f"{duration_seconds}s"


def _estimate_interval_seconds(duration_seconds: int | None, fallback_step: int, target_points: int = 800) -> int:
    if duration_seconds is None or duration_seconds <= 0:
        return max(1, fallback_step)
    estimated = max(1, duration_seconds // max(1, target_points))
    return max(1, fallback_step, estimated)


def _normalize_variables_map(variables_map: dict[str, str] | None) -> dict[str, str]:
    if not variables_map:
        return {}
    normalized: dict[str, str] = {}
    for k, v in variables_map.items():
        name = str(k).strip()
        if not name:
            continue
        if name.startswith("${") and name.endswith("}"):
            name = name[2:-1]
        if name.startswith("$"):
            name = name[1:]
        normalized[name] = str(v)
    return normalized


def _dashboard_default_variables(dashboard: dict[str, Any]) -> dict[str, str]:
    defaults: dict[str, str] = {}
    raw_variables = dashboard.get("variables", []) if isinstance(dashboard, dict) else []
    if not isinstance(raw_variables, list):
        return defaults
    for item in raw_variables:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        defaults[name] = str(item.get("defaultValue", ""))
    return defaults


def _build_runtime_variables_map(
    dashboard: dict[str, Any],
    variables_map: dict[str, str] | None,
    *,
    start: str,
    end: str,
    step: int,
) -> dict[str, str]:
    defaults = _dashboard_default_variables(dashboard)
    resolved = dict(defaults)
    resolved.update(_normalize_variables_map(variables_map))

    duration = _duration_seconds(start, end)
    interval = _estimate_interval_seconds(duration_seconds=duration, fallback_step=step)

    resolved.setdefault("__range", _format_range(duration))
    resolved.setdefault("__range_s", str(duration if duration is not None else 0))
    resolved.setdefault("__interval", f"{interval}s")
    resolved.setdefault("__interval_ms", str(interval * 1000))
    return resolved


def _render_query_template(query: str, variables_map: dict[str, str]) -> tuple[str, list[str]]:
    missing: set[str] = set()

    def repl(match: re.Match[str]) -> str:
        key = match.group(1) or match.group(2) or ""
        if key in variables_map:
            return variables_map[key]
        missing.add(key)
        return match.group(0)

    rendered = _VAR_PATTERN.sub(repl, query)
    return rendered, sorted(missing)


def _extract_dashboard_tile_queries(dashboard: dict[str, Any]) -> list[dict[str, str]]:
    tiles = dashboard.get("tiles", []) if isinstance(dashboard, dict) else []
    if not isinstance(tiles, list):
        return []

    candidates: list[dict[str, str]] = []
    for tile in tiles:
        candidates.extend(_extract_tile_queries_from_tile(tile))
    return _deduplicate_tile_queries(candidates)


def _extract_tile_queries_from_tile(tile: Any) -> list[dict[str, str]]:
    if not isinstance(tile, dict):
        return []

    title = str(tile.get("title", ""))
    tile_id = str(tile.get("id", ""))
    config = tile.get("config", {})
    if not isinstance(config, dict):
        return []

    out: list[dict[str, str]] = []
    for direct_key in ["metricsql", "metricsQl", "query", "queryString", "promql", "metricql"]:
        _append_direct_query(out, tile_id, title, config, direct_key)

    for key, value in config.items():
        if isinstance(value, dict):
            _append_query_from_dict(out, tile_id, title, key, value)
        elif isinstance(value, list):
            _append_query_from_list(out, tile_id, title, key, value)
    return out


def _append_direct_query(
    out: list[dict[str, str]],
    tile_id: str,
    title: str,
    config: dict[str, Any],
    query_key: str,
) -> None:
    value = config.get(query_key)
    if isinstance(value, str) and value.strip():
        out.append({"tile_id": tile_id, "tile_title": title, "query": value.strip(), "source": query_key})


def _append_query_from_dict(
    out: list[dict[str, str]],
    tile_id: str,
    title: str,
    parent_key: str,
    value: dict[str, Any],
) -> None:
    for key, nested in value.items():
        if isinstance(nested, str) and key.lower() in {"query", "querystring", "promql", "metricsql"}:
            out.append(
                {
                    "tile_id": tile_id,
                    "tile_title": title,
                    "query": nested.strip(),
                    "source": f"{parent_key}.{key}",
                }
            )


def _append_query_from_list(
    out: list[dict[str, str]],
    tile_id: str,
    title: str,
    parent_key: str,
    items: list[Any],
) -> None:
    for item in items:
        if not isinstance(item, dict):
            continue
        _append_query_from_dict(out, tile_id, title, f"{parent_key}[]", item)


def _deduplicate_tile_queries(candidates: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[dict[str, str]] = []
    for row in candidates:
        key = (row.get("tile_id", ""), row.get("tile_title", ""), row.get("query", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_execution_options(options: dict[str, Any] | None) -> dict[str, Any]:
    raw = options or {}
    return {
        "auto_downsample": bool(raw.get("auto_downsample", True)),
        "enable_sharding": bool(raw.get("enable_sharding", True)),
        "max_points_per_slice": max(100, _as_int(raw.get("max_points_per_slice", 8000), 8000)),
        "concurrency": max(1, min(20, _as_int(raw.get("concurrency", 4), 4))),
        "limit_tiles": max(0, _as_int(raw.get("limit_tiles", 0), 0)),
    }


def _resolve_output_options(options: dict[str, Any] | None) -> dict[str, Any]:
    raw = options or {}
    return {
        "include_rendered_query": bool(raw.get("include_rendered_query", True)),
        "include_dashboard": bool(raw.get("include_dashboard", False)),
    }


# @mcp.tool()
async def opsramp_auth_test(ctx: Context[ServerSession, AppContext]) -> str:
    """Validate OAuth flow and return token metadata (masked)."""
    return await opsramp_auth_test_on_platform(ctx=ctx, platform="")


# @mcp.tool()
async def opsramp_auth_test_on_platform(
    ctx: Context[ServerSession, AppContext],
    platform: str = "",
) -> str:
    """Validate OAuth flow for a selected platform and return token metadata (masked)."""
    platform_cfg = _platform_config(ctx, platform)
    client = _client_for_platform(ctx, platform_cfg.name)
    token = await client.get_access_token(force_refresh=True)
    return _json(
        {
            "ok": True,
            "version": __version__,
            "config_path": ctx.request_context.lifespan_context.config.config_path,
            "config_hash": ctx.request_context.lifespan_context.config.config_hash,
            "platform": platform_cfg.name,
            "api_base_url": platform_cfg.api_base_url,
            "default_tenant": platform_cfg.default_tenant,
            "tenants": sorted(platform_cfg.tenants.keys()),
            "token_preview": f"{token[:8]}...",
            "token_expires_at_epoch": client.token_expires_at,
        }
    )


# @mcp.tool()
async def opsramp_list_platforms(ctx: Context[ServerSession, AppContext]) -> str:
    """List configured platforms and tenants from TOML config."""
    cfg = ctx.request_context.lifespan_context.config
    platforms = []
    for platform_name, platform_cfg in cfg.platforms.items():
        platforms.append(
            {
                "name": platform_name,
                "api_base_url": platform_cfg.api_base_url,
                "default_tenant": platform_cfg.default_tenant,
                "tenants": [
                    {
                        "name": tenant_name,
                        "id": tenant_cfg.id,
                        "additional_headers": tenant_cfg.additional_headers,
                    }
                    for tenant_name, tenant_cfg in platform_cfg.tenants.items()
                ],
            }
        )
    return _json(
        {
            "version": __version__,
            "config_path": cfg.config_path,
            "config_hash": cfg.config_hash,
            "default_platform": cfg.default_platform,
            "platforms": platforms,
        }
    )


# @mcp.tool()
async def opsramp_server_info(ctx: Context[ServerSession, AppContext]) -> str:
    """Return server debug info including version and active config path."""
    cfg = ctx.request_context.lifespan_context.config
    return _json(
        {
            "ok": True,
            "server": "opsramp-mcp",
            "version": __version__,
            "config_path": cfg.config_path,
            "config_hash": cfg.config_hash,
            "default_platform": cfg.default_platform,
            "platform_count": len(cfg.platforms),
            "platforms": sorted(cfg.platforms.keys()),
        }
    )


@mcp.tool()
async def opsramp_dashboard_list_collections(
    ctx: Context[ServerSession, AppContext],
    platform: str = "",
    tenant: str = "",
    limit: int | None = None,
    offset: int | None = None,
    additional_headers: dict[str, str] | None = None,
) -> str:
    """
    List all dashboard collections (folders) in OpsRamp. 
    Use this FIRST when the user wants to find a dashboard but doesn't know the collection_id.
    """
    platform_cfg = _platform_config(ctx, platform)
    headers = _resolve_headers(platform_cfg, tenant=tenant, additional_headers=additional_headers)
    data = await _client_for_platform(ctx, platform_cfg.name).list_dashboard_collections_v3(
        limit=limit,
        offset=offset,
        additional_headers=headers
    )
    return _json(data)


@mcp.tool()
async def opsramp_dashboard_list_dashboards(
    collection_id: str,
    ctx: Context[ServerSession, AppContext],
    platform: str = "",
    tenant: str = "",
    limit: int | None = None,
    offset: int | None = None,
    additional_headers: dict[str, str] | None = None,
) -> str:
    """
    List all dashboards within a specific collection.
    Use this after getting the collection_id to find the specific dashboard_id.
    """
    platform_cfg = _platform_config(ctx, platform)
    headers = _resolve_headers(platform_cfg, tenant=tenant, additional_headers=additional_headers)
    data = await _client_for_platform(ctx, platform_cfg.name).list_collection_dashboards_v3(
        collection_id=collection_id,
        limit=limit,
        offset=offset,
        additional_headers=headers,
    )
    return _json(data)


@mcp.tool()
async def opsramp_dashboard_get(
    collection_id: str,
    dashboard_id: str,
    ctx: Context[ServerSession, AppContext],
    platform: str = "",
    tenant: str = "",
    additional_headers: dict[str, str] | None = None,
) -> str:
    """
    Get the full JSON configuration of a specific dashboard.
    Use this to understand what charts/tiles are in the dashboard and their raw queries.
    """
    platform_cfg = _platform_config(ctx, platform)
    headers = _resolve_headers(platform_cfg, tenant=tenant, additional_headers=additional_headers)
    data = await _client_for_platform(ctx, platform_cfg.name).get_dashboard_v3(
        collection_id=collection_id,
        dashboard_id=dashboard_id,
        additional_headers=headers,
    )
    return _json(data)


@mcp.tool()
async def opsramp_dashboard_get_variables(
    collection_id: str,
    dashboard_id: str,
    ctx: Context[ServerSession, AppContext],
    platform: str = "",
    tenant: str = "",
    variables_map: dict[str, str] | None = None,
    additional_headers: dict[str, str] | None = None,
) -> str:
    """
    Extract dashboard variables and their default values.
    Use this to see what parameters (like host, cluster) can be overridden before running the dashboard.
    """
    platform_cfg = _platform_config(ctx, platform)
    headers = _resolve_headers(platform_cfg, tenant=tenant, additional_headers=additional_headers)
    dashboard = await _client_for_platform(ctx, platform_cfg.name).get_dashboard_v3(
        collection_id=collection_id,
        dashboard_id=dashboard_id,
        additional_headers=headers,
    )

    raw_variables = dashboard.get("variables", []) if isinstance(dashboard, dict) else []
    variables: list[dict[str, Any]] = [v for v in raw_variables if isinstance(v, dict)]

    defaults: dict[str, str] = {}
    for v in variables:
        name = str(v.get("name", "")).strip()
        if not name:
            continue
        defaults[name] = str(v.get("defaultValue", ""))

    resolved: dict[str, str] = dict(defaults)
    if variables_map:
        resolved.update({str(k): str(val) for k, val in variables_map.items()})

    placeholders = [f"${name}" for name in defaults]

    return _json(
        {
            "collection_id": collection_id,
            "dashboard_id": dashboard_id,
            "dashboard_title": dashboard.get("title") if isinstance(dashboard, dict) else None,
            "variables": variables,
            "default_variables_map": defaults,
            "resolved_variables_map": resolved,
            "placeholders": placeholders,
        }
    )


@mcp.tool()
async def opsramp_dashboard_run_tiles_smart(
    collection_id: str,
    dashboard_id: str,
    ctx: Context[ServerSession, AppContext],
    platform: str = "",
    tenant: str = "",
    tenant_id: str = "",
    start: str = "0",
    end: str = "0",
    step: int = 60,
    variables_map: dict[str, str] | None = None,
    execution_options: dict[str, Any] | None = None,
    output_options: dict[str, Any] | None = None,
    additional_headers: dict[str, str] | None = None,
) -> str:
    """
    Execute all queries in a specific dashboard and return the data for all charts/tiles.
    This is the primary tool to get actual monitoring data from a dashboard.
    
    Args:
        start: Start time in epoch seconds (e.g., "1708473600") or relative time (e.g., "now-1h"). Default is "0".
        end: End time in epoch seconds or relative time. Default is "0".
        step: Resolution step in seconds (e.g., 60 for 1 minute).
        variables_map: Optional dictionary to override dashboard template variables (e.g., {"host": "server-1"}).
    """
    exec_options = _resolve_execution_options(execution_options)
    out_options = _resolve_output_options(output_options)

    platform_cfg = _platform_config(ctx, platform)
    resolved_tenant_id = _resolve_tenant_id(platform_cfg, tenant=tenant, tenant_id=tenant_id)
    headers = _resolve_headers(platform_cfg, tenant=tenant, additional_headers=additional_headers)
    client = _client_for_platform(ctx, platform_cfg.name)

    dashboard = await client.get_dashboard_v3(
        collection_id=collection_id,
        dashboard_id=dashboard_id,
        additional_headers=headers,
    )
    if not isinstance(dashboard, dict):
        return _json({"status": "failed", "error": "Unexpected dashboard payload", "payload": dashboard})

    variables = _build_runtime_variables_map(
        dashboard,
        variables_map,
        start=start,
        end=end,
        step=step,
    )
    tile_queries = _extract_dashboard_tile_queries(dashboard)
    if exec_options["limit_tiles"] > 0:
        tile_queries = tile_queries[: exec_options["limit_tiles"]]

    semaphore = asyncio.Semaphore(exec_options["concurrency"])

    async def run_one(row: dict[str, str]) -> dict[str, Any]:
        async with semaphore:
            raw_query = row.get("query", "")
            rendered_query, missing_vars = _render_query_template(raw_query, variables)
            item: dict[str, Any] = {
                "tile_id": row.get("tile_id", ""),
                "tile_title": row.get("tile_title", ""),
                "query_source": row.get("source", ""),
                "raw_query": raw_query,
                "missing_variables": missing_vars,
            }
            if out_options["include_rendered_query"]:
                item["rendered_query"] = rendered_query

            try:
                data = await client.query_metricsql_v3_smart(
                    tenant_id=resolved_tenant_id,
                    query=rendered_query,
                    start=start,
                    end=end,
                    step=step,
                    auto_downsample=exec_options["auto_downsample"],
                    enable_sharding=exec_options["enable_sharding"],
                    max_points_per_slice=exec_options["max_points_per_slice"],
                    additional_headers=headers,
                )
                item["status"] = "success"
                item["data"] = data
            except OpsRampAPIError as exc:
                item["status"] = "failed"
                item["error_code"] = exc.status_code
                item["error"] = exc.details
            except (ValueError, TypeError, RuntimeError) as exc:
                item["status"] = "failed"
                item["error"] = str(exc)
            return item

    tile_results = await asyncio.gather(*(run_one(row) for row in tile_queries))
    success_count = sum(1 for r in tile_results if r.get("status") == "success")

    response: dict[str, Any] = {
        "status": "success",
        "platform": platform_cfg.name,
        "tenant_id": resolved_tenant_id,
        "collection_id": collection_id,
        "dashboard_id": dashboard_id,
        "dashboard_title": dashboard.get("title"),
        "tile_query_candidates": len(tile_queries),
        "tile_success_count": success_count,
        "tile_failed_count": len(tile_results) - success_count,
        "resolved_variables_map": variables,
        "execution": {
            "start": start,
            "end": end,
            "step": step,
            "auto_downsample": exec_options["auto_downsample"],
            "enable_sharding": exec_options["enable_sharding"],
            "max_points_per_slice": exec_options["max_points_per_slice"],
            "concurrency": exec_options["concurrency"],
            "limit_tiles": exec_options["limit_tiles"],
        },
        "tile_results": tile_results,
    }
    if out_options["include_dashboard"]:
        response["dashboard"] = dashboard
    return _json(response)


@mcp.tool()
async def opsramp_metricsql_query(
    query: str,
    ctx: Context[ServerSession, AppContext],
    platform: str = "",
    tenant: str = "",
    tenant_id: str = "",
    start: str = "0",
    end: str = "0",
    step: int = 60,
    additional_headers: dict[str, str] | None = None,
) -> str:
    """
    Run a standard PromQL/MetricsQL query.
    Note: Prefer using opsramp_metricsql_query_smart for better reliability on large time ranges.
    """
    platform_cfg = _platform_config(ctx, platform)
    resolved_tenant_id = _resolve_tenant_id(platform_cfg, tenant=tenant, tenant_id=tenant_id)
    headers = _resolve_headers(platform_cfg, tenant=tenant, additional_headers=additional_headers)
    data = await _client_for_platform(ctx, platform_cfg.name).query_metricsql_v3(
        tenant_id=resolved_tenant_id,
        query=query,
        start=start,
        end=end,
        step=step,
        additional_headers=headers,
    )
    return _json(data)


@mcp.tool()
async def opsramp_metricsql_query_smart(
    query: str,
    ctx: Context[ServerSession, AppContext],
    platform: str = "",
    tenant: str = "",
    tenant_id: str = "",
    start: str = "0",
    end: str = "0",
    step: int = 60,
    auto_downsample: bool = True,
    enable_sharding: bool = True,
    max_points_per_slice: int = 8000,
    additional_headers: dict[str, str] | None = None,
) -> str:
    """
    Execute a PromQL/MetricsQL query against OpsRamp with built-in safety mechanisms (auto-downsampling and sharding).
    ALWAYS PREFER THIS TOOL over the standard query tool for fetching time-series data, especially for large time ranges.
    
    Args:
        query: The PromQL/MetricsQL query string.
        start: Start time in epoch seconds (e.g., "1708473600").
        end: End time in epoch seconds (e.g., "1708560000").
        step: Resolution step in seconds (e.g., 60).
    """
    platform_cfg = _platform_config(ctx, platform)
    resolved_tenant_id = _resolve_tenant_id(platform_cfg, tenant=tenant, tenant_id=tenant_id)
    headers = _resolve_headers(platform_cfg, tenant=tenant, additional_headers=additional_headers)
    data = await _client_for_platform(ctx, platform_cfg.name).query_metricsql_v3_smart(
        tenant_id=resolved_tenant_id,
        query=query,
        start=start,
        end=end,
        step=step,
        auto_downsample=auto_downsample,
        enable_sharding=enable_sharding,
        max_points_per_slice=max_points_per_slice,
        additional_headers=headers,
    )
    return _json(data)


@mcp.tool()
async def opsramp_metricsql_labels(
    ctx: Context[ServerSession, AppContext],
    platform: str = "",
    tenant: str = "",
    tenant_id: str = "",
    start: str = "0",
    end: str = "0",
    query: str = "",
    limit: int | None = None,
    offset: int | None = None,
    additional_headers: dict[str, str] | None = None,
) -> str:
    """
    Get a list of all available label keys (e.g., 'instance', 'job', 'tenant') in the time-series database.
    Use this when you need to know what dimensions you can filter by.
    """
    platform_cfg = _platform_config(ctx, platform)
    resolved_tenant_id = _resolve_tenant_id(platform_cfg, tenant=tenant, tenant_id=tenant_id)
    headers = _resolve_headers(platform_cfg, tenant=tenant, additional_headers=additional_headers)
    data = await _client_for_platform(ctx, platform_cfg.name).list_metricsql_labels_v3(
        tenant_id=resolved_tenant_id,
        start=start,
        end=end,
        query=query or None,
        limit=limit,
        offset=offset,
        additional_headers=headers,
    )
    return _json(data)


@mcp.tool()
async def opsramp_metricsql_label_values(
    label_name: str,
    ctx: Context[ServerSession, AppContext],
    platform: str = "",
    tenant: str = "",
    tenant_id: str = "",
    start: str = "0",
    end: str = "0",
    query: str = "",
    limit: int | None = None,
    offset: int | None = None,
    additional_headers: dict[str, str] | None = None,
) -> str:
    """
    List all available values for a specific MetricsQL label (e.g., all available hostnames for the 'instance' label).
    """
    platform_cfg = _platform_config(ctx, platform)
    resolved_tenant_id = _resolve_tenant_id(platform_cfg, tenant=tenant, tenant_id=tenant_id)
    headers = _resolve_headers(platform_cfg, tenant=tenant, additional_headers=additional_headers)
    data = await _client_for_platform(ctx, platform_cfg.name).list_metricsql_label_values_v3(
        tenant_id=resolved_tenant_id,
        label_name=label_name,
        start=start,
        end=end,
        query=query or None,
        limit=limit,
        offset=offset,
        additional_headers=headers,
    )
    return _json(data)


@mcp.tool()
async def opsramp_metricsql_push_data(
    payload: list[dict[str, Any]],
    ctx: Context[ServerSession, AppContext],
    platform: str = "",
    tenant: str = "",
    client_id: str = "",
    additional_headers: dict[str, str] | None = None,
) -> str:
    """
    Push custom timeseries samples to the OpsRamp MetricsQL ingest API.
    Use this to write mock data or custom metrics into the system.
    """
    platform_cfg = _platform_config(ctx, platform)
    resolved_client_id = _resolve_tenant_id(platform_cfg, tenant=tenant, tenant_id=client_id)
    headers = _resolve_headers(platform_cfg, tenant=tenant, additional_headers=additional_headers)
    data = await _client_for_platform(ctx, platform_cfg.name).push_metrics_data_v3(
        client_id=resolved_client_id,
        payload=payload,
        additional_headers=headers,
    )
    return _json(data)


@mcp.tool()
async def opsramp_v2_list_metrics(
    ctx: Context[ServerSession, AppContext],
    platform: str = "",
    tenant: str = "",
    tenant_id: str = "",
    query_string: str = "",
    metric_name: str = "",
    group_name: str = "",
    display_name: str = "",
    scope: str = "",
    page_no: int = 1,
    page_size: int = 100,
    additional_headers: dict[str, str] | None = None,
) -> str:
    """
    Search for available metric definitions and metadata in OpsRamp.
    Use this when the user asks "what metrics are available for X" or wants to find the exact metric name to use in a query.
    Supports fuzzy searching via query_string, metric_name, or display_name.
    """
    platform_cfg = _platform_config(ctx, platform)
    resolved_tenant_id = _resolve_tenant_id(platform_cfg, tenant=tenant, tenant_id=tenant_id)
    headers = _resolve_headers(platform_cfg, tenant=tenant, additional_headers=additional_headers)
    data = await _client_for_platform(ctx, platform_cfg.name).list_metrics_v2(
        tenant_id=resolved_tenant_id,
        query_string=query_string or None,
        metric_name=metric_name or None,
        group_name=group_name or None,
        display_name=display_name or None,
        scope=scope or None,
        page_no=page_no,
        page_size=page_size,
        additional_headers=headers,
    )
    return _json(data)


@mcp.tool()
async def opsramp_v2_get_metric(
    metric_name: str,
    ctx: Context[ServerSession, AppContext],
    platform: str = "",
    tenant: str = "",
    tenant_id: str = "",
    additional_headers: dict[str, str] | None = None,
) -> str:
    """
    Get detailed metadata (unit, description, type) for a specific metric by its exact name.
    """
    platform_cfg = _platform_config(ctx, platform)
    resolved_tenant_id = _resolve_tenant_id(platform_cfg, tenant=tenant, tenant_id=tenant_id)
    headers = _resolve_headers(platform_cfg, tenant=tenant, additional_headers=additional_headers)
    data = await _client_for_platform(ctx, platform_cfg.name).get_metric_v2(
        tenant_id=resolved_tenant_id,
        metric_name=metric_name,
        additional_headers=headers,
    )
    return _json(data)


@mcp.tool()
async def opsramp_v2_list_reporting_apps(
    ctx: Context[ServerSession, AppContext],
    platform: str = "",
    tenant: str = "",
    tenant_id: str = "",
    page_no: int = 1,
    page_size: int = 100,
    query_string: str = "",
    category: str = "",
    additional_headers: dict[str, str] | None = None,
) -> str:
    """
    List available reporting apps (dashboard-related metadata) via the v2 endpoint.
    """
    platform_cfg = _platform_config(ctx, platform)
    resolved_tenant_id = _resolve_tenant_id(platform_cfg, tenant=tenant, tenant_id=tenant_id)
    headers = _resolve_headers(platform_cfg, tenant=tenant, additional_headers=additional_headers)
    data = await _client_for_platform(ctx, platform_cfg.name).list_reporting_apps_v2(
        tenant_id=resolved_tenant_id,
        page_no=page_no,
        page_size=page_size,
        query_string=query_string or None,
        category=category or None,
        additional_headers=headers,
    )
    return _json(data)


def main() -> None:
    parser = argparse.ArgumentParser(description="OpsRamp MCP server")
    parser.add_argument(
        "--config",
        default="",
        help="Path to TOML config file (optional).",
    )
    args, _ = parser.parse_known_args()

    requested_config = args.config.strip() or None
    set_config_path(requested_config)
    if requested_config:
        print(
            f"OpsRamp MCP v{__version__} requested config: {requested_config}",
            file=sys.stderr,
        )
    else:
        print(f"OpsRamp MCP v{__version__}", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
