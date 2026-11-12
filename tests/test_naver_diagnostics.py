import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from common.atomic_io import TEMP_SUFFIX
from naver.crawler import _wait_for_product_list
from naver.diagnostics import (
    NaverAccessError, NaverCollectionError, NaverDiagnostics, classify_access, safe_url,
)


class NaverClassificationTests(unittest.TestCase):
    def test_protection_path_works_with_http_200_and_strips_token(self):
        url = "https://nid.naver.com/user2/help/idSafetyRelease?token_help=TOKEN#SECRET"
        self.assertEqual(classify_access(url), "account_protection")
        self.assertEqual(safe_url(url), "https://nid.naver.com/user2/help/idSafetyRelease")

    def test_similar_hostname_and_query_only_protection_url_are_not_evidence(self):
        for url in ("https://nid.naver.com.evil.test/user2/help/idSafetyRelease",
                    "https://smartstore.naver.com/goods?next=https://nid.naver.com/user2/help/idSafetyRelease"):
            self.assertIsNone(classify_access(url))

    def test_access_reasons_remain_distinct_and_protection_has_priority(self):
        self.assertEqual(classify_access("https://nid.naver.com/nidlogin.login?url=TOKEN"), "login_required")
        self.assertEqual(classify_access("https://smartstore.naver.com/", challenge_present=True), "security_challenge")
        self.assertEqual(classify_access("https://smartstore.naver.com/", authenticated=True), "authenticated_session")
        self.assertEqual(classify_access("https://smartstore.naver.com/", protection_present=True,
                                         login_present=True, authenticated=True), "account_protection")
        self.assertIsNone(classify_access("https://smartstore.naver.com/"))

    def test_url_redaction_removes_userinfo_and_invalid_urls(self):
        self.assertEqual(safe_url("https://USER:TOKEN@smartstore.naver.com:443/a?q=TOKEN#TOKEN"),
                         "https://smartstore.naver.com:443/a")
        for url in ("data:text/html,TOKEN", "javascript:TOKEN", "https://example.com:bad/", None):
            self.assertEqual(safe_url(url), "")


class NaverDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.page = MagicMock()
        self.page.url = "https://smartstore.naver.com/test/best?token_help=TOKEN"
        self.page.context.cookies = AsyncMock(return_value=[])
        self.page.evaluate = AsyncMock(return_value={
            "protection_present": False, "login_present": False, "challenge_present": False,
        })
        self.page.locator.return_value.count = AsyncMock(return_value=3)
        self.diagnostics = NaverDiagnostics(self.page, "test", self.page.url, "li.product", directory.name)

    def response(self, status, path="/i/v1/products", resource="xhr", host="smartstore.naver.com"):
        url = f"https://{host}{path}?token_help=TOKEN"
        response = MagicMock(status=status, url=url)
        response.request.url = url
        response.request.resource_type = resource
        response.request.frame = self.page.main_frame
        response.request.is_navigation_request.return_value = resource == "document"
        return response

    async def test_authenticated_session_is_recorded_and_collection_continues(self):
        self.page.context.cookies.return_value = [{"name": "NID_AUT", "value": "TOKEN"}]
        started = False
        async def operation():
            nonlocal started
            started = True
        await self.diagnostics.run(operation(), "navigation")
        self.assertTrue(started)
        self.assertIsNone(self.diagnostics.block_reason)
        self.assertTrue(self.diagnostics.data["authenticated_session"])
        self.assertNotIn("TOKEN", self.diagnostics.report_path.read_text())

    async def test_http_200_protection_redirect_is_blocked(self):
        self.page.url = "https://nid.naver.com/user2/help/idSafetyRelease?token_help=TOKEN"
        with self.assertRaises(NaverAccessError) as caught:
            await self.diagnostics.check("navigation")
        self.assertEqual(caught.exception.reason, "account_protection")

    async def test_normal_header_does_not_require_login(self):
        await self.diagnostics.check("product_list")
        self.assertIsNone(self.diagnostics.block_reason)
        self.assertEqual(self.diagnostics.data["card_count"], 3)

    async def test_product_api_rate_limit_cancels_current_operation(self):
        reached_followup = False
        cancelled = asyncio.Event()
        async def operation():
            nonlocal reached_followup
            self.diagnostics._on_response(self.response(429))
            try:
                await asyncio.sleep(10)
                reached_followup = True
            finally:
                cancelled.set()
        with self.assertRaises(NaverAccessError) as caught:
            await self.diagnostics.run(operation(), "scroll")
        self.assertEqual(caught.exception.reason, "http_429")
        self.assertTrue(cancelled.is_set())
        self.assertFalse(reached_followup)

    async def test_optional_analytics_and_image_denial_do_not_block(self):
        self.diagnostics._on_response(self.response(403, "/collect", "fetch", "lcs.naver.com"))
        self.diagnostics._on_response(self.response(403, "/banner", "image"))
        await self.diagnostics.check("scroll")
        self.assertIsNone(self.diagnostics.block_reason)

    async def test_diagnostics_never_serializes_auth_tokens(self):
        self.diagnostics.attach()
        self.diagnostics._on_request(self.response(200, "/test/best", "document").request)
        self.diagnostics._on_response(self.response(200, "/test/best", "document"))
        await self.diagnostics.check("complete")
        self.diagnostics.finish("success")
        self.diagnostics.close()
        content = self.diagnostics.report_path.read_text()
        self.assertNotIn("TOKEN", content)
        self.assertNotIn("token_help", content)
        report = json.loads(content)
        self.assertEqual(report["status"], "success")
        self.assertEqual(report["request_counts"]["document"], 1)
        self.assertEqual(report["http_counts"]["200"], 1)
        self.page.remove_listener.assert_called()

    async def test_login_document_response_pauses_and_run_does_not_abort(self):
        self.page.url = "https://nid.naver.com/nidlogin.login"
        self.page.evaluate = AsyncMock(return_value={
            "protection_present": False, "login_present": True, "challenge_present": False,
        })
        request = MagicMock()
        request.resource_type = "document"
        request.url = "https://nid.naver.com/nidlogin.login"
        request.frame = self.page.main_frame
        response = MagicMock(status=200, url=request.url)
        response.request = request
        self.diagnostics._on_response(response)
        self.assertIsNone(self.diagnostics.block_reason)
        self.assertEqual(self.diagnostics.pause_reason, "login_required")

        async def finished():
            return "loaded"

        self.assertEqual(await self.diagnostics.run(finished(), "public_search_home"), "loaded")
        self.assertEqual(self.diagnostics.pause_reason, "login_required")
        self.assertIsNone(self.diagnostics.block_reason)

        def resume(_prompt=""):
            self.page.url = "https://smartstore.naver.com/loutique"
            self.page.evaluate = AsyncMock(return_value={
                "protection_present": False, "login_present": False, "challenge_present": False,
            })
            return ""

        with patch("builtins.input", side_effect=resume):
            await self.diagnostics.wait_until_resumed()
        self.assertIsNone(self.diagnostics.pause_reason)
        self.assertIsNone(self.diagnostics.block_reason)

    async def test_login_screen_waits_for_operator_and_continues(self):
        self.page.url = "https://nid.naver.com/nidlogin.login"
        self.page.evaluate = AsyncMock(return_value={
            "protection_present": False, "login_present": True, "challenge_present": False,
        })
        self.page.locator.return_value.count = AsyncMock(return_value=0)
        calls = {"n": 0}

        async def wait_for_selector(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("list")
            self.page.url = "https://smartstore.naver.com/test/best"
            self.page.evaluate = AsyncMock(return_value={
                "protection_present": False, "login_present": False, "challenge_present": False,
            })
            self.page.locator.return_value.count = AsyncMock(return_value=4)

        self.page.wait_for_selector = wait_for_selector
        with patch("builtins.input", return_value="") as user_input:
            await _wait_for_product_list(self.page, "li.product", "test", self.diagnostics)
        user_input.assert_called_once()
        self.assertIsNone(self.diagnostics.block_reason)
        self.assertEqual(await self.page.locator().count(), 4)

    async def test_list_timeout_does_not_request_user_login_or_retry(self):
        self.page.wait_for_selector = AsyncMock(side_effect=TimeoutError("TOKEN in raw Playwright exception"))
        self.page.locator.return_value.count.return_value = 0
        with patch("builtins.input") as user_input:
            with self.assertRaises(NaverCollectionError) as caught:
                await _wait_for_product_list(self.page, "li.product", "test", self.diagnostics)
        self.assertEqual(caught.exception.reason, "product_list_timeout")
        self.page.wait_for_selector.assert_awaited_once()
        user_input.assert_not_called()
        self.assertNotIn("TOKEN", str(caught.exception))

    async def test_protection_after_navigation_timeout_is_detected(self):
        async def navigation():
            self.page.url = "https://nid.naver.com/user2/help/idSafetyRelease?token_help=TOKEN"
            raise TimeoutError("TOKEN")
        with self.assertRaises(NaverAccessError) as caught:
            await self.diagnostics.run(navigation(), "navigation")
        self.assertEqual(caught.exception.reason, "account_protection")

    async def test_report_write_failure_does_not_hide_account_protection(self):
        self.page.url = "https://nid.naver.com/user2/help/idSafetyRelease?token_help=TOKEN"
        with patch.object(Path, "mkdir", side_effect=OSError("disk full TOKEN")):
            with self.assertRaises(NaverAccessError) as caught:
                await self.diagnostics.check("navigation")
        self.assertEqual(caught.exception.reason, "account_protection")
        self.assertNotIn("TOKEN", str(caught.exception))

    def test_replace_oserror_warns_and_leaves_no_temp(self):
        original_replace = Path.replace

        def fail_temp_replace(self_path, target):
            if self_path.suffix == TEMP_SUFFIX:
                raise OSError("replace failed")
            return original_replace(self_path, target)

        before = self.diagnostics.report_path.read_text(encoding="utf-8")
        with patch.object(Path, "replace", fail_temp_replace), \
             self.assertLogs("naver.diagnostics", level="WARNING") as logs:
            self.diagnostics._save()
        self.assertIn("진단 파일 저장 실패 (OSError)", "\n".join(logs.output))
        self.assertEqual(self.diagnostics.data["diagnostic_write_error"], "OSError")
        self.assertEqual(self.diagnostics.report_path.read_text(encoding="utf-8"), before)
        self.assertFalse(self.diagnostics.report_path.with_suffix(TEMP_SUFFIX).exists())
        self.assertEqual(list(self.diagnostics.report_path.parent.glob(f"*{TEMP_SUFFIX}")), [])
