import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from common.ego_session import Request, Response
from common.models import KreamProduct, MarketplaceProduct
from common.pacing import CrawlBlockedError, check_response
from kream.comparator import find_arbitrage
from kream.crawler import (
    PAGE_LOAD_TIMEOUT,
    SELECTOR_TIMEOUT,
    _find_verified_kream_product,
    _load_detail_model_number,
)
from kream.parser import (
    KREAM_SELECTORS,
    model_numbers_match,
    parse_kream_model_number,
)


class KreamModelParserTests(unittest.TestCase):
    def test_parses_detail_model_number_label(self):
        self.assertEqual(parse_kream_model_number("모델번호 IG5349"), "IG5349")

    def test_rejects_unrelated_detail_text(self):
        self.assertIsNone(parse_kream_model_number("발매일 23/08/07"))

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


DETAIL_PRODUCT_URL = "https://kream.co.kr/products/156167"
HTTP_OK = 200
DOCUMENT_RESOURCE_TYPE = "document"
GOTO_WAIT_UNTIL = "domcontentloaded"
SELECTOR_WAIT_STATE = "attached"
DETAIL_MODEL_TEXT = "모델번호 IG5349"
PARSED_MODEL_NUMBER = "IG5349"
DUPLICATE_DETAIL_MODEL_TEXTS = (DETAIL_MODEL_TEXT, DETAIL_MODEL_TEXT)
UNPARSED_SIZE_TEXT = "사이즈 270"
UNPARSED_SHIPPING_TEXT = "배송 정보"
UNPARSED_DETAIL_TEXTS = (UNPARSED_SIZE_TEXT, UNPARSED_SHIPPING_TEXT)
EMPTY_DETAIL_TEXTS: tuple[str, ...] = ()
TEXT_READ_FAILURE = "상세 텍스트를 읽지 못했습니다"
BLOCKED_DETAIL_PATTERN = "Kream 상세 페이지 접근 제한/비정상 응답"


class KreamDetailModelLoadingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for patcher in (
            patch("kream.crawler._is_blocked_page", new=AsyncMock(return_value=False)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _document_response(self) -> Response:
        """본문이 없는 상세 문서 응답을 만든다."""
        return Response(
            DETAIL_PRODUCT_URL,
            HTTP_OK,
            {},
            Request(
                DETAIL_PRODUCT_URL,
                DOCUMENT_RESOURCE_TYPE,
                True,
                object(),
            ),
            None,
        )

    def _detail_page(
        self,
        texts: tuple[str, ...],
        *,
        goto_error: Exception | None = None,
        wait_error: Exception | None = None,
        text_error: Exception | None = None,
    ) -> tuple[MagicMock, MagicMock]:
        """상세 이동·셀렉터·텍스트 읽기를 각각 성공 또는 예외로 두는 페이지를 만든다."""
        model_locator = MagicMock()
        model_locator.wait_for = AsyncMock()
        if text_error is None:
            model_locator.all_text_contents = AsyncMock(return_value=list(texts))
        else:
            model_locator.all_text_contents = AsyncMock(side_effect=text_error)
        page = MagicMock()
        page._kream_blocked_status = None
        if goto_error is None:
            page.goto = AsyncMock(return_value=self._document_response())
        else:
            page.goto = AsyncMock(side_effect=goto_error)
        if wait_error is None:
            page.wait_for_selector = AsyncMock()
        else:
            page.wait_for_selector = AsyncMock(side_effect=wait_error)
        page.locator.return_value = model_locator
        return page, model_locator

    def _watch_parser(self):
        return patch(
            "kream.crawler.parse_kream_model_number",
            wraps=parse_kream_model_number,
        )

    def _assert_opened_detail(self, page: MagicMock) -> None:
        page.goto.assert_awaited_once_with(
            DETAIL_PRODUCT_URL,
            wait_until=GOTO_WAIT_UNTIL,
            timeout=PAGE_LOAD_TIMEOUT,
        )

    def _assert_waited_for_model_selector(self, page: MagicMock) -> None:
        page.wait_for_selector.assert_awaited_once_with(
            KREAM_SELECTORS["detail_model_number"],
            state=SELECTOR_WAIT_STATE,
            timeout=SELECTOR_TIMEOUT,
        )

    async def _assert_returns_none(self, page: MagicMock) -> None:
        """None을 반환하고 CrawlBlockedError로 오판하지 않는지 확인한다."""
        try:
            model_number = await _load_detail_model_number(page, DETAIL_PRODUCT_URL)
        except CrawlBlockedError as exc:
            self.fail(f"상세 DOM 실패를 차단으로 오판했습니다: {exc}")
        self.assertIsNone(model_number)

    async def test_reads_model_number_from_dom_when_response_body_is_none(self):
        page, _model_locator = self._detail_page(DUPLICATE_DETAIL_MODEL_TEXTS)

        model_number = await _load_detail_model_number(page, DETAIL_PRODUCT_URL)

        self.assertEqual(model_number, PARSED_MODEL_NUMBER)
        page.wait_for_selector.assert_awaited_once()
        self.assertEqual(
            page.wait_for_selector.await_args.kwargs["state"],
            SELECTOR_WAIT_STATE,
        )

    async def test_blocked_detail_page_raises_without_dom_wait(self):
        page, model_locator = self._detail_page((DETAIL_MODEL_TEXT,))

        with patch("kream.crawler._is_blocked_page", new=AsyncMock(return_value=True)):
            with self.assertRaisesRegex(CrawlBlockedError, BLOCKED_DETAIL_PATTERN):
                await _load_detail_model_number(page, DETAIL_PRODUCT_URL)

        page.wait_for_selector.assert_not_awaited()
        model_locator.wait_for.assert_not_awaited()

    async def test_selector_timeout_returns_none_without_reading_texts(self):
        """셀렉터 대기 예외는 텍스트를 읽지 않고 None이며 차단이 아니다."""
        page, model_locator = self._detail_page(
            DUPLICATE_DETAIL_MODEL_TEXTS,
            wait_error=TimeoutError(),
        )

        with self._watch_parser() as parse_model:
            await self._assert_returns_none(page)

        self._assert_opened_detail(page)
        self._assert_waited_for_model_selector(page)
        page.locator.assert_not_called()
        model_locator.all_text_contents.assert_not_awaited()
        parse_model.assert_not_called()

    async def test_text_read_error_returns_none_without_parsing(self):
        """텍스트 읽기 예외는 파싱하지 않고 None이며 차단이 아니다."""
        page, model_locator = self._detail_page(
            DUPLICATE_DETAIL_MODEL_TEXTS,
            text_error=RuntimeError(TEXT_READ_FAILURE),
        )

        with self._watch_parser() as parse_model:
            await self._assert_returns_none(page)

        self._assert_opened_detail(page)
        self._assert_waited_for_model_selector(page)
        page.locator.assert_called_once_with(KREAM_SELECTORS["detail_model_number"])
        model_locator.all_text_contents.assert_awaited_once()
        parse_model.assert_not_called()

    async def test_unparsed_detail_texts_return_none(self):
        """모델번호가 아닌 문단만 있으면 파싱이 모두 실패해 None이며 차단이 아니다."""
        page, model_locator = self._detail_page(UNPARSED_DETAIL_TEXTS)

        with self._watch_parser() as parse_model:
            await self._assert_returns_none(page)

        self._assert_opened_detail(page)
        self._assert_waited_for_model_selector(page)
        model_locator.all_text_contents.assert_awaited_once()
        self.assertEqual(
            [item.args for item in parse_model.call_args_list],
            [(UNPARSED_SIZE_TEXT,), (UNPARSED_SHIPPING_TEXT,)],
        )

    async def test_empty_detail_texts_return_none_without_parsing(self):
        """상세 텍스트가 비어 있으면 파싱하지 않고 None이며 차단이 아니다."""
        page, model_locator = self._detail_page(EMPTY_DETAIL_TEXTS)

        with self._watch_parser() as parse_model:
            await self._assert_returns_none(page)

        self._assert_opened_detail(page)
        self._assert_waited_for_model_selector(page)
        model_locator.all_text_contents.assert_awaited_once()
        self.assertEqual(model_locator.all_text_contents.return_value, [])
        parse_model.assert_not_called()

    async def test_goto_error_still_reads_model_number_when_response_is_none(self):
        """goto 예외 뒤 응답이 None이어도 check_response를 통과하고 DOM 모델번호를 반환한다."""
        page, model_locator = self._detail_page(
            DUPLICATE_DETAIL_MODEL_TEXTS,
            goto_error=TimeoutError(),
        )

        with patch("kream.crawler.check_response", wraps=check_response) as checked:
            model_number = await _load_detail_model_number(page, DETAIL_PRODUCT_URL)

        self.assertEqual(model_number, PARSED_MODEL_NUMBER)
        checked.assert_called_once_with(None)
        self._assert_opened_detail(page)
        self._assert_waited_for_model_selector(page)
        model_locator.all_text_contents.assert_awaited_once()


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
