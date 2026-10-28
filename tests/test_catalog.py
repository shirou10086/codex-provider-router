from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codex_provider_router.catalog import CatalogStore
from codex_provider_router.config import Route, RouterConfig


class CatalogTests(unittest.TestCase):
    def test_public_routes_replace_upstream_slugs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            codex = root / "codex"
            codex.write_text(
                "#!/bin/sh\n"
                "printf '%s' '"
                + json.dumps(
                    {
                        "models": [
                            {
                                "slug": "model-a",
                                "display_name": "Model A",
                                "default_reasoning_level": "medium",
                                "supported_reasoning_levels": [
                                    {"effort": "low", "description": "Low"}
                                ],
                            }
                        ]
                    }
                ).replace("'", "'\\''")
                + "'\n",
                encoding="utf-8",
            )
            codex.chmod(0o700)
            extra = root / "models.json"
            extra.write_text(
                json.dumps(
                    {
                        "models": [
                            {
                                "slug": "model-b",
                                "display_name": "Model B",
                                "default_reasoning_level": "high",
                                "supported_reasoning_levels": [
                                    {"effort": "high", "description": "High"}
                                ],
                                "service_tiers": ["standard"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            config = RouterConfig(
                path=root / ".codex/provider-router/config.json",
                codex_home=root / ".codex",
                codex_bin=str(codex),
                host="127.0.0.1",
                port=49687,
                catalog_paths=(extra,),
                routes=(
                    Route(
                        "primary-model",
                        "provider-a",
                        "model-a",
                        "Provider A - Model A",
                        True,
                    ),
                    Route(
                        "secondary-model",
                        "provider-b",
                        "model-b",
                        "Provider B - Model B",
                        True,
                    ),
                    Route(
                        "model-a",
                        "provider-a",
                        "model-a",
                        "Direct",
                        False,
                    ),
                ),
            )
            catalog = CatalogStore(config).get()
            self.assertEqual(
                [model["slug"] for model in catalog["models"]],
                ["primary-model", "secondary-model"],
            )
            self.assertEqual(catalog["models"][1]["display_name"], "Provider B - Model B")
            self.assertNotIn("service_tiers", catalog["models"][1])


if __name__ == "__main__":
    unittest.main()
