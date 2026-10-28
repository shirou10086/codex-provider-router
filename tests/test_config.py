from __future__ import annotations

import unittest

from codex_provider_router.config import routes_from_config


SOURCE = """
model = "model-a"
model_provider = "provider_a"

[model_providers.provider_a]
base_url = "https://provider-a.example/v1"
wire_api = "responses"

[model_providers.provider_b]
base_url = "https://provider-b.example/v1"
wire_api = "responses"
""".lstrip()


class RouteConfigTests(unittest.TestCase):
    def test_current_codex_model_becomes_public_route(self) -> None:
        routes = routes_from_config(SOURCE)
        self.assertEqual(routes[0]["alias"], "provider_a-model-a")
        self.assertTrue(routes[0]["public"])
        self.assertEqual(routes[1]["alias"], "model-a")
        self.assertFalse(routes[1]["public"])

    def test_explicit_routes_support_multiple_providers(self) -> None:
        routes = routes_from_config(
            SOURCE,
            (
                "fast=provider_a:model-a",
                "deep=provider_b:model-b",
            ),
        )
        public = [route for route in routes if route["public"]]
        self.assertEqual([route["alias"] for route in public], ["fast", "deep"])
        self.assertEqual(
            [(route["provider"], route["upstream_model"]) for route in public],
            [("provider_a", "model-a"), ("provider_b", "model-b")],
        )

    def test_invalid_route_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected"):
            routes_from_config(SOURCE, ("missing-colon",))


if __name__ == "__main__":
    unittest.main()
