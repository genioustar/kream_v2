import socket
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from common.pacing import CrawlBlockedError
from ssfshop import crawler

SALE_URL = (
    "https://www.ssfshop.com/ADIDAS-KOREA/main?"
    "dspCtgryNo=SFM&brandShopNo=BDMA29AKU&currentPage=40"
)
SALE_URL_WITHOUT_PAGE = (
    "https://www.ssfshop.com/ADIDAS-KOREA/main?brandShopNo=BDMA29AKU"
)

NAME_EXAMPLES = (
    ("🩵추가할인🩵 가젤 볼드 IE0420", "IE0420"),
    ("🩵추가할인🩵 핸드볼 스페지알 IF6561", "IF6561"),
    ("🩵추가할인🩵 SAMBAE JH8836", "JH8836"),
    ("🩵추가할인🩵 독일 홈 저지 1994 JN3705", "JN3705"),
    ("🩵추가할인🩵 에브리데이 아이콘 크루 삭스 2켤레 KE0662", "KE0662"),
    ("🩵추가할인🩵 3-스트라이프 티 KE2423", "KE2423"),
    ("🩵추가할인🩵 데님 버뮤다 쇼츠 KA2675", "KA2675"),
)


def _card(god_no: str, name: str, list_price: str, sale_price: str) -> str:
    """발췌 페이지의 가격 칸 구조만 남긴 카드."""
    return f"""
    <li class="god-item" view-godno="{god_no}">
      <span class="name">{name}</span>
      <span class="price">
        <del><span class="wa_hidden">원가</span>{list_price}<span class="wa_hidden">원</span></del>
        <em class="sale"><span class="wa_hidden">할인율</span>72%</em>
        <span class="wa_hidden">판매가</span>{sale_price}<span class="wa_hidden">원</span>
      </span>
    </li>
    """


def _page(cards: str, outside: str = "") -> str:
    return f"""
    <html><body>
      {outside}
      <section class="god-lists" id="goods_container">
        <ul class="list-col-4" id="dspGood">{cards}</ul>
      </section>
    </body></html>
    """


def _empty_page(outside: str = "") -> str:
    return _page("", outside)


def _corner_card() -> str:
    return _card("OUTSIDE", "바깥 코너 YY8888", "8,000", "1,000")


def _parent_wrapped(container_inner: str, after_container: str = "") -> str:
    """부모 section 안에 목록 컨테이너와 그 뒤의 다른 코너를 둔다."""
    return f"""
    <html><body>
      <section class="list-wrap">
        <section class="god-lists" id="goods_container">
          {container_inner}
        </section>
        {after_container}
      </section>
    </body></html>
    """


def _loose_card(god_no: str, name: str, inner: str = "") -> str:
    return (
        f'<li class="god-item" view-godno="{god_no}">'
        f"{inner}"
        f'<span class="name">{name}</span>'
        f'<span class="price">판매가 41,720원</span>'
        "</li>"
    )


def _unclosed_div_before_list_page() -> str:
    """ul 앞의 닫히지 않은 div 와, 부모 section 안의 바깥 코너 카드."""
    inside = "<div>" + _card("GQDO1", "가젤 IE0420", "149,000", "41,720")
    return _parent_wrapped(f"<ul>{inside}</ul>", f"<ul>{_corner_card()}</ul>")


def _maintenance_page() -> str:
    """상품 목록 컨테이너가 없는 점검 안내 화면."""
    return """
    <html><body>
      <div class="maintenance">
        <h1>시스템 점검 안내</h1>
        <p>서비스 점검 중입니다. 잠시 후 다시 이용해 주세요.</p>
      </div>
    </body></html>
    """


