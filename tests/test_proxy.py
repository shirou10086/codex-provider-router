from __future__ import annotations

import unittest

from codex_provider_router.proxy import outbound_headers, upstream_target


class DummyHeaders:
    def items(self):
        return [
            ("Authorization", "Bearer client-token"),
            ("Content-Type", "application/json"),
            ("Connection", "keep-alive"),
            ("X-Request", "abc"),
        ]


class ProxyTests(unittest.TestCase):
    def test_upstream_target_preserves_v1_suffix_and_query(self) -> None:
        provider = {
            "base_url": "https://provider.example/gateway",
            "query_params": {"tenant": "example"},
        }
        base, target = upstream_target(provider, "/v1/responses?trace=1")
        self.assertEqual(base.scheme, "https")
        self.assertEqual(target, "/gateway/responses?trace=1&tenant=example")

    def test_outbound_headers_replace_credentials(self) -> None:
        provider = {"http_headers": {"x-source": "internal"}}
        headers = outbound_headers(DummyHeaders(), provider, "upstream-token")
        self.assertEqual(headers["Authorization"], "Bearer upstream-token")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["x-source"], "internal")
        self.assertNotIn("Connection", headers)


if __name__ == "__main__":
    unittest.main()
