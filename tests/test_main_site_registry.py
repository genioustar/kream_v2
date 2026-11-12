"""STEP 1 사이트 등록 표와 --site 회귀 테스트.

SITE_CRAWLERS는 임포트 시점에 크롤러 함수 객체를 crawl 필드에 담는다.
그래서 patch("main.crawl_adidas")처럼 모듈 전역 이름만 바꿔도 표 안 함수는 그대로다.
테스트는 crawl을 AsyncMock으로 바꾼 SiteCrawler를 만들어
patch("main.SITE_CRAWLERS", ...)로 표를 바꾸거나, _crawl_site에 그 객체를 직접 넘긴다.
"""
import ast
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
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

    async def test_required_force_empty_keeps_existing_file(self):
        site = site_by_key("marketplace")
        mock = AsyncMock(return_value=[])
        original = '{"kept": true}\n'
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            target = output_dir / "marketplace_products.json"
            target.write_text(original, encoding="utf-8")
            with self.assertLogs("main", level="WARNING") as logs:
                await pipeline._crawl_site(replace(site, crawl=mock), output_dir, force=True)
            self.assertEqual(target.read_text(encoding="utf-8"), original)
        mock.assert_awaited_once()
        self.assertTrue(any("기존 파일 유지" in line for line in logs.output), logs.output)

    async def test_required_force_empty_saves_when_file_missing(self):
        site = site_by_key("marketplace")
        mock = AsyncMock(return_value=[])
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            target = output_dir / "marketplace_products.json"
            await pipeline._crawl_site(replace(site, crawl=mock), output_dir, force=True)
            saved = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(saved, [])
        mock.assert_awaited_once()

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


CRAWLER_MAIN_FILES = (
    "naver/crawler.py",
    "adidas/crawler.py",
    "musinsa/crawler.py",
    "nike/crawler.py",
    "special/crawler.py",
    "kream/crawler.py",
)

ROOT = Path(__file__).resolve().parent.parent


def _is_main_guard(test: ast.AST) -> bool:
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    if len(test.comparators) != 1:
        return False
    left, right = test.left, test.comparators[0]
    name_main = isinstance(left, ast.Name) and left.id == "__name__"
    const_main = isinstance(right, ast.Constant) and right.value == "__main__"
    swapped_name = isinstance(right, ast.Name) and right.id == "__name__"
    swapped_const = isinstance(left, ast.Constant) and left.value == "__main__"
    return (name_main and const_main) or (swapped_name and swapped_const)


def patched_sites(results):
    """등록 표와 같은 키 순서로 crawl만 바꾼 표와 키별 mock을 만든다."""
    mocks = {}
    sites = []
    for site in pipeline.SITE_CRAWLERS:
        mock = AsyncMock(return_value=results(site.key))
        mocks[site.key] = mock
        sites.append(replace(site, crawl=mock))
    return tuple(sites), mocks


