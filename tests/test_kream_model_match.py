import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from common.models import KreamProduct, MarketplaceProduct
from kream.comparator import find_arbitrage
from kream.crawler import (
    _find_verified_kream_product,
    _load_detail_model_number,
)
from kream.parser import (
    model_numbers_match,
    parse_kream_model_number,
    parse_kream_model_number_html,
)


class KreamModelParserTests(unittest.TestCase):
    def test_parses_detail_model_number_label(self):
        self.assertEqual(parse_kream_model_number("모델번호 IG5349"), "IG5349")

    def test_rejects_unrelated_detail_text(self):
        self.assertIsNone(parse_kream_model_number("발매일 23/08/07"))

    def test_parses_server_rendered_detail_html(self):
        detail_html = (
            '<p class="text-lookup"><!--[-->'
            "모델번호 IG5349"
            "<!--]--></p>"
        )
        self.assertEqual(
            parse_kream_model_number_html(detail_html),
            "IG5349",
        )

    def test_matches_only_full_normalized_model_number(self):
        self.assertTrue(model_numbers_match("ig-5749", "ＩＧ５７４９"))
        self.assertFalse(model_numbers_match("IG5749", "IG5349"))
        self.assertFalse(model_numbers_match("IG5749", "IG57490"))
        self.assertFalse(model_numbers_match("", ""))


class KreamCandidateVerificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cards = [
            {
                "href": "https://kream.co.kr/products/156167",
                "name": "Adidas Yeezy Foam RNNR Carbon",
                "priceStr": "79,000원",
                "tradeStr": "거래 9,893",
            },
            {
                "href": "https://kream.co.kr/products/999999",
                "name": "Expected Product",
                "priceStr": "80,000원",
                "tradeStr": "거래 321",
            },
        ]

    async def test_incident_mismatch_is_rejected(self):
        with patch(
            "kream.crawler._load_detail_model_number",
            new=AsyncMock(return_value="IG5349"),
        ):
            product = await _find_verified_kream_product(
                "IG5749", self.cards[:1], MagicMock()
            )

        self.assertIsNone(product)

    async def test_first_mismatch_then_exact_candidate_selects_exact_only(self):
        detail_model = AsyncMock(side_effect=["IG5349", "IG5749"])
        with patch("kream.crawler._load_detail_model_number", new=detail_model):
            product = await _find_verified_kream_product(
                "IG5749", self.cards, MagicMock()
            )

        self.assertIsNotNone(product)
        self.assertEqual(product.model_name, "IG5749")
        self.assertEqual(product.kream_url, self.cards[1]["href"])
        self.assertEqual(detail_model.await_count, 2)

    async def test_missing_detail_model_number_fails_closed(self):
        with patch(
            "kream.crawler._load_detail_model_number",
            new=AsyncMock(return_value=None),
        ):
            product = await _find_verified_kream_product(
                "IG5749", self.cards[:1], MagicMock()
            )

        self.assertIsNone(product)


class KreamDetailModelLoadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_model_number_from_server_response_without_dom_wait(self):
        response = MagicMock()
        response.text = AsyncMock(
            return_value='<p class="text-lookup">모델번호 IG5349</p>'
        )
        page = MagicMock()
        page.goto = AsyncMock(return_value=response)
        page.wait_for_selector = AsyncMock()

        model_number = await _load_detail_model_number(
            page,
            "https://kream.co.kr/products/156167",
        )

        self.assertEqual(model_number, "IG5349")
        page.wait_for_selector.assert_not_awaited()

    async def test_dom_fallback_waits_for_attached_responsive_duplicates(self):
        model_locator = MagicMock()
        model_locator.all_text_contents = AsyncMock(
            return_value=["모델번호 IG5349", "모델번호 IG5349"]
        )
        page = MagicMock()
        page.goto = AsyncMock(return_value=None)
        page.wait_for_selector = AsyncMock()
        page.locator.return_value = model_locator

        model_number = await _load_detail_model_number(
            page,
            "https://kream.co.kr/products/156167",
        )

        self.assertEqual(model_number, "IG5349")
        page.wait_for_selector.assert_awaited_once()
        self.assertEqual(
            page.wait_for_selector.await_args.kwargs["state"],
            "attached",
        )


class KreamComparatorModelGuardTests(unittest.TestCase):
    def setUp(self):
        self.marketplace = MarketplaceProduct(
            site_name="musinsa",
            product_name="Test Product",
            model_name="IG5749",
            price=41_990,
            url="https://www.musinsa.com/products/3974334",
            crawled_at="2026-08-03T14:22:05",
        )

    def _kream_product(self, model_name: str) -> KreamProduct:
        return KreamProduct(
            model_name=model_name,
            kream_name="Kream Product",
            kream_price=79_000,
            trade_count=9_893,
            kream_url="https://kream.co.kr/products/156167",
        )

    def test_mismatched_actual_model_never_creates_arbitrage_result(self):
        results = find_arbitrage(
            [self.marketplace],
            {"IG5749": [self._kream_product("IG5349")]},
        )
        self.assertEqual(results, [])

    def test_matching_actual_model_can_create_arbitrage_result(self):
        results = find_arbitrage(
            [self.marketplace],
            {"IG5749": [self._kream_product("IG5749")]},
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].model_name, "IG5749")


if __name__ == "__main__":
    unittest.main()
