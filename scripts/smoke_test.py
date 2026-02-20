"""Simple smoke test for OpsRamp MCP client components."""

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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpsRamp smoke test (TOML-only)")
    parser.add_argument("--config", default="", help="Path to TOML config file")
    parser.add_argument("--platform", default="", help="Platform alias in TOML")
    parser.add_argument("--tenant", default="", help="Tenant alias in TOML")
    parser.add_argument("--tenant-id", default="", help="Explicit tenant/client ID override")
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    app_config = load_config(args.config or None)
    platform_name = args.platform.strip()
    tenant_name = args.tenant.strip()
    tenant_id_override = args.tenant_id.strip()

    platform = app_config.get_platform(platform_name or None)
    client = OpsRampClient(platform)

    tenant_id = tenant_id_override
    if not tenant_id:
        try:
            tenant_id = platform.get_tenant(tenant_name or None).id
        except ValueError:
            tenant_id = ""

    report: dict[str, object] = {
        "platform": platform.name,
        "api_base_url": platform.api_base_url,
        "tenant": tenant_name or platform.default_tenant,
        "tenant_id": tenant_id,
        "auth": {},
        "dashboard": {},
        "metricsql": {},
        "v2": {},
    }

    # 1) OAuth
    try:
        token = await client.get_access_token(force_refresh=True)
        report["auth"] = {
            "ok": True,
            "token_preview": f"{token[:8]}...",
            "token_expires_at_epoch": client.token_expires_at,
        }
    except (OpsRampAPIError, ValueError) as exc:
        report["auth"] = {"ok": False, "error": str(exc)}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    # 2) Dashboard collection read
    try:
        report["dashboard"] = await client.list_dashboard_collections_v3()
    except OpsRampAPIError as exc:
        report["dashboard"] = {
            "ok": False,
            "status_code": exc.status_code,
            "details": exc.details,
        }

    # 3) MetricsQL labels
    if tenant_id:
        try:
            report["metricsql"] = await client.list_metricsql_labels_v3(
                tenant_id=tenant_id,
                start="0",
                end="0",
            )
        except OpsRampAPIError as exc:
            report["metricsql"] = {
                "ok": False,
                "status_code": exc.status_code,
                "details": exc.details,
            }

        # 4) v2 metrics list
        try:
            report["v2"] = await client.list_metrics_v2(tenant_id=tenant_id)
        except OpsRampAPIError as exc:
            report["v2"] = {
                "ok": False,
                "status_code": exc.status_code,
                "details": exc.details,
            }
    else:
        report["metricsql"] = {"skipped": True, "reason": "tenant ID not resolved from TOML/arguments"}
        report["v2"] = {"skipped": True, "reason": "tenant ID not resolved from TOML/arguments"}

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
