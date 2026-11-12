"""
adidas/crawler.py
아디다스 공홈 세일 목록 크롤러.

확장 없이 독립 Chrome 창에서 동작한다.
사전 준비: ego lite 설치.
"""
import asyncio
import logging
import random
import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

import config
from common.atomic_io import write_json_atomic
from common.browser import create_browser
from common.models import MarketplaceProduct
from common.pacing import CrawlBlockedError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
SITE_NAME             = "adidas"
PAGE_LOAD_TIMEOUT     = 60_000
SELECTOR_TIMEOUT      = 15_000

# 상품 카드 셀렉터 후보 (앞에서부터 시도)
# article[class*="product"] 를 우선 — product-card 는 너무 광범위하게 매칭됨
CARD_SELECTORS = [
    'article[class*="product"]',
    '[data-auto-id="glass-product-card"]',
    '[class*="product-card"]',
    '[class*="plp-products"] li',
]

# 다음 페이지 버튼 셀렉터 (data-testid 기준)
NEXT_BTN_SELECTOR = '[data-testid="pagination-next-button"]'

# 페이지 로드 후 카드 렌더링 대기 시간 (초)
PAGE_SETTLE_SEC = 3.0
MAX_RATE_LIMIT_RETRIES = 2
MAX_RETRY_WAIT_SEC = 60

# ---------------------------------------------------------------------------
# JS 추출 함수 (page.evaluate 에 전달)
# ---------------------------------------------------------------------------
_JS_EXTRACT = """(cardSelector) => {
    const cards = Array.from(document.querySelectorAll(cardSelector));
    return cards.map(card => {
        // 상품 링크 & 코드 (URL 끝 .html 앞 세그먼트)
        const anchor = card.querySelector('a[href*=".html"]') || card.querySelector('a[href]');
        const href   = anchor ? anchor.href : null;
        const code   = href
            ? href.split('/').pop().replace(/\\.html.*$/, '').toUpperCase()
            : null;

        // 상품명 — URL 경로에서 추출 (innerText 파싱보다 신뢰도 높음)
        // 예: /케이타키-알파-슬라이드/JR1153.html → '케이타키 알파 슬라이드'
        let name = null;
        if (href) {
            try {
                const parts = new URL(href).pathname.split('/').filter(Boolean);
                if (parts.length >= 2) {
                    name = decodeURIComponent(parts[parts.length - 2])
                        .replace(/-/g, ' ')
                        .trim();
                }
            } catch (e) {}
        }

        // Kids 카테고리 여부 ('Kids'는 영문 그대로 라벨에 노출됨)
        const isKids = /Kids/.test(card.innerText || '');

        // 세일가 — 텍스트에서 첫 번째 "숫자,숫자 원" 패턴
        const priceMatch = (card.innerText || '').match(/([\\d,]+)\\s*원/);
        const salePriceStr = priceMatch ? priceMatch[1] : null;

        return { href, code, name, isKids, salePriceStr };
    });
}"""


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _raise_http_error(status: int, url: str, retry_after: str | None = None) -> None:
    if status < 400:
        return
    advice = {
        400: "요청 URL과 쿼리를 확인하세요.",
        401: "사이트의 로그인 요구를 확인하세요.",
        403: "접근이 거부되었습니다. 일반 Chrome 접속 결과와 비교하세요.",
        404: "현재 세일 메뉴에서 URL을 확인하세요.",
        410: "종료된 페이지입니다. 현재 세일 메뉴에서 URL을 확인하세요.",
        429: f"요청 제한입니다. Retry-After={retry_after!r}; 제한 해제 후 실행하세요.",
    }.get(status, "서버 응답을 확인하세요.")
    error_type = CrawlBlockedError if status in (403, 429) else RuntimeError
    raise error_type(f"아디다스 HTTP {status}: {url} — {advice}")


def _retry_delay(retry_after: str | None, attempt: int) -> float:
    """Retry-After의 초/HTTP-date 지원. 누락·잘못된 값은 지수 대기."""
    if retry_after:
        try:
            return max(0, int(retry_after))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    return 5 * 2 ** attempt


