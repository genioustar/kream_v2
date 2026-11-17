"""Kream 검색 입력 대기와 Enter 뒤 렌더링 대기가 실측 낭비를 반복하지 않는지 본다.

가상 시계로만 기다리므로 이 모듈은 실제 sleep을 하지 않는다.
"""
import unittest
from unittest.mock import AsyncMock, patch

from kream.crawler import (
    RENDER_MAX_WAIT_SEC,
    RENDER_MIN_WAIT_MAX_SEC,
    RENDER_MIN_WAIT_MIN_SEC,
    RENDER_POLL_INTERVAL_SEC,
    SEARCH_BUTTON_SELECTORS,
    SEARCH_BUTTON_VISIBLE_TIMEOUT_MS,
    SEARCH_INPUT_AFTER_CLICK_TIMEOUT_MS,
    SEARCH_INPUT_ALREADY_VISIBLE_TIMEOUT_MS,
    SEARCH_INPUT_SELECTORS,
    SELECTOR_TIMEOUT,
    _open_search_and_get_input,
    _search_kream_once,
    _wait_for_search_render,
)
from kream.parser import KREAM_PRODUCT_CARD_SELECTOR, KREAM_SELECTORS

# 2026-11-16 실측에서 2004회 중 0회 일치했던 버튼 경로.
_UNUSED_LONG_BUTTON = (
    "#wrap > div.header-wrapper > div > div > div > div > div > div > "
    "div.header_main > div > div.right > div > button"
)
_EMPTY_RESULT_SELECTOR = "text=검색 결과가 없습니다"


class VirtualClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class _RecordingLocator:
    def __init__(self, page, selector: str):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    async def is_visible(self, timeout=0):
        self.page.events.append(("is_visible", self.selector, timeout))
        if self.selector in SEARCH_INPUT_SELECTORS:
            return self.page.input_visible
        if self.selector == "button.btn_search":
            return True
        return False

    async def click(self, **kwargs):
        self.page.events.append(("click", self.selector))
        if self.selector == "button.btn_search":
            self.page.input_visible = True


class _OpenSearchPage:
    def __init__(self, input_visible: bool):
        self.input_visible = input_visible
        self.events: list[tuple] = []
        self.url = "https://kream.co.kr/products/1"

    def locator(self, selector: str):
        return _RecordingLocator(self, selector)


class OpenSearchTests(unittest.IsolatedAsyncioTestCase):
    def test_button_selectors_drop_the_unused_long_path(self):
        self.assertEqual(
            SEARCH_BUTTON_SELECTORS,
            ["button.btn_search", "button.header-search-button"],
        )
        self.assertNotIn(_UNUSED_LONG_BUTTON, SEARCH_BUTTON_SELECTORS)
        self.assertEqual(SEARCH_INPUT_ALREADY_VISIBLE_TIMEOUT_MS, 0)
        self.assertEqual(SEARCH_BUTTON_VISIBLE_TIMEOUT_MS, 2_000)
        self.assertEqual(SEARCH_INPUT_AFTER_CLICK_TIMEOUT_MS, 3_000)

    async def test_detail_page_probes_input_without_waiting_and_clicks_btn_search(self):
        page = _OpenSearchPage(input_visible=False)
        wait = AsyncMock()
        with patch("kream.crawler._human_wait", wait):
            locator = await _open_search_and_get_input(page)

        self.assertEqual(locator.selector, "input.input_search")
        click_at = page.events.index(("click", "button.btn_search"))
        before = page.events[:click_at]
        after = page.events[click_at + 1:]
        input_probes = [
            event for event in before
            if event[0] == "is_visible" and event[1] in SEARCH_INPUT_SELECTORS
        ]
        button_probes = [
            event for event in before
            if event[0] == "is_visible" and event[1] not in SEARCH_INPUT_SELECTORS
        ]
        self.assertEqual([event[1] for event in input_probes], list(SEARCH_INPUT_SELECTORS))
        self.assertEqual({event[2] for event in input_probes}, {0})
        self.assertEqual(
            button_probes,
            [("is_visible", "button.btn_search", SEARCH_BUTTON_VISIBLE_TIMEOUT_MS)],
        )
        self.assertEqual(
            after[0],
            ("is_visible", "input.input_search", SEARCH_INPUT_AFTER_CLICK_TIMEOUT_MS),
        )
        self.assertNotIn(_UNUSED_LONG_BUTTON, [event[1] for event in page.events])
        # 클릭 직전(_click_search_button)과 클릭 뒤(_open_search_and_get_input)에 한 번씩.
        self.assertEqual(wait.await_count, 2)

    async def test_visible_input_skips_search_button(self):
        page = _OpenSearchPage(input_visible=True)
        wait = AsyncMock()
        with patch("kream.crawler._human_wait", wait):
            locator = await _open_search_and_get_input(page)

        self.assertEqual(locator.selector, "input.input_search")
        self.assertEqual(page.events, [("is_visible", "input.input_search", 0)])
        self.assertEqual(wait.await_count, 0)


