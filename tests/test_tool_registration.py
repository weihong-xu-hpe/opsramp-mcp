"""Tests to verify tool registration on the MCP server."""

from __future__ import annotations

import pytest
from opsramp_mcp.server import mcp


EXPECTED_TOOLS = sorted([
    "opsramp_metricsql_query_smart",
    "opsramp_metricsql_labels",
    "opsramp_metricsql_label_values",
    "opsramp_metricsql_batch_query",
    "opsramp_tracing_operation_insights",
    "opsramp_tracing_batch_insights",
    "opsramp_dashboard_get_variables",
    "opsramp_dashboard_run_tiles_smart",
    "opsramp_dashboard_find",
    "opsramp_v2_get_metric",
    "opsramp_v2_list_metrics",
])


class TestToolRegistration:
    def test_expected_tool_count(self):
        tools = mcp._tool_manager._tools
        assert len(tools) == 11, (
            f"Expected 11 tools, got {len(tools)}: {sorted(tools.keys())}"
        )

    def test_expected_tools_present(self):
        tools = sorted(mcp._tool_manager._tools.keys())
        assert tools == EXPECTED_TOOLS

    def test_deregistered_tools_absent(self):
        tools = mcp._tool_manager._tools
        deregistered = [
            "opsramp_dashboard_list_collections",
            "opsramp_dashboard_list_dashboards",
            "opsramp_dashboard_get",
            "opsramp_metricsql_query",
            "opsramp_metricsql_push_data",
            "opsramp_v2_list_reporting_apps",
            "opsramp_tracing_top_operations",
        ]
        for name in deregistered:
            assert name not in tools, f"{name} should be deregistered"

    def test_new_tools_have_output_format_param(self):
        """New and enhanced tools should accept output_format."""
        tools_with_output_format = [
            "opsramp_metricsql_query_smart",
            "opsramp_metricsql_batch_query",
            "opsramp_tracing_operation_insights",
            "opsramp_tracing_batch_insights",
            "opsramp_dashboard_run_tiles_smart",
        ]
        for name in tools_with_output_format:
            tool = mcp._tool_manager._tools.get(name)
            assert tool is not None, f"{name} not found"
            # tool.parameters is a JSON Schema dict with "properties" key
            schema = tool.parameters
            if isinstance(schema, dict) and "properties" in schema:
                param_names = list(schema["properties"].keys())
            else:
                param_names = list(schema.keys()) if isinstance(schema, dict) else [p.name for p in schema]
            assert "output_format" in param_names, (
                f"{name} missing output_format param"
            )
