import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import config
from common.model_names import MODEL_STRATEGIES, extract_model_names
from musinsa.crawler import (
    _build_ranking_api_url,
    _crawl_ranking_products,
    _is_ranking_url,
    _to_marketplace_products as to_musinsa_products,
    _to_ranking_marketplace_products as to_musinsa_ranking_products,
)
from naver.crawler import _crawl_site, _wait_for_product_list, crawl_marketplace
from special.crawler import _to_marketplace_products as to_special_products


class ModelNameExtractionTests(unittest.TestCase):
    def test_bracket_code_and_size_noise(self):
        self.assertEqual(
            extract_model_names(
                "우포스 우아 슬라이드 (OOAHH-BLACK)(size230-280)",
                strategy="bracket_first",
            ),
            ["OOAHH-BLACK"],
        )

    def test_bracket_slash_returns_multiple_models(self):
        self.assertEqual(
            extract_model_names(
                "멀티 상품 (ABC123 001 / DEF456-789)",
                strategy="bracket_first",
            ),
            ["ABC123 001", "DEF456-789"],
        )

    def test_bracket_noise_does_not_hide_real_code(self):
        cases = {
            "가방 (11L) (DR6091-017)": ["DR6091-017"],
            "신발 (D) (WRCXCS4)": ["WRCXCS4"],
            "주니어 (GS) (DH2920-111)": ["DH2920-111"],
            "푸마(PUMA) 모스트로 파워 그레이스 (39982701)": ["39982701"],
        }
        for product_name, expected in cases.items():
            with self.subTest(product_name=product_name):
                self.assertEqual(
                    extract_model_names(product_name, strategy="bracket_first"),
                    expected,
                )

    def test_english_descriptors_without_digits_are_rejected(self):
        self.assertEqual(
            extract_model_names(
                "플레이볼 오리진 뮬 (PLAYBALL ORIGIN MULE) NY (Black)",
                strategy="slash_first",
            ),
            [],
        )

    def test_inline_code_before_korean_color_is_kept_as_group(self):
        self.assertEqual(
            extract_model_names(
                "나이키 드라이핏 헤드밴드 IB0351 027 블랙",
                strategy="bracket_first",
            ),
            ["IB0351 027"],
        )

    def test_suffix_grouped_keeps_luxury_sku_segments(self):
        cases = {
            "디젤 스웨트셔츠 블랙 A21873 0BKBW 900": ["A21873 0BKBW 900"],
            "아페쎄 스웨트셔츠 COGVG F27854 FAA": ["COGVG F27854 FAA"],
            "구찌 숄더백 781554 AADLO 1000": ["781554 AADLO 1000"],
            "디젤 티셔츠 그레이 T-BOXT-PAK 96P": ["T-BOXT-PAK 96P"],
        }
        for product_name, expected in cases.items():
            with self.subTest(product_name=product_name):
                self.assertEqual(
                    extract_model_names(product_name, strategy="suffix_grouped"),
                    expected,
                )

    def test_long_numeric_alternatives_are_returned_separately(self):
        self.assertEqual(
            extract_model_names(
                "몽블랑 리버서블 벨트 113834 222139",
                strategy="suffix_grouped",
            ),
            ["113834", "222139"],
        )

    def test_season_pack_capacity_and_size_are_rejected(self):
        for product_name in (
            "QR 정품 디젤 가방 1DR 블랙 26SS",
            "양말 (3P)",
            "백팩 (11L)",
            "신발 (size230-280)",
            "운동화 (GS)",
            "시즌 상품 (2026-27)",
        ):
            with self.subTest(product_name=product_name):
                self.assertEqual(
                    extract_model_names(product_name, strategy="auto"),
                    [],
                )

    def test_grouped_suffix_does_not_absorb_english_color(self):
        for color in ("BLACK", "IVORY", "SILVER"):
            with self.subTest(color=color):
                self.assertEqual(
                    extract_model_names(
                        f"가방 ABC123 {color}",
                        strategy="suffix_grouped",
                    ),
                    ["ABC123"],
                )

    def test_slash_first_supports_musinsa_variants(self):
        cases = {
            "핸드볼 스페지알 - 블랙 / IF6562": ["IF6562"],
            "단테 - 블랙 / 16736001": ["16736001"],
            "모스트로 파워 그레이스 (39982701)": ["39982701"],
            "척 70 빈티지 화이트 A07453C": ["A07453C"],
            "스니커즈 4.5cm LCWC90M513": ["LCWC90M513"],
            "PCB-RUN2 피씨비 런 블랙": ["PCB-RUN2"],
            "메리제인 클로그 S24W519_3color": ["S24W519"],
            "펌프스 EBAL146140+57147": ["EBAL146140"],
        }
        for product_name, expected in cases.items():
            with self.subTest(product_name=product_name):
                self.assertEqual(
                    extract_model_names(product_name, strategy="slash_first"),
                    expected,
                )

    def test_slash_first_uses_rightmost_code_outside_brackets(self):
        cases = {
            "TS-01 나일론 - 브라운 / 396484-02": ["396484-02"],
            "프리미엄 뉴스쿨 MTE-1 - 블랙 / VN000CXRCJK1": [
                "VN000CXRCJK1"
            ],
            "FE LO 1920-MULE/WHITE/RED/BLUE/FU100173": ["FU100173"],
            "B300 누벅/스웨이드 (X41) SFPM2438322-X41": [
                "SFPM2438322-X41"
            ],
            "상품 (ABC123) / DEF456": ["DEF456"],
            "멀티 상품 (ABC123/DEF456-789)": ["ABC123", "DEF456-789"],
        }
        for product_name, expected in cases.items():
            with self.subTest(product_name=product_name):
                self.assertEqual(
                    extract_model_names(product_name, strategy="slash_first"),
                    expected,
                )

    def test_size_and_way_descriptors_do_not_hide_style_code(self):
        cases = {
            "WTRD7100-1 플랫폼 옥스퍼드 [230~250MM]": ["WTRD7100-1"],
            "WTRD4101 플립플랍 [230-290MM]": ["WTRD4101"],
            "GO73 모그 2way 쿠션 샌들": ["GO73"],
        }
        for product_name, expected in cases.items():
            with self.subTest(product_name=product_name):
                self.assertEqual(
                    extract_model_names(product_name, strategy="slash_first"),
                    expected,
                )

    def test_product_generation_is_not_prefixed_to_style_code(self):
        cases = {
            "듀라모 스피드 2 IH8201": ["IH8201"],
            "테크노카오스 2000 JR7229": ["JR7229"],
            "아딜렛 00s KK3578": ["KK3578"],
            "런팔콘 5 IH7758": ["IH7758"],
        }
        for product_name, expected in cases.items():
            with self.subTest(product_name=product_name):
                self.assertEqual(extract_model_names(product_name), expected)

    def test_unknown_strategy_is_rejected(self):
        with self.assertRaises(ValueError):
            extract_model_names("상품 ABC123", strategy="unknown")