def _unclosed_section_page() -> str:
    """컨테이너 바로 아래의 닫히지 않은 section 과, 부모 안의 바깥 코너."""
    inside = (
        "<ul>"
        + _card("IN1", "가젤 IE0420", "149,000", "41,720")
        + "</ul>"
        + "<section class='filter'>"
        + "<ul>"
        + _card("IN2", "삼바 ID0440", "139,000", "39,000")
        + "</ul>"
    )
    return _parent_wrapped(inside, f"<ul>{_corner_card()}</ul>")


def _stray_section_end_page() -> str:
    """컨테이너 안의 짝 없는 section 끝. 뒤 카드와 바깥 코너는 목록이 아니다."""
    kept = _card("IN1", "가젤 IE0420", "149,000", "41,720")
    dropped = _card("DROP1", "삼바 ID0440", "139,000", "39,000")
    return f"""
    <html><body>
      <section class="list-wrap">
        <section id="goods_container">
          <ul>{kept}</ul>
          </section>
          <ul>{dropped}</ul>
        </section>
        <ul>{_corner_card()}</ul>
      </section>
    </body></html>
    """


def _unclosed_card_page() -> str:
    """두 번째 카드의 끝 태그가 없다. 다음 section 끝은 목록 컨테이너를 닫는다."""
    first = _card("GQDO1", "가젤 IE0420", "149,000", "41,720")
    broken = """
    <li class="god-item" view-godno="GQDO2">
      <span class="name">삼바 ID0440</span>
      <span class="price"><span class="wa_hidden">판매가</span>39,000<span class="wa_hidden">원</span></span>
    """
    return f"""
    <html><body>
      <section class="god-lists" id="goods_container">
        <ul class="list-col-4" id="dspGood">{first}{broken}</ul>
      </section>
      </section>
    </body></html>
    """


def _response(body: str) -> MagicMock:
    response = MagicMock()
    response.read.return_value = body.encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _http_error(status: int) -> HTTPError:
    return HTTPError(
        "https://www.ssfshop.com/ADIDAS-KOREA/main",
        status,
        "error",
        hdrs=None,
        fp=None,
    )


