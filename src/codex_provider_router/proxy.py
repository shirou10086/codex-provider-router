from __future__ import annotations

import copy
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import threading
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import tomllib

from .catalog import CatalogStore
from .config import RouterConfig


MAX_REQUEST_BYTES = 256 * 1024 * 1024
HOP_BY_HOP_HEADERS = {
    "authorization",
    "connection",
    "content-length",
    "cookie",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def ensure_client_token(config: RouterConfig) -> str:
    path = config.client_token_path
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    if not path.exists():
        token = secrets.token_urlsafe(48)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(token + "\n")
                handle.flush()
                os.fsync(handle.fileno())
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise ValueError(f"client token permissions are too broad: {oct(mode)}")
    token = path.read_text(encoding="utf-8").strip()
    if not token or any(character.isspace() for character in token):
        raise ValueError("client token is invalid")
    return token


class ProviderStore:
    def __init__(self, config: RouterConfig) -> None:
        self.config_path = config.codex_config_path
        self._lock = threading.Lock()
        self._stamp: tuple[int, int] | None = None
        self._providers: dict[str, dict[str, Any]] = {}

    def get(self, provider_id: str) -> dict[str, Any]:
        stat = self.config_path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            if stamp != self._stamp:
                payload = tomllib.loads(self.config_path.read_text(encoding="utf-8"))
                providers = payload.get("model_providers")
                if not isinstance(providers, dict):
                    raise ValueError("config.toml has no model_providers table")
                self._providers = providers
                self._stamp = stamp
            provider = self._providers.get(provider_id)
            if not isinstance(provider, dict):
                raise ValueError(f"missing upstream provider: {provider_id}")
            if provider.get("wire_api", "responses") != "responses":
                raise ValueError(f"provider {provider_id!r} is not Responses-compatible")
            return copy.deepcopy(provider)


class TokenStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[str, float, str]] = {}

    @staticmethod
    def _signature(provider: dict[str, Any]) -> str:
        material = {
            "auth": provider.get("auth"),
            "env_key": provider.get("env_key"),
            "experimental_bearer_token": provider.get("experimental_bearer_token"),
        }
        encoded = json.dumps(material, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def invalidate(self, provider_id: str) -> None:
        with self._lock:
            self._cache.pop(provider_id, None)

    def get(
        self,
        provider_id: str,
        provider: dict[str, Any],
        *,
        force: bool = False,
    ) -> str:
        signature = self._signature(provider)
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(provider_id)
            if not force and cached and cached[1] > now and cached[2] == signature:
                return cached[0]

        auth = provider.get("auth")
        refresh_ms = 300_000
        if isinstance(auth, dict):
            command = auth.get("command")
            args = auth.get("args", [])
            if not isinstance(command, str) or not command:
                raise ValueError(f"invalid auth command for {provider_id}")
            if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
                raise ValueError(f"invalid auth arguments for {provider_id}")
            timeout = max(float(auth.get("timeout_ms", 5000)) / 1000.0, 0.1)
            refresh_ms = int(auth.get("refresh_interval_ms", 300_000))
            result = subprocess.run(
                [command, *args],
                cwd=auth.get("cwd") or None,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
            token = result.stdout.strip()
            if result.returncode or not token or any(character.isspace() for character in token):
                raise ValueError(f"credential helper failed for {provider_id}")
        elif isinstance(provider.get("env_key"), str):
            token = os.environ.get(provider["env_key"], "").strip()
            if not token:
                raise ValueError(f"credential environment is missing for {provider_id}")
        elif isinstance(provider.get("experimental_bearer_token"), str):
            token = provider["experimental_bearer_token"].strip()
            if not token:
                raise ValueError(f"empty bearer token for {provider_id}")
        else:
            raise ValueError(f"unsupported authentication for {provider_id}")

        lifetime = max(refresh_ms / 1000.0, 1.0) if refresh_ms else 1.0
        with self._lock:
            self._cache[provider_id] = (token, now + lifetime, signature)
        return token


class ProxyApp:
    def __init__(self, config: RouterConfig) -> None:
        self.config = config
        self.providers = ProviderStore(config)
        self.tokens = TokenStore()
        self.catalog = CatalogStore(config)
        self.client_token = ensure_client_token(config)


class RouterServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: ProxyApp) -> None:
        self.app = app
        super().__init__(address, RouterHandler)


class RouterHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CodexProviderRouter/0.1"

    @property
    def app(self) -> ProxyApp:
        return self.server.app  # type: ignore[attr-defined,no-any-return]

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = "Bearer " + self.app.client_token
        return hmac.compare_digest(supplied, expected)

    def _send_bytes(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def _send_json(self, status: int, value: Any) -> None:
        self._send_bytes(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str, error_type: str = "router_error") -> None:
        self._send_json(status, {"error": {"message": message, "type": error_type}})

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
            try:
                for provider_id in self.app.config.provider_ids:
                    self.app.providers.get(provider_id)
                self.app.catalog.get()
                self._send_json(200, {"status": "ok"})
            except Exception:
                self._send_json(503, {"status": "error"})
            return
        if path in {"/v1/models", "/models"}:
            if not self._authorized():
                self._error(401, "invalid local router credential", "authentication_error")
                return
            try:
                self._send_json(200, self.app.catalog.get())
            except Exception as error:
                self._error(500, f"model catalog unavailable: {type(error).__name__}")
            return
        self._error(404, "route not found", "not_found")

    def do_POST(self) -> None:
        if not self._authorized():
            self._error(401, "invalid local router credential", "authentication_error")
            return
        path = urlsplit(self.path).path
        if not (path == "/v1/responses" or path.startswith("/v1/responses/")):
            self._error(404, "route not found", "not_found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._error(400, "invalid Content-Length", "invalid_request_error")
            return
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._error(
                413 if length > MAX_REQUEST_BYTES else 400,
                "invalid request size",
                "invalid_request_error",
            )
            return
        raw = self.rfile.read(length)
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            self._error(400, "request body must be JSON", "invalid_request_error")
            return
        alias = request.get("model")
        try:
            route = self.app.config.require_route(str(alias or ""))
        except ValueError as error:
            self._error(400, str(error), "invalid_request_error")
            return
        request["model"] = route.upstream_model
        body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        connection: http.client.HTTPConnection | None = None
        try:
            provider = self.app.providers.get(route.provider)
            connection, response = _open_upstream(
                self.app.tokens,
                route.provider,
                provider,
                "POST",
                self.path,
                body,
                self.headers,
            )
            content_type = response.getheader("Content-Type", "application/json")
            if response.status >= 400 or "text/event-stream" not in content_type.lower():
                payload = response.read()
                self.send_response(response.status, response.reason)
                for key, value in response.getheaders():
                    if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "content-length":
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)
                self.close_connection = True
                return

            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "content-length":
                    self.send_header(key, value)
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while chunk := response.read1(65_536):
                    self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                    self.wfile.write(chunk)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True
        except (
            OSError,
            ValueError,
            subprocess.SubprocessError,
            json.JSONDecodeError,
            socket.timeout,
        ) as error:
            if not self.wfile.closed:
                try:
                    self._error(
                        502,
                        f"upstream unavailable: {type(error).__name__}",
                        "upstream_error",
                    )
                except (BrokenPipeError, ConnectionResetError):
                    pass
        finally:
            if connection is not None:
                connection.close()


def _open_upstream(
    tokens: TokenStore,
    provider_id: str,
    provider: dict[str, Any],
    method: str,
    path: str,
    body: bytes,
    inbound_headers: Any,
) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    base, target = upstream_target(provider, path)
    timeout = max(float(provider.get("stream_idle_timeout_ms", 300_000)) / 1000.0, 5.0)
    port = base.port or (443 if base.scheme == "https" else 80)
    token = tokens.get(provider_id, provider)
    headers = outbound_headers(inbound_headers, provider, token)

    def request(current_token: str) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        current_headers = dict(headers)
        current_headers["Authorization"] = f"Bearer {current_token}"
        if base.scheme == "https":
            connection: http.client.HTTPConnection = http.client.HTTPSConnection(
                base.hostname,
                port,
                timeout=timeout,
                context=ssl.create_default_context(),
            )
        else:
            connection = http.client.HTTPConnection(base.hostname, port, timeout=timeout)
        connection.request(method, target, body=body, headers=current_headers)
        return connection, connection.getresponse()

    connection, response = request(token)
    if response.status != 401:
        return connection, response
    response.read()
    connection.close()
    tokens.invalidate(provider_id)
    refreshed = tokens.get(provider_id, provider, force=True)
    return request(refreshed)


def upstream_target(
    provider: dict[str, Any], inbound_path: str
) -> tuple[Any, str]:
    base = urlsplit(str(provider.get("base_url", "")))
    if base.scheme not in {"http", "https"} or not base.hostname:
        raise ValueError("upstream provider has an invalid base_url")
    inbound = urlsplit(inbound_path)
    suffix = inbound.path
    if suffix.startswith("/v1"):
        suffix = suffix[3:]
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    path = base.path.rstrip("/") + suffix
    query = list(parse_qsl(inbound.query, keep_blank_values=True))
    configured_query = provider.get("query_params", {})
    if isinstance(configured_query, dict):
        query.extend((str(key), str(value)) for key, value in configured_query.items())
    if query:
        path += "?" + urlencode(query)
    return base, path


def outbound_headers(
    inbound_headers: Any,
    provider: dict[str, Any],
    token: str,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in inbound_headers.items():
        if key.lower() not in HOP_BY_HOP_HEADERS:
            headers[key] = value
    headers["Authorization"] = f"Bearer {token}"
    headers["Accept-Encoding"] = "identity"
    configured = provider.get("http_headers", {})
    if isinstance(configured, dict):
        headers.update({str(key): str(value) for key, value in configured.items()})
    environment_headers = provider.get("env_http_headers", {})
    if isinstance(environment_headers, dict):
        for header, variable in environment_headers.items():
            value = os.environ.get(str(variable))
            if value:
                headers[str(header)] = value
    return headers


def validate_runtime(config: RouterConfig, *, credentials: bool = False) -> dict[str, Any]:
    app = ProxyApp(config)
    for provider_id in config.provider_ids:
        provider = app.providers.get(provider_id)
        if credentials:
            app.tokens.get(provider_id, provider)
    catalog = app.catalog.get()
    return catalog


def serve(config: RouterConfig) -> None:
    app = ProxyApp(config)
    for provider_id in config.provider_ids:
        app.providers.get(provider_id)
    app.catalog.get()
    server = RouterServer((config.host, config.port), app)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
