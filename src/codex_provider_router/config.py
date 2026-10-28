from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import tomllib
from typing import Any


CONFIG_VERSION = 1
DEFAULT_PORT = 49687
ROUTER_PROVIDER_ID = "local-router"
ROUTER_HOME_NAME = "provider-router"
ROUTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class Route:
    alias: str
    provider: str
    upstream_model: str
    display_name: str
    public: bool = False


@dataclass(frozen=True)
class RouterConfig:
    path: Path
    codex_home: Path
    codex_bin: str
    host: str
    port: int
    catalog_paths: tuple[Path, ...]
    routes: tuple[Route, ...]

    @property
    def router_home(self) -> Path:
        return self.codex_home / ROUTER_HOME_NAME

    @property
    def codex_config_path(self) -> Path:
        return self.codex_home / "config.toml"

    @property
    def client_token_path(self) -> Path:
        return self.router_home / "client-token"

    @property
    def route_map(self) -> dict[str, Route]:
        return {route.alias: route for route in self.routes}

    @property
    def public_routes(self) -> tuple[Route, ...]:
        return tuple(route for route in self.routes if route.public)

    @property
    def provider_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(route.provider for route in self.routes))

    @property
    def default_model(self) -> str:
        if not self.public_routes:
            raise ValueError("router config has no public models")
        return self.public_routes[0].alias

    def require_route(self, alias: str) -> Route:
        route = self.route_map.get(alias)
        if route is None:
            raise ValueError(f"unknown router model alias: {alias!r}")
        return route


def default_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def default_router_config_path(codex_home: Path) -> Path:
    return codex_home.expanduser() / ROUTER_HOME_NAME / "config.json"


