"""Tests for server utility functions."""

from __future__ import annotations

import time

import pytest

from opsramp_mcp.server import (
    _validate_output_format,
    _resolve_time_range_params,
    _parse_epoch_seconds,
    _duration_seconds,
)


class TestValidateOutputFormat:
    def test_csv(self):
        assert _validate_output_format("csv") == "csv"

    def test_text(self):
        assert _validate_output_format("text") == "text"

    def test_json(self):
        assert _validate_output_format("json") == "json"

    def test_uppercase(self):
        assert _validate_output_format("CSV") == "csv"

    def test_mixed_case(self):
        assert _validate_output_format("Json") == "json"

    def test_whitespace(self):
        assert _validate_output_format("  text  ") == "text"

    def test_invalid_falls_back_to_csv(self):
        assert _validate_output_format("yaml") == "csv"
        assert _validate_output_format("") == "csv"
        assert _validate_output_format("xml") == "csv"


class TestResolveTimeRangeParams:
    def test_time_range_overrides_defaults(self):
        start, end, step = _resolve_time_range_params("1h", "0", "0", 0)
        assert int(end) - int(start) == 3600
        assert step > 0

    def test_time_range_with_empty_defaults(self):
        start, end, step = _resolve_time_range_params("24h", "", "", 0)
        assert int(end) - int(start) == 86400
        assert step == 300  # auto_step for 24h

    def test_explicit_values_preserved(self):
        start, end, step = _resolve_time_range_params("1h", "1000", "2000", 60)
        assert start == "1000"
        assert end == "2000"
        assert step == 60

    def test_custom_step_preserved(self):
        start, end, step = _resolve_time_range_params("1h", "0", "0", 120)
        assert step == 120
        assert int(end) - int(start) == 3600

    def test_invalid_time_range_passthrough(self):
        start, end, step = _resolve_time_range_params("abc", "0", "0", 0)
        assert start == "0"
        assert end == "0"
        assert step == 0


class TestParseEpochSeconds:
    def test_valid(self):
        assert _parse_epoch_seconds("1709000000") == 1709000000

    def test_empty(self):
        assert _parse_epoch_seconds("") is None

    def test_non_numeric(self):
        assert _parse_epoch_seconds("now-1h") is None

    def test_whitespace(self):
        assert _parse_epoch_seconds("  1709000000  ") == 1709000000


class TestDurationSeconds:
    def test_valid(self):
        assert _duration_seconds("1000", "2000") == 1000

    def test_reversed(self):
        assert _duration_seconds("2000", "1000") is None

    def test_equal(self):
        assert _duration_seconds("1000", "1000") is None

    def test_invalid_start(self):
        assert _duration_seconds("abc", "2000") is None
