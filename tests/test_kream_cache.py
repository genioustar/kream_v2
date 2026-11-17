"""Kream 상세 모델번호 캐시와 검색 부정 캐시.

오늘 날짜는 테스트가 넣는 date 다. 임시 디렉터리는 TemporaryDirectory() 기본값만 쓴다.
"""
import ast
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import config
import main as pipeline
from common.atomic_io import write_json_atomic
from common.pacing import CrawlBlockedError
from kream.cache import (
    DETAIL_CACHE_FILENAME,
    KREAM_CACHE_DIR_NAME,
    NEGATIVE_CACHE_FILENAME,
    NEGATIVE_CACHE_TTL_DAYS,
    REASON_NO_MATCH,
    REASON_NO_RESULT,
    KreamCache,
    detail_cache_key,
    exclude_fresh_negative,
)
from kream.crawler import (
    _find_verified_kream_product,
    _load_detail_model_number,
    _search_kream_once,
)
from main import _save_kream_cache

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 11, 17)
FRESH_ON = TODAY - timedelta(days=6)
EXPIRED_ON = TODAY - timedelta(days=8)
BOUNDARY_ON = TODAY - timedelta(days=7)
PRODUCT_URL = "https://kream.co.kr/products/156167"
OTHER_URL = "https://kream.co.kr/products/999999"
MODEL_TEXT = "모델번호 IG5349"
PARSED_MODEL = "IG5349"


def _cache_dir() -> tuple[tempfile.TemporaryDirectory, KreamCache]:
    directory = tempfile.TemporaryDirectory()
    return directory, KreamCache(Path(directory.name))


def _card(url: str, name: str = "Expected Product") -> dict:
    return {
        "href": url,
        "name": name,
        "priceStr": "79,000원",
        "tradeStr": "거래 100",
    }


def _detail_page(texts: tuple[str, ...], *, wait_error: Exception | None = None) -> MagicMock:
    page = MagicMock()
    page._kream_blocked_status = None
    page.goto = AsyncMock(return_value=MagicMock(status=200))
    if wait_error is None:
        page.wait_for_selector = AsyncMock()
    else:
        page.wait_for_selector = AsyncMock(side_effect=wait_error)
    locator = MagicMock()
    locator.all_text_contents = AsyncMock(return_value=list(texts))
    page.locator.return_value = locator
    return page


