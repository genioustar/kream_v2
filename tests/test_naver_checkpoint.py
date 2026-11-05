import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import config
from common.models import MarketplaceProduct
from common.pacing import CrawlBlockedError
from naver.checkpoint import load_checkpoint, save_checkpoint
from naver.crawler import crawl_marketplace


class NaverCheckpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        patcher = patch.object(config, "OUTPUT_DIR", directory.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.site = {"site_name": "first", "url": "https://smartstore.naver.com/first/search?q=a",
                     "type": "smart_store", "model_strategy": "auto"}
        self.product = MarketplaceProduct("first", "Product ABC123", "ABC123", 12000,
                                          "https://smartstore.naver.com/first/products/1",
                                          date.today().isoformat() + "T09:00:00")

    def test_roundtrip_and_changed_search_conditions_require_new_collection(self):
        self.assertIsNone(load_checkpoint(self.site))
        path = save_checkpoint(self.site, [self.product])
        self.assertEqual(load_checkpoint(self.site), [self.product])
        self.assertIsNone(load_checkpoint({**self.site, "url": self.site["url"] + "b"}))
        self.assertIsNone(load_checkpoint({**self.site, "model_strategy": "bracket_first"}))
        self.assertIsNone(load_checkpoint({**self.site, "type": "brand_store"}))
        self.assertIsNone(load_checkpoint({**self.site, "navigation": "store_search_ui"}))
        self.assertNotIn("?q=", path.read_text())
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_corrupt_incomplete_and_old_data_are_not_reused(self):
        for content in ('{', '[]', '{"version":1,"status":"in_progress","products":[]}',
                        '{"version":1,"status":"complete","products":[{}]}'):
            path = save_checkpoint(self.site, [self.product])
            path.write_text(content)
            self.assertIsNone(load_checkpoint(self.site))
        self.product.crawled_at = (date.today() - timedelta(days=1)).isoformat() + "T09:00:00"
        save_checkpoint(self.site, [self.product])
        self.assertIsNone(load_checkpoint(self.site))

    def test_empty_success_is_distinct_from_cache_miss(self):
        save_checkpoint(self.site, [])
        self.assertEqual(load_checkpoint(self.site), [])

    def test_partial_files_are_not_final_pipeline_inputs(self):
        path = save_checkpoint(self.site, [self.product])
        day_dir = self.directory / date.today().strftime("%Y%m%d")
        self.assertTrue(path.is_relative_to(day_dir / "checkpoints" / "naver"))
        self.assertEqual(list(day_dir.glob("*_products.json")), [])

    async def test_all_completed_stores_do_not_open_browser(self):
        save_checkpoint(self.site, [self.product])
        with patch.object(config, "SEARCH_URLS", [self.site]), \
             patch("naver.crawler.create_browser") as browser:
            self.assertEqual(await crawl_marketplace(), [self.product])
        browser.assert_not_called()

    async def test_resume_skips_success_and_failed_store_has_no_checkpoint(self):
        second = {**self.site, "site_name": "second", "url": "https://smartstore.naver.com/second/best"}
        third = {**self.site, "site_name": "third", "url": "https://smartstore.naver.com/third/best"}
        manager = MagicMock(__aenter__=AsyncMock(return_value=MagicMock()),
                            __aexit__=AsyncMock(return_value=False))
        crawl = AsyncMock(side_effect=[[self.product], CrawlBlockedError("http_429")])
        with patch.object(config, "SEARCH_URLS", [self.site, second, third]), \
             patch("naver.crawler.create_browser", return_value=manager), \
             patch("naver.crawler._crawl_site", crawl), \
             patch("naver.crawler.pause", new=AsyncMock()):
            with self.assertRaises(CrawlBlockedError):
                await crawl_marketplace()
        self.assertEqual(crawl.await_count, 2)
        self.assertEqual(load_checkpoint(self.site), [self.product])
        self.assertIsNone(load_checkpoint(second))
        self.assertIsNone(load_checkpoint(third))
        crawl = AsyncMock(side_effect=CrawlBlockedError("still blocked"))
        with patch.object(config, "SEARCH_URLS", [self.site, second, third]), \
             patch("naver.crawler.create_browser", return_value=manager), \
             patch("naver.crawler._crawl_site", crawl):
            with self.assertRaises(CrawlBlockedError):
                await crawl_marketplace()
        self.assertEqual(crawl.await_count, 1)
        self.assertEqual(crawl.call_args.args[1], second)
