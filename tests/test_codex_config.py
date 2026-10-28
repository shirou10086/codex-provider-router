from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

from codex_provider_router.codex_config import (
    apply_router_config,
    uninstall_router_config,
    validate_upstreams,
)
from codex_provider_router.config import Route, RouterConfig


SOURCE = """
model = "model-a"
model_provider = "provider-a"
model_reasoning_effort = "high"
service_tier = "default"

[model_providers.provider-a]
name = "Provider A"
base_url = "https://provider-a.example/v1"
wire_api = "responses"

[model_providers.provider-b]
name = "Provider B"
base_url = "https://provider-b.example"
wire_api = "responses"

[model_providers.provider-b.http_headers]
x-source = "router"
""".lstrip()


class CodexConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.codex_home = self.root / ".codex"
        self.codex_home.mkdir()
        self.config_path = self.codex_home / "provider-router" / "config.json"
        self.config_path.parent.mkdir(parents=True)
        self.codex_config = self.codex_home / "config.toml"
        self.codex_config.write_text(SOURCE, encoding="utf-8")
        self.codex_config.chmod(0o600)
        self.router = RouterConfig(
            path=self.config_path,
            codex_home=self.codex_home,
            codex_bin="/usr/bin/true",
            host="127.0.0.1",
            port=49687,
            catalog_paths=(),
            routes=(
                Route(
                    "primary-model",
                    "provider-a",
                    "model-a",
                    "Provider A",
                    True,
                ),
                Route(
                    "secondary-model",
                    "provider-b",
                    "model-b",
                    "Provider B",
                    True,
                ),
                Route(
                    "model-a",
                    "provider-a",
                    "model-a",
                    "Provider A - Model A",
                    False,
                ),
            ),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_install_and_uninstall_round_trip(self) -> None:
        validate_upstreams(SOURCE, self.router)
        result = apply_router_config(SOURCE, self.router)
        self.assertTrue(result.changed)
        installed = tomllib.loads(self.codex_config.read_text(encoding="utf-8"))
        self.assertEqual(installed["model"], "primary-model")
        self.assertEqual(installed["model_provider"], "local-router")
        self.assertNotIn("service_tier", installed)
        self.assertEqual(
            installed["model_providers"]["provider-a"]["wire_api"],
            "responses",
        )
        router_provider = installed["model_providers"]["local-router"]
        self.assertEqual(router_provider["base_url"], "http://127.0.0.1:49687/v1")
        self.assertEqual(router_provider["wire_api"], "responses")
        self.assertTrue(result.backup_path and result.backup_path.is_file())

        restored = uninstall_router_config(
            self.codex_config.read_text(encoding="utf-8"),
            self.router,
        )
        self.assertTrue(restored.changed)
        payload = tomllib.loads(self.codex_config.read_text(encoding="utf-8"))
        self.assertEqual(payload["model"], "model-a")
        self.assertEqual(payload["model_provider"], "provider-a")
        self.assertNotIn("local-router", payload["model_providers"])

    def test_install_is_idempotent(self) -> None:
        apply_router_config(SOURCE, self.router)
        installed = self.codex_config.read_text(encoding="utf-8")
        second = apply_router_config(installed, self.router)
        self.assertFalse(second.changed)
        self.assertIsNone(second.backup_path)

    def test_rejects_non_responses_provider(self) -> None:
        invalid = SOURCE.replace(
            'wire_api = "responses"\n\n[model_providers.provider-b]',
            'wire_api = "chat"\n\n[model_providers.provider-b]',
        )
        with self.assertRaisesRegex(ValueError, "responses wire API"):
            validate_upstreams(invalid, self.router)


if __name__ == "__main__":
    unittest.main()