class _ResponseDiagnostics:
    """문서와 XHR/fetch 오류 기록. 목록에 필요한 응답만 중단에 반영."""

    def __init__(self, page):
        self.page = page
        self.errors = []
        self.fatal_response = None

    def on_response(self, response):
        kind = response.request.resource_type
        if kind not in ("document", "xhr", "fetch"):
            return
        if kind == "document":
            logger.info("문서 응답: HTTP %s %s", response.status, response.url)
        if response.status < 400:
            return
        logger.warning("HTTP 오류: %s %s %s", response.status, kind, response.url)
        self.errors.append(response)
        self.errors = self.errors[-30:]
        parsed = urlparse(response.url)
        host = parsed.hostname or ""
        # 개별 상품 보조 API가 403이어도 목록 HTML의 코드·가격은 정상일 수 있다.
        # 실제 관측 경로만 제외하고 목록 API의 실패는 계속 중단한다.
        product_detail = re.fullmatch(r"/plp-app/api/product/[^/]+/?", parsed.path) is not None
        product_api = (
            (host == "adidas.co.kr" or host.endswith(".adidas.co.kr"))
            and "/api/" in parsed.path
            and any(part in parsed.path.lower() for part in ("product", "plp", "search"))
            and not product_detail
        )
        main_document = kind == "document" and response.request.frame == self.page.main_frame
        if main_document or (kind in ("xhr", "fetch") and product_api):
            self.fatal_response = response

    def check(self):
        if self.fatal_response is not None:
            response = self.fatal_response
            _raise_http_error(response.status, response.url, response.headers.get("retry-after"))

    async def save(self, error):
        """진단 저장 실패가 원래 오류를 덮지 않게 하며 쿠키·인증 헤더는 저장하지 않는다."""
        try:
            directory = Path(config.OUTPUT_DIR) / date.today().strftime("%Y%m%d") / "diagnostics"
            directory.mkdir(parents=True, exist_ok=True)
            prefix = directory / f"adidas_{datetime.now():%H%M%S_%f}"
            data = {"error": str(error), "url": self.page.url, "responses": []}
            for response in self.errors:
                item = {"status": response.status, "url": response.url,
                        "resource_type": response.request.resource_type,
                        "retry_after": response.headers.get("retry-after")}
                try:
                    item["body_excerpt"] = (await asyncio.wait_for(response.text(), 2))[:2000]
                except Exception:
                    item["body_excerpt"] = "응답 본문을 읽을 수 없습니다."
                data["responses"].append(item)
            try:
                data["title"] = await self.page.title()
                data["body_excerpt"] = await self.page.evaluate("() => document.body?.innerText?.slice(0, 2000) || ''")
                await self.page.screenshot(path=str(prefix.with_suffix(".png")), timeout=5000)
            except Exception as exc:
                data["capture_error"] = str(exc)
            write_json_atomic(prefix.with_suffix(".json"), data)
            logger.error("아디다스 실패 진단 저장: %s.json", prefix)
        except Exception as exc:
            logger.warning("아디다스 진단 저장 실패: %s", exc)


