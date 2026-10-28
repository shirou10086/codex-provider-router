from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_provider_router.config import Route, RouterConfig
from codex_provider_router.service import LABEL, launch_agent_payload


class ServiceTests(unittest.TestCase):
    def test_launch_agent_payload_uses_installed_module(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = RouterConfig(
                path=root / ".codex/provider-router/config.json",
                codex_home=root / ".codex",
                codex_bin="/usr/bin/true",
                host="127.0.0.1",
                port=49687,
                catalog_paths=(),
                routes=(
                    Route("model", "provider", "upstream", "Model", True),
                ),
            )
            payload = launch_agent_payload(config)
            self.assertEqual(payload["Label"], LABEL)
            self.assertIn("serve", payload["ProgramArguments"])
            self.assertIn("-m", payload["ProgramArguments"])
            self.assertTrue(payload["KeepAlive"])
            self.assertEqual(
                payload["EnvironmentVariables"]["CODEX_HOME"],
                str(config.codex_home),
            )


if __name__ == "__main__":
    unittest.main()
