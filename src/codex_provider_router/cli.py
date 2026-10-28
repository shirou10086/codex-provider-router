from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from . import __version__
from .codex_config import (
    apply_router_config,
    uninstall_router_config,
    validate_upstreams,
    write_auth_metadata,
)
from .config import (
    RouterConfig,
    default_codex_home,
    default_router_config_path,
    ensure_config,
    load_config,
)
from .proxy import ProxyApp, ensure_client_token, serve, validate_runtime
from .service import LaunchAgentService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-provider-router",
        description="Run and install a local Responses API router for Codex.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=default_codex_home(),
        help="Codex home directory (default: $CODEX_HOME or ~/.codex)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Router config path (default: <codex-home>/provider-router/config.json)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="Configure Codex and start the service")
    install.add_argument("--port", type=int, help="Loopback port (default: 49687)")
    install.add_argument("--model", help="Default public model alias")
    install.add_argument(
        "--route",
        action="append",
        default=[],
        metavar="[ALIAS=]PROVIDER:MODEL",
        help="Add a public route; repeat for multiple providers",
    )
    install.add_argument(
        "--no-service",
        action="store_true",
        help="Update Codex config without installing the macOS LaunchAgent",
    )
    install.add_argument(
        "--reset-config",
        action="store_true",
        help="Replace provider-router/config.json with the packaged defaults",
    )

    subparsers.add_parser("serve", help="Run the router in the foreground")
    subparsers.add_parser("status", help="Show Codex, service, and router status")
    subparsers.add_parser("check", help="Validate config, catalog, and credentials")
    subparsers.add_parser("catalog", help="Print the merged model catalog")
    subparsers.add_parser("routes", help="List configured model aliases")
    subparsers.add_parser("token", help="Print the local client token")

    uninstall = subparsers.add_parser(
        "uninstall",
        help="Stop the service and restore the current thread config to its upstream provider",
    )
    uninstall.add_argument(
        "--keep-service",
        action="store_true",
        help="Leave the LaunchAgent installed",
    )
    return parser


def _router_config(
    args: argparse.Namespace,
    *,
    create: bool = False,
    source: str | None = None,
) -> RouterConfig:
    if args.config:
        return load_config(args.config, codex_home=args.codex_home)
    if create:
        return ensure_config(
            args.codex_home,
            port=getattr(args, "port", None),
            reset=bool(getattr(args, "reset_config", False)),
            source=source,
            route_specs=getattr(args, "route", ()),
        )
    return load_config(default_router_config_path(args.codex_home))


def _install(args: argparse.Namespace) -> int:
    codex_config = args.codex_home.expanduser() / "config.toml"
    source = codex_config.read_text(encoding="utf-8")
    config = _router_config(args, create=True, source=source)
    validate_upstreams(source, config)
    service = LaunchAgentService(config)
    service_started = False
    if not args.no_service:
        if not service.supported:
            raise ValueError(
                "automatic service installation is supported on macOS; "
                "use --no-service and run 'codex-provider-router serve'"
            )
        service.install()
        service_started = True
        if not _wait_for_health(config, timeout=10):
            service.stop()
            raise ValueError("router did not become healthy after service start")

    try:
        result = apply_router_config(
            source,
            config,
            default_model=args.model,
        )
        write_auth_metadata(config)
    except Exception:
        if service_started:
            service.stop()
        raise

    print(f"Router config: {config.path}")
    print(f"Codex config: {config.codex_config_path}")
    print(f"Default model: {result.selected_model}")
    if result.backup_path:
        print(f"Backup: {result.backup_path}")
    if args.no_service:
        print(f"Start it with: {sys.executable} -m codex_provider_router serve")
    else:
        print("Service: installed and running")
    print("Restart the Codex desktop app to reload the model catalog.")
    return 0


def _status(args: argparse.Namespace) -> int:
    config = _router_config(args)
    source = config.codex_config_path.read_text(encoding="utf-8")
    import tomllib

    codex = tomllib.loads(source)
    service = LaunchAgentService(config).status()
    health = _health(config)
    payload = {
        "version": __version__,
        "router_config": str(config.path),
        "codex_config": str(config.codex_config_path),
        "codex_model": codex.get("model"),
        "codex_provider": codex.get("model_provider"),
        "router_health": health,
        "service": service,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if health.get("status") == "ok" else 1


def _check(args: argparse.Namespace) -> int:
    config = _router_config(args)
    source = config.codex_config_path.read_text(encoding="utf-8")
    validate_upstreams(source, config)
    catalog = validate_runtime(config, credentials=True)
    aliases = [model.get("slug") for model in catalog.get("models", [])]
    if aliases != [route.alias for route in config.public_routes]:
        raise ValueError("router catalog does not match configured public routes")
    print("Router config, model catalog, and upstream credentials are valid.")
    print("No model request was sent.")
    return 0


def _catalog(args: argparse.Namespace) -> int:
    config = _router_config(args)
    catalog = validate_runtime(config)
    print(json.dumps(catalog, ensure_ascii=False, indent=2))
    return 0


def _routes(args: argparse.Namespace) -> int:
    config = _router_config(args)
    for route in config.routes:
        visibility = "picker" if route.public else "alias"
        print(
            f"{route.alias:<36} {visibility:<7} "
            f"{route.provider}/{route.upstream_model}"
        )
    return 0


def _token(args: argparse.Namespace) -> int:
    print(ensure_client_token(_router_config(args)))
    return 0


def _uninstall(args: argparse.Namespace) -> int:
    config = _router_config(args)
    source = config.codex_config_path.read_text(encoding="utf-8")
    result = uninstall_router_config(source, config)
    if not args.keep_service:
        LaunchAgentService(config).remove()
    print(f"Codex model restored to: {result.selected_model}")
    if result.backup_path:
        print(f"Backup: {result.backup_path}")
    print("Restart the Codex desktop app to reload the direct provider configuration.")
    return 0


def _health(config: RouterConfig) -> dict[str, Any]:
    try:
        with urlopen(
            f"http://{config.host}:{config.port}/health",
            timeout=2,
        ) as response:
            payload = json.loads(response.read())
            return payload if isinstance(payload, dict) else {"status": "error"}
    except (OSError, URLError, ValueError, json.JSONDecodeError) as error:
        return {"status": "error", "error": type(error).__name__}


def _wait_for_health(config: RouterConfig, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _health(config).get("status") == "ok":
            return True
        time.sleep(0.2)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            return _install(args)
        if args.command == "serve":
            serve(_router_config(args))
            return 0
        if args.command == "status":
            return _status(args)
        if args.command == "check":
            return _check(args)
        if args.command == "catalog":
            return _catalog(args)
        if args.command == "routes":
            return _routes(args)
        if args.command == "token":
            return _token(args)
        if args.command == "uninstall":
            return _uninstall(args)
        parser.error(f"unknown command: {args.command}")
    except (
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as error:
        print(f"codex-provider-router: {error}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
