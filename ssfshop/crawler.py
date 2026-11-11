"""
ssfshop/crawler.py
SSF SHOP 브랜드관 목록 크롤러.

ADIDAS KOREA 브랜드관은 인증 없이 서버가 렌더한 HTML을 준다.
currentPage 쿼리만 바꿔 다음 페이지를 받고, #goods_container 안의 카드만 수집한다.
"""
import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

import config
from common.logger import get_logger
from common.model_names import extract_model_names
from common.models import MarketplaceProduct
from common.pacing import check_response, pause

logger = get_logger("ssfshop.crawler")

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
SITE_NAME = "ssfshop"
# 모델코드는 상품명 끝의 단일 코드다. 이름 예 7개에서 suffix_single이 기대값과 같다.
MODEL_STRATEGY = "suffix_single"
# 빈 목록이 이 페이지까지 나오지 않으면 잘린 결과를 성공으로 돌려주지 않는다.
MAX_PAGES = 150
# 목록 영역에 카드가 0개인 페이지로 끝나도, 이 수보다 적으면 중간 점검·차단 화면일 수 있어
# 성공으로 돌려주지 않는다. 2026-11-11 실측 2,184개의 약 46%. 세일 규모가 크게 줄면 이 값을 내린다.
MIN_PRODUCTS = 1000
REQUEST_TIMEOUT_SEC = 20
# 목록은 currentPage만 다른 서버 HTML이다. 다음 페이지를 요청하기 전에만 쉰다.
PAGE_DELAY_MIN_SEC = 2.5
PAGE_DELAY_MAX_SEC = 4.5
PAGE_DELAY_REASON = "SSF SHOP 다음 목록 페이지 요청 전 대기"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
ACCEPT_LANGUAGE = "ko-KR,ko;q=0.9"
GOODS_CONTAINER_ID = "goods_container"
CARD_CLASS = "god-item"
NAME_CLASS = "name"
PRICE_CLASS = "price"
DETAIL_URL = "https://www.ssfshop.com/{brand_slug}/{god_no}/good"
# 판매가는 이미 추가할인이 반영된 금액이다. 앞쪽 원가·할인율 숫자는 쓰지 않는다.
SALE_PRICE_RE = re.compile(r"판매가\s*([0-9][0-9,]*)")
# 한 카드에서 이름·가격 캡처가 두 번 열리면 서로 다른 상품이 한 카드로 합쳐진 것이다.
MAX_CAPTURES_PER_CARD_FIELD = 1
# 끝 태그가 없는 요소를 스택에 넣으면 컨테이너 안 짝 검사가 어긋난다.
HTML_VOID_ELEMENTS = frozenset({
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "track",
    "wbr",
})


@dataclass(frozen=True)
class _GoodsCard:
    """목록 영역 카드 하나. 가격 문자열은 아직 판매가로 해석하기 전이다."""

    name: str
    god_no: str
    price_text: str


