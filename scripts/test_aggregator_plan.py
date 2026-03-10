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

def get_queries(service_name: str) -> dict[str, str]:
    return {
        "SERVER_IN": f'topk(100, sum by (operation) (rate(trace_operations_total{{app="default",service_name="{service_name}",kind="server"}}[15m])))',
        "HTTP_OUT": f'topk(100, sum by (peer_service, net_peer_name, operation) (rate(trace_operations_total{{app="default",service_name="{service_name}",kind="client",transaction_category=~"(?i)http"}}[15m])))',
        "DB_CACHE": f'topk(100, sum by (db_system, peer_service, operation) (rate(trace_operations_total{{app="default",service_name="{service_name}",kind="client",transaction_category=~"(?i)database.*|db.*"}}[15m])))',
        "MQ": f'topk(100, sum by (messaging_system, messaging_destination, operation) (rate(trace_operations_total{{app="default",service_name="{service_name}",kind=~"producer|consumer"}}[15m])))'
    }

async def run_queries(service: str, env: str, tenant: str):
    cfg = load_config(None)
    platform = cfg.get_platform(env)
    tenant_id = platform.get_tenant(tenant).id

    client = OpsRampClient(platform)
    await client.get_access_token(force_refresh=True)

    queries = get_queries(service)
    results = {}

    async def fetch(name, q):
        try:
            resp = await client.query_metricsql_v3(
                tenant_id=tenant_id,
                query=f'({q}) > 0',  # Only return if > 0
                step=300,
                start="0",
                end="0"
            )
            # Find the actual metric data returned
            if isinstance(resp, dict) and "data" in resp and "result" in resp["data"]:
                series = resp["data"]["result"]
                return name, [{"labels": s.get("metric", {}), "has_data": True} for s in series]
            return name, []
        except Exception as e:
            return name, [{"error": str(e)}]

    tasks = [fetch(name, q) for name, q in queries.items()]
    completed = await asyncio.gather(*tasks)
    
    for name, data in completed:
        results[name] = data
        
    print(json.dumps({service: results}, indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", required=True)
    parser.add_argument("--env", default="mira-east")
    parser.add_argument("--tenant", default="qa_glcp")
    args = parser.parse_args()
    asyncio.run(run_queries(args.service, args.env, args.tenant))