async def _navigate(page, sale_url: str, diagnostics: _ResponseDiagnostics) -> None:
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        diagnostics.fatal_response = None
        response = await page.goto(sale_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
        if response is None:
            raise RuntimeError("아디다스 문서 응답이 없습니다.")
        retry_after = response.headers.get("retry-after")
        if response.status == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
            delay = _retry_delay(retry_after, attempt)
            if delay <= MAX_RETRY_WAIT_SEC:
                logger.warning("HTTP 429 — %.1f초 후 재시도 (%s/%s)", delay, attempt + 1, MAX_RATE_LIMIT_RETRIES)
                await asyncio.sleep(delay)
                continue
        _raise_http_error(response.status, response.url, retry_after)
        diagnostics.check()
        return


async def _find_card_selector(page, diagnostics: _ResponseDiagnostics) -> str:
    for _ in range(SELECTOR_TIMEOUT // 1000):
        diagnostics.check()
        try:
            await page.wait_for_selector(
                ", ".join(CARD_SELECTORS), state="attached", timeout=1000,
            )
        except PlaywrightTimeoutError:
            continue
        diagnostics.check()
        for selector in CARD_SELECTORS:
            if await page.locator(selector).count():
                return selector
    diagnostics.check()
    raise RuntimeError("아디다스 상품 카드를 찾지 못했습니다. 상품 0개로 처리하지 않습니다.")



async def _extract_products(page, card_selector: str, seen_codes: set[str]) -> list[MarketplaceProduct]:
    """
    현재 페이지의 상품 카드를 파싱하여 MarketplaceProduct 목록을 반환한다.
    seen_codes 에 이미 있는 상품코드는 중복으로 간주해 건너뜀.
    """
    crawled_at = datetime.now().isoformat(timespec="seconds")
    cards_data: list[dict] = await page.evaluate(_JS_EXTRACT, card_selector)

    products: list[MarketplaceProduct] = []
    for card in cards_data:
        if card["isKids"]:
            logger.debug("Kids 상품 제외: %r", card['name'])
            continue

        if not card["code"] or not card["name"] or not card["salePriceStr"]:
            logger.debug("필수 정보 누락 — 건너뜀: %s", card)
            continue

        if card["code"] in seen_codes:
            logger.debug("중복 상품코드 — 건너뜀: %s", card['code'])
            continue

        try:
            price = int(card["salePriceStr"].replace(",", ""))
        except ValueError:
            logger.warning("가격 파싱 실패: %r", card['salePriceStr'])
            continue

        seen_codes.add(card["code"])
        products.append(MarketplaceProduct(
            site_name=SITE_NAME,
            product_name=card["name"],
            model_name=card["code"],
            price=price,
            url=card["href"],
            crawled_at=crawled_at,
        ))
        logger.info(
            "수집: %r | 코드=%s | 가격=%s원", card['name'], card['code'], format(price, ",")
        )

    return products


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

async def crawl_adidas() -> list[MarketplaceProduct]:
    """세일 목록을 수집한다. 미설정은 [], 접근·수집 실패는 예외로 구분한다."""
    sale_url = config.ADIDAS_SALE_URL.strip()
    if not sale_url:
        logger.info("config.ADIDAS_SALE_URL 미설정 — 아디다스 크롤링 건너뜀")
        return []

    expected_path = urlparse(sale_url).path.rstrip("/")
    all_products: list[MarketplaceProduct] = []
    async with create_browser() as context:
        page = await context.new_page()
        diagnostics = _ResponseDiagnostics(page)
        page.on("response", diagnostics.on_response)
        try:
            logger.info("아디다스 세일 페이지 이동: %s", sale_url)
            await _navigate(page, sale_url, diagnostics)
            logger.info("페이지 로드 완료 — URL: %s | 제목: %r", page.url, await page.title())
            if urlparse(page.url).path.rstrip("/") != expected_path:
                raise RuntimeError(f"아디다스 세일 URL이 다른 페이지로 이동했습니다: {page.url}")

            card_selector = await _find_card_selector(page, diagnostics)
            logger.info("카드 셀렉터 사용: %r", card_selector)
            seen_codes: set[str] = set()
            seen_pages = set()
            page_num = 1
            while True:
                await asyncio.sleep(PAGE_SETTLE_SEC)
                diagnostics.check()
                # Kids만 있는 페이지도 구분할 수 있도록 필터 전 링크로 진행을 확인한다.
                fingerprint = tuple(await page.locator(card_selector).evaluate_all(
                    "cards => cards.map(c => c.querySelector('a[href*=\".html\"]')?.href || '')"
                ))
                if not fingerprint or not any(fingerprint) or fingerprint in seen_pages:
                    raise RuntimeError("아디다스 페이지가 비었거나 다음 페이지로 진행되지 않았습니다.")
                seen_pages.add(fingerprint)
                products = await _extract_products(page, card_selector, seen_codes)
                all_products.extend(products)
                logger.info("페이지 %s: %s개 수집 (누적: %s개)", page_num, len(products), len(all_products))

                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1.0)
                diagnostics.check()
                next_btn = page.locator(NEXT_BTN_SELECTOR).first
                if not await next_btn.is_visible() or await next_btn.is_disabled():
                    logger.info("마지막 페이지 — 크롤링 완료")
                    break
                await next_btn.click()
                await asyncio.sleep(random.uniform(8.0, 12.0))
                diagnostics.check()
                card_selector = await _find_card_selector(page, diagnostics)
                page_num += 1
            diagnostics.check()
        except Exception as exc:
            await diagnostics.save(exc)
            raise
        finally:
            page.remove_listener("response", diagnostics.on_response)
            if not page.is_closed():
                await page.close()

    logger.info("아디다스 크롤링 완료 — 총 %s개", len(all_products))
    return all_products