class KreamCacheLoadTests(unittest.TestCase):
    def test_missing_files_start_empty_without_warning(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        with self.assertNoLogs("kream.cache", level="WARNING"):
            cache.load()
        self.assertEqual(cache.detail_models, {})
        self.assertEqual(cache.negative, {})

    def test_broken_json_warns_and_starts_empty(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        cache.detail_path.write_text("{", encoding="utf-8")
        cache.negative_path.write_text("{", encoding="utf-8")
        with self.assertLogs("kream.cache", level="WARNING") as captured:
            cache.load()
        self.assertEqual(cache.detail_models, {})
        self.assertEqual(cache.negative, {})
        self.assertGreaterEqual(len(captured.output), 2)
        self.assertTrue(all("빈 캐시" in line for line in captured.output))

    def test_wrong_shape_warns_and_starts_empty(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        cache.detail_path.write_text(
            json.dumps({PRODUCT_URL: 12, OTHER_URL: PARSED_MODEL}),
            encoding="utf-8",
        )
        cache.negative_path.write_text(
            json.dumps({
                "KEEP": {"checked_at": "2026-11-11", "reason": REASON_NO_RESULT},
                "BAD": {"checked_at": "2026-11-11", "reason": "timeout"},
            }),
            encoding="utf-8",
        )
        with self.assertLogs("kream.cache", level="WARNING") as captured:
            cache.load()
        self.assertEqual(cache.detail_models, {})
        self.assertEqual(cache.negative, {})
        self.assertTrue(all("빈 캐시" in line for line in captured.output))

    def test_bad_detail_file_does_not_drop_valid_negative_file(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        cache.detail_path.write_text("[]", encoding="utf-8")
        cache.negative_path.write_text(
            json.dumps({"KEEP": {"checked_at": "2026-11-11", "reason": REASON_NO_MATCH}}),
            encoding="utf-8",
        )
        with self.assertLogs("kream.cache", level="WARNING"):
            cache.load()
        self.assertEqual(cache.detail_models, {})
        self.assertTrue(cache.negative_is_fresh("KEEP", date(2026, 11, 11)))

    def test_invalid_calendar_date_is_a_format_mismatch(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        cache.negative_path.write_text(
            json.dumps({"M": {"checked_at": "2026-11-31", "reason": REASON_NO_RESULT}}),
            encoding="utf-8",
        )
        with self.assertLogs("kream.cache", level="WARNING"):
            cache.load()
        self.assertEqual(cache.negative, {})


class KreamCacheTtlTests(unittest.TestCase):
    def test_six_days_is_fresh_and_eight_days_is_expired(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        cache.remember_negative("FRESH", REASON_NO_RESULT, FRESH_ON)
        cache.remember_negative("STALE", REASON_NO_MATCH, EXPIRED_ON)
        cache.remember_negative("BOUNDARY", REASON_NO_RESULT, BOUNDARY_ON)
        self.assertTrue(cache.negative_is_fresh("FRESH", TODAY))
        self.assertFalse(cache.negative_is_fresh("STALE", TODAY))
        self.assertFalse(cache.negative_is_fresh("BOUNDARY", TODAY))
        self.assertFalse(cache.negative_is_fresh("ABSENT", TODAY))

    def test_future_checked_at_is_not_fresh(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        future_on = TODAY + timedelta(days=1)
        cache.remember_negative("FUTURE", REASON_NO_RESULT, future_on)
        self.assertFalse(cache.negative_is_fresh("FUTURE", TODAY))
        self.assertEqual(exclude_fresh_negative(["FUTURE"], cache, TODAY), ["FUTURE"])

    def test_fresh_negative_models_leave_the_search_list(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        cache.remember_negative("FRESH", REASON_NO_RESULT, FRESH_ON)
        cache.remember_negative("STALE", REASON_NO_MATCH, EXPIRED_ON)
        kept = exclude_fresh_negative(
            ["FRESH", "STALE", "NEW"],
            cache,
            TODAY,
        )
        self.assertEqual(kept, ["STALE", "NEW"])


class KreamCacheSaveTests(unittest.TestCase):
    def test_save_uses_write_json_atomic_and_reloads(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        nested = Path(directory.name) / "kream_cache"
        cache = KreamCache(nested)
        cache.remember_detail_model(PRODUCT_URL, PARSED_MODEL)
        cache.remember_negative("M-1", REASON_NO_RESULT, FRESH_ON)
        with patch("kream.cache.write_json_atomic", wraps=write_json_atomic) as spy:
            cache.save()
        self.assertEqual(spy.call_count, 2)
        self.assertEqual(
            {call.args[0].name for call in spy.call_args_list},
            {DETAIL_CACHE_FILENAME, NEGATIVE_CACHE_FILENAME},
        )
        loaded = KreamCache(nested)
        loaded.load()
        self.assertEqual(loaded.detail_model(PRODUCT_URL), PARSED_MODEL)
        self.assertTrue(loaded.negative_is_fresh("M-1", TODAY))

    def test_save_failure_warns_and_does_not_raise(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        cache.remember_negative("M-1", REASON_NO_RESULT, TODAY)
        with patch("kream.cache.write_json_atomic", side_effect=OSError("disk")):
            with self.assertLogs("main", level="WARNING") as captured:
                _save_kream_cache(cache)
        self.assertTrue(any("파이프라인은 계속" in line for line in captured.output))


class KreamSearchListWiringTests(unittest.TestCase):
    def test_main_filters_with_injected_today_before_search(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        main_fn = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
        )
        calls = []
        for node in ast.walk(main_fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                calls.append(node)
        exclude = next(node for node in calls if node.func.id == "exclude_fresh_negative")
        search = next(node for node in calls if node.func.id == "iter_kream_results")
        save = next(node for node in calls if node.func.id == "_save_kream_cache")
        self.assertLess(exclude.lineno, search.lineno)
        self.assertLess(search.lineno, save.lineno)
        self.assertEqual(ast.unparse(exclude.args[2]), "date.today()")
        self.assertIn("부정 캐시로 %s개 제외 (유효기간 %s일)", source)
        self.assertIn("검색할 모델 없음 — 브라우저를 열지 않습니다.", source)


def _write_negative(directory: str, entries: dict[str, str]) -> None:
    cache_dir = Path(directory) / KREAM_CACHE_DIR_NAME
    cache_dir.mkdir(parents=True)
    payload = {
        model_name: {"checked_at": checked_on, "reason": REASON_NO_RESULT}
        for model_name, checked_on in entries.items()
    }
    (cache_dir / NEGATIVE_CACHE_FILENAME).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


class KreamSearchFilterBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_negative_models_are_excluded_from_search(self):
        seen: list[list[str]] = []

        async def results(models, context, *, cache):
            seen.append(list(models))
            for name in models:
                yield name, []

        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=MagicMock())
        manager.__aexit__ = AsyncMock(return_value=False)
        products = [MagicMock(model_name="FRESH"), MagicMock(model_name="KEEP")]
        fresh_on = (date.today() - timedelta(days=6)).isoformat()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "OUTPUT_DIR", directory), \
             patch("main._load_all_products", return_value=products), \
             patch("main.discover_dates", return_value=[]), \
             patch("main.create_browser", return_value=manager) as browser, \
             patch("main.iter_kream_results", results), \
             patch("main.find_arbitrage", return_value=[]):
            _write_negative(directory, {"FRESH": fresh_on})
            with self.assertLogs("main", level="INFO") as captured:
                await pipeline.main("kream")
        self.assertEqual(seen, [["KEEP"]])
        browser.assert_called_once()
        expected = "부정 캐시로 1개 제외 (유효기간 %s일)" % NEGATIVE_CACHE_TTL_DAYS
        self.assertTrue(any(expected in line for line in captured.output))

    async def test_all_fresh_negatives_do_not_open_browser(self):
        async def results(*args, **kwargs):
            raise AssertionError("유효 캐시만 남으면 검색하면 안 된다")
            yield "", []

        products = [MagicMock(model_name="FRESH"), MagicMock(model_name="ALSO")]
        fresh_on = (date.today() - timedelta(days=6)).isoformat()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "OUTPUT_DIR", directory), \
             patch("main._load_all_products", return_value=products), \
             patch("main.discover_dates", return_value=[]), \
             patch("main.create_browser") as browser, \
             patch("main.iter_kream_results", results):
            _write_negative(directory, {"FRESH": fresh_on, "ALSO": fresh_on})
            with self.assertLogs("main", level="INFO") as captured:
                await pipeline.main("kream")
        browser.assert_not_called()
        expected = "부정 캐시로 2개 제외 (유효기간 %s일)" % NEGATIVE_CACHE_TTL_DAYS
        self.assertTrue(any(expected in line for line in captured.output))
        self.assertTrue(any("검색할 모델 없음 — 브라우저를 열지 않습니다." in line for line in captured.output))


class KreamDetailCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_hit_returns_without_goto(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        cache.remember_detail_model(PRODUCT_URL, PARSED_MODEL)
        page = MagicMock()
        page.goto = AsyncMock()
        with self.assertLogs("kream.crawler", level="DEBUG") as captured:
            model_number = await _load_detail_model_number(
                page, PRODUCT_URL, cache=cache
            )
        self.assertEqual(model_number, PARSED_MODEL)
        page.goto.assert_not_awaited()
        self.assertTrue(any("상세 모델번호 캐시 사용" in line for line in captured.output))

    async def test_miss_records_only_after_parse_succeeds(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        page = _detail_page((MODEL_TEXT, MODEL_TEXT))
        with patch("kream.crawler._is_blocked_page", new=AsyncMock(return_value=False)):
            model_number = await _load_detail_model_number(
                page, PRODUCT_URL, cache=cache
            )
        self.assertEqual(model_number, PARSED_MODEL)
        page.goto.assert_awaited()
        self.assertEqual(cache.detail_model(PRODUCT_URL), PARSED_MODEL)

    async def test_miss_parse_failure_does_not_record(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        page = _detail_page(("사이즈 270",), wait_error=None)
        with patch("kream.crawler._is_blocked_page", new=AsyncMock(return_value=False)):
            model_number = await _load_detail_model_number(
                page, PRODUCT_URL, cache=cache
            )
        self.assertIsNone(model_number)
        page.goto.assert_awaited()
        self.assertIsNone(cache.detail_model(PRODUCT_URL))

    async def test_miss_timeout_does_not_record(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        page = _detail_page((MODEL_TEXT,), wait_error=TimeoutError())
        with patch("kream.crawler._is_blocked_page", new=AsyncMock(return_value=False)):
            model_number = await _load_detail_model_number(
                page, PRODUCT_URL, cache=cache
            )
        self.assertIsNone(model_number)
        page.goto.assert_awaited()
        self.assertIsNone(cache.detail_model(PRODUCT_URL))

    def test_detail_key_keeps_the_observed_product_url(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        self.assertEqual(detail_cache_key(PRODUCT_URL), PRODUCT_URL)
        cache.remember_detail_model(PRODUCT_URL, PARSED_MODEL)
        self.assertEqual(cache.detail_model(PRODUCT_URL), PARSED_MODEL)
        self.assertIsNone(cache.detail_model(PRODUCT_URL + "/"))
        self.assertIsNone(cache.detail_model(PRODUCT_URL + "?size=270"))


class KreamNegativeRecordTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        self.cache = cache

    async def test_no_result_phrase_records_even_when_recommendation_cards_exist(self):
        page = MagicMock()
        page._kream_blocked_status = None
        page.evaluate = AsyncMock(return_value=[PRODUCT_URL] * 50)
        page.keyboard.press = AsyncMock()
        page.title = AsyncMock(return_value="검색")
        page.locator.return_value.count = AsyncMock(return_value=1)
        page.wait_for_selector = AsyncMock(side_effect=AssertionError("카드 대기로 내려가면 안 된다"))
        search_input = MagicMock()
        search_input.click = AsyncMock()
        verified = AsyncMock(side_effect=AssertionError("카드로 결과 없음을 판정하면 안 된다"))
        with patch("kream.crawler._open_search_and_get_input", new=AsyncMock(return_value=search_input)), \
             patch("kream.crawler._human_wait", new=AsyncMock()), \
             patch("kream.crawler._human_type", new=AsyncMock()), \
             patch("kream.crawler._wait_for_search_render", new=AsyncMock(return_value=True)), \
             patch("kream.crawler._is_blocked_page", new=AsyncMock(return_value=False)), \
             patch("kream.crawler._product_card_hrefs", new=AsyncMock(return_value=[PRODUCT_URL] * 50)), \
             patch("kream.crawler._location_href", new=AsyncMock(return_value="https://kream.co.kr/search?keyword=NOPE")), \
             patch("kream.crawler._find_verified_kream_product", new=verified):
            result = await _search_kream_once("NOPE", page, cache=self.cache)
        self.assertEqual(result, [])
        self.assertEqual(self.cache.negative["NOPE"]["reason"], REASON_NO_RESULT)
        self.assertEqual(
            (date.today() - date.fromisoformat(self.cache.negative["NOPE"]["checked_at"])).days,
            0,
        )
        verified.assert_not_awaited()

    async def test_zero_cards_does_not_record_negative(self):
        page = MagicMock()
        page._kream_blocked_status = None
        page.evaluate = AsyncMock(return_value=[])
        page.keyboard.press = AsyncMock()
        page.title = AsyncMock(return_value="검색")
        page.locator.return_value.count = AsyncMock(return_value=0)
        page.wait_for_selector = AsyncMock()
        search_input = MagicMock()
        search_input.click = AsyncMock()
        with patch("kream.crawler._open_search_and_get_input", new=AsyncMock(return_value=search_input)), \
             patch("kream.crawler._human_wait", new=AsyncMock()), \
             patch("kream.crawler._human_type", new=AsyncMock()), \
             patch("kream.crawler._wait_for_search_render", new=AsyncMock(return_value=True)), \
             patch("kream.crawler._is_blocked_page", new=AsyncMock(return_value=False)), \
             patch("kream.crawler._product_card_hrefs", new=AsyncMock(return_value=[])), \
             patch("kream.crawler._location_href", new=AsyncMock(return_value="https://kream.co.kr/search?keyword=EMPTY")):
            result = await _search_kream_once("EMPTY", page, cache=self.cache)
        self.assertEqual(result, [])
        self.assertNotIn("EMPTY", self.cache.negative)

    async def _search(self, model_name, *, rendered, keyword, no_result_count, cards):
        page = MagicMock()
        page._kream_blocked_status = None
        page.keyboard.press = AsyncMock()
        page.title = AsyncMock(return_value="검색")
        page.locator.return_value.count = AsyncMock(return_value=no_result_count)
        page.wait_for_selector = AsyncMock()
        page.evaluate = AsyncMock(return_value=cards)
        page.goto = AsyncMock()
        search_input = MagicMock()
        search_input.click = AsyncMock()
        href = "https://kream.co.kr/search?keyword=%s" % keyword
        with patch("kream.crawler._open_search_and_get_input", new=AsyncMock(return_value=search_input)), \
             patch("kream.crawler._human_wait", new=AsyncMock()), \
             patch("kream.crawler._human_type", new=AsyncMock()), \
             patch("kream.crawler._wait_for_search_render", new=AsyncMock(return_value=rendered)), \
             patch("kream.crawler._is_blocked_page", new=AsyncMock(return_value=False)), \
             patch("kream.crawler._product_card_hrefs", new=AsyncMock(return_value=[card["href"] for card in cards])), \
             patch("kream.crawler._location_href", new=AsyncMock(return_value=href)):
            return await _search_kream_once(model_name, page, cache=self.cache)

    async def test_unrendered_different_keyword_does_not_record_no_result(self):
        result = await self._search(
            "NEW1", rendered=False, keyword="PREV", no_result_count=1, cards=[]
        )
        self.assertEqual(result, [])
        self.assertNotIn("NEW1", self.cache.negative)

    async def test_unrendered_different_keyword_does_not_record_no_match(self):
        card = _card(PRODUCT_URL, name="Previous model shoe")
        self.cache.remember_detail_model(PRODUCT_URL, "PREV")
        result = await self._search(
            "NEW1", rendered=False, keyword="PREV", no_result_count=0, cards=[card]
        )
        self.assertEqual(result, [])
        self.assertNotIn("NEW1", self.cache.negative)

    async def test_unrendered_same_keyword_does_not_record_no_result(self):
        result = await self._search(
            "NEW1", rendered=False, keyword="NEW1", no_result_count=1, cards=[]
        )
        self.assertEqual(result, [])
        self.assertNotIn("NEW1", self.cache.negative)

    async def test_unrendered_same_keyword_does_not_record_no_match(self):
        card = _card(PRODUCT_URL, name="Previous model shoe")
        self.cache.remember_detail_model(PRODUCT_URL, "PREV")
        result = await self._search(
            "NEW1", rendered=False, keyword="NEW1", no_result_count=0, cards=[card]
        )
        self.assertEqual(result, [])
        self.assertNotIn("NEW1", self.cache.negative)

    async def test_rendered_mismatch_records_no_match(self):
        card = _card(PRODUCT_URL, name="Previous model shoe")
        self.cache.remember_detail_model(PRODUCT_URL, "PREV")
        result = await self._search(
            "NEW1", rendered=True, keyword="NEW1", no_result_count=0, cards=[card]
        )
        self.assertEqual(result, [])
        self.assertEqual(self.cache.negative["NEW1"]["reason"], REASON_NO_MATCH)

    async def test_card_selector_timeout_returns_none_without_negative(self):
        page = MagicMock()
        page._kream_blocked_status = None
        page.keyboard.press = AsyncMock()
        page.title = AsyncMock(return_value="검색")
        page.content = AsyncMock(return_value="x" * 1000)
        page.locator.return_value.count = AsyncMock(return_value=0)
        page.wait_for_selector = AsyncMock(side_effect=TimeoutError("cards"))
        page.evaluate = AsyncMock(side_effect=AssertionError("카드 추출로 내려가면 안 된다"))
        search_input = MagicMock()
        search_input.click = AsyncMock()
        with patch("kream.crawler._open_search_and_get_input", new=AsyncMock(return_value=search_input)), \
             patch("kream.crawler._human_wait", new=AsyncMock()), \
             patch("kream.crawler._human_type", new=AsyncMock()), \
             patch("kream.crawler._wait_for_search_render", new=AsyncMock(return_value=True)), \
             patch("kream.crawler._is_blocked_page", new=AsyncMock(return_value=False)), \
             patch("kream.crawler._product_card_hrefs", new=AsyncMock(return_value=[])), \
             patch("kream.crawler._location_href", new=AsyncMock(return_value="https://kream.co.kr/search?keyword=WANTED")):
            result = await _search_kream_once("WANTED", page, cache=self.cache)
        self.assertIsNone(result)
        self.assertEqual(self.cache.negative, {})

    async def test_all_confirmed_mismatches_record_no_match_without_goto(self):
        self.cache.remember_detail_model(PRODUCT_URL, "OTHER-1")
        self.cache.remember_detail_model(OTHER_URL, "OTHER-2")
        page = MagicMock()
        page.goto = AsyncMock()
        product = await _find_verified_kream_product(
            "WANTED",
            [_card(PRODUCT_URL), _card(OTHER_URL)],
            page,
            cache=self.cache,
            allow_negative=True,
        )
        self.assertIsNone(product)
        self.assertEqual(self.cache.negative["WANTED"]["reason"], REASON_NO_MATCH)
        page.goto.assert_not_awaited()

    async def test_any_detail_failure_does_not_record_no_match(self):
        self.cache.remember_detail_model(PRODUCT_URL, "OTHER-1")
        page = _detail_page((MODEL_TEXT,), wait_error=TimeoutError())
        with patch("kream.crawler._is_blocked_page", new=AsyncMock(return_value=False)):
            product = await _find_verified_kream_product(
                "WANTED",
                [_card(PRODUCT_URL), _card(OTHER_URL)],
                page,
                cache=self.cache,
                allow_negative=True,
            )
        self.assertIsNone(product)
        self.assertNotIn("WANTED", self.cache.negative)
        page.goto.assert_awaited_once()

    async def test_missing_price_prevents_no_match_when_others_mismatch(self):
        self.cache.remember_detail_model(PRODUCT_URL, "OTHER-1")
        incomplete = _card(OTHER_URL)
        incomplete["priceStr"] = None
        product = await _find_verified_kream_product(
            "WANTED",
            [incomplete, _card(PRODUCT_URL)],
            MagicMock(),
            cache=self.cache,
            allow_negative=True,
        )
        self.assertIsNone(product)
        self.assertNotIn("WANTED", self.cache.negative)

    async def test_candidate_exception_does_not_record_no_match(self):
        with patch(
            "kream.crawler._load_detail_model_number",
            new=AsyncMock(side_effect=RuntimeError("detail failed")),
        ):
            product = await _find_verified_kream_product(
                "WANTED",
                [_card(PRODUCT_URL)],
                MagicMock(),
                cache=self.cache,
                allow_negative=True,
            )
        self.assertIsNone(product)
        self.assertNotIn("WANTED", self.cache.negative)

    async def test_blocked_detail_does_not_record_no_match(self):
        with patch(
            "kream.crawler._load_detail_model_number",
            new=AsyncMock(side_effect=CrawlBlockedError("429")),
        ):
            with self.assertRaises(CrawlBlockedError):
                await _find_verified_kream_product(
                    "WANTED",
                    [_card(PRODUCT_URL)],
                    MagicMock(),
                    cache=self.cache,
                    allow_negative=True,
                )
        self.assertNotIn("WANTED", self.cache.negative)

    async def test_match_drops_existing_negative_entry(self):
        self.cache.remember_negative("IG5349", REASON_NO_RESULT, EXPIRED_ON)
        self.cache.remember_detail_model(PRODUCT_URL, PARSED_MODEL)
        page = MagicMock()
        page.goto = AsyncMock()
        product = await _find_verified_kream_product(
            "IG5349",
            [_card(PRODUCT_URL)],
            page,
            cache=self.cache,
            allow_negative=True,
        )
        self.assertIsNotNone(product)
        self.assertEqual(product.model_name, PARSED_MODEL)
        self.assertNotIn("IG5349", self.cache.negative)
        page.goto.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