class _RenderPage:
    def __init__(
        self,
        location_href: str,
        card_hrefs: list[str],
        empty: int = 0,
        cached_url: str | None = None,
    ):
        self.location_href = location_href
        self.card_hrefs = list(card_hrefs)
        self._empty = empty
        self.url = cached_url if cached_url is not None else location_href
        self.evaluations: list[tuple] = []
        self.selectors: list[str] = []
        self.calls: list[tuple] = []

    async def title(self) -> str:
        self.calls.append(("title",))
        return "unused"

    async def evaluate(self, script, arg=None):
        self.evaluations.append((script, arg))
        self.calls.append(("evaluate", script, arg))
        if script == "location.href":
            return self.location_href
        if script == "window.scrollTo(0, 0)":
            return None
        if arg == KREAM_PRODUCT_CARD_SELECTOR and isinstance(script, str) and "priceStr" in script:
            return []
        if arg == KREAM_PRODUCT_CARD_SELECTOR:
            return list(self.card_hrefs)
        return None

    def locator(self, selector: str):
        self.selectors.append(selector)
        self.calls.append(("locator", selector))
        page = self

        class _Count:
            def __init__(self, selector: str):
                self.selector = selector

            async def count(self) -> int:
                if self.selector == _EMPTY_RESULT_SELECTOR:
                    return page._empty
                return 0

        return _Count(selector)


class RenderWaitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = VirtualClock()
        self.wait_calls: list[tuple[float, float]] = []
        async def spy(min_s: float = 1.0, max_s: float = 3.0) -> None:
            self.wait_calls.append((min_s, max_s))

        self._patches = [
            patch("kream.crawler._monotonic", self.clock.monotonic),
            patch("kream.crawler._poll_sleep", self.clock.sleep),
            patch("kream.crawler._human_wait", spy),
        ]
        for item in self._patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self._patches):
            item.stop()

    def poll_sleeps(self) -> list[float]:
        return list(self.clock.sleeps)

    def assert_min_human_wait(self):
        self.assertIn((RENDER_MIN_WAIT_MIN_SEC, RENDER_MIN_WAIT_MAX_SEC), self.wait_calls)
        self.assertEqual(RENDER_MIN_WAIT_MIN_SEC, 1.5)
        self.assertEqual(RENDER_MIN_WAIT_MAX_SEC, 2.5)
        self.assertEqual(RENDER_MAX_WAIT_SEC, 7.0)
        self.assertGreater(RENDER_POLL_INTERVAL_SEC, 0)

    def assert_waits_until_cap(self, rendered: bool):
        self.assertFalse(rendered)
        self.assertAlmostEqual(sum(self.poll_sleeps()), RENDER_MAX_WAIT_SEC, delta=0.001)
        self.assert_min_human_wait()

    async def test_previous_keyword_with_cards_is_not_rendered(self):
        page = _RenderPage(
            "https://kream.co.kr/search?keyword=OLD111",
            ["https://kream.co.kr/products/9"],
        )
        rendered = await _wait_for_search_render(page, "NEW222", [], False)
        self.assert_waits_until_cap(rendered)

    async def test_same_card_hrefs_as_snapshot_are_not_rendered(self):
        hrefs = ["https://kream.co.kr/products/1"]
        page = _RenderPage("https://kream.co.kr/search?keyword=NEW222", hrefs)
        rendered = await _wait_for_search_render(page, "NEW222", list(hrefs), False)
        self.assert_waits_until_cap(rendered)
        self.assertEqual(self.wait_calls, [(RENDER_MIN_WAIT_MIN_SEC, RENDER_MIN_WAIT_MAX_SEC)])

    async def test_matching_keyword_and_new_cards_pass(self):
        page = _RenderPage(
            "https://kream.co.kr/search?keyword=NEW222",
            ["https://kream.co.kr/products/2"],
        )
        rendered = await _wait_for_search_render(
            page, "NEW222", ["https://kream.co.kr/products/1"], False
        )
        self.assertTrue(rendered)
        self.assertEqual(self.poll_sleeps(), [])
        self.assert_min_human_wait()
        href_calls = [item for item in page.evaluations if item[0] != "location.href"]
        self.assertEqual(href_calls[0][1], KREAM_PRODUCT_CARD_SELECTOR)
        self.assertEqual(
            KREAM_SELECTORS["search_results"],
            KREAM_PRODUCT_CARD_SELECTOR,
        )

    async def test_matching_keyword_and_empty_text_pass(self):
        hrefs = ["https://kream.co.kr/products/1"]
        page = _RenderPage(
            "https://kream.co.kr/search?keyword=NEW222",
            hrefs,
            empty=1,
        )
        rendered = await _wait_for_search_render(page, "NEW222", list(hrefs), False)
        self.assertTrue(rendered)
        self.assertEqual(self.poll_sleeps(), [])
        self.assertIn(_EMPTY_RESULT_SELECTOR, page.selectors)
        self.assert_min_human_wait()

    async def test_leftover_no_result_text_is_not_rendered(self):
        hrefs = ["https://kream.co.kr/products/1"]
        page = _RenderPage(
            "https://kream.co.kr/search?keyword=NEW222",
            hrefs,
            empty=1,
        )
        rendered = await _wait_for_search_render(page, "NEW222", list(hrefs), True)
        self.assert_waits_until_cap(rendered)

    async def test_empty_cards_while_snapshot_has_cards_are_not_rendered(self):
        page = _RenderPage("https://kream.co.kr/search?keyword=NEW222", [])
        rendered = await _wait_for_search_render(
            page, "NEW222", ["https://kream.co.kr/products/1"], False
        )
        self.assert_waits_until_cap(rendered)

    async def test_keyword_with_space_is_decoded(self):
        model_name = "T-BOXT-PAK 96P"
        for keyword in ("T-BOXT-PAK+96P", "T-BOXT-PAK%2096P"):
            page = _RenderPage(
                f"https://kream.co.kr/search?keyword={keyword}",
                ["https://kream.co.kr/products/9"],
            )
            rendered = await _wait_for_search_render(page, model_name, [], False)
            self.assertTrue(rendered, keyword)
            self.assertEqual(self.poll_sleeps(), [])
            self.clock.sleeps.clear()
            self.wait_calls.clear()

    async def test_cached_page_url_does_not_decide(self):
        page = _RenderPage(
            "https://kream.co.kr/search?keyword=NEW222",
            ["https://kream.co.kr/products/2"],
            cached_url="https://kream.co.kr/search?keyword=OLD111",
        )
        rendered = await _wait_for_search_render(
            page, "NEW222", ["https://kream.co.kr/products/1"], False
        )
        self.assertTrue(rendered)
        self.assertEqual(page.url, "https://kream.co.kr/search?keyword=OLD111")
        self.assertIn(("location.href", None), page.evaluations)
        self.assertEqual(self.poll_sleeps(), [])

    async def test_unmet_render_stops_at_cap_and_keeps_existing_flow(self):
        page = _RenderPage(
            "https://kream.co.kr/search?keyword=OLD111",
            ["https://kream.co.kr/products/1"],
            cached_url="https://kream.co.kr/search?keyword=CACHED",
        )

        async def content():
            page.calls.append(("content",))
            return "x" * 800

        async def wait_for_selector(selector, timeout=None, state=None):
            page.calls.append(("wait_for_selector", selector, timeout))

        async def press(key):
            page.calls.append(("press", key))

        page.content = content
        page.wait_for_selector = wait_for_selector
        page.keyboard = AsyncMock()
        page.keyboard.press = press
        page._kream_blocked_status = None
        search_input = AsyncMock()

        with patch("kream.crawler._open_search_and_get_input", new=AsyncMock(return_value=search_input)), \
             patch("kream.crawler._human_type", new=AsyncMock()), \
             self.assertLogs("kream.crawler", level="DEBUG") as captured:
            result = await _search_kream_once("NEW222", page)

        self.assertEqual(result, [])
        self.assertAlmostEqual(sum(self.poll_sleeps()), RENDER_MAX_WAIT_SEC, delta=0.001)
        self.assert_min_human_wait()
        self.assertEqual(self.wait_calls[-1], (RENDER_MIN_WAIT_MIN_SEC, RENDER_MIN_WAIT_MAX_SEC))
        self.assertIn(("content",), page.calls)
        self.assertIn(("locator", _EMPTY_RESULT_SELECTOR), page.calls)
        self.assertIn(
            ("wait_for_selector", KREAM_SELECTORS["search_results"], SELECTOR_TIMEOUT),
            page.calls,
        )
        content_at = page.calls.index(("content",))
        empty_indexes = [
            index for index, call in enumerate(page.calls)
            if call == ("locator", _EMPTY_RESULT_SELECTOR)
        ]
        cards_at = page.calls.index(
            ("wait_for_selector", KREAM_SELECTORS["search_results"], SELECTOR_TIMEOUT)
        )
        press_at = page.calls.index(("press", "Enter"))
        self.assertGreaterEqual(len(empty_indexes), 2)
        self.assertLess(empty_indexes[0], press_at)
        self.assertLess(content_at, empty_indexes[-1])
        self.assertLess(empty_indexes[-1], cards_at)
        snapshot_at = next(
            index for index, call in enumerate(page.calls)
            if call[0] == "evaluate" and call[2] == KREAM_PRODUCT_CARD_SELECTOR
        )
        self.assertLess(snapshot_at, press_at)
        self.assertTrue(
            any(
                "렌더링 대기 완료" in line
                and "조건충족=False" in line
                and "keyword=OLD111" in line
                and "스냅샷카드=1" in line
                and "현재카드=1" in line
                and "직전결과없음=False" in line
                and "CACHED" not in line
                for line in captured.output
            )
        )
