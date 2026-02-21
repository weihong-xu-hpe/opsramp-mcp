"""Async OpsRamp API client with OAuth token management."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
import json

import httpx

from .config import PlatformConfig


JSON_MIME = "application/json"
FORM_MIME = "application/x-www-form-urlencoded"


class OpsRampAPIError(RuntimeError):
    """Raised on non-success API responses from OpsRamp."""

    def __init__(self, message: str, status_code: int | None = None, details: Any | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details


@dataclass
class _TokenCache:
    access_token: str = ""
    expires_at: float = 0.0

    @property
    def is_valid(self) -> bool:
        # Keep a 30-second buffer to avoid edge expiry.
        return bool(self.access_token) and (self.expires_at - 30.0) > time.time()


class OpsRampClient:
    """HTTP client wrapper for OpsRamp v2/v3 APIs."""

    def __init__(self, config: PlatformConfig):
        self.config = config
        self._token = _TokenCache()
        self._http_client = httpx.AsyncClient(
            base_url=self.config.api_base_url,
            timeout=self.config.timeout_seconds,
            verify=self.config.verify_tls,
        )

    async def aclose(self) -> None:
        await self._http_client.aclose()

    @property
    def token_expires_at(self) -> float:
        return self._token.expires_at

    async def get_access_token(self, force_refresh: bool = False) -> str:
        """Get (and cache) OAuth token via client credentials flow."""
        if not force_refresh and self._token.is_valid:
            return self._token.access_token

        payload = {
            "grant_type": "client_credentials",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
        }
        headers = {
            "Accept": JSON_MIME,
            "Content-Type": FORM_MIME,
        }

        resp = await self._http_client.post("/tenancy/auth/oauth/token", data=payload, headers=headers)

        if resp.status_code != 200:
            raise OpsRampAPIError(
                message=f"OAuth token request failed with status {resp.status_code}",
                status_code=resp.status_code,
                details=_safe_body(resp),
            )

        data = resp.json()
        token = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))
        if not token:
            raise OpsRampAPIError("OAuth response missing access_token", status_code=resp.status_code, details=data)

        self._token.access_token = token
        self._token.expires_at = time.time() + expires_in
        return token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        additional_headers: dict[str, str] | None = None,
    ) -> Any:
        token = await self.get_access_token()
        headers: dict[str, str] = {
            "Authorization": f"Bearer {token}",
            "Accept": JSON_MIME,
        }
        if json_body is not None:
            headers["Content-Type"] = JSON_MIME
        if additional_headers:
            headers.update(additional_headers)

        resp = await self._http_client.request(method, path, params=params, json=json_body, headers=headers)

        if resp.status_code >= 400:
            raise OpsRampAPIError(
                message=f"OpsRamp API request failed: {method} {path} -> {resp.status_code}",
                status_code=resp.status_code,
                details=_safe_body(resp),
            )

        content_type = resp.headers.get("content-type", "")
        if JSON_MIME in content_type:
            return resp.json()

        return {
            "status_code": resp.status_code,
            "content_type": content_type,
            "text": resp.text,
        }

    # -----------------------------
    # Dashboard APIs (v3)
    # -----------------------------

    async def list_dashboard_collections_v3(
        self,
        limit: int | None = None,
        offset: int | None = None,
        additional_headers: dict[str, str] | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        return await self._request(
            "GET",
            "/dashboards/api/v3/collections",
            params=params,
            additional_headers=additional_headers,
        )

    async def list_collection_dashboards_v3(
        self,
        collection_id: str,
        limit: int | None = None,
        offset: int | None = None,
        additional_headers: dict[str, str] | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        return await self._request(
            "GET",
            f"/dashboards/api/v3/collections/{collection_id}/dashboards",
            params=params,
            additional_headers=additional_headers,
        )

    async def get_dashboard_v3(
        self,
        collection_id: str,
        dashboard_id: str,
        additional_headers: dict[str, str] | None = None,
    ) -> Any:
        return await self._request(
            "GET",
            f"/dashboards/api/v3/collections/{collection_id}/dashboards/{dashboard_id}",
            additional_headers=additional_headers,
        )

    # -----------------------------
    # MetricsQL APIs (v3)
    # -----------------------------

    async def query_metricsql_v3(
        self,
        tenant_id: str,
        query: str,
        *,
        start: str | None = None,
        end: str | None = None,
        step: int | None = None,
        additional_headers: dict[str, str] | None = None,
    ) -> Any:
        params: dict[str, Any] = {"query": query}
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        if step is not None:
            params["step"] = step

        return await self._request(
            "GET",
            f"/metricsql/api/v3/tenants/{tenant_id}/metrics",
            params=params,
            additional_headers=additional_headers,
        )

    async def query_metricsql_v3_smart(
        self,
        tenant_id: str,
        query: str,
        *,
        start: str,
        end: str,
        step: int = 60,
        auto_downsample: bool = True,
        enable_sharding: bool = True,
        max_points_per_slice: int = 8000,
        additional_headers: dict[str, str] | None = None,
    ) -> Any:
        """Smart query executor with adaptive step and optional sharded fallback.

        Strategy:
        1) Compute an effective step via duration-based recommendation (if enabled).
        2) Try a normal query once.
        3) If response hits data-limit (406), shard time range and merge results.
        """
        parsed_start = _parse_epoch_seconds(start)
        parsed_end = _parse_epoch_seconds(end)
        duration_seconds = _duration_seconds(parsed_start, parsed_end)

        effective_step = _effective_step(step=step, duration_seconds=duration_seconds, auto_downsample=auto_downsample)
        request_start = str(parsed_start) if parsed_start is not None else start
        request_end = str(parsed_end) if parsed_end is not None else end
        single_meta = _build_single_meta(
            requested_step=step,
            effective_step=effective_step,
            duration_seconds=duration_seconds,
        )

        try:
            single = await self.query_metricsql_v3(
                tenant_id=tenant_id,
                query=query,
                start=request_start,
                end=request_end,
                step=effective_step,
                additional_headers=additional_headers,
            )
            return _with_meta(single, single_meta)
        except OpsRampAPIError as exc:
            if not _should_shard(
                exc=exc,
                enable_sharding=enable_sharding,
                start=parsed_start,
                end=parsed_end,
            ):
                raise

        if parsed_start is None or parsed_end is None:
            raise OpsRampAPIError("Cannot shard query without numeric start/end")

        sharded = await self._query_metricsql_v3_sharded(
            tenant_id=tenant_id,
            query=query,
            start=parsed_start,
            end=parsed_end,
            step=effective_step,
            auto_downsample=auto_downsample,
            max_points_per_slice=max_points_per_slice,
            additional_headers=additional_headers,
        )

        merged = _merge_metricsql_matrix_results(sharded["responses"])
        return _with_meta(
            merged,
            {
                "requested_step": step,
                "effective_step": effective_step,
                "duration_seconds": duration_seconds,
                "mode": "sharded",
                "slice_count": len(sharded["slices"]),
                "max_points_per_slice": max_points_per_slice,
                "slice_steps": sharded["slice_steps"],
            },
        )

    async def _query_metricsql_v3_sharded(
        self,
        *,
        tenant_id: str,
        query: str,
        start: int,
        end: int,
        step: int,
        auto_downsample: bool,
        max_points_per_slice: int,
        additional_headers: dict[str, str] | None,
    ) -> dict[str, Any]:
        slices = _build_time_slices(
            start=start,
            end=end,
            step=step,
            max_points_per_slice=max_points_per_slice,
        )

        responses: list[dict[str, Any]] = []
        slice_steps: list[int] = []
        for slice_start, slice_end in slices:
            response, used_step = await self._query_metricsql_slice_with_retry(
                tenant_id=tenant_id,
                query=query,
                start=slice_start,
                end=slice_end,
                step=step,
                auto_downsample=auto_downsample,
                additional_headers=additional_headers,
            )
            responses.append(response)
            slice_steps.append(used_step)

        return {
            "slices": slices,
            "responses": responses,
            "slice_steps": slice_steps,
        }

    async def _query_metricsql_slice_with_retry(
        self,
        *,
        tenant_id: str,
        query: str,
        start: int,
        end: int,
        step: int,
        auto_downsample: bool,
        additional_headers: dict[str, str] | None,
    ) -> tuple[dict[str, Any], int]:
        current_step = step
        for attempt in range(4):
            try:
                data = await self.query_metricsql_v3(
                    tenant_id=tenant_id,
                    query=query,
                    start=str(start),
                    end=str(end),
                    step=current_step,
                    additional_headers=additional_headers,
                )
                return _normalize_metricsql_response(data), current_step
            except OpsRampAPIError as exc:
                if not _can_retry_with_larger_step(exc=exc, auto_downsample=auto_downsample, attempt=attempt):
                    raise
                current_step = current_step * 2

        raise OpsRampAPIError("Failed to fetch sharded slice response")

    async def list_metricsql_labels_v3(
        self,
        tenant_id: str,
        *,
        start: str | None = None,
        end: str | None = None,
        query: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        additional_headers: dict[str, str] | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        if query is not None:
            params["query"] = query
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset

        return await self._request(
            "GET",
            f"/metricsql/api/v3/tenants/{tenant_id}/metrics/labels",
            params=params,
            additional_headers=additional_headers,
        )

    async def list_metricsql_label_values_v3(
        self,
        tenant_id: str,
        label_name: str,
        *,
        start: str | None = None,
        end: str | None = None,
        query: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        additional_headers: dict[str, str] | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        if query is not None:
            params["query"] = query
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset

        return await self._request(
            "GET",
            f"/metricsql/api/v3/tenants/{tenant_id}/metrics/labels/{label_name}",
            params=params,
            additional_headers=additional_headers,
        )

    async def push_metrics_data_v3(
        self,
        client_id: str,
        payload: list[dict[str, Any]],
        additional_headers: dict[str, str] | None = None,
    ) -> Any:
        return await self._request(
            "POST",
            f"/metricsql/api/v3/tenants/{client_id}/metrics/data",
            json_body=payload,
            additional_headers=additional_headers,
        )

    # -----------------------------
    # Tracing APIs (Reverse-engineered from UI)
    # -----------------------------

    async def get_tracing_top_operations(
        self,
        tenant_id: str,
        query: str,
        start: str,
        end: str,
        sort_by: str = "maxLatency",
        additional_headers: dict[str, str] | None = None,
    ) -> Any:
        params: dict[str, Any] = {
            "query": query,
            "start": start,
            "end": end,
            "sortBy": sort_by,
        }
        return await self._request(
            "GET",
            f"/tracing-query/api/v1/tenants/{tenant_id}/top-operations",
            params=params,
            additional_headers=additional_headers,
        )

    async def get_tracing_operation_insights(
        self,
        tenant_id: str,
        query: str,
        start: str,
        end: str,
        page_no: int = 1,
        page_size: int = 100,
        limit: int = 100,
        sort_by: str = "averageLatency",
        sort_by_option: str = "desc",
        additional_headers: dict[str, str] | None = None,
    ) -> Any:
        params: dict[str, Any] = {
            "query": query,
            "start": start,
            "end": end,
            "pageNo": page_no,
            "pageSize": page_size,
            "limit": limit,
            "sortBy": sort_by,
            "sortByOption": sort_by_option,
        }
        return await self._request(
            "GET",
            f"/tracing-query/api/v1/tenants/{tenant_id}/operation-insights",
            params=params,
            additional_headers=additional_headers,
        )

    # -----------------------------
    # v2 compatibility APIs
    # -----------------------------

    async def list_metrics_v2(
        self,
        tenant_id: str,
        *,
        query_string: str | None = None,
        metric_name: str | None = None,
        group_name: str | None = None,
        display_name: str | None = None,
        scope: str | None = None,
        page_no: int = 1,
        page_size: int = 100,
        additional_headers: dict[str, str] | None = None,
    ) -> Any:
        params: dict[str, Any] = {
            "pageNo": page_no,
            "pageSize": page_size,
        }
        if query_string:
            params["queryString"] = query_string
        if metric_name:
            params["metricName"] = metric_name
        if group_name:
            params["groupName"] = group_name
        if display_name:
            params["displayName"] = display_name
        if scope:
            params["scope"] = scope

        return await self._request(
            "GET",
            f"/api/v2/tenants/{tenant_id}/metrics",
            params=params,
            additional_headers=additional_headers,
        )

    async def get_metric_v2(
        self,
        tenant_id: str,
        metric_name: str,
        additional_headers: dict[str, str] | None = None,
    ) -> Any:
        return await self._request(
            "GET",
            f"/api/v2/tenants/{tenant_id}/metrics/{metric_name}",
            additional_headers=additional_headers,
        )

    async def list_reporting_apps_v2(
        self,
        tenant_id: str,
        *,
        page_no: int = 1,
        page_size: int = 100,
        query_string: str | None = None,
        category: str | None = None,
        additional_headers: dict[str, str] | None = None,
    ) -> Any:
        params: dict[str, Any] = {
            "pageNo": page_no,
            "pageSize": page_size,
        }
        if query_string:
            params["queryString"] = query_string
        if category:
            params["category"] = category

        return await self._request(
            "GET",
            f"/api/v2/tenants/{tenant_id}/reporting-apps/available/search",
            params=params,
            additional_headers=additional_headers,
        )


def _safe_body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text


def _parse_epoch_seconds(value: str | None) -> int | None:
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if not v.isdigit():
        return None
    return int(v)


def _duration_seconds(start: int | None, end: int | None) -> int | None:
    if start is None or end is None:
        return None
    if end <= start:
        return None
    return end - start


def _recommended_step_seconds(duration_seconds: int) -> int:
    # Based on OpsRamp MetricsQL guide recommendations.
    # up to 1 day => 60s
    # >1 day and up to 1 month => 3600s
    # >1 month => 86400s
    one_day = 24 * 3600
    one_month = 30 * one_day
    if duration_seconds <= one_day:
        return 60
    if duration_seconds <= one_month:
        return 3600
    return 86400


def _is_data_limit_error(exc: OpsRampAPIError) -> bool:
    return exc.status_code == 406


def _effective_step(step: int, duration_seconds: int | None, auto_downsample: bool) -> int:
    effective_step = max(1, int(step))
    if auto_downsample and duration_seconds is not None:
        effective_step = max(effective_step, _recommended_step_seconds(duration_seconds))
    return effective_step


def _build_single_meta(requested_step: int, effective_step: int, duration_seconds: int | None) -> dict[str, Any]:
    return {
        "requested_step": requested_step,
        "effective_step": effective_step,
        "duration_seconds": duration_seconds,
        "mode": "single",
    }


def _should_shard(exc: OpsRampAPIError, enable_sharding: bool, start: int | None, end: int | None) -> bool:
    if not _is_data_limit_error(exc):
        return False
    if not enable_sharding:
        return False
    if start is None or end is None:
        return False
    return end > start


def _can_retry_with_larger_step(exc: OpsRampAPIError, auto_downsample: bool, attempt: int) -> bool:
    return _is_data_limit_error(exc) and auto_downsample and attempt < 3


def _normalize_metricsql_response(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return data
    return {"status": "success", "data": data}


def _build_time_slices(start: int, end: int, step: int, max_points_per_slice: int) -> list[tuple[int, int]]:
    step = max(1, step)
    max_points_per_slice = max(100, max_points_per_slice)
    window_seconds = step * max_points_per_slice
    slices: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        slice_end = min(cursor + window_seconds, end)
        slices.append((cursor, slice_end))
        cursor = slice_end
    return slices


def _merge_metricsql_matrix_results(responses: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, dict[str, Any]] = {}
    for resp in responses:
        for series in _extract_series_from_response(resp):
            _merge_series(merged, series)

    final_result: list[dict[str, Any]] = []
    for bucket in merged.values():
        points = _sorted_points(bucket)
        final_result.append(
            {
                "metric": bucket["metric"],
                "values": points,
            }
        )

    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": final_result,
        },
    }


def _with_meta(payload: Any, meta: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        data = dict(payload)
    else:
        data = {"status": "success", "data": payload}
    data["meta"] = meta
    return data


def _extract_series_from_response(resp: dict[str, Any]) -> list[dict[str, Any]]:
    data = resp.get("data", {}) if isinstance(resp, dict) else {}
    if not isinstance(data, dict):
        return []
    result = data.get("result", [])
    if not isinstance(result, list):
        return []
    return [series for series in result if isinstance(series, dict)]


def _merge_series(merged: dict[str, dict[str, Any]], series: dict[str, Any]) -> None:
    metric = _extract_metric(series)
    key = json.dumps(metric, sort_keys=True, ensure_ascii=False)
    bucket = merged.setdefault(
        key,
        {
            "metric": metric,
            "values": {},
        },
    )
    _add_point_list(bucket, series.get("values", []))
    _add_single_point(bucket, series.get("value", None))


def _extract_metric(series: dict[str, Any]) -> dict[str, Any]:
    metric = series.get("metric", {})
    return metric if isinstance(metric, dict) else {}


def _add_point_list(bucket: dict[str, Any], values: Any) -> None:
    if not isinstance(values, list):
        return
    for point in values:
        _add_point(bucket, point)


def _add_single_point(bucket: dict[str, Any], value: Any) -> None:
    if isinstance(value, list):
        _add_point(bucket, value)


def _add_point(bucket: dict[str, Any], point: Any) -> None:
    if not isinstance(point, list) or len(point) < 2:
        return
    timestamp = point[0]
    bucket["values"][str(timestamp)] = [point[0], point[1]]


def _sorted_points(bucket: dict[str, Any]) -> list[list[Any]]:
    points = list(bucket["values"].values())
    points.sort(key=lambda x: float(x[0]))
    return points
