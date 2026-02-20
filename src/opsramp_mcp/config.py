"""Configuration loader for OpsRamp MCP.

Strict mode:
1) TOML configuration only (no environment variables)
"""

from __future__ import annotations

import sys
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib  # type: ignore[import]
    except ImportError:
        import tomli as tomllib  # type: ignore[import,no-redef]


@dataclass
class TenantConfig:
    """Tenant-level configuration under a platform."""

    name: str
    id: str
    additional_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class PlatformConfig:
    """OpsRamp platform/instance configuration."""

    name: str
    api_base_url: str
    client_id: str
    client_secret: str
    verify_tls: bool = True
    timeout_seconds: float = 30.0
    default_tenant: str = ""
    tenants: dict[str, TenantConfig] = field(default_factory=dict)

    def get_tenant(self, tenant: str | None = None) -> TenantConfig:
        tenant_name = (tenant or self.default_tenant or "").strip()
        if not tenant_name:
            raise ValueError(
                f"Platform '{self.name}' has no default_tenant. "
                "Provide a tenant alias explicitly."
            )
        if tenant_name not in self.tenants:
            available = ", ".join(self.tenants.keys()) or "(none)"
            raise ValueError(
                f"Unknown tenant '{tenant_name}' on platform '{self.name}'. "
                f"Available: {available}"
            )
        return self.tenants[tenant_name]


@dataclass
class AppConfig:
    """Top-level app configuration with multiple platforms."""

    default_platform: str
    platforms: dict[str, PlatformConfig] = field(default_factory=dict)
    config_path: str = ""
    config_hash: str = ""

    def get_platform(self, platform: str | None = None) -> PlatformConfig:
        platform_name = (platform or self.default_platform or "").strip()
        if not platform_name:
            raise ValueError("No platform specified and no default_platform configured")
        if platform_name not in self.platforms:
            available = ", ".join(self.platforms.keys()) or "(none)"
            raise ValueError(f"Unknown platform '{platform_name}'. Available: {available}")
        return self.platforms[platform_name]


def _as_bool(raw: str | bool | None, default: bool = True) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _candidate_toml_paths(explicit_path: str | Path | None = None) -> list[Path]:
    if explicit_path is not None:
        return [Path(explicit_path).expanduser()]
    return [
        Path("opsramp.toml"),
        Path("config.toml"),
        Path.home() / ".config" / "opsramp-mcp" / "config.toml",
    ]


def _resolve_toml_path(explicit_path: str | Path | None = None) -> Path | None:
    for p in _candidate_toml_paths(explicit_path):
        if p.is_file():
            return p
    return None


def _load_toml_config(config_path: Path) -> AppConfig:
    with config_path.open("rb") as f:
        content = f.read()
        raw: dict[str, Any] = tomllib.loads(content.decode("utf-8"))
        config_hash = hashlib.sha256(content).hexdigest()

    default_platform = str(raw.get("default_platform", "")).strip()
    platforms_raw: dict[str, Any] = raw.get("platforms", {})
    if not isinstance(platforms_raw, dict) or not platforms_raw:
        raise ValueError(f"No [platforms] configured in TOML: {config_path}")

    platforms: dict[str, PlatformConfig] = {}
    for platform_name, info in platforms_raw.items():
        if not isinstance(info, dict):
            continue
        platforms[platform_name] = _parse_platform(platform_name, info)

    if not default_platform:
        default_platform = next(iter(platforms))
    if default_platform not in platforms:
        raise ValueError(f"default_platform '{default_platform}' not found in [platforms]")

    return AppConfig(
        default_platform=default_platform,
        platforms=platforms,
        config_path=str(config_path.expanduser().resolve()),
        config_hash=config_hash,
    )


def _parse_platform(platform_name: str, info: dict[str, Any]) -> PlatformConfig:
    api_base_url = str(info.get("api_base_url", "")).strip().rstrip("/")
    client_id = str(info.get("client_id", "")).strip()
    client_secret = str(info.get("client_secret", "")).strip()
    verify_tls = _as_bool(info.get("verify_tls"), default=True)
    timeout_seconds = float(info.get("timeout_seconds", 30.0))
    default_tenant = str(info.get("default_tenant", "")).strip()

    if not api_base_url:
        raise ValueError(f"platforms.{platform_name}.api_base_url is required")
    if not client_id:
        raise ValueError(f"platforms.{platform_name}.client_id is required")
    if not client_secret:
        raise ValueError(f"platforms.{platform_name}.client_secret is required")

    tenants = _parse_tenants(platform_name, info.get("tenants", {}))

    return PlatformConfig(
        name=platform_name,
        api_base_url=api_base_url,
        client_id=client_id,
        client_secret=client_secret,
        verify_tls=verify_tls,
        timeout_seconds=timeout_seconds,
        default_tenant=default_tenant,
        tenants=tenants,
    )


def _parse_tenants(platform_name: str, tenants_raw: Any) -> dict[str, TenantConfig]:
    tenants: dict[str, TenantConfig] = {}
    if not isinstance(tenants_raw, dict):
        return tenants

    for tenant_name, tenant_info in tenants_raw.items():
        if not isinstance(tenant_info, dict):
            continue
        tenants[tenant_name] = _parse_tenant(platform_name, tenant_name, tenant_info)
    return tenants


def _parse_tenant(platform_name: str, tenant_name: str, tenant_info: dict[str, Any]) -> TenantConfig:
    tenant_id = str(tenant_info.get("id", "")).strip()
    if not tenant_id:
        raise ValueError(f"platforms.{platform_name}.tenants.{tenant_name}.id is required")

    additional_headers_raw: dict[str, Any] = tenant_info.get("additional_headers", {})
    additional_headers = {
        str(k): str(v)
        for k, v in additional_headers_raw.items()
    }
    return TenantConfig(
        name=tenant_name,
        id=tenant_id,
        additional_headers=additional_headers,
    )


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load app configuration.

    Search order:
    1) explicit path
    2) ./opsramp.toml
    3) ./config.toml
    4) ~/.config/opsramp-mcp/config.toml
    """
    toml_path = _resolve_toml_path(config_path)
    if toml_path is None:
        searched = ", ".join(str(p) for p in _candidate_toml_paths(config_path))
        raise FileNotFoundError(
            "TOML config file not found. "
            f"Searched: {searched}. "
            "Create config.toml (or pass an explicit path) and retry."
        )
    return _load_toml_config(toml_path)
