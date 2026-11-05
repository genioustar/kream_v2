import unittest
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import config
import main as pipeline
from common.pacing import CrawlBlockedError, check_response, pause
from kream.crawler import (
    iter_kream_results, search_kream, _find_verified_kream_product,
    _record_kream_response, _search_kream_once,
)
from naver.crawler import crawl_marketplace


class SequentialCrawlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_kream_results_are_saved_before_stopping(self):
        async def results(*args):
            yield "A", []
            raise CrawlBlockedError("blocked")
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=MagicMock())
        manager.__aexit__ = AsyncMock(return_value=False)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "OUTPUT_DIR", directory), \
             patch("main._load_all_products", return_value=[MagicMock(model_name="A")]), \
             patch("main.discover_dates", return_value=[]), \
             patch("main.create_browser", return_value=manager), \
             patch("main.iter_kream_results", results), \
             patch("main.find_arbitrage", return_value=[]) as compare:
            with self.assertRaises(CrawlBlockedError):
                await pipeline.main("kream")
            output = Path(directory) / date.today().strftime("%Y%m%d") / "arbitrage_results.json"
            self.assertEqual(output.read_text(), "[]")
            self.assertEqual(compare.call_args.args[1], {"A": []})

    async def test_connection_failure_preserves_previous_output(self):
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(side_effect=RuntimeError("connection failed"))
        manager.__aexit__ = AsyncMock(return_value=False)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "OUTPUT_DIR", directory), \
             patch("main._load_all_products", return_value=[MagicMock(model_name="A")]), \
             patch("main.discover_dates", return_value=[]), \
             patch("main.create_browser", return_value=manager):
            output = Path(directory) / date.today().strftime("%Y%m%d") / "arbitrage_results.json"
            output.parent.mkdir()
            output.write_text('[{"previous": true}]')
            with self.assertRaisesRegex(RuntimeError, "connection failed"):
                await pipeline.main("kream")
            self.assertEqual(output.read_text(), '[{"previous": true}]')

    async def test_no_changed_models_does_not_open_browser(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "OUTPUT_DIR", directory), \
             patch("main.FULL_CRAWL_DAYS", frozenset()), \
             patch("main._load_all_products", return_value=[MagicMock(model_name="A")]), \
             patch("main.discover_dates", return_value=["20260917", "20260918"]), \
             patch("main.diff_date_pair", return_value=[]), \
             patch("main.create_browser") as browser:
            await pipeline.main("kream")
        browser.assert_not_called()

    async def test_stores_finish_and_pause_before_next_store(self):
        events = []
        sites = [{"site_name": name, "url": "https://example.com", "type": "smart_store"}
                 for name in ("first", "second", "third")]
        async def crawl(context, site, lock):
            events.append(site["site_name"])
            return [site["site_name"]]
        async def rest(*args):
            self.assertEqual(args[:2], (1.0, 3.0))
            events.append("pause")
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=MagicMock())
        manager.__aexit__ = AsyncMock(return_value=False)
        with patch.object(config, "SEARCH_URLS", sites), \
             patch("naver.crawler.create_browser", return_value=manager), \
             patch("naver.crawler.load_checkpoint", return_value=None), \
             patch("naver.crawler.save_checkpoint"), \
             patch("naver.crawler._crawl_site", side_effect=crawl), \
             patch("naver.crawler.pause", side_effect=rest):
            self.assertEqual(await crawl_marketplace(), ["first", "second", "third"])
        self.assertEqual(events, ["first", "pause", "second", "pause", "third"])

    async def test_blocked_store_does_not_open_remaining_stores(self):
        sites = [{"site_name": str(i), "url": "https://example.com"} for i in range(3)]
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=MagicMock())
        manager.__aexit__ = AsyncMock(return_value=False)
        crawl = AsyncMock(side_effect=CrawlBlockedError("blocked"))
        with patch.object(config, "SEARCH_URLS", sites), \
             patch("naver.crawler.create_browser", return_value=manager), \
             patch("naver.crawler.load_checkpoint", return_value=None), \
             patch("naver.crawler._crawl_site", crawl):
            with self.assertRaises(CrawlBlockedError):
                await crawl_marketplace()
        crawl.assert_awaited_once()

    async def test_kream_uses_one_page_without_periodic_break(self):
        models = [str(index) for index in range(21)]
        page = MagicMock()
        init = AsyncMock(return_value=page)
        search = AsyncMock(return_value=[])
        rest = AsyncMock()
        with patch("kream.crawler.init_kream_page", init), \
             patch("kream.crawler.search_kream", search), \
             patch("kream.crawler.asyncio.sleep", rest):
            results = [item async for item in iter_kream_results(models, MagicMock())]
        self.assertEqual([name for name, _ in results], models)
        init.assert_awaited_once()
        self.assertTrue(all(call.args[1] is page for call in search.await_args_list))
        rest.assert_not_awaited()

    async def test_kream_block_does_not_retry_or_search_next_model(self):
        once = AsyncMock(return_value=None)
        with patch("kream.crawler.init_kream_page", new=AsyncMock(return_value=MagicMock())), \
             patch("kream.crawler._search_kream_once", once):
            with self.assertRaises(CrawlBlockedError):
                async for _ in iter_kream_results(["A", "B"], MagicMock()):
                    self.fail("blocked result must not be yielded")
        once.assert_awaited_once()

    async def test_detail_block_is_not_swallowed_by_candidate_loop(self):
        cards = [{"name": "a", "priceStr": "10,000원", "href": "https://example.com"}] * 2
        detail = AsyncMock(side_effect=CrawlBlockedError("429"))
        with patch("kream.crawler._load_detail_model_number", detail):
            with self.assertRaises(CrawlBlockedError):
                await _find_verified_kream_product("A", cards, MagicMock())
        detail.assert_awaited_once()

    async def test_search_has_no_added_rest(self):
        events = []
        async def rest(*args): events.append("pause")
        async def request(*args):
            events.append("search")
            return []
        with patch("kream.crawler.asyncio.sleep", side_effect=rest), \
             patch("kream.crawler._search_kream_once", side_effect=request):
            await search_kream("A", MagicMock())
        self.assertEqual(events, ["search"])

    async def test_spa_rate_limit_stops_before_next_search(self):
        page = MagicMock()
        page._kream_blocked_status = None
        response = MagicMock(status=429, url="https://api.kream.co.kr/api/search")
        response.request.resource_type = "xhr"
        _record_kream_response(page, response)
        request = AsyncMock()
        with patch("kream.crawler._search_kream_once", request):
            with self.assertRaises(CrawlBlockedError):
                await search_kream("A", page)
        request.assert_not_awaited()

    def test_third_party_image_denial_does_not_stop_search(self):
        page = MagicMock()
        page._kream_blocked_status = None
        response = MagicMock(status=403, url="https://ads.example.com/banner")
        response.request.resource_type = "image"
        _record_kream_response(page, response)
        self.assertIsNone(page._kream_blocked_status)

    async def test_missing_result_cards_signal_failure_not_empty_success(self):
        page = MagicMock()
        page._kream_blocked_status = None
        page.evaluate = AsyncMock()
        page.keyboard.press = AsyncMock()
        page.title = AsyncMock(return_value="Search")
        page.content = AsyncMock(return_value="x" * 1000)
        page.locator.return_value.count = AsyncMock(return_value=0)
        page.wait_for_selector = AsyncMock(side_effect=TimeoutError("cards missing"))
        search_input = MagicMock(click=AsyncMock())
        with patch("kream.crawler._open_search_and_get_input", new=AsyncMock(return_value=search_input)), \
             patch("kream.crawler._human_wait", new=AsyncMock()), \
             patch("kream.crawler._human_type", new=AsyncMock()), \
             patch("kream.crawler._is_blocked_page", new=AsyncMock(return_value=False)):
            self.assertIsNone(await _search_kream_once("A", page))

    async def test_pause_rejects_invalid_range(self):
        with self.assertRaises(ValueError):
            await pause(20, 10, "invalid")

    def test_access_limits_stop_instead_of_becoming_empty_results(self):
        for status in (403, 429):
            with self.assertRaises(CrawlBlockedError):
                check_response(MagicMock(status=status))
        check_response(MagicMock(status=200))
        check_response(None)
