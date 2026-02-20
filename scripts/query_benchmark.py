from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opsramp_mcp.client import OpsRampAPIError, OpsRampClient
from opsramp_mcp.config import load_config


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Benchmark one MetricsQL query")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--platform", default="")
    p.add_argument("--tenant", default="")
    p.add_argument("--tenant-id", default="")
    p.add_argument("--query", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--step", type=int, default=60)
    p.add_argument("--smart", action="store_true", help="Use smart query (adaptive step + sharding fallback)")
    p.add_argument("--disable-auto-downsample", action="store_true")
    p.add_argument("--disable-sharding", action="store_true")
    p.add_argument("--max-points-per-slice", type=int, default=8000)
    return p.parse_args()


async def main() -> None:
    a = parse_args()
    cfg = load_config(a.config)
    platform = cfg.get_platform(a.platform or None)
    tenant_id = a.tenant_id or platform.get_tenant(a.tenant or None).id

    client = OpsRampClient(platform)

    t0 = time.perf_counter()
    try:
        if a.smart:
            resp = await client.query_metricsql_v3_smart(
                tenant_id=tenant_id,
                query=a.query,
                start=a.start,
                end=a.end,
                step=a.step,
                auto_downsample=not a.disable_auto_downsample,
                enable_sharding=not a.disable_sharding,
                max_points_per_slice=a.max_points_per_slice,
            )
        else:
            resp = await client.query_metricsql_v3(
                tenant_id=tenant_id,
                query=a.query,
                start=a.start,
                end=a.end,
                step=a.step,
            )

        elapsed = time.perf_counter() - t0
        result = resp.get("data", {}).get("result", []) if isinstance(resp, dict) else []
        print(
            json.dumps(
                {
                    "ok": True,
                    "smart": a.smart,
                    "elapsed_seconds": round(elapsed, 3),
                    "timeout_seconds_config": platform.timeout_seconds,
                    "status": resp.get("status") if isinstance(resp, dict) else None,
                    "series_count": len(result) if isinstance(result, list) else 0,
                    "meta": resp.get("meta") if isinstance(resp, dict) else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except OpsRampAPIError as exc:
        elapsed = time.perf_counter() - t0
        print(
            json.dumps(
                {
                    "ok": False,
                    "smart": a.smart,
                    "elapsed_seconds": round(elapsed, 3),
                    "timeout_seconds_config": platform.timeout_seconds,
                    "error_type": "OpsRampAPIError",
                    "status_code": exc.status_code,
                    "error": exc.details,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except httpx.TimeoutException as exc:
        elapsed = time.perf_counter() - t0
        print(
            json.dumps(
                {
                    "ok": False,
                    "smart": a.smart,
                    "elapsed_seconds": round(elapsed, 3),
                    "timeout_seconds_config": platform.timeout_seconds,
                    "error_type": "TimeoutException",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
