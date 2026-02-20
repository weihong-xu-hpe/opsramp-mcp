"""Run a small suite of MetricsQL (PromQL-compatible) queries for validation."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsramp_mcp.client import OpsRampAPIError, OpsRampClient
from opsramp_mcp.config import load_config


def _series_count(result: dict) -> int:
    data = result.get("data", {}) if isinstance(result, dict) else {}
    series = data.get("result", []) if isinstance(data, dict) else []
    return len(series) if isinstance(series, list) else 0


async def _discover_queries(client: OpsRampClient, tenant_id: str) -> list[str]:
    base_queries = [
        "system_cpu_utilization",
        "system_cpu_utilization{instance=~\".+\"}",
        "{__name__=~\"system_cpu_utilization|system_memory_util_percent\"}",
        "topk(5, system_cpu_utilization)",
    ]

    try:
        discovered = await client.list_metricsql_label_values_v3(
            tenant_id=tenant_id,
            label_name="__name__",
            start="0",
            end="0",
        )
        discovered_names = discovered.get("data", []) if isinstance(discovered, dict) else []
        if isinstance(discovered_names, list):
            for metric_name in discovered_names[:3]:
                if isinstance(metric_name, str) and metric_name:
                    base_queries.append(metric_name)
    except OpsRampAPIError:
        pass

    return base_queries


async def _run_query(client: OpsRampClient, tenant_id: str, query: str) -> dict[str, object]:
    row: dict[str, object] = {"query": query}
    try:
        resp = await client.query_metricsql_v3(
            tenant_id=tenant_id,
            query=query,
            start="0",
            end="0",
            step=300,
        )
        row["status"] = resp.get("status", "unknown") if isinstance(resp, dict) else "unknown"
        row["series_count"] = _series_count(resp) if isinstance(resp, dict) else 0
    except OpsRampAPIError as exc:
        row["status"] = "failed"
        row["error_code"] = exc.status_code
        row["error"] = exc.details
    return row


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpsRamp PromQL smoke test (TOML-only)")
    parser.add_argument("--config", default="", help="Path to TOML config file")
    parser.add_argument("--platform", default="", help="Platform alias in TOML")
    parser.add_argument("--tenant", default="", help="Tenant alias in TOML")
    parser.add_argument("--tenant-id", default="", help="Explicit tenant/client ID override")
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config or None)
    platform_name = args.platform.strip()
    tenant_name = args.tenant.strip()
    tenant_id_override = args.tenant_id.strip()

    platform = cfg.get_platform(platform_name or None)
    tenant_id = tenant_id_override or platform.get_tenant(tenant_name or None).id

    client = OpsRampClient(platform)
    token = await client.get_access_token(force_refresh=True)

    queries = await _discover_queries(client, tenant_id)

    results: list[dict[str, object]] = []

    report: dict[str, object] = {
        "platform": platform.name,
        "api_base_url": platform.api_base_url,
        "tenant_id": tenant_id,
        "auth_ok": bool(token),
        "results": results,
    }

    for q in queries:
        results.append(await _run_query(client, tenant_id, q))

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
