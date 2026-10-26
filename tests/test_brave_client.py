"""Offline tests for Brave result filtering."""
import unittest
from unittest.mock import AsyncMock

from brave_client import BraveClient, canonical_url


class BraveClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_search(self):
        client = BraveClient("test-token")
        client._get = AsyncMock(return_value={"web": {"results": [
            {"title": "Article", "url": "https://example.com/a?utm_source=x"},
            {"title": "", "url": "https://example.com/b"},
        ]}})
        hits = await client.discover("AI")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].url, "https://example.com/a")

    async def test_context_rejects_wrong_article(self):
        client = BraveClient("test-token")
        client._get = AsyncMock(return_value={"grounding": {"generic": [
            {"url": "https://example.com/other", "snippets": ["Incorrect article"]}
        ]}})
        self.assertIsNone(await client.context_for("Test", "https://example.com/actual"))

    def test_tracking_params(self):
        self.assertEqual(canonical_url("https://example.com/a?utm_source=x"), "https://example.com/a")


if __name__ == "__main__":
    unittest.main()