class CrawlerIntegrationTests(unittest.TestCase):
    def test_configured_naver_strategies_are_supported(self):
        for site in config.SEARCH_URLS:
            self.assertIn(site["model_strategy"], MODEL_STRATEGIES)

    def test_musinsa_converter_uses_slash_first_strategy(self):
        products = to_musinsa_products(
            {
                "goodsName": "모스트로 파워 그레이스 (39982701)",
                "goodsLinkUrl": "https://www.musinsa.com/products/6359372",
                "finalPrice": 31900,
                "isSoldOut": False,
            },
            "2026-07-28T12:00:00",
        )
        self.assertEqual([product.model_name for product in products], ["39982701"])

    def test_musinsa_ranking_url_and_converter(self):
        page_url = (
            "https://www.musinsa.com/main/outlet/ranking"
            "?gf=A&storeCode=outlet&sectionId=233&contentsId="
            "&categoryCode=107002&ageBand=AGE_BAND_ALL"
        )
        self.assertTrue(_is_ranking_url(page_url))
        api_url = _build_ranking_api_url(page_url)
        self.assertTrue(
            api_url.startswith(
                "https://client.musinsa.com/api/home/web/v5/pans/ranking?"
            )
        )
        self.assertIn("categoryCode=107002", api_url)

        products = to_musinsa_ranking_products(
            {
                "type": "PRODUCT_COLUMN",
                "info": {
                    "productName": "마쯔 남성 페니 로퍼 3.5cm LCMD82I111",
                    "finalPrice": 35900,
                    "isSoldOut": False,
                },
                "onClick": {
                    "url": "https://www.musinsa.com/products/1775338",
                },
            },
            "2026-07-28T12:00:00",
        )
        self.assertEqual(
            [product.model_name for product in products],
            ["LCMD82I111"],
        )

    def test_special_converter_preserves_multiple_model_names(self):
        products = to_special_products(
            {
                "name": "멀티 상품 (ABC123/DEF456-789)",
                "url": "https://example.com/products/multi",
                "price": 10000,
                "code": "multi",
            },
            "2026-07-28T12:00:00",
            "auto",
        )
        self.assertEqual(
            [product.model_name for product in products],
            ["ABC123", "DEF456-789"],
        )


class MusinsaPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ranking_follows_link_next_when_has_next_is_false(self):
        def response(model: str, next_url: str | None) -> dict:
            return {
                "hasNext": False,
                "data": {
                    "modules": [
                        {
                            "type": "MULTICOLUMN",
                            "items": [
                                {
                                    "type": "PRODUCT_COLUMN",
                                    "info": {
                                        "productName": f"테스트 상품 / {model}",
                                        "finalPrice": 10000,
                                        "isSoldOut": False,
                                    },
                                    "onClick": {
                                        "url": f"https://www.musinsa.com/products/{model}",
                                    },
                                }
                            ],
                        }
                    ]
                },
                "link": {"next": next_url} if next_url else {},
            }

        fetch = AsyncMock(
            side_effect=[
                response("ABC123", "https://api.example.test/page-2"),
                response("DEF456", None),
            ]
        )
        with (
            patch("musinsa.crawler._fetch_json_async", fetch),
            patch("musinsa.crawler.asyncio.sleep", new=AsyncMock()),
        ):
            products = await _crawl_ranking_products(
                "https://www.musinsa.com/main/outlet/ranking?categoryCode=107002",
                "2026-07-28T12:00:00",
            )

        self.assertEqual(
            [product.model_name for product in products],
            ["ABC123", "DEF456"],
        )
        self.assertEqual(fetch.await_count, 2)


class NaverManualWaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_for_enter_and_rechecks_product_list(self):
        page = MagicMock()
        page.wait_for_selector = AsyncMock(
            side_effect=[TimeoutError("initial timeout"), None]
        )
        locator = MagicMock()
        locator.count = AsyncMock(side_effect=[0, 1])
        page.locator.return_value = locator

        with patch(
            "naver.crawler.asyncio.to_thread",
            new=AsyncMock(return_value=""),
        ) as to_thread:
            await _wait_for_product_list(
                page,
                "li.product",
                "test-store",
                asyncio.Lock(),
            )

        to_thread.assert_awaited_once()
        self.assertEqual(page.wait_for_selector.await_count, 2)

    async def test_q_aborts_manual_wait(self):
        page = MagicMock()
        page.wait_for_selector = AsyncMock(side_effect=TimeoutError("timeout"))
        locator = MagicMock()
        locator.count = AsyncMock(return_value=0)
        page.locator.return_value = locator

        with (
            patch(
                "naver.crawler.asyncio.to_thread",
                new=AsyncMock(return_value="q"),
            ),
            self.assertRaises(RuntimeError),
        ):
            await _wait_for_product_list(
                page,
                "li.product",
                "test-store",
                asyncio.Lock(),
            )

    async def test_noninteractive_eof_fails_explicitly(self):
        page = MagicMock()
        page.wait_for_selector = AsyncMock(side_effect=TimeoutError("timeout"))
        locator = MagicMock()
        locator.count = AsyncMock(return_value=0)
        page.locator.return_value = locator

        with (
            patch(
                "naver.crawler.asyncio.to_thread",
                new=AsyncMock(side_effect=EOFError),
            ),
            self.assertRaisesRegex(RuntimeError, "사용자 입력을 받을 수 없음"),
        ):
            await _wait_for_product_list(
                page,
                "li.product",
                "test-store",
                asyncio.Lock(),
            )

    async def test_invalid_strategy_fails_before_browser_use(self):
        site = {
            "site_name": "bad-config",
            "url": "https://example.test",
            "type": "smart_store",
            "model_strategy": "typo",
        }
        with self.assertRaises(ValueError):
            await _crawl_site(None, site, asyncio.Lock())

    async def test_public_crawl_rejects_strategy_before_browser_creation(self):
        site = {
            "site_name": "bad-config",
            "url": "https://example.test",
            "type": "smart_store",
            "model_strategy": "typo",
        }
        with (
            patch.object(config, "SEARCH_URLS", [site]),
            patch("naver.crawler.create_browser") as create_browser,
            self.assertRaises(ValueError),
        ):
            await crawl_marketplace()

        create_browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