class SiteOptionParsingTests(unittest.TestCase):
    def test_site_adidas_nike_parses_in_argv_order(self):
        args = pipeline.parse_args(["--mode", "crawl", "--site", "adidas", "nike"])
        self.assertEqual(args.mode, "crawl")
        self.assertEqual(args.site, ["adidas", "nike"])
        self.assertEqual(
            [site.key for site in pipeline._select_sites(args.site)],
            ["adidas", "nike"],
        )

    def test_duplicate_site_is_selected_once_in_registry_order(self):
        args = pipeline.parse_args(["--mode", "crawl", "--site", "nike", "adidas", "nike"])
        self.assertEqual(args.site, ["nike", "adidas", "nike"])
        self.assertEqual(
            [site.key for site in pipeline._select_sites(args.site)],
            ["adidas", "nike"],
        )

    def test_site_choices_match_registry_keys(self):
        action = next(
            item for item in pipeline._build_parser()._actions
            if "--site" in item.option_strings
        )
        self.assertEqual(action.nargs, "+")
        self.assertEqual(list(action.choices), [site.key for site in pipeline.SITE_CRAWLERS])

    def test_site_with_full_or_kream_exits(self):
        for argv in (
            ["--mode", "full", "--site", "adidas"],
            ["--mode", "kream", "--site", "adidas"],
        ):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                pipeline.parse_args(argv)
            self.assertEqual(caught.exception.code, 2)

    def test_unknown_site_exits(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            pipeline.parse_args(["--mode", "crawl", "--site", "unknown"])
        self.assertEqual(caught.exception.code, 2)

    def test_crawlers_have_no_main_guard(self):
        for relative in CRAWLER_MAIN_FILES:
            source = (ROOT / relative).read_text(encoding="utf-8")
            tree = ast.parse(source)
            guards = [node for node in ast.walk(tree) if isinstance(node, ast.If) and _is_main_guard(node.test)]
            self.assertEqual(guards, [], relative)
            self.assertNotIn('__name__ == "__main__"', source, relative)
            self.assertNotIn('"__main__" == __name__', source, relative)


class SiteOptionCrawlTests(unittest.IsolatedAsyncioTestCase):
    async def test_site_adidas_nike_runs_only_those_in_registry_order(self):
        order = []
        sites = []
        for site in pipeline.SITE_CRAWLERS:
            async def crawl(key=site.key):
                order.append(key)
                return [sample_product(key)]

            sites.append(replace(site, crawl=crawl))
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "OUTPUT_DIR", directory), \
             patch("main.SITE_CRAWLERS", tuple(sites)), \
             self.assertLogs("main", level="INFO") as logs:
            await pipeline.main("crawl", ["nike", "adidas", "nike"])
        self.assertEqual(order, ["adidas", "nike"])
        self.assertTrue(any("crawl 모드 완료" in line for line in logs.output), logs.output)

    async def test_named_site_overwrites_existing_file(self):
        price = 10000
        sites, mocks = patched_sites(
            lambda key: [sample_product(key, price=price)] if key == "adidas" else []
        )
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "OUTPUT_DIR", directory), \
             patch("main.SITE_CRAWLERS", sites):
            output = Path(directory) / date.today().strftime("%Y%m%d")
            output.mkdir(parents=True)
            target = output / "adidas_products.json"
            target.write_text('[{"old": true}]', encoding="utf-8")
            await pipeline.main("crawl", ["adidas"])
            saved = json.loads(target.read_text(encoding="utf-8"))
        mocks["adidas"].assert_awaited_once()
        for key, mock in mocks.items():
            if key != "adidas":
                mock.assert_not_awaited()
        rate = config.SITE_DISCOUNT_RATES["adidas"]
        self.assertGreater(rate, 0)
        self.assertEqual(saved[0]["price"], price)
        self.assertEqual(saved[0]["sale_price"], int(round(price * (1 - rate / 100))))
        self.assertNotIn("old", json.dumps(saved))

    async def test_crawl_without_site_skips_existing_files(self):
        sites, mocks = patched_sites(lambda key: [sample_product(key)])
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "OUTPUT_DIR", directory), \
             patch("main.SITE_CRAWLERS", sites):
            output = Path(directory) / date.today().strftime("%Y%m%d")
            output.mkdir(parents=True)
            for site in sites:
                (output / f"{site.key}_products.json").write_text("[]", encoding="utf-8")
            await pipeline.main("crawl")
            for site in sites:
                self.assertEqual(
                    (output / f"{site.key}_products.json").read_text(encoding="utf-8"),
                    "[]",
                )
        for mock in mocks.values():
            mock.assert_not_awaited()

    async def test_named_optional_empty_result_keeps_existing_file(self):
        sites, mocks = patched_sites(lambda key: [])
        original = '{"kept": true}\n'
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "OUTPUT_DIR", directory), \
             patch("main.SITE_CRAWLERS", sites), \
             self.assertLogs("main", level="WARNING") as logs:
            output = Path(directory) / date.today().strftime("%Y%m%d")
            output.mkdir(parents=True)
            target = output / "adidas_products.json"
            target.write_text(original, encoding="utf-8")
            await pipeline.main("crawl", ["adidas"])
            self.assertEqual(target.read_text(encoding="utf-8"), original)
        mocks["adidas"].assert_awaited_once()
        for key, mock in mocks.items():
            if key != "adidas":
                mock.assert_not_awaited()
        message = "아디다스 수집 결과 없음 — 파일 저장 생략 (다음 실행 시 재시도)"
        self.assertTrue(any(message in line for line in logs.output), logs.output)

    async def test_full_with_site_keys_uses_skip_rules_for_whole_registry(self):
        sites, mocks = patched_sites(lambda key: [sample_product(key)])
        original = '{"kept": true}\n'
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "OUTPUT_DIR", directory), \
             patch("main.SITE_CRAWLERS", sites), \
             patch("main.FULL_CRAWL_DAYS", frozenset()), \
             patch("main._load_all_products", return_value=[sample_product("adidas")]), \
             patch("main.discover_dates", return_value=["20261111", "20261112"]), \
             patch("main.diff_date_pair", return_value=[]), \
             patch("main.create_browser") as browser, \
             self.assertLogs("main", level="INFO") as logs:
            output = Path(directory) / date.today().strftime("%Y%m%d")
            output.mkdir(parents=True)
            for site in sites:
                (output / f"{site.key}_products.json").write_text(original, encoding="utf-8")
            await pipeline.main("full", ["adidas"])
            for site in sites:
                self.assertEqual(
                    (output / f"{site.key}_products.json").read_text(encoding="utf-8"),
                    original,
                )
        mocks["adidas"].assert_not_awaited()
        for mock in mocks.values():
            mock.assert_not_awaited()
        browser.assert_not_called()
        for site in sites:
            skipped = f"오늘자 파일 존재 — {site.label} 크롤링 스킵"
            self.assertTrue(any(skipped in line for line in logs.output), site.key)
        self.assertFalse(any("지정 사이트 재수집" in line for line in logs.output), logs.output)
