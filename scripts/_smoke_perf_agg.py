"""Smoke-test the refactored service_performance_aggregator latency columns."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from opsramp_mcp.client import OpsRampClient
from opsramp_mcp.config import load_config
from opsramp_mcp.formatters import (
    _extract_metricsql_series,
    _last_real,
    _series_stats,
    format_service_performance,
)


async def main() -> None:
    cfg = load_config("config.toml")
    plat = cfg.get_platform("qa_glcp")
    tid = plat.get_tenant("mira-west").id
    client = OpsRampClient(plat)

    svc = "authz"
    time_range = "15m"
    start, end, step = "0", "0", 60

    kind_filter = 'kind="server"'
    svc_base = f'app="default",service_name="{svc}",{kind_filter}'
    lat_inner = (
        f'sum by (le, operation) '
        f'(rate(trace_operations_latency_bucket{{{svc_base}}}[{time_range}]))'
    )
    queries = {
        "tput": (
            f'topk(20, sum by (operation) '
            f'(rate(trace_operations_total{{{svc_base}}}[{time_range}]))) > 0'
        ),
        "p50": f"histogram_quantile(0.50, {lat_inner})",
        "p95": f"histogram_quantile(0.95, {lat_inner})",
        "p99": f"histogram_quantile(0.99, {lat_inner})",
        "err": (
            f'sum by (operation) (rate(trace_operations_total{{{svc_base},status_code="error"}}[{time_range}]))'
            f" / "
            f"sum by (operation) (rate(trace_operations_total{{{svc_base}}}[{time_range}])) * 100"
        ),
    }

    async def _q(query: str):
        r = await client.query_metricsql_v3(
            tenant_id=tid, query=query, start=start, end=end, step=step
        )
        return _extract_metricsql_series(r)

    tput_s, p50_s, p95_s, p99_s, err_s = await asyncio.gather(
        *[_q(v) for v in queries.values()]
    )

    def _om(sl):
        return {
            s["metric"].get("operation", ""): _last_real(s.get("values", []))
            for s in sl
            if s.get("metric", {}).get("operation")
        }

    p50m, p95m, p99m, errm = _om(p50_s), _om(p95_s), _om(p99_s), _om(err_s)

    mapped = []
    for s in tput_s:
        op = s["metric"].get("operation", "")
        mapped.append(
            {
                "labels": s["metric"],
                "stats": _series_stats(s.get("values", [])),
                "p50_ms": p50m.get(op),
                "p95_ms": p95m.get(op),
                "p99_ms": p99m.get(op),
                "err_pct": errm.get(op),
            }
        )

    out = format_service_performance(svc, {"SERVER_IN": mapped})
    print(out)
    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