class _GoodsListParser(HTMLParser):
    """#goods_container 안의 god-item만 카드로 읽는다.

    컨테이너는 section#goods_container 가 한 번 열리는 것만 인정한다.
    열린 뒤부터 닫힐 때까지는 태그 이름 스택으로 짝을 검사한다.
    컨테이너 안 section 시작, 컨테이너가 두 번 열리는 것, 짝 없는 section 끝은 예외다.
    section 이외의 컨테이너 밖 태그는 스택에 넣지 않는다.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[_GoodsCard] = []
        self._stack: list[str] = []
        self._container_seen = False
        # section 시작·끝만 센다. 0 아래로 내려가면 짝 없는 끝 태그다.
        self._section_depth = 0
        self._card_level: int | None = None
        self._name_parts: list[str] = []
        self._price_parts: list[str] = []
        self._name_captures = 0
        self._price_captures = 0
        self._god_no = ""
        # 닫힌 카드의 번호. 영역 안·카드 밖 오류는 열린 카드가 아니라 이 값을 가리킨다.
        self._prev_god_no: str | None = None
        self._capture: str | None = None
        self._capture_level: int | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        classes = attr.get("class", "").split()
        if tag == "section" and attr.get("id") == GOODS_CONTAINER_ID:
            self._open_container()
            return
        if not self._stack:
            if tag == "section":
                self._section_depth += 1
            return
        if tag == "section":
            raise RuntimeError(
                "상품 목록 영역 안에 section 이 있습니다: "
                + self._location()
            )
        if tag not in HTML_VOID_ELEMENTS:
            self._stack.append(tag)
        if (
            self._card_level is None
            and tag == "li"
            and CARD_CLASS in classes
        ):
            self._card_level = len(self._stack)
            self._name_captures = 0
            self._price_captures = 0
            self._god_no = attr.get("view-godno", "")
            self._name_parts = []
            self._price_parts = []
        if self._card_level is not None and self._capture is None and tag == "span":
            if NAME_CLASS in classes:
                self._begin_capture("name")
            elif PRICE_CLASS in classes:
                self._begin_capture("price")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # 기본 구현은 시작 다음 끝 처리를 호출한다. void 는 스택에 넣지 않으므로
        # 끝 처리까지 타면 닫는 태그가 스택 맨 위와 달라 예외가 된다.
        if tag in HTML_VOID_ELEMENTS:
            self.handle_starttag(tag, attrs)
            return
        super().handle_startendtag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            if tag == "section":
                self._note_section_end()
            return
        open_tag = self._stack[-1]
        if tag != open_tag:
            raise RuntimeError(
                "상품 목록 컨테이너 안의 태그가 맞지 않습니다: "
                f"열린 태그={open_tag}, 닫는 태그={tag}, {self._location()}"
            )
        if self._capture is not None and len(self._stack) == self._capture_level:
            self._finish_capture()
        if (
            self._card_level is not None
            and tag == "li"
            and len(self._stack) == self._card_level
        ):
            self.cards.append(
                _GoodsCard(
                    name="".join(self._name_parts),
                    god_no=self._god_no,
                    price_text="".join(self._price_parts),
                )
            )
            self._prev_god_no = self._god_no
            self._card_level = None
        self._stack.pop()
        if tag == "section":
            self._note_section_end()

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._buffer.append(data)

    def _begin_capture(self, field: str) -> None:
        if field == "name":
            self._name_captures += 1
            count = self._name_captures
            label = "이름"
        else:
            self._price_captures += 1
            count = self._price_captures
            label = "가격"
        if count > MAX_CAPTURES_PER_CARD_FIELD:
            raise RuntimeError(
                f"한 상품 카드에서 {label} 영역이 두 번 열렸습니다: "
                + self._location()
            )
        self._capture = field
        self._capture_level = len(self._stack)
        self._buffer = []

    def _finish_capture(self) -> None:
        text = "".join(self._buffer)
        if self._capture == "name":
            self._name_parts.append(text)
        elif self._capture == "price":
            self._price_parts.append(text)
        self._capture = None
        self._buffer = []

    def _open_container(self) -> None:
        """section#goods_container 는 문서에서 한 번만 목록 영역이 된다."""
        if self._stack:
            raise RuntimeError(
                "상품 목록 영역이 이미 열려 있는데 다시 열렸습니다: "
                + self._location()
            )
        if self._container_seen:
            raise RuntimeError(
                "상품 목록 영역이 이미 닫힌 뒤에 다시 열렸습니다: "
                + self._location()
            )
        self._container_seen = True
        self._section_depth += 1
        self._stack.append("section")

    def _note_section_end(self) -> None:
        """section 끝 하나만 깊이를 줄인다. 0 아래는 짝이 없는 끝이다."""
        if self._section_depth == 0:
            raise RuntimeError(
                "짝 없는 section 끝 태그입니다: "
                + self._location()
            )
        self._section_depth -= 1

    def _location(self) -> str:
        """오류가 난 자리. 영역 밖 오류에는 마지막으로 본 카드 번호를 붙이지 않는다."""
        if not self._container_seen:
            return "상품 목록 영역 앞"
        if not self._stack:
            return "상품 목록 영역 뒤"
        if self._card_level is not None:
            return f"카드 godNo={self._god_no!r}"
        if self._prev_god_no is None:
            return "상품 목록 영역 안(첫 카드 전)"
        return f"상품 목록 영역 안(카드 밖), 직전 카드 godNo={self._prev_god_no!r}"

    def _raise_if_unclosed(self) -> None:
        """컨테이너가 없거나 닫히지 않으면 빈 목록으로 끝내지 않는다."""
        if not self._container_seen:
            raise RuntimeError(
                "상품 목록 영역이 없습니다. 점검·차단 화면이거나 구조가 바뀌었을 수 있습니다."
            )
        if self._stack:
            raise RuntimeError(
                "상품 목록 컨테이너가 열렸지만 닫히지 않았습니다: "
                + self._location()
            )