def find_codex_bin() -> str:
    override = os.environ.get("CODEX_PROVIDER_ROUTER_CODEX")
    if override:
        return override
    for candidate in (
        shutil.which("codex"),
        "/opt/homebrew/bin/codex",
        "/usr/local/bin/codex",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    raise ValueError("Codex CLI was not found; install Codex or set CODEX_PROVIDER_ROUTER_CODEX")


def discover_catalog_paths(codex_home: Path) -> tuple[Path, ...]:
    candidates: list[Path] = []
    profiles = [codex_home / "config.toml", *sorted(codex_home.glob("*.config.toml"))]
    for profile in dict.fromkeys(profiles):
        try:
            import tomllib

            payload = tomllib.loads(profile.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        value = payload.get("model_catalog_json")
        if isinstance(value, str) and value.strip():
            candidates.append(Path(value).expanduser())
    candidates.append(Path.home() / ".llmbox" / "config" / "codex_models_cache.json")

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        normalized = candidate.expanduser()
        if normalized in seen or not normalized.is_file():
            continue
        seen.add(normalized)
        unique.append(normalized)
    return tuple(unique)


def default_config(
    codex_home: Path,
    *,
    port: int = DEFAULT_PORT,
    codex_bin: str | None = None,
    catalog_paths: tuple[Path, ...] | None = None,
    routes: list[dict[str, Any]],
) -> dict[str, Any]:
    codex_home = codex_home.expanduser()
    catalogs = discover_catalog_paths(codex_home) if catalog_paths is None else catalog_paths
    return {
        "version": CONFIG_VERSION,
        "codex_bin": codex_bin or find_codex_bin(),
        "host": "127.0.0.1",
        "port": port,
        "catalog_paths": [str(path.expanduser()) for path in catalogs],
        "routes": routes,
    }


def routes_from_config(
    source: str | None,
    route_specs: list[str] | tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    parsed: list[tuple[str, str, str, str]] = []
    explicit: list[tuple[str, str, str, str]] = []
    for spec in route_specs:
        left, separator, target = spec.partition("=")
        if not separator:
            target = left
            left = ""
        provider, colon, model = target.partition(":")
        provider = provider.strip()
        model = model.strip()
        if not colon or not provider or not model:
            raise ValueError(
                f"invalid route {spec!r}; expected [alias=]provider:model"
            )
        alias = left.strip() or _default_alias(provider, model)
        explicit.append((alias, provider, model, f"{provider} - {model}"))

    if explicit:
        parsed.extend(explicit)
    elif source:
        payload = tomllib.loads(source)
        provider = str(payload.get("model_provider") or "").strip()
        model = str(payload.get("model") or "").strip()
        if not provider or not model:
            raise ValueError(
                "the current Codex config has no model/provider; pass --route"
            )
        if provider == ROUTER_PROVIDER_ID:
            raise ValueError(
                "local-router is already active; use --reset-config and pass --route"
            )
        parsed.append(
            (
                _default_alias(provider, model),
                provider,
                model,
                f"{provider} - {model}",
            )
        )
    else:
        raise ValueError("at least one --route value is required")

    routes: list[dict[str, Any]] = []
    aliases: set[str] = set()
    for alias, provider, model, display_name in parsed:
        if not ROUTE_RE.fullmatch(alias):
            raise ValueError(f"invalid route alias: {alias!r}")
        if alias in aliases:
            raise ValueError(f"duplicate route alias: {alias}")
        if not provider or any(character.isspace() for character in provider):
            raise ValueError(f"invalid provider for route {alias}")
        if not model or any(character.isspace() for character in model):
            raise ValueError(f"invalid model for route {alias}")
        route = {
            "alias": alias,
            "provider": provider,
            "upstream_model": model,
            "display_name": display_name,
            "public": True,
        }
        routes.append(route)
        aliases.add(alias)

    provider_models: set[tuple[str, str]] = set()
    for route in list(routes):
        provider_model = (route["provider"], route["upstream_model"])
        if provider_model in provider_models:
            continue
        provider_models.add(provider_model)
        model = route["upstream_model"]
        if model in aliases:
            continue
        routes.append(
            {
                "alias": model,
                "provider": route["provider"],
                "upstream_model": model,
                "display_name": route["display_name"],
                "public": False,
            }
        )
        aliases.add(model)
    return routes


def _default_alias(provider: str, model: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{provider}-{model}").strip("-")
    return value.lower() or "default"


def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_config(path: Path, payload: dict[str, Any]) -> None:
    atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def load_config(path: Path, *, codex_home: Path | None = None) -> RouterConfig:
    path = path.expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"cannot read router config {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid router JSON at {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError("router config must be a JSON object")
    if payload.get("version") != CONFIG_VERSION:
        raise ValueError(f"unsupported router config version: {payload.get('version')!r}")

    host = str(payload.get("host") or "127.0.0.1")
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("router host must be a loopback address")
    port = int(payload.get("port", DEFAULT_PORT))
    if not 1 <= port <= 65535:
        raise ValueError("router port must be between 1 and 65535")

    effective_codex_home = (codex_home or path.parent.parent).expanduser()
    raw_catalogs = payload.get("catalog_paths") or []
    if not isinstance(raw_catalogs, list):
        raise ValueError("catalog_paths must be a list")
    catalog_paths: list[Path] = []
    for item in raw_catalogs:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("catalog path must be a non-empty string")
        candidate = Path(item).expanduser()
        if not candidate.is_absolute():
            candidate = path.parent / candidate
        catalog_paths.append(candidate)

    raw_routes = payload.get("routes")
    if not isinstance(raw_routes, list) or not raw_routes:
        raise ValueError("routes must be a non-empty list")
    routes: list[Route] = []
    aliases: set[str] = set()
    for item in raw_routes:
        if not isinstance(item, dict):
            raise ValueError("each route must be an object")
        alias = str(item.get("alias") or "").strip()
        provider = str(item.get("provider") or "").strip()
        model = str(item.get("upstream_model") or "").strip()
        display_name = str(item.get("display_name") or alias).strip()
        if not ROUTE_RE.fullmatch(alias):
            raise ValueError(f"invalid route alias: {alias!r}")
        if alias in aliases:
            raise ValueError(f"duplicate route alias: {alias}")
        if not provider or any(character.isspace() for character in provider):
            raise ValueError(f"invalid provider for route {alias}")
        if not model or any(character.isspace() for character in model):
            raise ValueError(f"invalid upstream model for route {alias}")
        aliases.add(alias)
        routes.append(
            Route(
                alias=alias,
                provider=provider,
                upstream_model=model,
                display_name=display_name,
                public=bool(item.get("public", False)),
            )
        )
    if not any(route.public for route in routes):
        raise ValueError("at least one route must be public")

    raw_codex_bin = str(payload.get("codex_bin") or find_codex_bin()).strip()
    codex_bin_path = Path(raw_codex_bin).expanduser()
    if not codex_bin_path.is_file():
        resolved = shutil.which(raw_codex_bin)
        if not resolved:
            raise ValueError(f"Codex executable was not found: {raw_codex_bin}")
        codex_bin_path = Path(resolved)

    return RouterConfig(
        path=path,
        codex_home=effective_codex_home,
        codex_bin=str(codex_bin_path.resolve()),
        host=host,
        port=port,
        catalog_paths=tuple(catalog_paths),
        routes=tuple(routes),
    )


def ensure_config(
    codex_home: Path,
    *,
    port: int | None = None,
    reset: bool = False,
    source: str | None = None,
    route_specs: list[str] | tuple[str, ...] = (),
) -> RouterConfig:
    codex_home = codex_home.expanduser()
    path = default_router_config_path(codex_home)
    if reset or not path.exists():
        payload = default_config(
            codex_home,
            port=port or DEFAULT_PORT,
            routes=routes_from_config(source, route_specs),
        )
        write_config(path, payload)
    return load_config(path, codex_home=codex_home)
