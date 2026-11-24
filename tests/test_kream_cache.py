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
from common.models import KreamProduct, MarketplaceProduct
from common.pacing import CrawlBlockedError
from kream.cache import (
    DETAIL_CACHE_FILENAME,
    KREAM_CACHE_DIR_NAME,
    NEGATIVE_CACHE_FILENAME,
    REASON_LOW_TRADE,
    REASON_NO_MATCH,
    REASON_NO_RESULT,
    REASON_PRICE_GAP,
    KreamCache,
    detail_cache_key,
    exclude_fresh_negative,
    judge_unprofitable,
    lowest_prices_by_model,
    record_unprofitable_models,
)
from kream.comparator import find_arbitrage
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

# 사유별 유효기간 기대값. 구현 상수를 import 하지 않고 숫자로 적어 표 값이 바뀌면 깨지게 한다.
EXPECTED_TTL_DAYS = {
    REASON_NO_RESULT: 7,
    REASON_NO_MATCH: 7,
    REASON_LOW_TRADE: 7,
    REASON_PRICE_GAP: 3,
}
MIN_TRADE = config.MIN_TRADE_COUNT
MIN_GAP = config.MIN_PRICE_DIFF
MODEL = "IG5349"
BASE_PRICE = 80000
ENOUGH_TRADE = MIN_TRADE + 50


def _cache_dir() -> tuple[tempfile.TemporaryDirectory, KreamCache]:
    directory = tempfile.TemporaryDirectory()
    return directory, KreamCache(Path(directory.name))


def _marketplace(
    model_name: str,
    price: int,
    *,
    sale_price: int | None = None,
    site: str = "musinsa",
) -> MarketplaceProduct:
    return MarketplaceProduct(
        site_name=site,
        product_name="상품",
        model_name=model_name,
        price=price,
        url=None,
        crawled_at="2026-11-24T00:00:00",
        sale_price=sale_price,
    )


def _kream(price: int, trade_count: int, model_name: str = MODEL) -> KreamProduct:
    return KreamProduct(
        model_name=model_name,
        kream_name="Kream 상품",
        kream_price=price,
        trade_count=trade_count,
        kream_url=PRODUCT_URL,
    )