def _normalize_text(value: str) -> str:
    """카드 텍스트의 개행과 연속 공백을 한 칸으로 합친다."""
    return re.sub(r"\s+", " ", value).strip()


def _sale_price(price_text: str) -> int | None:
    """가격 영역에서 판매가 숫자만 읽는다. 없거나 0 이하면 None."""
    match = SALE_PRICE_RE.search(_normalize_text(price_text))
    if not match:
        return None
    value = int(match.group(1).replace(",", ""))
    if value <= 0:
        return None
    return value


def _parse_cards(html: str) -> list[_GoodsCard]:
    """목록 HTML에서 상품 영역 카드만 꺼낸다.

    section#goods_container 가 한 번도 열리지 않으면 예외다.
    컨테이너가 있고 카드가 0개면 빈 리스트다.
    """
    parser = _GoodsListParser()
    parser.feed(html)
    parser.close()
    parser._raise_if_unclosed()
    return parser.cards


def _brand_slug(sale_url: str) -> str:
    """입력 URL 경로의 첫 세그먼트가 브랜드관 slug다."""
    parts = [part for part in urlparse(sale_url).path.split("/") if part]
    return parts[0] if parts else ""


def _page_url(sale_url: str, page: int) -> str:
    """쿼리의 currentPage를 덮어 항상 요청한 페이지 주소를 만든다."""
    parsed = urlparse(sale_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["currentPage"] = [str(page)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _detail_url(brand_slug: str, god_no: str) -> str | None:
    slug = brand_slug.strip()
    code = god_no.strip()
    if not slug or not code:
        return None
    return DETAIL_URL.format(brand_slug=slug, god_no=code)


def _products_from_card(
    card: _GoodsCard,
    brand_slug: str,
    crawled_at: str,
) -> list[MarketplaceProduct]:
    """카드 하나를 MarketplaceProduct로 변환한다. 빠진 카드는 건너뛴다."""
    name = _normalize_text(card.name)
    price = _sale_price(card.price_text)
    url = _detail_url(brand_slug, card.god_no)
    if not name or url is None or price is None:
        logger.debug(
            "필수 정보 누락 — 건너뜀: name=%r, url=%r, price=%r",
            name,
            url,
            price,
        )
        return []

    model_names = extract_model_names(name, strategy=MODEL_STRATEGY)
    if not model_names:
        logger.debug("모델코드 추출 실패 — 건너뜀: %r", name)
        return []

    # SSF 상품명은 모델코드가 이름 끝에 온다.
    if len(model_names) > 1:
        model_names = [model_names[-1]]

    return [
        MarketplaceProduct(
            site_name=SITE_NAME,
            product_name=name,
            model_name=model_name,
            price=price,
            url=url,
            crawled_at=crawled_at,
        )
        for model_name in model_names
    ]


def _fetch_html(url: str) -> str:
    """SSF SHOP 목록 HTML을 동기 HTTP GET으로 가져온다."""
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": ACCEPT_LANGUAGE,
        },
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SEC) as response:
            return response.read().decode("utf-8")
    except HTTPError as exc:
        # 403/429만 파이프라인 중단이다. 나머지 HTTP 오류는 그대로 올라간다.
        try:
            check_response(exc)
        finally:
            exc.close()
        raise


