import asyncio
import unittest

from common.browser import create_browser
from common.ego_session import Headers, role_selector


class FakeClient:
    def __init__(self):
        self.calls = []
        self.page = None

    async def call(self, op, params=None, timeout=60):
        self.calls.append(op)
        if op == "open":
            return {"spaceId": 3, "url": "about:blank"}
        if op == "goto":
            return {
                "url": params["url"],
                "title": "Example Domain",
                "response": {
                    "url": params["url"],
                    "status": 200,
                    "headers": {"Retry-After": "2"},
                    "body": "ok",
                },
            }
        if op == "evaluate":
            return {"value": False}
        return {"url": "about:blank", "title": "", "value": 0}


class BrowserSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_one_task_space_page(self):
        client = FakeClient()
        async with create_browser(client) as tabs:
            page = await tabs.new_page()
            with self.assertRaisesRegex(RuntimeError, "하나만"):
                await tabs.new_page()
            page._closed = True
            again = await tabs.new_page()
        self.assertIsNot(page, again)
        self.assertEqual(client.calls.count("open"), 2)

    async def test_goto_returns_document_status(self):
        client = FakeClient()
        async with create_browser(client) as tabs:
            page = await tabs.new_page()
            response = await page.goto("https://example.com")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.get("retry-after"), "2")
        self.assertEqual(page.url, "https://example.com")


class SocketReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_reader_accepts_hangul_split_across_chunks(self):
        import socket
        import threading

        from common.ego_session import EgoClient

        left, right = socket.socketpair()
        client = EgoClient()
        client._conn = left
        client._loop = asyncio.get_running_loop()
        client._ready = asyncio.Event()
        client._pending = {}
        client._stop = False
        payload = '{"ready":true,"name":"가"}\n'.encode()
        cut = payload.index("가".encode()) + 1
        thread = threading.Thread(target=client._read_socket, daemon=True)
        thread.start()
        try:
            right.send(payload[:cut])
            await asyncio.sleep(0.05)
            self.assertTrue(thread.is_alive())
            right.send(payload[cut:])
            await asyncio.wait_for(client._ready.wait(), 1)
        finally:
            client._stop = True
            right.close()
            left.close()
            thread.join(1)


class SelectorTests(unittest.TestCase):
    def test_role_selector_escapes_quotes(self):
        self.assertEqual(role_selector("button", "검색하기"), "loc=role:button[name='검색하기']")

    def test_header_lookup_ignores_case(self):
        headers = Headers({"Retry-After": "5"})
        self.assertEqual(headers.get("retry-after"), "5")

    def test_nth_spec_keeps_card_index(self):
        from common.ego_session import Locator

        child = Locator(object(), "li.product").nth(2).locator("a").first
        self.assertEqual(child._spec(), {
            "selector": "li.product",
            "index": 2,
            "child": {"selector": "a", "index": 0},
        })