class SsfshopCollectionTests(unittest.IsolatedAsyncioTestCase):
    def _urlopen(self, pages: list[str | BaseException]):
        seen: list[str] = []

        def urlopen(request, timeout=None):
            seen.append(request.full_url)
            item = pages[len(seen) - 1]
            if isinstance(item, BaseException):
                raise item
            return _response(item)

        return seen, urlopen

    async def test_parser_reads_sale_price_and_ignores_outside_cards(self):
        inside = (
            _card("GQDO24040878590", "🩵추가할인🩵 가젤 볼드 IE0420", "149,000", "41,720")
            + _card("GQDO24042605161", "🩵추가할인🩵 핸드볼 스페지알 IF6561", "149,000", "41,720")
            + _card("GQDO24081420101", "🩵추가할인🩵 삼바 ID0440", "149,000", "41,720")
        )
        html = (
            "<html><body>"
            + _card("OUTSIDE1", "가짜 앞 카드 ZZ9999", "10,000", "9,999")
            + '<section class="god-lists" id="goods_container"><ul>'
            + inside
            + "</ul></section>"
            + _card("OUTSIDE2", "가짜 뒤 카드 YY8888", "8,000", "1,000")
            + "</body></html>"
        )
        seen, urlopen = self._urlopen([html, _empty_page()])
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL), \
             patch.object(crawler, "urlopen", urlopen), \
             patch.object(crawler, "pause", new_callable=AsyncMock):
            products = await crawler.crawl_ssfshop()

        self.assertEqual(
            [(item.product_name, item.model_name, item.price, item.url) for item in products],
            [
                (
                    "🩵추가할인🩵 가젤 볼드 IE0420",
                    "IE0420",
                    41720,
                    "https://www.ssfshop.com/ADIDAS-KOREA/GQDO24040878590/good",
                ),
                (
                    "🩵추가할인🩵 핸드볼 스페지알 IF6561",
                    "IF6561",
                    41720,
                    "https://www.ssfshop.com/ADIDAS-KOREA/GQDO24042605161/good",
                ),
                (
                    "🩵추가할인🩵 삼바 ID0440",
                    "ID0440",
                    41720,
                    "https://www.ssfshop.com/ADIDAS-KOREA/GQDO24081420101/good",
                ),
            ],
        )
        self.assertTrue(all(item.site_name == "ssfshop" and item.sale_price is None for item in products))
        self.assertEqual([parse_qs(urlparse(url).query)["currentPage"] for url in seen], [["1"], ["2"]])

    async def test_example_names_keep_trailing_model_code(self):
        cards = "".join(
            _card(f"GQDO{index:04d}", name, "149,000", "41,720")
            for index, (name, _model) in enumerate(NAME_EXAMPLES, start=1)
        )
        _seen, urlopen = self._urlopen([_page(cards), _empty_page()])
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL), \
             patch.object(crawler, "urlopen", urlopen), \
             patch.object(crawler, "pause", new_callable=AsyncMock):
            products = await crawler.crawl_ssfshop()

        self.assertEqual(
            [item.model_name for item in products],
            [model for _name, model in NAME_EXAMPLES],
        )
        self.assertNotIn("1994", [item.model_name for item in products])
        self.assertNotIn("2", [item.model_name for item in products])

    async def test_pages_advance_from_one_and_pause_only_between_pages(self):
        pages = [
            _page(_card("GQDO1", "가젤 IE0420", "149,000", "41,720") + _card("GQDO2", "삼바 ID0440", "139,000", "39,000")),
            _page(_card("GQDO3", "핸드볼 IF6561", "129,000", "36,000")),
            _page(_card("GQDO4", "쇼츠 KA2675", "99,000", "27,000")),
            _empty_page(_card("OUTSIDE", "바깥 ZZ9999", "10,000", "1,000")),
            _empty_page(),
        ]
        seen, urlopen = self._urlopen(pages)
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL), \
             patch.object(crawler, "urlopen", urlopen), \
             patch.object(crawler, "pause", new_callable=AsyncMock) as pause:
            products = await crawler.crawl_ssfshop()

        self.assertEqual([item.model_name for item in products], ["IE0420", "ID0440", "IF6561", "KA2675"])
        self.assertEqual([item.price for item in products], [41720, 39000, 36000, 27000])
        self.assertEqual(
            [parse_qs(urlparse(url).query)["currentPage"] for url in seen],
            [["1"], ["2"], ["3"], ["4"]],
        )
        self.assertTrue(all(parse_qs(urlparse(url).query)["brandShopNo"] == ["BDMA29AKU"] for url in seen))
        self.assertEqual(
            [call.args for call in pause.await_args_list],
            [(crawler.PAGE_DELAY_MIN_SEC, crawler.PAGE_DELAY_MAX_SEC, crawler.PAGE_DELAY_REASON)] * 3,
        )

    async def test_duplicate_card_is_stored_once_and_page_starts_at_one(self):
        card = _card("GQDO1", "가젤 IE0420", "149,000", "41,720")
        seen, urlopen = self._urlopen([_page(card), _page(card), _empty_page()])
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL_WITHOUT_PAGE), \
             patch.object(crawler, "urlopen", urlopen), \
             patch.object(crawler, "pause", new_callable=AsyncMock):
            products = await crawler.crawl_ssfshop()

        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].model_name, "IE0420")
        self.assertEqual(
            [parse_qs(urlparse(url).query)["currentPage"] for url in seen],
            [["1"], ["2"], ["3"]],
        )

    async def test_one_card_with_two_codes_keeps_the_last(self):
        html = _page(_card("GQDO1", "테스트 AA0001 BB0002", "80,000", "20,000"))
        _seen, urlopen = self._urlopen([html, _empty_page()])
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL), \
             patch.object(crawler, "urlopen", urlopen), \
             patch.object(crawler, "pause", new_callable=AsyncMock):
            products = await crawler.crawl_ssfshop()

        self.assertEqual(
            [(item.model_name, item.price, item.url) for item in products],
            [
                ("BB0002", 20000, "https://www.ssfshop.com/ADIDAS-KOREA/GQDO1/good"),
            ],
        )

    async def test_trailing_code_only_when_name_contains_an_abbreviation(self):
        cards = (
            _card("GQDO1", "CLUB 3STR TEE JG0978", "89,000", "24,920")
            + _card("GQDO2", "가젤 볼드 IE0420", "149,000", "41,720")
            + _card("GQDO3", "독일 홈 저지 1994 JN3705", "129,000", "36,120")
            + _card("GQDO4", "SOLID CLX SH SL", "79,000", "39,000")
        )
        _seen, urlopen = self._urlopen([_page(cards), _empty_page()])
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL), \
             patch.object(crawler, "urlopen", urlopen), \
             patch.object(crawler, "pause", new_callable=AsyncMock):
            products = await crawler.crawl_ssfshop()

        self.assertEqual(
            [(item.product_name, item.model_name) for item in products],
            [
                ("CLUB 3STR TEE JG0978", "JG0978"),
                ("가젤 볼드 IE0420", "IE0420"),
                ("독일 홈 저지 1994 JN3705", "JN3705"),
            ],
        )
        self.assertNotIn("3STR", [item.model_name for item in products])

    async def test_skips_missing_sale_price_or_blank_name(self):
        cards = (
            _card("GQDO1", "가젤 IE0420", "149,000", "41,720")
            + """
            <li class="god-item" view-godno="GQDO2">
              <span class="name">코드 있는 품절 KE0662</span>
              <span class="price">품절</span>
            </li>
            <li class="god-item" view-godno="GQDO3">
              <span class="name">원가만 있는 카드 KA2675</span>
              <span class="price"><span class="wa_hidden">원가</span>10,000<span class="wa_hidden">원</span></span>
            </li>
            <li class="god-item" view-godno="GQDO4">
              <span class="name">   </span>
              <span class="price"><span class="wa_hidden">판매가</span>5,000<span class="wa_hidden">원</span></span>
            </li>
            <li class="god-item" view-godno="GQDO5">
              <span class="name">모델코드 없는 샌들</span>
              <span class="price"><span class="wa_hidden">판매가</span>12,000<span class="wa_hidden">원</span></span>
            </li>
            """
            + _card("GQDO6", "삼바 JH8836", "139,000", "39,000")
        )
        _seen, urlopen = self._urlopen([_page(cards), _empty_page()])
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL), \
             patch.object(crawler, "urlopen", urlopen), \
             patch.object(crawler, "pause", new_callable=AsyncMock):
            products = await crawler.crawl_ssfshop()

        self.assertEqual(
            [(item.model_name, item.price) for item in products],
            [("IE0420", 41720), ("JH8836", 39000)],
        )

    async def test_second_page_429_is_pipeline_block(self):
        await self._assert_second_page_error(_http_error(429), CrawlBlockedError)

    async def test_second_page_403_is_pipeline_block(self):
        await self._assert_second_page_error(_http_error(403), CrawlBlockedError)

    async def test_second_page_500_is_not_pipeline_block(self):
        await self._assert_second_page_error(_http_error(500), HTTPError)

    async def test_second_page_timeout_propagates(self):
        await self._assert_second_page_error(socket.timeout("timed out"), socket.timeout)

    async def test_first_page_500_does_not_return_empty_list(self):
        _seen, urlopen = self._urlopen([_http_error(500)])
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL), \
             patch.object(crawler, "urlopen", urlopen), \
             patch.object(crawler, "pause", new_callable=AsyncMock) as pause:
            with self.assertRaises(HTTPError) as caught:
                await crawler.crawl_ssfshop()
        self.assertNotIsInstance(caught.exception, CrawlBlockedError)
        pause.assert_not_awaited()

    async def test_max_pages_with_more_products_raises(self):
        card = _page(_card("GQDO1", "가젤 IE0420", "149,000", "41,720"))
        _seen, urlopen = self._urlopen([card, card, card, card])
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL), \
             patch.object(crawler, "urlopen", urlopen), \
             patch.object(crawler, "MAX_PAGES", 3), \
             patch.object(crawler, "pause", new_callable=AsyncMock):
            with self.assertRaisesRegex(RuntimeError, "상한"):
                await crawler.crawl_ssfshop()
        self.assertEqual(len(_seen), 3)

    async def test_blank_sale_url_returns_empty_without_network(self):
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", ""), \
             patch.object(crawler, "urlopen") as urlopen, \
             patch.object(crawler, "pause", new_callable=AsyncMock) as pause:
            self.assertEqual(await crawler.crawl_ssfshop(), [])
        urlopen.assert_not_called()
        pause.assert_not_awaited()

    async def test_first_empty_page_returns_empty(self):
        seen, urlopen = self._urlopen([
            _empty_page(_card("OUTSIDE", "바깥 ZZ9999", "10,000", "1")),
            _empty_page(),
        ])
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL), \
             patch.object(crawler, "urlopen", urlopen), \
             patch.object(crawler, "pause", new_callable=AsyncMock) as pause:
            self.assertEqual(await crawler.crawl_ssfshop(), [])
        self.assertEqual(len(seen), 1)
        pause.assert_not_awaited()

    async def test_unslashed_img_keeps_all_three_cards(self):
        first = _card("GQDO1", "가젤 볼드 IE0420", "149,000", "41,720")
        first = first.replace(">", '><img src="x">', 1)
        second = _card("GQDO2", "삼바 ID0440", "139,000", "39,000")
        second = second.replace(">", '><img src="y" />', 1)
        third = _card("GQDO3", "핸드볼 IF6561", "129,000", "36,000")
        _seen, urlopen = self._urlopen([_page(first + second + third), _empty_page()])
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL), \
             patch.object(crawler, "urlopen", urlopen), \
             patch.object(crawler, "pause", new_callable=AsyncMock):
            products = await crawler.crawl_ssfshop()

        self.assertEqual(
            [item.model_name for item in products],
            ["IE0420", "ID0440", "IF6561"],
        )

    async def test_br_inside_name_closes_card_and_normalizes_text(self):
        cards = (
            _card("GQDO1", "가젤<br>\n볼드 IE0420", "149,000", "41,720")
            + _card("GQDO2", "삼바 ID0440", "139,000", "39,000")
            + _card("GQDO3", "핸드볼 IF6561", "129,000", "36,000")
        )
        _seen, urlopen = self._urlopen([_page(cards), _empty_page()])
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL), \
             patch.object(crawler, "urlopen", urlopen), \
             patch.object(crawler, "pause", new_callable=AsyncMock):
            products = await crawler.crawl_ssfshop()

        self.assertEqual(
            [(item.product_name, item.model_name) for item in products],
            [
                ("가젤 볼드 IE0420", "IE0420"),
                ("삼바 ID0440", "ID0440"),
                ("핸드볼 IF6561", "IF6561"),
            ],
        )

    async def test_unslashed_input_outside_cards_excludes_corner_item(self):
        inside = (
            _card("GQDO1", "가젤 IE0420", "149,000", "41,720")
            + _card("GQDO2", "삼바 ID0440", "139,000", "39,000")
            + _card("GQDO3", "핸드볼 IF6561", "129,000", "36,000")
        )
        html = (
            "<html><body>"
            '<section class="god-lists" id="goods_container">'
            '<input type="hidden" name="ref" value="1">'
            f"<ul>{inside}</ul>"
            "</section>"
            + _card("OUTSIDE", "가짜 뒤 카드 YY8888", "8,000", "1,000")
            + "</body></html>"
        )
        _seen, urlopen = self._urlopen([html, _empty_page()])
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL), \
             patch.object(crawler, "urlopen", urlopen), \
             patch.object(crawler, "pause", new_callable=AsyncMock):
            products = await crawler.crawl_ssfshop()

        self.assertEqual(
            [item.model_name for item in products],
            ["IE0420", "ID0440", "IF6561"],
        )

    def test_stray_div_end_between_cards_raises(self):
        html = _parent_wrapped(
            "<ul>"
            + _card("GQDO1", "가젤 IE0420", "149,000", "41,720")
            + "</div>"
            + _card("GQDO2", "삼바 ID0440", "139,000", "39,000")
            + "</ul>",
            f"<ul>{_corner_card()}</ul>",
        )
        with self.assertRaises(RuntimeError):
            crawler._parse_cards(html)

    def test_unclosed_div_before_list_inside_parent_section_raises(self):
        with self.assertRaises(RuntimeError):
            crawler._parse_cards(_unclosed_div_before_list_page())

    def test_stray_div_end_before_list_raises(self):
        html = _parent_wrapped(
            "</div><ul>" + _card("GQDO1", "가젤 IE0420", "149,000", "41,720") + "</ul>",
            f"<ul>{_corner_card()}</ul>",
        )
        with self.assertRaises(RuntimeError):
            crawler._parse_cards(html)

    def test_unclosed_paragraph_between_cards_raises(self):
        html = _page(
            _card("GQDO1", "가젤 IE0420", "149,000", "41,720")
            + "<p>"
            + _card("GQDO2", "삼바 ID0440", "139,000", "39,000")
        )
        with self.assertRaises(RuntimeError):
            crawler._parse_cards(html)

    def test_early_list_item_end_raises(self):
        broken = (
            '<li class="god-item" view-godno="GQDO2">'
            "</li>"
            '<span class="name">삼바 ID0440</span>'
            '<span class="price">판매가 39,000원</span>'
            "</li>"
        )
        html = _page(_card("GQDO1", "가젤 IE0420", "149,000", "41,720") + broken)
        with self.assertRaises(RuntimeError):
            crawler._parse_cards(html)

    def test_compensating_paragraph_tags_raise(self):
        cards = "".join(
            _loose_card(
                f"G{index}",
                f"상품 AB{index:04d}",
                "<p>" if index == 1 else "</p>" if index == 3 else "",
            )
            for index in range(5)
        )
        with self.assertRaisesRegex(RuntimeError, "G1"):
            crawler._parse_cards(_page(cards))

    def test_second_name_or_price_span_raises(self):
        repeated_name = _page(
            '<li class="god-item" view-godno="G1">'
            '<span class="name">가젤 IE0420</span>'
            '<span class="name">삼바 ID0440</span>'
            '<span class="price">판매가 41,720원</span>'
            "</li>"
        )
        repeated_price = _page(
            '<li class="god-item" view-godno="G1">'
            '<span class="name">가젤 IE0420</span>'
            '<span class="price">판매가 41,720원</span>'
            '<span class="price">판매가 39,000원</span>'
            "</li>"
        )
        with self.assertRaisesRegex(RuntimeError, "이름"):
            crawler._parse_cards(repeated_name)
        with self.assertRaisesRegex(RuntimeError, "가격"):
            crawler._parse_cards(repeated_price)

    def test_unclosed_container_raises(self):
        html = (
            '<section id="goods_container"><ul>'
            + _card("GQDO1", "가젤 IE0420", "149,000", "41,720")
            + "</ul>"
        )
        with self.assertRaisesRegex(RuntimeError, "닫히지 않았습니다"):
            crawler._parse_cards(html)

    def test_balanced_markup_in_container_parses(self):
        marked = """
        <li class="god-item" view-godno="G1">
          <BR><br/><br /><IMG SRC=x>
          <p>설명</p>
          <svg><path d="x"/></svg>
          <!-- <div><li class="god-item" view-godno="HIDE"> -->
          <script>var s='<div><li class="god-item" view-godno="HIDE2">';</script>
          <span class="name">가젤 <b>볼드</b> <span class="name">IE0420</span></span>
          <span class="price"><span class="wa_hidden">판매가</span>41,720<span class="wa_hidden">원</span></span>
        </li>
        """
        html = _parent_wrapped(
            '<input type="hidden" name="x" value="1"><ul>'
            + marked
            + _card("G2", "삼바 ID0440", "139,000", "39,000")
            + "</ul>",
            f"<ul>{_corner_card()}</ul>",
        )
        cards = crawler._parse_cards(html)
        self.assertEqual([card.god_no for card in cards], ["G1", "G2"])
        products = [
            product
            for card in cards
            for product in crawler._products_from_card(card, "ADIDAS-KOREA", "t")
        ]
        self.assertEqual(
            [(item.product_name, item.model_name, item.price) for item in products],
            [
                ("가젤 볼드 IE0420", "IE0420", 41720),
                ("삼바 ID0440", "ID0440", 39000),
            ],
        )

    async def test_unclosed_div_before_list_raises_from_crawl(self):
        fetch = AsyncMock(return_value=_unclosed_div_before_list_page())
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL), \
             patch.object(crawler, "_fetch_html_async", fetch), \
             patch.object(crawler, "pause", new_callable=AsyncMock) as pause:
            with self.assertRaises(RuntimeError):
                await crawler.crawl_ssfshop()
        pause.assert_not_awaited()

    def test_missing_card_end_raises(self):
        with self.assertRaisesRegex(RuntimeError, "GQDO2"):
            crawler._parse_cards(_unclosed_card_page())

    async def test_missing_card_end_on_first_page_raises_from_crawl(self):
        fetch = AsyncMock(return_value=_unclosed_card_page())
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL), \
             patch.object(crawler, "_fetch_html_async", fetch), \
             patch.object(crawler, "pause", new_callable=AsyncMock) as pause:
            with self.assertRaisesRegex(RuntimeError, "GQDO2"):
                await crawler.crawl_ssfshop()
        pause.assert_not_awaited()

    def test_missing_container_page_raises(self):
        with self.assertRaisesRegex(RuntimeError, "목록 영역이 없습니다"):
            crawler._parse_cards(_maintenance_page())

    async def test_missing_container_on_later_page_does_not_return_partial(self):
        pages = [
            _page(_card("GQDO1", "가젤 IE0420", "149,000", "41,720")),
            _page(_card("GQDO2", "삼바 ID0440", "139,000", "39,000")),
            _page(_card("GQDO3", "핸드볼 IF6561", "129,000", "36,000")),
            _maintenance_page(),
        ]
        fetch = AsyncMock(side_effect=pages)
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL), \
             patch.object(crawler, "_fetch_html_async", fetch), \
             patch.object(crawler, "pause", new_callable=AsyncMock):
            with self.assertRaisesRegex(RuntimeError, "목록 영역이 없습니다"):
                await crawler.crawl_ssfshop()
        self.assertEqual(fetch.await_count, 4)

    def test_empty_container_returns_no_cards(self):
        self.assertEqual(crawler._parse_cards(_empty_page(_corner_card())), [])

    async def test_crawl_stops_on_empty_container_after_products(self):
        pages = [
            _page(_card("GQDO1", "가젤 IE0420", "149,000", "41,720")),
            _empty_page(_corner_card()),
        ]
        fetch = AsyncMock(side_effect=pages)
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL), \
             patch.object(crawler, "_fetch_html_async", fetch), \
             patch.object(crawler, "pause", new_callable=AsyncMock):
            products = await crawler.crawl_ssfshop()
        self.assertEqual([item.model_name for item in products], ["IE0420"])
        self.assertEqual(fetch.await_count, 2)

    async def test_first_page_empty_container_returns_empty(self):
        fetch = AsyncMock(side_effect=[
            _empty_page(_corner_card()),
            _page(_card("GQDO1", "가젤 IE0420", "149,000", "41,720")),
        ])
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL), \
             patch.object(crawler, "_fetch_html_async", fetch), \
             patch.object(crawler, "pause", new_callable=AsyncMock) as pause:
            self.assertEqual(await crawler.crawl_ssfshop(), [])
        self.assertEqual(fetch.await_count, 1)
        pause.assert_not_awaited()

    def test_unclosed_section_inside_container_raises(self):
        with self.assertRaisesRegex(RuntimeError, "IN1"):
            crawler._parse_cards(_unclosed_section_page())

    def test_stray_section_end_raises(self):
        with self.assertRaisesRegex(RuntimeError, "짝 없는"):
            crawler._parse_cards(_stray_section_end_page())

    def test_second_container_in_sequence_raises(self):
        html = (
            "<html><body>"
            '<section id="goods_container"><ul>'
            + _card("A1", "가젤 IE0420", "149,000", "41,720")
            + "</ul></section>"
            '<section id="goods_container"><ul>'
            + _card("A2", "삼바 ID0440", "139,000", "39,000")
            + "</ul></section>"
            "</body></html>"
        )
        with self.assertRaisesRegex(RuntimeError, "이미 닫힌 뒤"):
            crawler._parse_cards(html)

    def test_nested_container_raises(self):
        html = (
            "<html><body>"
            '<section id="goods_container"><ul>'
            + _card("A1", "가젤 IE0420", "149,000", "41,720")
            + "</ul>"
            '<section id="goods_container"><ul>'
            + _card("A2", "삼바 ID0440", "139,000", "39,000")
            + "</ul></section>"
            "</section>"
            "</body></html>"
        )
        with self.assertRaisesRegex(RuntimeError, "이미 열려"):
            crawler._parse_cards(html)

    def test_nested_list_item_does_not_close_card(self):
        inner = """
        <li class="god-item" view-godno="G1">
          <ul><li class="opt">사이즈</li></ul>
          <span class="name">가젤 IE0420</span>
          <span class="price">판매가 41,720원</span>
        </li>
        """
        cards = crawler._parse_cards(
            _page(inner + _card("G2", "삼바 ID0440", "139,000", "39,000"))
        )
        self.assertEqual([card.god_no for card in cards], ["G1", "G2"])
        self.assertEqual(crawler._normalize_text(cards[0].name), "가젤 IE0420")
        self.assertEqual(crawler._sale_price(cards[0].price_text), 41720)

    def test_div_container_is_not_the_list_area(self):
        html = (
            "<html><body>"
            '<div id="goods_container"><ul>'
            + _card("IN1", "가젤 IE0420", "149,000", "41,720")
            + "</ul></div>"
            f"<ul>{_corner_card()}</ul>"
            "</body></html>"
        )
        with self.assertRaisesRegex(RuntimeError, "목록 영역이 없습니다"):
            crawler._parse_cards(html)

    async def _assert_second_page_error(self, error: BaseException, expected: type[BaseException]) -> None:
        pages = [
            _page(_card("GQDO1", "가젤 IE0420", "149,000", "41,720")),
            error,
        ]
        seen, urlopen = self._urlopen(pages)
        with patch.object(crawler.config, "SSFSHOP_SALE_URL", SALE_URL), \
             patch.object(crawler, "urlopen", urlopen), \
             patch.object(crawler, "pause", new_callable=AsyncMock):
            with self.assertRaises(expected) as caught:
                await crawler.crawl_ssfshop()
        if expected is HTTPError:
            self.assertNotIsInstance(caught.exception, CrawlBlockedError)
        self.assertEqual(len(seen), 2)