async def _fetch_html_async(url: str) -> str:
    """blocking urllib 요청을 async 크롤러에서 실행한다."""
    return await asyncio.to_thread(_fetch_html, url)


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

async def crawl_ssfshop() -> list[MarketplaceProduct]:
    """
    config.SSFSHOP_SALE_URL 의 브랜드관 상품을 1페이지부터 수집한다.

    section#goods_container 가 있고 그 안의 god-item 이 0개면 종료한다.
    그때 수집 수가 MIN_PRODUCTS 미만이면 RuntimeError 이며 일부 목록을 반환하지 않는다.
    컨테이너가 없거나, 두 번 열리거나, 컨테이너 안 section 이 있거나,
    짝 없는 section 끝·닫히지 않은 컨테이너·짝이 맞지 않는 태그는 예외로 올린다.
    중간 페이지 실패나 MAX_PAGES 도달 시 일부 목록을 반환하지 않고 예외를 전파한다.

    Returns:
        site_name='ssfshop' 인 MarketplaceProduct 리스트.
        URL이 비어 있으면 하한 검사 없이 즉시 [] 를 반환한다.
    """
    sale_url = config.SSFSHOP_SALE_URL
    if not sale_url.strip():
        logger.info("config.SSFSHOP_SALE_URL 미설정 — SSF SHOP 크롤링 건너뜀")
        return []

    brand_slug = _brand_slug(sale_url)
    if not brand_slug:
        logger.error("SSFSHOP_SALE_URL 경로에서 브랜드관 slug를 찾지 못했습니다: %s", sale_url)
        raise ValueError("SSFSHOP_SALE_URL 경로에 브랜드관 slug가 없습니다.")

    crawled_at = datetime.now().isoformat(timespec="seconds")
    products: list[MarketplaceProduct] = []
    seen_keys: set[tuple[str, str]] = set()
    logger.info("SSF SHOP 상품 크롤링 시작: url=%s", sale_url)

    for page in range(1, MAX_PAGES + 1):
        page_url = _page_url(sale_url, page)
        try:
            html = await _fetch_html_async(page_url)
        except Exception as exc:
            logger.error("SSF SHOP 페이지 요청 실패 (page=%s): %s", page, exc)
            raise

        try:
            cards = _parse_cards(html)
        except Exception as exc:
            logger.error("SSF SHOP 페이지 파싱 실패 (page=%s): %s", page, exc)
            raise
        if not cards:
            logger.info("SSF SHOP page=%s 목록 영역에 카드 0개 → 종료", page)
            break

        added = 0
        for card in cards:
            for product in _products_from_card(card, brand_slug, crawled_at):
                key = (product.url or "", product.model_name)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                products.append(product)
                added += 1

        logger.info(
            "SSF SHOP page=%s 수집 %s개 / 누적 %s개 (카드 %s개)",
            page,
            added,
            len(products),
            len(cards),
        )
        if page == MAX_PAGES:
            logger.error("SSF SHOP 페이지 상한 도달 (page=%s): %s", page, MAX_PAGES)
            raise RuntimeError(
                f"SSF SHOP 페이지 상한({MAX_PAGES})에 도달했지만 상품 영역이 비어 있지 않습니다."
            )
        await pause(PAGE_DELAY_MIN_SEC, PAGE_DELAY_MAX_SEC, PAGE_DELAY_REASON)

    if len(products) < MIN_PRODUCTS:
        raise RuntimeError(
            f"SSF SHOP 수집 {len(products)}개가 하한 {MIN_PRODUCTS}개보다 적습니다 "
            f"(종료 페이지 {page}). "
            "점검·차단 화면이거나 상품이 크게 줄었을 수 있음, "
            "실제 감소면 MIN_PRODUCTS 를 내린다."
        )

    logger.info("SSF SHOP 크롤링 완료 — 총 %s개", len(products))
    return products