def _remember(
    cache: KreamCache,
    model_name: str,
    reason: str,
    checked_on: date,
    *,
    kream_price: int = BASE_PRICE + 10000,
    marketplace_price: int = BASE_PRICE,
) -> None:
    """price_gap 만 가격을 함께 넘긴다."""
    if reason == REASON_PRICE_GAP:
        cache.remember_negative(
            model_name, reason, checked_on,
            kream_price=kream_price, marketplace_price=marketplace_price,
        )
    else:
        cache.remember_negative(model_name, reason, checked_on)


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
                "BAD": {"checked_at": "2026-11-11"},
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

    def test_legacy_file_without_price_fields_loads_unchanged(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        legacy = {
            "RESULT": {"checked_at": "2026-11-15", "reason": REASON_NO_RESULT},
            "MATCH": {"checked_at": "2026-11-15", "reason": REASON_NO_MATCH},
        }
        cache.negative_path.write_text(json.dumps(legacy), encoding="utf-8")
        with self.assertNoLogs("kream.cache", level="WARNING"):
            cache.load()
        self.assertEqual(cache.negative, legacy)
        self.assertEqual(
            exclude_fresh_negative(["RESULT", "MATCH", "NEW"], cache, TODAY, {}),
            (["NEW"], {REASON_NO_RESULT: 1, REASON_NO_MATCH: 1, REASON_LOW_TRADE: 0, REASON_PRICE_GAP: 0}),
        )

    def test_new_reasons_round_trip_through_the_file(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        _remember(cache, "LOW", REASON_LOW_TRADE, TODAY)
        _remember(cache, "GAP", REASON_PRICE_GAP, TODAY, kream_price=90000, marketplace_price=84000)
        cache.save()
        saved = json.loads(cache.negative_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["LOW"], {
            "checked_at": TODAY.isoformat(),
            "reason": REASON_LOW_TRADE,
            "min_trade_count": MIN_TRADE,
        })
        self.assertEqual(saved["GAP"], {
            "checked_at": TODAY.isoformat(),
            "reason": REASON_PRICE_GAP,
            "kream_price": 90000,
            "marketplace_price": 84000,
            "min_price_diff": MIN_GAP,
        })
        loaded = KreamCache(Path(directory.name))
        with self.assertNoLogs("kream.cache", level="WARNING"):
            loaded.load()
        self.assertEqual(loaded.negative, cache.negative)

    def test_unknown_reason_is_warned_ignored_and_not_excluded(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        cache.negative_path.write_text(
            json.dumps({
                "OLD": {"checked_at": TODAY.isoformat(), "reason": REASON_NO_RESULT},
                "NEWER": {"checked_at": TODAY.isoformat(), "reason": "future_reason", "extra": 1},
                "ODD": {"checked_at": TODAY.isoformat(), "reason": ["not", "a", "string"]},
            }),
            encoding="utf-8",
        )
        with self.assertLogs("kream.cache", level="WARNING") as captured:
            cache.load()
        self.assertEqual(list(cache.negative), ["OLD"])
        self.assertEqual(len(captured.output), 1)
        self.assertIn("future_reason", captured.output[0])
        kept, _ = exclude_fresh_negative(["OLD", "NEWER", "ODD"], cache, TODAY, {})
        self.assertEqual(kept, ["NEWER", "ODD"])

    def test_bad_price_or_criterion_fields_warn_and_the_model_is_searched_again(self):
        valid_gap = {
            "checked_at": "2026-11-15", "reason": REASON_PRICE_GAP,
            "kream_price": 90000, "marketplace_price": 84000, "min_price_diff": MIN_GAP,
        }
        valid_low = {"checked_at": "2026-11-15", "reason": REASON_LOW_TRADE, "min_trade_count": MIN_TRADE}
        for label, entry in {
            "가격 필드 없음": {"checked_at": "2026-11-15", "reason": REASON_PRICE_GAP, "min_price_diff": MIN_GAP},
            "문자열 크림 가격": {**valid_gap, "kream_price": "90000"},
            "실수 크림 가격": {**valid_gap, "kream_price": 90000.5},
            "bool 크림 가격": {**valid_gap, "kream_price": True},
            "문자열 판매처 가격": {**valid_gap, "marketplace_price": "84000"},
            "실수 판매처 가격": {**valid_gap, "marketplace_price": 84000.5},
            "bool 판매처 가격": {**valid_gap, "marketplace_price": True},
            "문자열 가격차 기준": {**valid_gap, "min_price_diff": str(MIN_GAP)},
            "bool 가격차 기준": {**valid_gap, "min_price_diff": True},
            "문자열 거래량 기준": {**valid_low, "min_trade_count": str(MIN_TRADE)},
            "bool 거래량 기준": {**valid_low, "min_trade_count": True},
            "다른 사유에 가격 필드": {**valid_low, "kream_price": 90000, "marketplace_price": 84000},
            "다른 사유의 기준 필드": {
                "checked_at": "2026-11-15", "reason": REASON_NO_RESULT, "min_trade_count": MIN_TRADE,
            },
            "사유에 맞지 않는 기준 필드": {**valid_low, "min_price_diff": MIN_GAP, "min_trade_count": MIN_TRADE},
        }.items():
            with self.subTest(label):
                directory, cache = _cache_dir()
                self.addCleanup(directory.cleanup)
                cache.negative_path.write_text(json.dumps({"M": entry}), encoding="utf-8")
                with self.assertLogs("kream.cache", level="WARNING") as captured:
                    cache.load()
                self.assertEqual(cache.negative, {})
                self.assertTrue(all("빈 캐시" in line for line in captured.output))
                kept, excluded = exclude_fresh_negative(["M"], cache, TODAY, {"M": 84000})
                self.assertEqual(kept, ["M"])
                self.assertEqual(sum(excluded.values()), 0)


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

    def test_each_reason_is_fresh_until_its_own_ttl(self):
        for reason, ttl_days in EXPECTED_TTL_DAYS.items():
            for age_days, expected in (
                (0, True), (ttl_days - 1, True), (ttl_days, False), (ttl_days + 1, False),
            ):
                with self.subTest(reason=reason, age_days=age_days):
                    directory, cache = _cache_dir()
                    self.addCleanup(directory.cleanup)
                    _remember(cache, "M", reason, TODAY - timedelta(days=age_days))
                    self.assertEqual(
                        cache.negative_is_fresh("M", TODAY, BASE_PRICE), expected
                    )

    def test_future_checked_at_is_not_fresh(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        future_on = TODAY + timedelta(days=1)
        for reason in EXPECTED_TTL_DAYS:
            with self.subTest(reason=reason):
                _remember(cache, "FUTURE", reason, future_on)
                self.assertFalse(cache.negative_is_fresh("FUTURE", TODAY, BASE_PRICE))
                kept, excluded = exclude_fresh_negative(
                    ["FUTURE"], cache, TODAY, {"FUTURE": BASE_PRICE}
                )
                self.assertEqual(kept, ["FUTURE"])
                self.assertEqual(sum(excluded.values()), 0)

    def test_fresh_negative_models_leave_the_search_list(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        cache.remember_negative("FRESH", REASON_NO_RESULT, FRESH_ON)
        cache.remember_negative("STALE", REASON_NO_MATCH, EXPIRED_ON)
        kept, excluded = exclude_fresh_negative(
            ["FRESH", "STALE", "NEW"],
            cache,
            TODAY,
            {},
        )
        self.assertEqual(kept, ["STALE", "NEW"])
        self.assertEqual(excluded[REASON_NO_RESULT], 1)

    def test_exclusion_counts_are_split_by_reason_and_keep_order(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        _remember(cache, "R1", REASON_NO_RESULT, TODAY)
        _remember(cache, "L1", REASON_LOW_TRADE, TODAY - timedelta(days=6))
        _remember(cache, "L2", REASON_LOW_TRADE, TODAY - timedelta(days=1))
        _remember(cache, "G1", REASON_PRICE_GAP, TODAY - timedelta(days=2))
        _remember(cache, "L-OLD", REASON_LOW_TRADE, TODAY - timedelta(days=7))
        prices = {name: BASE_PRICE for name in ("R1", "L1", "L2", "G1", "L-OLD", "NEW")}
        kept, excluded = exclude_fresh_negative(
            ["NEW", "L-OLD", "R1", "L1", "G1", "L2"], cache, TODAY, prices
        )
        self.assertEqual(kept, ["NEW", "L-OLD"])
        self.assertEqual(excluded, {
            REASON_NO_RESULT: 1, REASON_NO_MATCH: 0, REASON_LOW_TRADE: 2, REASON_PRICE_GAP: 1,
        })

    def test_price_gap_is_searched_again_when_lowest_price_dropped(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        for name in ("LOWER", "SAME", "HIGHER", "UNKNOWN"):
            _remember(cache, name, REASON_PRICE_GAP, TODAY - timedelta(days=1), marketplace_price=BASE_PRICE)
        kept, excluded = exclude_fresh_negative(
            ["LOWER", "SAME", "HIGHER", "UNKNOWN"],
            cache,
            TODAY,
            {"LOWER": BASE_PRICE - 1, "SAME": BASE_PRICE, "HIGHER": BASE_PRICE + 1000},
        )
        self.assertEqual(kept, ["LOWER", "UNKNOWN"])
        self.assertEqual(excluded[REASON_PRICE_GAP], 2)

    def test_price_drop_does_not_affect_other_reasons(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        _remember(cache, "LOW", REASON_LOW_TRADE, TODAY)
        _remember(cache, "RES", REASON_NO_RESULT, TODAY)
        kept, _ = exclude_fresh_negative(["LOW", "RES"], cache, TODAY, {"LOW": 1, "RES": 1})
        self.assertEqual(kept, [])

    def test_remember_negative_rejects_wrong_price_arguments(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        with self.assertRaises(ValueError):
            cache.remember_negative("M", "timeout", TODAY)
        with self.assertRaises(ValueError):
            cache.remember_negative("M", REASON_PRICE_GAP, TODAY)
        with self.assertRaises(ValueError):
            cache.remember_negative("M", REASON_PRICE_GAP, TODAY, kream_price=90000)
        with self.assertRaises(ValueError):
            cache.remember_negative("M", REASON_LOW_TRADE, TODAY, kream_price=90000, marketplace_price=80000)
        self.assertEqual(cache.negative, {})


class KreamCriterionTests(unittest.TestCase):
    """기록 시점 기준값이 현재 config 와 다르면 옛 판정이 모델을 가리지 않도록 다시 검색한다."""

    NAMES = ["LOW", "GAP", "RES", "MATCH"]

    def setUp(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        self.cache = cache

    def _record_under(self, min_trade: int, min_gap: int) -> None:
        with patch("kream.cache.MIN_TRADE_COUNT", min_trade), patch("kream.cache.MIN_PRICE_DIFF", min_gap):
            _remember(self.cache, "LOW", REASON_LOW_TRADE, TODAY)
            _remember(self.cache, "GAP", REASON_PRICE_GAP, TODAY)
            self.cache.remember_negative("RES", REASON_NO_RESULT, TODAY)
            self.cache.remember_negative("MATCH", REASON_NO_MATCH, TODAY)

    def _kept_under(self, min_trade: int, min_gap: int) -> list[str]:
        prices = dict.fromkeys(self.NAMES, BASE_PRICE)
        with patch("kream.cache.MIN_TRADE_COUNT", min_trade), patch("kream.cache.MIN_PRICE_DIFF", min_gap):
            kept, _ = exclude_fresh_negative(self.NAMES, self.cache, TODAY, prices)
        return kept

    def test_same_criteria_keep_every_entry_excluded(self):
        self._record_under(100, 5000)
        self.assertEqual(self._kept_under(100, 5000), [])

    def test_lowered_criteria_return_both_models_to_the_search_list(self):
        self._record_under(100, 5000)
        self.assertEqual(self._kept_under(50, 3000), ["LOW", "GAP"])

    def test_raised_criteria_return_both_models_to_the_search_list(self):
        self._record_under(100, 5000)
        self.assertEqual(self._kept_under(200, 10000), ["LOW", "GAP"])

    def test_each_reason_follows_only_its_own_criterion(self):
        self._record_under(100, 5000)
        self.assertEqual(self._kept_under(50, 5000), ["LOW"])
        self.assertEqual(self._kept_under(100, 3000), ["GAP"])

    def test_search_misses_ignore_the_criteria(self):
        self._record_under(100, 5000)
        kept = self._kept_under(1, 1)
        self.assertNotIn("RES", kept)
        self.assertNotIn("MATCH", kept)

    def test_criteria_survive_save_and_load(self):
        self._record_under(100, 5000)
        self.cache.save()
        loaded = KreamCache(self.cache.directory)
        with self.assertNoLogs("kream.cache", level="WARNING"):
            loaded.load()
        self.cache = loaded
        self.assertEqual(self._kept_under(100, 5000), [])
        self.assertEqual(self._kept_under(50, 3000), ["LOW", "GAP"])

    def test_entries_recorded_by_the_pipeline_carry_the_criteria(self):
        record_unprofitable_models(
            self.cache,
            {
                "LOW": [_kream(BASE_PRICE + MIN_GAP, MIN_TRADE - 1)],
                "GAP": [_kream(BASE_PRICE + MIN_GAP - 1, MIN_TRADE)],
            },
            {"LOW": BASE_PRICE, "GAP": BASE_PRICE},
            TODAY,
        )
        self.cache.save()
        loaded = KreamCache(self.cache.directory)
        loaded.load()
        self.cache = loaded
        self.assertEqual(self._kept_under(MIN_TRADE, MIN_GAP), ["RES", "MATCH"])
        self.assertEqual(self._kept_under(MIN_TRADE - 1, MIN_GAP), ["LOW", "RES", "MATCH"])
        self.assertEqual(self._kept_under(MIN_TRADE, MIN_GAP - 1), ["GAP", "RES", "MATCH"])

    def test_entries_without_criterion_field_are_searched_again_without_warning(self):
        self.cache.negative_path.write_text(
            json.dumps({
                "LOW": {"checked_at": TODAY.isoformat(), "reason": REASON_LOW_TRADE},
                "GAP": {
                    "checked_at": TODAY.isoformat(), "reason": REASON_PRICE_GAP,
                    "kream_price": BASE_PRICE + 1000, "marketplace_price": BASE_PRICE,
                },
                "RES": {"checked_at": TODAY.isoformat(), "reason": REASON_NO_RESULT},
            }),
            encoding="utf-8",
        )
        with self.assertNoLogs("kream.cache", level="WARNING"):
            self.cache.load()
        self.assertEqual(set(self.cache.negative), {"LOW", "GAP", "RES"})
        self.assertFalse(self.cache.negative_is_fresh("LOW", TODAY))
        self.assertFalse(self.cache.negative_is_fresh("GAP", TODAY, BASE_PRICE))
        self.assertTrue(self.cache.negative_is_fresh("RES", TODAY))
        kept, excluded = exclude_fresh_negative(
            ["LOW", "GAP", "RES"], self.cache, TODAY, {"LOW": BASE_PRICE, "GAP": BASE_PRICE}
        )
        self.assertEqual(kept, ["LOW", "GAP"])
        self.assertEqual(excluded[REASON_NO_RESULT], 1)


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
        self.assertEqual(ast.unparse(exclude.args[3]), "lowest_prices")
        self.assertIn("부정 캐시로 %s개 제외 — %s", source)
        self.assertIn("검색할 모델 없음 — 브라우저를 열지 않습니다.", source)


def _write_negative_payload(directory: str, payload: dict[str, dict]) -> None:
    cache_dir = Path(directory) / KREAM_CACHE_DIR_NAME
    cache_dir.mkdir(parents=True)
    (cache_dir / NEGATIVE_CACHE_FILENAME).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _write_negative(directory: str, entries: dict[str, str]) -> None:
    _write_negative_payload(directory, {
        model_name: {"checked_at": checked_on, "reason": REASON_NO_RESULT}
        for model_name, checked_on in entries.items()
    })


def _price_gap_entry(days_ago: int, marketplace_price: int) -> dict:
    return {
        "checked_at": (date.today() - timedelta(days=days_ago)).isoformat(),
        "reason": REASON_PRICE_GAP,
        "kream_price": marketplace_price + 3000,
        "marketplace_price": marketplace_price,
        "min_price_diff": MIN_GAP,
    }


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
        products = [_marketplace("FRESH", BASE_PRICE), _marketplace("KEEP", BASE_PRICE)]
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
        expected = (
            "부정 캐시로 1개 제외 — no_result 1개(7일), no_match 0개(7일), "
            "low_trade 0개(7일), price_gap 0개(3일)"
        )
        self.assertTrue(any(expected in line for line in captured.output))

    async def test_all_fresh_negatives_do_not_open_browser(self):
        async def results(*args, **kwargs):
            raise AssertionError("유효 캐시만 남으면 검색하면 안 된다")
            yield "", []

        products = [_marketplace("FRESH", BASE_PRICE), _marketplace("ALSO", BASE_PRICE)]
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
        self.assertTrue(any("부정 캐시로 2개 제외 — no_result 2개(7일)" in line for line in captured.output))
        self.assertTrue(any("검색할 모델 없음 — 브라우저를 열지 않습니다." in line for line in captured.output))

    async def test_price_gap_is_searched_again_only_when_marketplace_price_dropped(self):
        seen: list[list[str]] = []

        async def results(models, context, *, cache):
            seen.append(list(models))
            for name in models:
                yield name, []

        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=MagicMock())
        manager.__aexit__ = AsyncMock(return_value=False)
        products = [
            _marketplace("DROPPED", BASE_PRICE - 2000),
            _marketplace("SAME", BASE_PRICE),
            _marketplace("HIGHER", BASE_PRICE + 2000),
            _marketplace("LOW-TRADE", BASE_PRICE),
        ]
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "OUTPUT_DIR", directory), \
             patch("main._load_all_products", return_value=products), \
             patch("main.discover_dates", return_value=[]), \
             patch("main.create_browser", return_value=manager), \
             patch("main.iter_kream_results", results), \
             patch("main.find_arbitrage", return_value=[]):
            _write_negative_payload(directory, {
                "DROPPED": _price_gap_entry(1, BASE_PRICE),
                "SAME": _price_gap_entry(1, BASE_PRICE),
                "HIGHER": _price_gap_entry(1, BASE_PRICE),
                "LOW-TRADE": {
                    "checked_at": (date.today() - timedelta(days=6)).isoformat(),
                    "reason": REASON_LOW_TRADE,
                    "min_trade_count": MIN_TRADE,
                },
            })
            with self.assertLogs("main", level="INFO") as captured:
                await pipeline.main("kream")
        self.assertEqual(seen, [["DROPPED"]])
        expected = "부정 캐시로 3개 제외 — no_result 0개(7일), no_match 0개(7일), low_trade 1개(7일), price_gap 2개(3일)"
        self.assertTrue(any(expected in line for line in captured.output))

    async def test_models_judged_under_other_criteria_are_searched_again(self):
        seen: list[list[str]] = []

        async def results(models, context, *, cache):
            seen.append(list(models))
            for name in models:
                yield name, []

        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=MagicMock())
        manager.__aexit__ = AsyncMock(return_value=False)
        names = ("LOW-OLD-RULE", "GAP-OLD-RULE", "LOW-NO-RULE", "GAP-NO-RULE", "LOW-SAME", "GAP-SAME")
        products = [_marketplace(name, BASE_PRICE) for name in names]
        checked_at = (date.today() - timedelta(days=1)).isoformat()
        gap_prices = {"kream_price": BASE_PRICE + 3000, "marketplace_price": BASE_PRICE}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(config, "OUTPUT_DIR", directory), \
             patch("main._load_all_products", return_value=products), \
             patch("main.discover_dates", return_value=[]), \
             patch("main.create_browser", return_value=manager), \
             patch("main.iter_kream_results", results), \
             patch("main.find_arbitrage", return_value=[]):
            _write_negative_payload(directory, {
                "LOW-OLD-RULE": {"checked_at": checked_at, "reason": REASON_LOW_TRADE, "min_trade_count": MIN_TRADE + 1},
                "GAP-OLD-RULE": {
                    "checked_at": checked_at, "reason": REASON_PRICE_GAP, **gap_prices, "min_price_diff": MIN_GAP - 1,
                },
                "LOW-NO-RULE": {"checked_at": checked_at, "reason": REASON_LOW_TRADE},
                "GAP-NO-RULE": {"checked_at": checked_at, "reason": REASON_PRICE_GAP, **gap_prices},
                "LOW-SAME": {"checked_at": checked_at, "reason": REASON_LOW_TRADE, "min_trade_count": MIN_TRADE},
                "GAP-SAME": {
                    "checked_at": checked_at, "reason": REASON_PRICE_GAP, **gap_prices, "min_price_diff": MIN_GAP,
                },
            })
            await pipeline.main("kream")
        self.assertEqual(seen, [["LOW-OLD-RULE", "GAP-OLD-RULE", "LOW-NO-RULE", "GAP-NO-RULE"]])


class KreamRecordOrderBehaviorTests(unittest.IsolatedAsyncioTestCase):
    """main 전체를 돌려 crawler 의 일치 시 삭제와 새 기록의 순서를 확인한다."""

    async def _run_main(self, directory: str, products, kream_cards: dict[str, dict]):
        async def results(models, context, *, cache):
            for name in models:
                cache.remember_detail_model(kream_cards[name]["href"], name)
                found = await _find_verified_kream_product(
                    name, [kream_cards[name]], MagicMock(), cache=cache, allow_negative=True
                )
                yield name, [found] if found is not None else []

        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=MagicMock())
        manager.__aexit__ = AsyncMock(return_value=False)
        with patch.object(config, "OUTPUT_DIR", directory), \
             patch("main._load_all_products", return_value=products), \
             patch("main.discover_dates", return_value=[]), \
             patch("main.create_browser", return_value=manager), \
             patch("main.iter_kream_results", results):
            await pipeline.main("kream")
        cache = KreamCache(Path(directory) / KREAM_CACHE_DIR_NAME)
        cache.load()
        arbitrage = json.loads(
            (Path(directory) / date.today().strftime("%Y%m%d") / "arbitrage_results.json")
            .read_text(encoding="utf-8")
        )
        return cache, arbitrage

    @staticmethod
    def _card(model: str, price: int, trade_count: int) -> dict:
        return {
            "href": "https://kream.co.kr/products/%s" % model,
            "name": "Kream %s 상품" % model,
            "priceStr": "{:,}원".format(price),
            "tradeStr": "거래 %s" % trade_count,
        }

    async def test_new_record_survives_the_match_time_drop_in_the_same_run(self):
        expired_on = (date.today() - timedelta(days=30)).isoformat()
        products = [
            _marketplace("LOWTRADE", BASE_PRICE),
            _marketplace("PRICEGAP", BASE_PRICE),
            _marketplace("PROFIT", BASE_PRICE),
        ]
        cards = {
            "LOWTRADE": self._card("LOWTRADE", BASE_PRICE + MIN_GAP + 5000, MIN_TRADE - 1),
            "PRICEGAP": self._card("PRICEGAP", BASE_PRICE + MIN_GAP - 1, ENOUGH_TRADE),
            "PROFIT": self._card("PROFIT", BASE_PRICE + MIN_GAP, MIN_TRADE),
        }
        with tempfile.TemporaryDirectory() as directory:
            _write_negative_payload(directory, {
                name: {"checked_at": expired_on, "reason": REASON_NO_RESULT}
                for name in ("LOWTRADE", "PRICEGAP", "PROFIT")
            })
            cache, arbitrage = await self._run_main(directory, products, cards)
        self.assertEqual(cache.negative["LOWTRADE"]["reason"], REASON_LOW_TRADE)
        self.assertEqual(cache.negative["PRICEGAP"]["reason"], REASON_PRICE_GAP)
        self.assertEqual(cache.negative["PRICEGAP"]["marketplace_price"], BASE_PRICE)
        self.assertNotIn("PROFIT", cache.negative)
        self.assertEqual([item["model_name"] for item in arbitrage], ["PROFIT"])

    async def test_main_judges_the_whole_chunk_so_a_zero_trade_chunk_is_not_recorded(self):
        names = ["ZERO%02d" % index for index in range(20)]
        products = [_marketplace(name, BASE_PRICE) for name in names]
        cards = {name: self._card(name, BASE_PRICE + MIN_GAP, 0) for name in names}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertLogs("kream.cache", level="WARNING") as captured:
                cache, arbitrage = await self._run_main(directory, products, cards)
        self.assertEqual(cache.negative, {})
        self.assertEqual(arbitrage, [])
        self.assertTrue(any("100%(20/20개)" in line for line in captured.output))

    async def test_recorded_models_are_not_searched_in_the_next_run(self):
        products = [_marketplace("LOWTRADE", BASE_PRICE)]
        cards = {"LOWTRADE": self._card("LOWTRADE", BASE_PRICE + MIN_GAP, MIN_TRADE - 1)}
        with tempfile.TemporaryDirectory() as directory:
            await self._run_main(directory, products, cards)
            second_search = AsyncMock()
            manager = MagicMock()
            with patch.object(config, "OUTPUT_DIR", directory), \
                 patch("main._load_all_products", return_value=products), \
                 patch("main.discover_dates", return_value=[]), \
                 patch("main.create_browser", return_value=manager) as browser, \
                 patch("main.iter_kream_results", second_search):
                with self.assertLogs("main", level="INFO") as captured:
                    await pipeline.main("kream")
        browser.assert_not_called()
        second_search.assert_not_called()
        self.assertTrue(any("low_trade 1개(7일)" in line for line in captured.output))


class KreamLowestPriceTests(unittest.TestCase):
    def test_lowest_effective_price_wins_regardless_of_order(self):
        products = [
            _marketplace("M1", 95000, site="naver"),
            _marketplace("M1", 88000, site="musinsa"),
            _marketplace("M1", 91000, site="adidas"),
            _marketplace("M2", 50000),
        ]
        self.assertEqual(lowest_prices_by_model(products), {"M1": 88000, "M2": 50000})
        self.assertEqual(lowest_prices_by_model(products[::-1]), {"M1": 88000, "M2": 50000})

    def test_sale_price_replaces_price_and_names_are_stripped(self):
        products = [
            _marketplace("M1", 100000, sale_price=70000),
            _marketplace(" M1 ", 80000),
            _marketplace("  ", 1),
        ]
        self.assertEqual(lowest_prices_by_model(products), {"M1": 70000})


class KreamJudgeTests(unittest.TestCase):
    def test_low_trade_wins_even_when_the_gap_is_huge(self):
        verdict = judge_unprofitable([_kream(BASE_PRICE + MIN_GAP * 3, MIN_TRADE - 1)], BASE_PRICE)
        self.assertEqual(verdict.reason, REASON_LOW_TRADE)
        self.assertIsNone(verdict.kream_price)
        self.assertIsNone(verdict.marketplace_price)

    def test_low_trade_wins_when_both_conditions_fail(self):
        verdict = judge_unprofitable([_kream(BASE_PRICE, 0)], BASE_PRICE)
        self.assertEqual(verdict.reason, REASON_LOW_TRADE)

    def test_enough_trade_with_small_gap_is_price_gap_with_both_prices(self):
        verdict = judge_unprofitable([_kream(BASE_PRICE + MIN_GAP - 1, MIN_TRADE)], BASE_PRICE)
        self.assertEqual(verdict.reason, REASON_PRICE_GAP)
        self.assertEqual(verdict.kream_price, BASE_PRICE + MIN_GAP - 1)
        self.assertEqual(verdict.marketplace_price, BASE_PRICE)

    def test_a_loss_making_gap_is_also_price_gap(self):
        verdict = judge_unprofitable([_kream(BASE_PRICE - 20000, ENOUGH_TRADE)], BASE_PRICE)
        self.assertEqual(verdict.reason, REASON_PRICE_GAP)

    def test_threshold_edges_pass_exactly_when_the_comparator_passes(self):
        for trade_count in (0, MIN_TRADE - 1, MIN_TRADE, MIN_TRADE + 1):
            for gap in (-1, MIN_GAP - 1, MIN_GAP, MIN_GAP + 1):
                with self.subTest(trade_count=trade_count, gap=gap):
                    kream = _kream(BASE_PRICE + gap, trade_count)
                    compared = find_arbitrage(
                        [_marketplace(MODEL, BASE_PRICE)], {MODEL: [kream]}
                    )
                    verdict = judge_unprofitable([kream], BASE_PRICE)
                    self.assertEqual(verdict is None, bool(compared))
                    if verdict is not None:
                        expected = REASON_LOW_TRADE if trade_count < MIN_TRADE else REASON_PRICE_GAP
                        self.assertEqual(verdict.reason, expected)

    def test_any_passing_kream_product_means_no_verdict(self):
        products = [_kream(BASE_PRICE + 100, ENOUGH_TRADE), _kream(BASE_PRICE + MIN_GAP, MIN_TRADE)]
        self.assertIsNone(judge_unprofitable(products, BASE_PRICE))

    def test_price_gap_records_the_closest_product_among_enough_trade(self):
        products = [
            _kream(BASE_PRICE + MIN_GAP - 10, MIN_TRADE - 1),
            _kream(BASE_PRICE + 1000, ENOUGH_TRADE),
            _kream(BASE_PRICE + 3000, ENOUGH_TRADE),
        ]
        verdict = judge_unprofitable(products, BASE_PRICE)
        self.assertEqual(verdict.reason, REASON_PRICE_GAP)
        self.assertEqual(verdict.kream_price, BASE_PRICE + 3000)

    def test_all_low_trade_products_are_low_trade(self):
        products = [_kream(BASE_PRICE + 1, 1), _kream(BASE_PRICE + MIN_GAP, MIN_TRADE - 1)]
        self.assertEqual(judge_unprofitable(products, BASE_PRICE).reason, REASON_LOW_TRADE)


class KreamRecordTests(unittest.TestCase):
    def setUp(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        self.cache = cache

    def _record(self, results: dict[str, list[KreamProduct]], lowest: dict[str, int]) -> None:
        record_unprofitable_models(self.cache, results, lowest, TODAY)

    def test_low_trade_is_recorded_for_the_run_date(self):
        self._record({MODEL: [_kream(BASE_PRICE + MIN_GAP, MIN_TRADE - 1)]}, {MODEL: BASE_PRICE})
        self.assertEqual(self.cache.negative[MODEL], {
            "checked_at": TODAY.isoformat(),
            "reason": REASON_LOW_TRADE,
            "min_trade_count": MIN_TRADE,
        })
        self.assertTrue(self.cache.negative_is_fresh(MODEL, TODAY + timedelta(days=6)))
        self.assertFalse(self.cache.negative_is_fresh(MODEL, TODAY + timedelta(days=8)))

    def test_price_gap_is_recorded_with_prices_and_lives_three_days(self):
        self._record({MODEL: [_kream(BASE_PRICE + 2000, ENOUGH_TRADE)]}, {MODEL: BASE_PRICE})
        self.assertEqual(self.cache.negative[MODEL], {
            "checked_at": TODAY.isoformat(),
            "reason": REASON_PRICE_GAP,
            "kream_price": BASE_PRICE + 2000,
            "marketplace_price": BASE_PRICE,
            "min_price_diff": MIN_GAP,
        })
        self.assertTrue(self.cache.negative_is_fresh(MODEL, TODAY + timedelta(days=2), BASE_PRICE))
        self.assertFalse(self.cache.negative_is_fresh(MODEL, TODAY + timedelta(days=4), BASE_PRICE))

    def test_profitable_model_is_not_recorded_and_old_entries_are_removed(self):
        for reason in (REASON_NO_RESULT, REASON_LOW_TRADE, REASON_PRICE_GAP):
            with self.subTest(reason=reason):
                _remember(self.cache, MODEL, reason, TODAY - timedelta(days=1))
                self._record({MODEL: [_kream(BASE_PRICE + MIN_GAP, MIN_TRADE)]}, {MODEL: BASE_PRICE})
                self.assertNotIn(MODEL, self.cache.negative)

    def test_lowest_marketplace_price_decides_not_the_first_product(self):
        products = [_marketplace(MODEL, BASE_PRICE + 15000), _marketplace(MODEL, BASE_PRICE)]
        lowest = lowest_prices_by_model(products)
        profitable = [_kream(BASE_PRICE + MIN_GAP, MIN_TRADE)]
        self._record({MODEL: profitable}, lowest)
        self.assertNotIn(MODEL, self.cache.negative)
        near = [_kream(BASE_PRICE + MIN_GAP - 1, MIN_TRADE)]
        self._record({MODEL: near}, lowest)
        self.assertEqual(self.cache.negative[MODEL]["marketplace_price"], BASE_PRICE)

    def test_sale_price_is_the_marketplace_price_for_the_gap(self):
        lowest = lowest_prices_by_model([_marketplace(MODEL, 120000, sale_price=BASE_PRICE)])
        self._record({MODEL: [_kream(BASE_PRICE + MIN_GAP, MIN_TRADE)]}, lowest)
        self.assertNotIn(MODEL, self.cache.negative)

    def test_empty_result_is_not_judged_and_keeps_existing_entry(self):
        _remember(self.cache, MODEL, REASON_NO_MATCH, TODAY - timedelta(days=2))
        before = dict(self.cache.negative[MODEL])
        self._record({MODEL: [], "OTHER": []}, {MODEL: BASE_PRICE, "OTHER": BASE_PRICE})
        self.assertEqual(self.cache.negative, {MODEL: before})

    def test_models_without_a_search_result_are_not_recorded(self):
        _remember(self.cache, "STALE", REASON_LOW_TRADE, TODAY - timedelta(days=1))
        before = dict(self.cache.negative)
        self._record({}, {MODEL: BASE_PRICE, "STALE": BASE_PRICE})
        self.assertEqual(self.cache.negative, before)

    def test_model_without_marketplace_price_is_skipped_with_warning(self):
        _remember(self.cache, MODEL, REASON_LOW_TRADE, TODAY - timedelta(days=1))
        before = dict(self.cache.negative)
        with self.assertLogs("kream.cache", level="WARNING") as captured:
            self._record({MODEL: [_kream(BASE_PRICE + MIN_GAP, MIN_TRADE)]}, {})
        self.assertEqual(self.cache.negative, before)
        self.assertTrue(any(MODEL in line for line in captured.output))

    def test_summary_log_counts_each_outcome(self):
        results = {
            "L1": [_kream(BASE_PRICE, 1)],
            "L2": [_kream(BASE_PRICE, 2)],
            "G1": [_kream(BASE_PRICE + 1, ENOUGH_TRADE)],
            "P1": [_kream(BASE_PRICE + MIN_GAP, MIN_TRADE)],
            "E1": [],
        }
        with self.assertLogs("kream.cache", level="INFO") as captured:
            self._record(results, {name: BASE_PRICE for name in results})
        self.assertTrue(any(
            "부정 캐시 기록 — low_trade 2개, price_gap 1개, 차익 조건 통과 1개" in line
            for line in captured.output
        ))


class KreamZeroTradeGuardTests(unittest.TestCase):
    """거래량 0 이 한 청크를 지배하면 거래량 문구 형식 변경으로 보고 그 청크의 low_trade 를 기록하지 않는다.

    한도(80% 초과)와 최소 표본(20개)은 구현 상수를 import 하지 않고 숫자로 적어 값이 바뀌면 깨지게 한다.
    """

    def setUp(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        self.cache = cache

    @staticmethod
    def _chunk(zero_count: int, total: int) -> dict[str, list[KreamProduct]]:
        """검증 통과 결과 total 개. 앞 zero_count 개는 거래량 0, 나머지는 1. 모두 가격차는 충분하다."""
        return {
            "M%02d" % index: [_kream(BASE_PRICE + MIN_GAP, 0 if index < zero_count else 1)]
            for index in range(total)
        }

    def _record(self, results: dict[str, list[KreamProduct]]) -> None:
        record_unprofitable_models(self.cache, results, dict.fromkeys(results, BASE_PRICE), TODAY)

    def test_ratio_above_the_limit_skips_every_low_trade_record_and_warns(self):
        with self.assertLogs("kream.cache", level="WARNING") as captured:
            self._record(self._chunk(17, 20))
        self.assertEqual(self.cache.negative, {})
        self.assertEqual(len(captured.output), 1)
        self.assertIn("85%(17/20개)", captured.output[0])

    def test_ratio_exactly_at_the_limit_is_recorded(self):
        with self.assertNoLogs("kream.cache", level="WARNING"):
            self._record(self._chunk(16, 20))
        self.assertEqual(len(self.cache.negative), 20)

    def test_all_zero_chunk_at_the_minimum_sample_is_skipped(self):
        with self.assertLogs("kream.cache", level="WARNING") as captured:
            self._record(self._chunk(20, 20))
        self.assertEqual(self.cache.negative, {})
        self.assertIn("100%(20/20개)", captured.output[0])

    def test_sample_below_the_minimum_is_recorded_even_when_all_zero(self):
        with self.assertNoLogs("kream.cache", level="WARNING"):
            self._record(self._chunk(19, 19))
        self.assertEqual(len(self.cache.negative), 19)

    def test_empty_results_are_not_part_of_the_sample(self):
        results = self._chunk(17, 20)
        results.update({"EMPTY%02d" % index: [] for index in range(30)})
        with self.assertLogs("kream.cache", level="WARNING"):
            self._record(results)
        self.assertEqual(self.cache.negative, {})

    def test_price_gap_and_profitable_models_are_still_handled_in_a_skipped_chunk(self):
        results = self._chunk(17, 17)
        results["GAP"] = [_kream(BASE_PRICE + MIN_GAP - 1, ENOUGH_TRADE)]
        results["PROFIT"] = [_kream(BASE_PRICE + MIN_GAP, MIN_TRADE)]
        results["OTHER-LOW"] = [_kream(BASE_PRICE + MIN_GAP, 1)]
        _remember(self.cache, "PROFIT", REASON_LOW_TRADE, TODAY - timedelta(days=1))
        with self.assertLogs("kream.cache", level="WARNING"):
            self._record(results)
        self.assertEqual(set(self.cache.negative), {"GAP"})
        self.assertEqual(self.cache.negative["GAP"]["reason"], REASON_PRICE_GAP)

    def test_existing_low_trade_entry_is_left_alone_in_a_skipped_chunk(self):
        _remember(self.cache, "M00", REASON_LOW_TRADE, TODAY - timedelta(days=2))
        before = dict(self.cache.negative["M00"])
        with self.assertLogs("kream.cache", level="WARNING"):
            self._record(self._chunk(17, 20))
        self.assertEqual(self.cache.negative, {"M00": before})

    def test_the_next_chunk_is_judged_on_its_own_ratio(self):
        with self.assertLogs("kream.cache", level="WARNING"):
            self._record(self._chunk(20, 20))
        later = {"L%02d" % index: [_kream(BASE_PRICE + MIN_GAP, 1)] for index in range(20)}
        with self.assertNoLogs("kream.cache", level="WARNING"):
            self._record(later)
        self.assertEqual(set(self.cache.negative), set(later))


class KreamRecordUnverifiedResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_verification_leaves_an_empty_result_that_is_not_recorded(self):
        directory, cache = _cache_dir()
        self.addCleanup(directory.cleanup)
        cache.remember_detail_model(PRODUCT_URL, "OTHER-1")
        page = _detail_page((MODEL_TEXT,), wait_error=TimeoutError())
        with patch("kream.crawler._is_blocked_page", new=AsyncMock(return_value=False)):
            product = await _find_verified_kream_product(
                "WANTED", [_card(PRODUCT_URL), _card(OTHER_URL)], page,
                cache=cache, allow_negative=True,
            )
        self.assertIsNone(product)
        record_unprofitable_models(cache, {"WANTED": []}, {"WANTED": BASE_PRICE}, TODAY)
        self.assertEqual(cache.negative, {})


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
