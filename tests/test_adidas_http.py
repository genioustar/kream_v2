import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from adidas import crawler
from common.pacing import CrawlBlockedError

ADIDAS_TEST_SALE_URL = 'https://www.adidas.co.kr/outlet?grid=true'


def response(status, url='https://www.adidas.co.kr/big_discount', kind='document', headers=None):
    return MagicMock(status=status, url=url, headers=headers or {},
                     request=MagicMock(resource_type=kind), text=AsyncMock(return_value='denied'))


class AdidasHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_404_fails_without_retry(self):
        page = MagicMock(goto=AsyncMock(return_value=response(404)))
        with self.assertRaisesRegex(RuntimeError, 'HTTP 404'):
            await crawler._navigate(page, 'url', crawler._ResponseDiagnostics(page))
        self.assertEqual(page.goto.await_count, 1)

    async def test_403_is_pipeline_block(self):
        page = MagicMock(goto=AsyncMock(return_value=response(403)))
        with self.assertRaises(CrawlBlockedError):
            await crawler._navigate(page, 'url', crawler._ResponseDiagnostics(page))
        self.assertEqual(page.goto.await_count, 1)

    async def test_429_recovers_and_clears_previous_document_failure(self):
        limited, ok = response(429, headers={'retry-after': '7'}), response(200)
        page = MagicMock()
        diagnostics = crawler._ResponseDiagnostics(page)
        limited.request.frame = page.main_frame
        async def goto(*args, **kwargs):
            result = limited if page.goto.await_count == 1 else ok
            diagnostics.on_response(result)
            return result
        page.goto = AsyncMock(side_effect=goto)
        with patch.object(crawler.asyncio, 'sleep', new_callable=AsyncMock) as sleep:
            await crawler._navigate(page, 'url', diagnostics)
            sleep.assert_awaited_once_with(7)
        self.assertIsNone(diagnostics.fatal_response)

    async def test_429_retry_limit_and_backoff(self):
        page = MagicMock(goto=AsyncMock(return_value=response(429)))
        with patch.object(crawler.asyncio, 'sleep', new_callable=AsyncMock) as sleep:
            with self.assertRaises(CrawlBlockedError):
                await crawler._navigate(page, 'url', crawler._ResponseDiagnostics(page))
        self.assertEqual(page.goto.await_count, 3)
        self.assertEqual([c.args[0] for c in sleep.await_args_list], [5, 10])

    async def test_long_retry_after_never_retries_early(self):
        page = MagicMock(goto=AsyncMock(return_value=response(429, headers={'retry-after': '3600'})))
        with patch.object(crawler.asyncio, 'sleep', new_callable=AsyncMock) as sleep:
            with self.assertRaises(CrawlBlockedError):
                await crawler._navigate(page, 'url', crawler._ResponseDiagnostics(page))
            sleep.assert_not_awaited()
        self.assertEqual(page.goto.await_count, 1)

    def test_retry_after_http_date_and_invalid_value(self):
        future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30))
        self.assertTrue(28 <= crawler._retry_delay(future, 0) <= 30)
        self.assertEqual(crawler._retry_delay('invalid', 1), 10)

    def test_ad_error_is_diagnostic_only_but_product_api_is_fatal(self):
        diagnostics = crawler._ResponseDiagnostics(MagicMock())
        diagnostics.on_response(response(403, 'https://analytics.example/api/products', 'fetch'))
        diagnostics.on_response(response(401, 'https://www.adidas.co.kr/api/account', 'xhr'))
        diagnostics.on_response(response(403, 'https://www.adidas.co.kr/plp-app/api/product/JS3905', 'fetch'))
        diagnostics.check()
        diagnostics.on_response(response(403, 'https://www.adidas.co.kr/api/plp/content-engine', 'fetch'))
        with self.assertRaises(CrawlBlockedError):
            diagnostics.check()
        self.assertEqual(len(diagnostics.errors), 4)

    async def test_failure_saves_diagnostics_even_when_page_closed(self):
        page = MagicMock(url='https://www.adidas.co.kr/big_discount', title=AsyncMock(side_effect=RuntimeError('closed')))
        diagnostics = crawler._ResponseDiagnostics(page)
        diagnostics.on_response(response(404))
        with tempfile.TemporaryDirectory() as directory, patch.object(crawler.config, 'OUTPUT_DIR', directory):
            await diagnostics.save(RuntimeError('original error'))
            files = list(Path(directory).rglob('*.json'))
            self.assertEqual(len(files), 1)
            data = json.loads(files[0].read_text())
            self.assertEqual(data['responses'][0]['status'], 404)
            self.assertEqual(data['error'], 'original error')
            self.assertEqual(data['capture_error'], 'closed')

    async def test_crawl_does_not_turn_http_failure_into_empty_products(self):
        page = MagicMock(goto=AsyncMock(return_value=response(404)),
                         close=AsyncMock(), is_closed=MagicMock(return_value=False))
        @asynccontextmanager
        async def browser():
            yield MagicMock(new_page=AsyncMock(return_value=page))
        with patch.object(crawler.config, 'ADIDAS_SALE_URL', ADIDAS_TEST_SALE_URL), \
             patch.object(crawler, 'create_browser', browser), \
             patch.object(crawler._ResponseDiagnostics, 'save', new_callable=AsyncMock) as save, \
             patch.object(crawler, '_find_card_selector', new_callable=AsyncMock) as find:
            with self.assertRaisesRegex(RuntimeError, 'HTTP 404'):
                await crawler.crawl_adidas()
            find.assert_not_awaited()
            save.assert_awaited_once()
            page.close.assert_awaited_once()

    async def test_disabled_next_finishes_and_same_page_fails(self):
        with patch.object(crawler.config, 'ADIDAS_SALE_URL', ADIDAS_TEST_SALE_URL):
            page = MagicMock(url=crawler.config.ADIDAS_SALE_URL, title=AsyncMock(return_value='sale'),
                             evaluate=AsyncMock(), close=AsyncMock(), is_closed=MagicMock(return_value=False))
            cards = MagicMock(evaluate_all=AsyncMock(return_value=['product.html']))
            button = MagicMock(is_visible=AsyncMock(return_value=True), is_disabled=AsyncMock(return_value=True), click=AsyncMock())
            page.locator.side_effect = lambda sel: MagicMock(first=button) if sel == crawler.NEXT_BTN_SELECTOR else cards
            @asynccontextmanager
            async def browser():
                yield MagicMock(new_page=AsyncMock(return_value=page))
            with patch.object(crawler, 'create_browser', browser), \
                 patch.object(crawler, '_navigate', new_callable=AsyncMock), \
                 patch.object(crawler, '_find_card_selector', AsyncMock(return_value='cards')), \
                 patch.object(crawler, '_extract_products', AsyncMock(return_value=[])), \
                 patch.object(crawler.asyncio, 'sleep', new_callable=AsyncMock), \
                 patch.object(crawler._ResponseDiagnostics, 'save', new_callable=AsyncMock):
                self.assertEqual(await crawler.crawl_adidas(), [])
                button.click.assert_not_awaited()
                button.is_disabled.return_value = False
                with self.assertRaisesRegex(RuntimeError, '진행되지'):
                    await crawler.crawl_adidas()
                button.click.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
