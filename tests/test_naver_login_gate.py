import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from naver.crawler import _is_naver_login_gate, _wait_for_product_list


STORE_URL = (
    "https://smartstore.naver.com/reasonershop/category/"
    "9e4d920d708943489393d2ca83467562?cp=1"
)
LOGIN_URL = (
    "https://nid.naver.com/nidlogin.login?url="
    "https%3A%2F%2Fsmartstore.naver.com%2Freasonershop%2Fcategory%2F"
    "9e4d920d708943489393d2ca83467562%3Fcp%3D1"
)


class LoginGateTests(unittest.TestCase):
    def test_login_host_is_a_gate(self):
        self.assertTrue(_is_naver_login_gate(LOGIN_URL))

    def test_store_host_is_not_a_gate(self):
        self.assertFalse(_is_naver_login_gate(STORE_URL))


class Page:
    def __init__(self, url: str):
        self.url = url
        self.visits: list[str] = []

    async def wait_for_selector(self, item_selector: str, **kwargs):
        if "nid.naver.com" in self.url:
            raise TimeoutError("login page has no product cards")
        return None

    def locator(self, item_selector: str):
        page = self

        class Locator:
            async def count(self):
                return 0 if "nid.naver.com" in page.url else 3

        return Locator()

    async def goto(self, url: str, **kwargs):
        self.visits.append(url)
        self.url = url

    async def bring_to_front(self):
        return None


class ReturnToStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_enter_after_login_reopens_the_store_url(self):
        page = Page(LOGIN_URL)
        page.context = object()
        ready = asyncio.Event()
        with (
            patch("builtins.input", return_value=""),
            patch("naver.crawler._save_cookies", new=AsyncMock()) as save_cookies,
        ):
            await _wait_for_product_list(
                page,
                "li",
                "reasonershop",
                asyncio.Lock(),
                STORE_URL,
                ready,
            )
        self.assertEqual(page.visits, [STORE_URL])
        self.assertEqual(page.url, STORE_URL)
        save_cookies.assert_awaited_once()
        self.assertTrue(ready.is_set())

    async def test_next_login_tab_reopens_without_another_prompt(self):
        page = Page(LOGIN_URL)
        page.context = object()
        ready = asyncio.Event()
        ready.set()
        with (
            patch("builtins.input") as prompt,
            patch("naver.crawler._save_cookies", new=AsyncMock()),
        ):
            await _wait_for_product_list(
                page,
                "li",
                "artemoa",
                asyncio.Lock(),
                STORE_URL,
                ready,
            )
        prompt.assert_not_called()
        self.assertEqual(page.visits, [STORE_URL])
