"""Locate a dashboard by title keyword and try fetching tile-level query data.

TOML-only usage:
  python scripts/dashboard_data_test.py --config config.toml --platform qa_glcp --tenant hoku --keyword "enforce" --keyword "Service Pod Insights Dashboard"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsramp_mcp.client import OpsRampAPIError, OpsRampClient
from opsramp_mcp.config import load_config


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Dashboard data test")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--platform", default="")
    p.add_argument("--tenant", default="")
    p.add_argument("--tenant-id", default="")
    p.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Title keyword. Can be passed multiple times.",
    )
    p.add_argument("--limit-tiles", type=int, default=8)
    return p.parse_args()


def _match(title: str, keywords: list[str]) -> bool:
    t = (title or "").lower()
    return all(k.lower() in t for k in keywords)


def _extract_query_candidates(dashboard: dict[str, Any]) -> list[dict[str, str]]:
    """Best-effort extraction of tile queries from dashboard payload."""
    out: list[dict[str, str]] = []

    tiles = dashboard.get("tiles", []) if isinstance(dashboard, dict) else []
    if not isinstance(tiles, list):
        return out

    for tile in tiles:
        if not isinstance(tile, dict):
            continue
        title = str(tile.get("title", ""))
        config = tile.get("config", {})
        if not isinstance(config, dict):
            continue

        # Common guessed fields for metricsql query payloads.
        direct_fields = ["metricsql", "metricsQl", "query", "queryString", "promql", "metricql"]
        for f in direct_fields:
            v = config.get(f)
            if isinstance(v, str) and v.strip():
                out.append({"tile_title": title, "query": v.strip(), "source": f})

        # Check nested query arrays/objects.
        for k, v in config.items():
            if isinstance(v, str):
                continue
            if isinstance(v, dict):
                for kk, vv in v.items():
                    if isinstance(vv, str) and kk.lower() in {"query", "querystring", "promql", "metricsql"}:
                        out.append({"tile_title": title, "query": vv.strip(), "source": f"{k}.{kk}"})
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        for kk, vv in item.items():
                            if isinstance(vv, str) and kk.lower() in {"query", "querystring", "promql", "metricsql"}:
                                out.append({"tile_title": title, "query": vv.strip(), "source": f"{k}[].{kk}"})

    # de-duplicate
    seen: set[tuple[str, str]] = set()
    uniq: list[dict[str, str]] = []
    for row in out:
        key = (row["tile_title"], row["query"])
        if key not in seen:
            seen.add(key)
            uniq.append(row)
    return uniq


async def main() -> None:
    a = _args()
    cfg = load_config(a.config)
    platform = cfg.get_platform(a.platform or None)
    tenant_id = a.tenant_id or platform.get_tenant(a.tenant or None).id
    client = OpsRampClient(platform)

    auth = await client.get_access_token(force_refresh=True)
    collections = await client.list_dashboard_collections_v3()

    if not isinstance(collections, list):
        print(json.dumps({"error": "Unexpected collections payload", "type": str(type(collections))}, ensure_ascii=False, indent=2))
        return

    keywords = a.keyword or ["enforce", "service pod insights dashboard"]

    found: list[dict[str, str]] = []
    # Pass 1: use embedded dashboards from collections response if present
    for c in collections:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id", ""))
        ctitle = str(c.get("title", ""))
        dashboards = c.get("dashboards", [])
        if not isinstance(dashboards, list):
            continue
        for d in dashboards:
            if not isinstance(d, dict):
                continue
            dtitle = str(d.get("title", ""))
            did = str(d.get("id", ""))
            if _match(dtitle, keywords):
                found.append(
                    {
                        "collection_id": cid,
                        "collection_title": ctitle,
                        "dashboard_id": did,
                        "dashboard_title": dtitle,
                    }
                )

    # Fallback pass if not found: enumerate each collection explicitly.
    if not found:
        for c in collections:
            if not isinstance(c, dict):
                continue
            cid = str(c.get("id", ""))
            ctitle = str(c.get("title", ""))
            if not cid:
                continue
            try:
                cd = await client.list_collection_dashboards_v3(cid)
            except OpsRampAPIError:
                continue
            dashboards = cd.get("dashboards", []) if isinstance(cd, dict) else []
            if not isinstance(dashboards, list):
                continue
            for d in dashboards:
                if not isinstance(d, dict):
                    continue
                dtitle = str(d.get("title", ""))
                did = str(d.get("id", ""))
                if _match(dtitle, keywords):
                    found.append(
                        {
                            "collection_id": cid,
                            "collection_title": ctitle,
                            "dashboard_id": did,
                            "dashboard_title": dtitle,
                        }
                    )

    if not found:
        print(
            json.dumps(
                {
                    "auth_ok": bool(auth),
                    "platform": platform.name,
                    "tenant_id": tenant_id,
                    "keywords": keywords,
                    "matched": 0,
                    "message": "No dashboard matched keywords",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    # Fetch first match in detail
    target = found[0]
    detail = await client.get_dashboard_v3(target["collection_id"], target["dashboard_id"])
    queries = _extract_query_candidates(detail if isinstance(detail, dict) else {})

    tile_data_results: list[dict[str, Any]] = []
    for row in queries[: a.limit_tiles]:
        q = row["query"]
        result_row: dict[str, Any] = {
            "tile_title": row["tile_title"],
            "query_source": row["source"],
            "query": q,
        }
        try:
            resp = await client.query_metricsql_v3(
                tenant_id=tenant_id,
                query=q,
                start="0",
                end="0",
                step=300,
            )
            series = []
            if isinstance(resp, dict):
                series = resp.get("data", {}).get("result", []) if isinstance(resp.get("data", {}), dict) else []
            result_row["status"] = resp.get("status", "unknown") if isinstance(resp, dict) else "unknown"
            result_row["series_count"] = len(series) if isinstance(series, list) else 0
        except OpsRampAPIError as exc:
            result_row["status"] = "failed"
            result_row["error_code"] = exc.status_code
            result_row["error"] = exc.details
        tile_data_results.append(result_row)

    print(
        json.dumps(
            {
                "auth_ok": bool(auth),
                "platform": platform.name,
                "tenant_id": tenant_id,
                "keywords": keywords,
                "matched": len(found),
                "target": target,
                "dashboard_detail_keys": sorted(list(detail.keys())) if isinstance(detail, dict) else [],
                "tile_query_candidates": len(queries),
                "tile_data_results": tile_data_results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
