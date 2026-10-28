from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlsplit

from .config import ROUTER_PROVIDER_ID, RouterConfig
from .toml_edit import (
    atomic_write,
    parse_toml,
    remove_table_tree,
    safe_write_config,
    update_table,
)


ROUTER_HOST_KEYS = (
    "base_url",
    "wire_api",
    "service_tier",
    "model_catalog_json",
    "model_context_window",
    "model_auto_compact_token_limit",
    "model_auto_compact_token_limit_scope",
)


@dataclass(frozen=True)
class InstallResult:
    changed: bool
    backup_path: Path | None
    selected_model: str


def validate_upstreams(source: str, config: RouterConfig) -> None:
    payload = parse_toml(source, str(config.codex_config_path))
    providers = payload.get("model_providers")
    if not isinstance(providers, dict):
        raise ValueError("config.toml has no model_providers table")
    missing = [provider for provider in config.provider_ids if provider not in providers]
    if missing:
        raise ValueError(
            "missing upstream provider configuration: " + ", ".join(sorted(missing))
        )
    for provider_id in config.provider_ids:
        if provider_id == ROUTER_PROVIDER_ID:
            raise ValueError("local-router cannot be used as an upstream provider")
        provider = providers.get(provider_id)
        if not isinstance(provider, dict):
            raise ValueError(f"provider {provider_id!r} is invalid")
        if provider.get("wire_api", "responses") != "responses":
            raise ValueError(f"provider {provider_id!r} must use the responses wire API")
        base_url = provider.get("base_url")
        parsed = urlsplit(str(base_url or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"provider {provider_id!r} has an invalid base_url")


def router_provider_block(config: RouterConfig) -> str:
    base_url = f"http://{config.host}:{config.port}/v1"
    auth_args = '["-m", "codex_provider_router", "token"]'
    return (
        "\n\n[model_providers.local-router]\n"
        'name = "Codex Provider Router"\n'
        f"base_url = {_toml_string(base_url)}\n"
        f"model_catalog_url = {_toml_string(base_url + '/models')}\n"
        'wire_api = "responses"\n'
        "supports_websockets = false\n"
        "\n[model_providers.local-router.capabilities]\n"
        'remote_compaction = "unsupported"\n'
        "\n[model_providers.local-router.auth]\n"
        f"command = {_toml_string(sys.executable)}\n"
        f"args = {auth_args}\n"
        "timeout_ms = 5000\n"
        "refresh_interval_ms = 3600000\n"
    )


def apply_router_config(
    source: str,
    config: RouterConfig,
    *,
    default_model: str | None = None,
    backup_dir: Path | None = None,
) -> InstallResult:
    validate_upstreams(source, config)
    payload = parse_toml(source, str(config.codex_config_path))
    selected = default_model or str(payload.get("model") or "")
    selected_route = config.route_map.get(selected)
    if selected_route is None or not selected_route.public:
        selected = config.default_model
    config.require_route(selected)

    updates: dict[str, Any] = {
        "model": selected,
        "model_provider": ROUTER_PROVIDER_ID,
    }
    updates.update({key: None for key in ROUTER_HOST_KEYS if key not in {"model", "model_provider"}})
    updated = update_table(source, None, updates)
    updated = remove_table_tree(updated, "model_providers.local-router")
    updated = updated.rstrip() + router_provider_block(config)
    parse_toml(updated, str(config.codex_config_path))

    changed = updated != source
    backup_path = None
    if changed:
        directory = backup_dir or config.router_home / "backups"
        backup_path = safe_write_config(config.codex_config_path, updated, directory)
    return InstallResult(
        changed=changed,
        backup_path=backup_path,
        selected_model=selected,
    )


def uninstall_router_config(
    source: str,
    config: RouterConfig,
    *,
    backup_dir: Path | None = None,
) -> InstallResult:
    payload = parse_toml(source, str(config.codex_config_path))
    current_model = str(payload.get("model") or "")
    updates: dict[str, Any] = {}
    if payload.get("model_provider") == ROUTER_PROVIDER_ID:
        route = config.route_map.get(current_model)
        if route is None:
            route = config.public_routes[0]
        updates["model"] = route.upstream_model
        updates["model_provider"] = route.provider
    updated = update_table(source, None, updates) if updates else source
    updated = remove_table_tree(updated, "model_providers.local-router")
    parse_toml(updated, str(config.codex_config_path))

    changed = updated != source
    backup_path = None
    if changed:
        directory = backup_dir or config.router_home / "backups"
        backup_path = safe_write_config(config.codex_config_path, updated, directory)
    return InstallResult(
        changed=changed,
        backup_path=backup_path,
        selected_model=str(updates.get("model") or current_model),
    )


def write_auth_metadata(config: RouterConfig) -> None:
    path = config.router_home / "install.json"
    payload = {
        "version": 1,
        "python": sys.executable,
        "host": config.host,
        "port": config.port,
        "default_model": config.default_model,
        "codex_config": str(config.codex_config_path),
    }
    import json

    atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
