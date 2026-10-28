from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from codex_provider_router.config import Route, RouterConfig
from codex_provider_router.proxy import ProxyApp, RouterServer, ensure_client_token


class UpstreamHandler(BaseHTTPRequestHandler):
    status = 200
    requests: list[dict] = []

    def log_message(self, format: str, *args) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        type(self).requests.append(payload)
        body = json.dumps({"ok": True}).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.codex_home = self.root / ".codex"
        self.codex_home.mkdir()

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        self.upstream = upstream
        self.upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        self.upstream_thread.start()

        self.codex_config = self.codex_home / "config.toml"
        self.codex_config.write_text(
            f"""
[model_providers.test-provider]
base_url = "http://127.0.0.1:{upstream.server_port}"
wire_api = "responses"
experimental_bearer_token = "upstream-secret"
""".lstrip(),
            encoding="utf-8",
        )
        self.codex_config.chmod(0o600)

        codex = self.root / "codex"
        catalog = {
            "models": [
                {
                    "slug": "model-b",
                    "display_name": "Model B",
                    "default_reasoning_level": "medium",
                    "supported_reasoning_levels": [],
                }
            ]
        }
        codex.write_text(
            "#!/bin/sh\nprintf '%s' '"
            + json.dumps(catalog).replace("'", "'\\''")
            + "'\n",
            encoding="utf-8",
        )
        codex.chmod(0o700)
        self.config = RouterConfig(
            path=self.codex_home / "provider-router" / "config.json",
            codex_home=self.codex_home,
            codex_bin=str(codex),
            host="127.0.0.1",
            port=49687,
            catalog_paths=(),
            routes=(
                Route(
                    "secondary-model",
                    "test-provider",
                    "model-b",
                    "Test Model B",
                    True,
                ),
                Route(
                    "model-b",
                    "test-provider",
                    "model-b",
                    "Test Model B",
                    False,
                ),
            ),
        )
        self.app = ProxyApp(self.config)
        self.server = RouterServer(("127.0.0.1", 0), self.app)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.token = ensure_client_token(self.config)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.temporary.cleanup()

    def test_health_catalog_auth_and_model_rewrite(self) -> None:
        with urlopen(self.base_url + "/health", timeout=2) as response:
            self.assertEqual(json.loads(response.read()), {"status": "ok"})

        with self.assertRaises(HTTPError) as unauthenticated:
            urlopen(self.base_url + "/v1/models", timeout=2)
        self.assertEqual(unauthenticated.exception.code, 401)

        request = Request(
            self.base_url + "/v1/responses",
            data=json.dumps({"model": "secondary-model", "input": "hi"}).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            self.assertEqual(json.loads(response.read()), {"ok": True})
        self.assertEqual(UpstreamHandler.requests[-1]["model"], "model-b")

    def test_forbidden_is_not_retried(self) -> None:
        UpstreamHandler.status = 403
        before = len(UpstreamHandler.requests)
        request = Request(
            self.base_url + "/v1/responses",
            data=json.dumps({"model": "model-b"}).encode(),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as forbidden:
            urlopen(request, timeout=2)
        self.assertEqual(forbidden.exception.code, 403)
        self.assertEqual(len(UpstreamHandler.requests), before + 1)
        UpstreamHandler.status = 200


if __name__ == "__main__":
    unittest.main()
