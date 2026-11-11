"""STEP 1 사이트 등록 표 회귀 테스트.

SITE_CRAWLERS는 임포트 시점에 크롤러 함수 객체를 crawl 필드에 담는다.
그래서 patch("main.crawl_adidas")처럼 모듈 전역 이름만 바꿔도 표 안 함수는 그대로다.
테스트는 crawl을 AsyncMock으로 바꾼 SiteCrawler를 만들어
patch("main.SITE_CRAWLERS", ...)로 표를 바꾸거나, _crawl_site에 그 객체를 직접 넘긴다.
"""
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import config
import main as pipeline
from adidas.crawler import crawl_adidas
from common.models import MarketplaceProduct
from common.pacing import CrawlBlockedError
from musinsa.crawler import crawl_musinsa
from naver.crawler import crawl_marketplace
from nike.crawler import crawl_nike
from special.crawler import crawl_special
from ssfshop.crawler import crawl_ssfshop

# 네이버 할인율은 0이라 호출을 지워도 기본 설정 테스트는 통과한다. 이 값으로 덮어 적용을 확인한다.
MARKETPLACE_DISCOUNT_PROBE_RATE = 10

REGISTRY_ORDER = (
    ("marketplace", "네이버", crawl_marketplace, True),
    ("adidas", "아디다스", crawl_adidas, False),
    ("musinsa", "무신사", crawl_musinsa, False),
    ("ssfshop", "SSF SHOP", crawl_ssfshop, False),
    ("nike", "나이키", crawl_nike, False),
    ("special", "special", crawl_special, False),
)


def sample_product(site_name: str, price: int = 10000) -> MarketplaceProduct:
    return MarketplaceProduct(
        site_name=site_name,
        product_name="테스트 상품",
        model_name="AB1234",
        price=price,
        url="https://example.com/item",
        crawled_at="2026-11-11T00:00:00",
    )


def site_by_key(key: str) -> pipeline.SiteCrawler:
    return next(site for site in pipeline.SITE_CRAWLERS if site.key == key)


class SiteRegistryTests(unittest.TestCase):
    def test_registry_keys_match_discount_rates(self):
        registered = [(site.key, site.label, site.crawl, site.required) for site in pipeline.SITE_CRAWLERS]
        self.assertEqual(registered, list(REGISTRY_ORDER))
        self.assertEqual(
            {site.key for site in pipeline.SITE_CRAWLERS},
            set(config.SITE_DISCOUNT_RATES),
        )


class SiteCrawlBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_file_skips_crawler(self):
        for key in ("marketplace", "adidas"):
            mock = AsyncMock(return_value=[sample_product(key)])
            with tempfile.TemporaryDirectory() as directory:
                output_dir = Path(directory)
                (output_dir / f"{key}_products.json").write_text("[]", encoding="utf-8")
                await pipeline._crawl_site(replace(site_by_key(key), crawl=mock), output_dir)
                self.assertEqual((output_dir / f"{key}_products.json").read_text(encoding="utf-8"), "[]")
            mock.assert_not_awaited()

    async def test_optional_empty_result_does_not_save(self):
        for key in ("adidas", "special"):
            site = site_by_key(key)
            mock = AsyncMock(return_value=[])
            with tempfile.TemporaryDirectory() as directory:
                output_dir = Path(directory)
                with self.assertLogs("main", level="WARNING") as logs:
                    await pipeline._crawl_site(replace(site, crawl=mock), output_dir)
                self.assertFalse((output_dir / f"{key}_products.json").exists())
            mock.assert_awaited_once()
            message = f"{site.label} 수집 결과 없음 — 파일 저장 생략 (다음 실행 시 재시도)"
            self.assertTrue(any(message in line for line in logs.output), logs.output)

    async def test_optional_error_continues_to_next_site(self):
        mocks = {}
        sites = []
        for site in pipeline.SITE_CRAWLERS:
            if site.key == "adidas":
                mock = AsyncMock(side_effect=ValueError("수집 실패"))
            elif site.key == "musinsa":
                mock = AsyncMock(return_value=[sample_product("musinsa")])
            else:
                mock = AsyncMock(return_value=[])
            mocks[site.key] = mock
            sites.append(replace(site, crawl=mock))
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "OUTPUT_DIR", directory), \
             patch("main.SITE_CRAWLERS", tuple(sites)):
            await pipeline.main("crawl")
            output = Path(directory) / date.today().strftime("%Y%m%d")
            self.assertFalse((output / "adidas_products.json").exists())
            self.assertTrue((output / "musinsa_products.json").exists())
        for mock in mocks.values():
            mock.assert_awaited_once()

    async def test_optional_block_propagates(self):
        mocks = {}
        sites = []
        for site in pipeline.SITE_CRAWLERS:
            if site.key == "adidas":
                mock = AsyncMock(side_effect=CrawlBlockedError("blocked"))
            else:
                mock = AsyncMock(return_value=[])
            mocks[site.key] = mock
            sites.append(replace(site, crawl=mock))
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "OUTPUT_DIR", directory), \
             patch("main.SITE_CRAWLERS", tuple(sites)):
            with self.assertRaises(CrawlBlockedError):
                await pipeline.main("crawl")
        mocks["marketplace"].assert_awaited_once()
        mocks["adidas"].assert_awaited_once()
        mocks["musinsa"].assert_not_awaited()
        mocks["special"].assert_not_awaited()

    async def test_required_runtime_error_exits(self):
        mocks = {}
        sites = []
        for site in pipeline.SITE_CRAWLERS:
            if site.key == "marketplace":
                mock = AsyncMock(side_effect=RuntimeError("전체 실패"))
            else:
                mock = AsyncMock(return_value=[sample_product(site.key)])
            mocks[site.key] = mock
            sites.append(replace(site, crawl=mock))
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "OUTPUT_DIR", directory), \
             patch("main.SITE_CRAWLERS", tuple(sites)):
            with self.assertRaises(SystemExit) as caught:
                await pipeline.main("crawl")
            output = Path(directory) / date.today().strftime("%Y%m%d")
            self.assertFalse((output / "marketplace_products.json").exists())
        self.assertEqual(caught.exception.code, 1)
        mocks["marketplace"].assert_awaited_once()
        mocks["adidas"].assert_not_awaited()

    async def test_required_empty_result_saves_empty_list(self):
        site = site_by_key("marketplace")
        mock = AsyncMock(return_value=[])
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            with self.assertLogs("main", level="INFO") as logs:
                await pipeline._crawl_site(replace(site, crawl=mock), output_dir)
            saved = json.loads((output_dir / "marketplace_products.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, [])
        mock.assert_awaited_once()
        self.assertTrue(any("네이버 크롤링 시작" in line for line in logs.output), logs.output)

    async def test_required_site_applies_patched_discount_rate(self):
        price = 10000
        mock = AsyncMock(return_value=[sample_product("marketplace", price=price)])
        rates = {"marketplace": MARKETPLACE_DISCOUNT_PROBE_RATE}
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(config.SITE_DISCOUNT_RATES, rates):
            output_dir = Path(directory)
            await pipeline._crawl_site(replace(site_by_key("marketplace"), crawl=mock), output_dir)
            saved = json.loads((output_dir / "marketplace_products.json").read_text(encoding="utf-8"))
        expected = int(round(price * (1 - MARKETPLACE_DISCOUNT_PROBE_RATE / 100)))
        self.assertEqual(saved[0]["price"], price)
        self.assertEqual(saved[0]["sale_price"], expected)
        self.assertNotEqual(expected, price)

    async def test_required_value_error_propagates_without_saving(self):
        mock = AsyncMock(side_effect=ValueError("저장 실패"))
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            with self.assertRaises(ValueError):
                await pipeline._crawl_site(replace(site_by_key("marketplace"), crawl=mock), output_dir)
            self.assertFalse((output_dir / "marketplace_products.json").exists())
        mock.assert_awaited_once()

    async def test_adidas_sale_price_uses_discount_rate(self):
        price = 10000
        rate = config.SITE_DISCOUNT_RATES["adidas"]
        self.assertGreater(rate, 0)
        mock = AsyncMock(return_value=[sample_product("adidas", price=price)])
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            await pipeline._crawl_site(replace(site_by_key("adidas"), crawl=mock), output_dir)
            saved = json.loads((output_dir / "adidas_products.json").read_text(encoding="utf-8"))
        self.assertEqual(saved[0]["price"], price)
        self.assertEqual(saved[0]["sale_price"], int(round(price * (1 - rate / 100))))

    async def test_sites_are_called_in_registry_order(self):
        order = []
        sites = []
        for site in pipeline.SITE_CRAWLERS:
            async def crawl(key=site.key):
                order.append(key)
                return []
            sites.append(replace(site, crawl=crawl))
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "OUTPUT_DIR", directory), \
             patch("main.SITE_CRAWLERS", tuple(sites)):
            await pipeline.main("crawl")
        self.assertEqual(order, [site.key for site in pipeline.SITE_CRAWLERS])
        self.assertEqual(
            order,
            ["marketplace", "adidas", "musinsa", "ssfshop", "nike", "special"],
        )
