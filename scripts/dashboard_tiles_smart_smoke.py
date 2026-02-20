from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsramp_mcp.client import OpsRampAPIError, OpsRampClient
from opsramp_mcp.config import load_config
from opsramp_mcp.server import _build_runtime_variables_map, _extract_dashboard_tile_queries, _render_query_template


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Smoke test: dashboard tile smart executor flow")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--platform", default="")
    p.add_argument("--tenant", default="")
    p.add_argument("--tenant-id", default="")
    p.add_argument("--keyword", action="append", default=[], help="Dashboard title keyword; repeatable")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--step", type=int, default=300)
    p.add_argument("--limit-tiles", type=int, default=3)
    return p.parse_args()


def match_title(title: str, keywords: list[str]) -> bool:
    text = (title or "").lower()
    return all(k.lower() in text for k in keywords)


def _dashboard_match_from_list(cid: str, dashboards: Any, keywords: list[str]) -> dict[str, str] | None:
    if not isinstance(dashboards, list):
        return None
    for d in dashboards:
        if isinstance(d, dict) and match_title(str(d.get("title", "")), keywords):
            return {
                "collection_id": cid,
                "dashboard_id": str(d.get("id", "")),
                "dashboard_title": str(d.get("title", "")),
            }
    return None


def _find_from_embedded(rows: list[dict[str, Any]], keywords: list[str]) -> dict[str, str] | None:
    for c in rows:
        cid = str(c.get("id", ""))
        target = _dashboard_match_from_list(cid, c.get("dashboards", []), keywords)
        if target:
            return target
    return None


async def _find_by_collection_calls(
    client: OpsRampClient,
    rows: list[dict[str, Any]],
    headers: dict[str, str] | None,
    keywords: list[str],
) -> dict[str, str] | None:
    for c in rows:
        cid = str(c.get("id", ""))
        if not cid:
            continue
        try:
            data = await client.list_collection_dashboards_v3(cid, additional_headers=headers)
        except OpsRampAPIError:
            continue
        target = _dashboard_match_from_list(cid, data.get("dashboards", []) if isinstance(data, dict) else [], keywords)
        if target:
            return target
    return None


async def find_dashboard(client: OpsRampClient, headers: dict[str, str] | None, keywords: list[str]) -> dict[str, str] | None:
    collections = await client.list_dashboard_collections_v3(additional_headers=headers)
    rows = [r for r in collections if isinstance(r, dict)] if isinstance(collections, list) else []

    embedded = _find_from_embedded(rows, keywords)
    if embedded:
        return embedded

    return await _find_by_collection_calls(client, rows, headers, keywords)


async def _run_tile_tests(
    client: OpsRampClient,
    tenant_id: str,
    headers: dict[str, str] | None,
    test_rows: list[dict[str, str]],
    variables: dict[str, str],
    *,
    start: int,
    end: int,
    step: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for row in test_rows:
        rendered, missing = _render_query_template(row.get("query", ""), variables)
        item: dict[str, Any] = {
            "tile_id": row.get("tile_id", ""),
            "tile_title": row.get("tile_title", ""),
            "missing_variables": missing,
        }
        try:
            data = await client.query_metricsql_v3_smart(
                tenant_id=tenant_id,
                query=rendered,
                start=str(start),
                end=str(end),
                step=max(1, step),
                auto_downsample=True,
                enable_sharding=True,
                max_points_per_slice=8000,
                additional_headers=headers,
            )
            series = data.get("data", {}).get("result", []) if isinstance(data, dict) else []
            item["status"] = "success"
            item["series_count"] = len(series) if isinstance(series, list) else 0
            item["meta"] = data.get("meta") if isinstance(data, dict) else None
        except OpsRampAPIError as exc:
            item["status"] = "failed"
            item["error_code"] = exc.status_code
            item["error"] = exc.details
        results.append(item)
    return results


async def main() -> None:
    a = parse_args()
    cfg = load_config(a.config)
    platform = cfg.get_platform(a.platform or None)
    tenant_cfg = platform.get_tenant(a.tenant or None)
    tenant_id = a.tenant_id or tenant_cfg.id
    headers = tenant_cfg.additional_headers or None

    client = OpsRampClient(platform)
    keywords = a.keyword or ["enforce", "service pod insights dashboard"]
    target = await find_dashboard(client, headers, keywords)
    if not target:
        print(json.dumps({"ok": False, "error": "dashboard not found", "keywords": keywords}, ensure_ascii=False, indent=2))
        return

    dashboard = await client.get_dashboard_v3(target["collection_id"], target["dashboard_id"], additional_headers=headers)
    if not isinstance(dashboard, dict):
        print(json.dumps({"ok": False, "error": "unexpected dashboard payload"}, ensure_ascii=False, indent=2))
        return

    end = int(time.time())
    start = end - max(1, a.days) * 24 * 3600

    variables = _build_runtime_variables_map(
        dashboard,
        variables_map=None,
        start=str(start),
        end=str(end),
        step=max(1, a.step),
    )
    queries = _extract_dashboard_tile_queries(dashboard)
    test_rows = queries[: max(0, a.limit_tiles)] if a.limit_tiles > 0 else queries
    results = await _run_tile_tests(
        client,
        tenant_id,
        headers,
        test_rows,
        variables,
        start=start,
        end=end,
        step=a.step,
    )

    print(
        json.dumps(
            {
                "ok": True,
                "platform": platform.name,
                "tenant_id": tenant_id,
                "target": target,
                "tile_query_candidates": len(queries),
                "tested_tiles": len(results),
                "success_count": sum(1 for r in results if r.get("status") == "success"),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
