"""Tests for the guarded, deny-by-default web-search client (deep research)."""
import json
import unittest

from app.control_plane.web_search import (
    WebResult,
    WebSearchClient,
    WebSearchConfig,
    parse_web_search_config,
)


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self.body = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()


def _cfg(**kw):
    d = dict(provider="p", url="https://search.example/api?q=",
             allowed_hosts=frozenset({"search.example"}), top_k=5)
    d.update(kw)
    return WebSearchConfig(**d)


class TestParse(unittest.TestCase):
    def test_none_when_unset(self):
        self.assertIsNone(parse_web_search_config(None))
        self.assertIsNone(parse_web_search_config(""))

    def test_none_on_bad_json(self):
        self.assertIsNone(parse_web_search_config("{not json"))

    def test_none_without_url_or_host(self):
        self.assertIsNone(parse_web_search_config(json.dumps({"url": "https://x"})))
        self.assertIsNone(parse_web_search_config(json.dumps({"host": "x"})))

    def test_parses_and_pins_host(self):
        c = parse_web_search_config(json.dumps({
            "provider": "acme", "url": "https://api.acme.dev/s?q=", "host": "api.acme.dev",
            "api_key": "k", "top_k": 3}))
        self.assertEqual(c.provider, "acme")
        self.assertEqual(c.allowed_hosts, frozenset({"api.acme.dev"}))
        self.assertEqual(c.top_k, 3)


class TestSearch(unittest.TestCase):
    def _client(self, resp, capture=None, **cfg):
        def opener(url, headers):
            if capture is not None:
                capture["url"] = url
                capture["headers"] = headers
            return resp
        return WebSearchClient(_cfg(api_key="secret", api_key_header="X-Key", **cfg), opener=opener)

    def test_parses_results_shape(self):
        r = _Resp(200, {"results": [{"title": "T", "url": "https://a/1", "snippet": "s"}]})
        out = self._client(r).search("q")
        self.assertEqual(out, [WebResult(title="T", url="https://a/1", snippet="s")])

    def test_parses_organic_and_webpages_shapes(self):
        r1 = _Resp(200, {"organic": [{"title": "O", "link": "https://a/2", "snippet": "x"}]})
        self.assertEqual(self._client(r1).search("q")[0].url, "https://a/2")
        r2 = _Resp(200, {"webPages": {"value": [{"name": "B", "url": "https://a/3", "snippet": "y"}]}})
        self.assertEqual(self._client(r2).search("q")[0].title, "B")

    def test_query_encoded_and_apikey_header_sent(self):
        cap = {}
        self._client(_Resp(200, {"results": []}), cap).search("a b")
        self.assertIn("a%20b", cap["url"])          # query URL-encoded
        self.assertEqual(cap["headers"]["X-Key"], "secret")  # api key attached

    def test_non_2xx_returns_empty(self):
        self.assertEqual(self._client(_Resp(500, b"err")).search("q"), [])

    def test_bad_body_returns_empty(self):
        self.assertEqual(self._client(_Resp(200, b"not json")).search("q"), [])

    def test_top_k_cap(self):
        items = [{"title": str(i), "url": f"https://a/{i}", "snippet": "s"} for i in range(10)]
        c = WebSearchClient(_cfg(top_k=2), opener=lambda u, h: _Resp(200, {"results": items}))
        self.assertEqual(len(c.search("q")), 2)

    def test_guard_blocks_disallowed_host(self):
        # Real guard path (no injected opener): a URL host outside allowed_hosts is
        # rejected BEFORE any network/DNS, and search swallows it as []. This is
        # the SSRF protection — deny-by-default egress.
        c = WebSearchClient(WebSearchConfig(
            provider="p", url="https://evil.example/s?q=",
            allowed_hosts=frozenset({"good.example"})))
        self.assertEqual(c.search("q"), [])


if __name__ == "__main__":
    unittest.main()
