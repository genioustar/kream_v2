import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import config
from naver.crawler import (
    SKIP_REASON_MODEL_MISSING,
    SKIP_REASON_PARSE_ERROR,
    SKIP_REASON_SOLD_OUT,
    _crawl_site,
    crawl_marketplace,
)
from naver.diagnostics import NaverAccessError, NaverCollectionError, NaverDiagnostics
from naver.parser import get_selectors


class NaverCollectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.output_dir = Path(directory.name)
        output_patch = patch.object(config, "OUTPUT_DIR", directory.name)
        output_patch.start()
        self.addCleanup(output_patch.stop)

        self.site = {
            "site_name": "collection-test",
            "url": "https://smartstore.naver.com/collection-test/best",
            "type": "smart_store",
            "model_strategy": "bracket_first",
        }
        self.selectors = get_selectors(self.site["type"])
        self.page = MagicMock()
        self.page.url = self.site["url"]
        self.page.goto = AsyncMock(return_value=None)
        self.page.wait_for_selector = AsyncMock(return_value=None)
        self.page.close = AsyncMock()
        self.page.context.cookies = AsyncMock(return_value=[])
        self.page.evaluate = AsyncMock(return_value={
            "protection_present": False,
            "login_present": False,
            "challenge_present": False,
        })
        self.context = MagicMock(new_page=AsyncMock(return_value=self.page))

    def item(self, *, sold_out=False, model_missing=False, failed_field=None):
        """실제 상품 파서가 정상·의도된 제외·DOM 실패를 구별하도록 Locator를 구성한다."""
        item = MagicMock()
        item.evaluate = AsyncMock(return_value=sold_out)
        name = MagicMock()
        name.first.inner_text = AsyncMock(return_value=(
            "모델 코드 없는 상품" if model_missing else "테스트 상품 (ABC12345)"
        ))
        if failed_field == "name":
            name.first.inner_text.side_effect = TimeoutError("private TOKEN in browser exception")
        price = MagicMock()
        price.first.inner_text = AsyncMock(return_value="100,000원")
        if failed_field == "price":
            price.first.inner_text.side_effect = TimeoutError("private TOKEN in browser exception")
        url = MagicMock()
        url.first.get_attribute = AsyncMock(return_value=self.site["url"] + "/products/1")

        def locate(selector):
            if selector == self.selectors["name"]:
                return name
            if selector == self.selectors["url"]:
                return url
            if selector in self.selectors["price"]:
                return price
            raise AssertionError(f"Unexpected item selector: {selector}")

        item.locator.side_effect = locate
        return item

    def prepare_items(self, items):
        locator = self.page.locator.return_value
        locator.count = AsyncMock(return_value=len(items))
        locator.nth.side_effect = items.__getitem__
        scroll_patch = patch("naver.crawler._scroll_to_bottom", new=AsyncMock(return_value=len(items)))
        scroll_patch.start()
        self.addCleanup(scroll_patch.stop)

    def report(self):
        paths = list(self.output_dir.glob("*/diagnostics/naver_*.json"))
        self.assertEqual(len(paths), 1)
        content = paths[0].read_text(encoding="utf-8")
        self.assertNotIn("TOKEN", content)
        return json.loads(content)

    def assert_closed(self):
        self.page.close.assert_awaited_once()
        self.assertEqual(self.page.remove_listener.call_count, self.page.on.call_count)

    async def test_all_actual_parse_errors_fail_instead_of_returning_empty_success(self):
        self.prepare_items([self.item(failed_field="name"), self.item(failed_field="price")])
        with self.assertRaises(NaverCollectionError) as caught:
            await _crawl_site(self.context, self.site)

        self.assertEqual(caught.exception.reason, "collection_error")
        report = self.report()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failure_detail"], "product_parse_error")
        self.assertEqual(report["product_count"], 0)
        self.assertEqual(report["skip_counts"][SKIP_REASON_PARSE_ERROR], 2)
        self.assert_closed()

    async def test_mixed_actual_parse_error_prevents_checkpoint_and_next_store(self):
        self.prepare_items([self.item(), self.item(failed_field="price")])
        next_site = {**self.site, "site_name": "next-store", "url": "https://smartstore.naver.com/next/best"}
        manager = MagicMock(
            __aenter__=AsyncMock(return_value=self.context),
            __aexit__=AsyncMock(return_value=False),
        )
        with (
            patch.object(config, "SEARCH_URLS", [self.site, next_site]),
            patch("naver.crawler.create_browser", return_value=manager),
            patch("naver.crawler.load_checkpoint", return_value=None),
            patch("naver.crawler.save_checkpoint") as save,
        ):
            with self.assertRaises(NaverCollectionError):
                await crawl_marketplace()

        save.assert_not_called()
        self.context.new_page.assert_awaited_once()
        report = self.report()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failure_detail"], "product_parse_error")
        self.assertEqual(report["product_count"], 1)
        self.assertEqual(report["skip_counts"][SKIP_REASON_PARSE_ERROR], 1)
        self.assert_closed()

    async def test_only_expected_exclusions_are_a_valid_empty_result(self):
        self.prepare_items([self.item(sold_out=True), self.item(model_missing=True)])
        self.assertEqual(await _crawl_site(self.context, self.site), [])

        report = self.report()
        self.assertEqual(report["status"], "success")
        self.assertIsNone(report["failure_detail"])
        self.assertEqual(report["skip_counts"], {
            SKIP_REASON_SOLD_OUT: 1,
            SKIP_REASON_MODEL_MISSING: 1,
            SKIP_REASON_PARSE_ERROR: 0,
        })
        self.assert_closed()

    async def test_final_guard_rejects_products_when_protection_appears_at_completion(self):
        self.prepare_items([self.item()])
        original_check = NaverDiagnostics.check

        async def check_with_late_redirect(diagnostics, stage, force=True):
            if stage == "complete":
                self.page.url = "https://nid.naver.com/user2/help/idSafetyRelease?token_help=TOKEN"
            return await original_check(diagnostics, stage, force=force)

        with patch.object(NaverDiagnostics, "check", new=check_with_late_redirect):
            with self.assertRaises(NaverAccessError) as caught:
                await _crawl_site(self.context, self.site)

        self.assertEqual(caught.exception.reason, "account_protection")
        report = self.report()
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["stage"], "complete")
        self.assertEqual(report["product_count"], 1)
        self.assert_closed()

    async def test_failed_public_search_never_parses_homepage_cards(self):
        self.prepare_items([self.item()])
        site = {**self.site, "navigation": "store_search_ui"}

        async def failed_search(page, url, diagnostics, timeout_ms):
            diagnostics.data["failure_detail"] = "public_search_navigation_failed"
            raise NaverCollectionError("collection_error", diagnostics.report_path)

        with (
            patch("naver.crawler.navigate_public_search", new=failed_search),
            patch("naver.crawler._extract_product", new=AsyncMock()) as extract,
        ):
            with self.assertRaises(NaverCollectionError):
                await _crawl_site(self.context, site)

        extract.assert_not_awaited()
        self.page.wait_for_selector.assert_not_awaited()
        self.assertEqual(self.report()["status"], "failed")
        self.assert_closed()

    async def test_incomplete_public_search_count_never_returns_partial_products(self):
        self.prepare_items([self.item()])
        site = {**self.site, "navigation": "store_search_ui"}
        with (
            patch("naver.crawler.navigate_public_search", new=AsyncMock(return_value=2)),
            patch("naver.crawler._extract_product", new=AsyncMock()) as extract,
        ):
            with self.assertRaises(NaverCollectionError):
                await _crawl_site(self.context, site)

        extract.assert_not_awaited()
        report = self.report()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failure_detail"], "incomplete_search_results")
        self.assertEqual(report["observed_card_count"], 1)
        self.assert_closed()


if __name__ == "__main__":
    unittest.main()
