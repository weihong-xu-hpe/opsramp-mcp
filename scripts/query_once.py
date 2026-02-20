"""Run one MetricsQL query with TOML config.

Example:
python scripts/query_once.py --config config.toml --platform qa_glcp --tenant hoku \
  --query 'sum by (k8s_pod_name) (k8s_pod_cpu_usage{k8s_deployment_name="enforce"})' \
  --start 0 --end 0 --step 300
"""

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

from opsramp_mcp.client import OpsRampClient
from opsramp_mcp.config import load_config


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run one MetricsQL query")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--platform", default="")
    p.add_argument("--tenant", default="")
    p.add_argument("--tenant-id", default="")
    p.add_argument("--query", required=True)
    p.add_argument("--start", default="0")
    p.add_argument("--end", default="0")
    p.add_argument("--step", type=int, default=300)
    return p.parse_args()


async def main() -> None:
    a = _args()
    cfg = load_config(a.config)
    platform = cfg.get_platform(a.platform or None)
    tenant_id = a.tenant_id or platform.get_tenant(a.tenant or None).id

    client = OpsRampClient(platform)
    token = await client.get_access_token(force_refresh=True)
    resp = await client.query_metricsql_v3(
        tenant_id=tenant_id,
        query=a.query,
        start=a.start,
        end=a.end,
        step=a.step,
    )

    result = []
    if isinstance(resp, dict):
        result = resp.get("data", {}).get("result", []) if isinstance(resp.get("data", {}), dict) else []

    print(
        json.dumps(
            {
                "auth_ok": bool(token),
                "platform": platform.name,
                "tenant_id": tenant_id,
                "query": a.query,
                "status": resp.get("status", "unknown") if isinstance(resp, dict) else "unknown",
                "series_count": len(result) if isinstance(result, list) else 0,
                "sample": result[0] if isinstance(result, list) and result else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
