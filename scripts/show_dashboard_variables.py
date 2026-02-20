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


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.toml")
    p.add_argument("--platform", default="")
    p.add_argument("--tenant", default="")
    p.add_argument("--collection-id", required=True)
    p.add_argument("--dashboard-id", required=True)
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    platform = cfg.get_platform(args.platform or None)
    _tenant = platform.get_tenant(args.tenant or None)

    client = OpsRampClient(platform)
    dashboard = await client.get_dashboard_v3(args.collection_id, args.dashboard_id)
    variables = dashboard.get("variables", []) if isinstance(dashboard, dict) else []

    print(
        json.dumps(
            {
                "dashboard_title": dashboard.get("title") if isinstance(dashboard, dict) else None,
                "variables_count": len(variables) if isinstance(variables, list) else 0,
                "variables": variables,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
