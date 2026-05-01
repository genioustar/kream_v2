"""
adidas/crawler.py
아디다스 공홈 Extra Sale 신발 페이지 크롤러.

Akamai Bot Manager 우회를 위해 실제 크롬에 CDP로 연결한다.
Playwright가 직접 띄운 브라우저는 Akamai fingerprint 탐지에 걸리지만,
실제 크롬은 쿠키·fingerprint가 정상이므로 차단되지 않는다.

사전 준비:
  크롬을 --remote-debugging-port=9222 옵션으로 실행해야 한다.
  macOS: open -a "Google Chrome" --args --remote-debugging-port=9222
"""
import asyncio
import json
import random
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

import config
from common.logger import get_logger
from common.models import NaverProduct

logger = get_logger("adidas.crawler")

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
SITE_NAME             = "adidas"
PAGE_LOAD_TIMEOUT     = 60_000
SELECTOR_TIMEOUT      = 15_000
CDP_URL               = "http://127.0.0.1:9222"

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

async def _find_card_selector(page) -> str | None:
    """
    CARD_SELECTORS 순서로 시도하여 실제 DOM에 존재하는 셀렉터를 반환한다.
    각 셀렉터는 wait_for_selector 로 렌더링 완료까지 대기한다.
    모든 셀렉터 실패 시 진단 정보를 WARNING 로그에 출력한다.
    """
    for sel in CARD_SELECTORS:
        try:
            await page.wait_for_selector(sel, timeout=SELECTOR_TIMEOUT)
            count = await page.locator(sel).count()
            if count > 0:
                logger.debug(f"카드 셀렉터 탐지: {sel!r} ({count}개)")
                return sel
        except Exception as exc:
            logger.debug(f"셀렉터 실패 {sel!r}: {type(exc).__name__}")
            continue

    # 모든 셀렉터 실패 — 페이지 상태 진단
    try:
        current_url = page.url
        title = await page.title()
        body_text = await page.evaluate("() => document.body?.innerText?.slice(0, 500) || ''")
        logger.warning(
            f"셀렉터 전부 실패 — 현재 URL: {current_url} | 제목: {title!r} | "
            f"본문 앞 500자: {body_text!r}"
        )
    except Exception as diag_exc:
        logger.warning(f"셀렉터 실패 진단 중 오류: {diag_exc}")

    return None



async def _extract_products(page, card_selector: str, seen_codes: set[str]) -> list[NaverProduct]:
    """
    현재 페이지의 상품 카드를 파싱하여 NaverProduct 목록을 반환한다.
    seen_codes 에 이미 있는 상품코드는 중복으로 간주해 건너뜀.
    """
    crawled_at = datetime.now().isoformat(timespec="seconds")
    cards_data: list[dict] = await page.evaluate(_JS_EXTRACT, card_selector)

    products: list[NaverProduct] = []
    for card in cards_data:
        if card["isKids"]:
            logger.debug(f"Kids 상품 제외: {card['name']!r}")
            continue

        if not card["code"] or not card["name"] or not card["salePriceStr"]:
            logger.debug(f"필수 정보 누락 — 건너뜀: {card}")
            continue

        if card["code"] in seen_codes:
            logger.debug(f"중복 상품코드 — 건너뜀: {card['code']}")
            continue

        try:
            price = int(card["salePriceStr"].replace(",", ""))
        except ValueError:
            logger.warning(f"가격 파싱 실패: {card['salePriceStr']!r}")
            continue

        seen_codes.add(card["code"])
        products.append(NaverProduct(
            site_name=SITE_NAME,
            product_name=card["name"],
            model_name=card["code"],
            price=price,
            url=card["href"],
            crawled_at=crawled_at,
        ))
        logger.info(
            f"수집: {card['name']!r} | 코드={card['code']} | 가격={price:,}원"
        )

    return products


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

async def crawl_adidas() -> list[NaverProduct]:
    """
    실제 크롬(CDP)에 연결하여 아디다스 Extra Sale 신발 페이지를 크롤링한다.

    Akamai Bot Manager 우회를 위해 CDP 연결 방식을 사용한다.
    실행 전 크롬을 --remote-debugging-port=9222 로 먼저 실행해야 한다.

    Returns:
        NaverProduct 포맷으로 수집된 아디다스 상품 목록.
        config.ADIDAS_SALE_URL 미설정/빈 값이면 즉시 [] 반환 (크롤링 스킵).
    """
    sale_url = getattr(config, "ADIDAS_SALE_URL", "") or ""
    if not sale_url.strip():
        logger.info("config.ADIDAS_SALE_URL 미설정 — 아디다스 크롤링 건너뜀")
        return []

    # redirect 감지용 키워드: URL path 의 마지막 segment
    # 예) /extra_sale-shoes?sort=... → "extra_sale-shoes"
    expected_segment = urlparse(sale_url).path.rstrip("/").rsplit("/", 1)[-1]

    all_products: list[NaverProduct] = []

    pw = await async_playwright().start()
    try:
        # 실제 크롬에 CDP 연결
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
            logger.info(f"실제 크롬 CDP 연결 성공 ({CDP_URL})")
        except Exception as exc:
            logger.error(
                f"크롬 CDP 연결 실패: {exc}\n"
                f"크롬을 디버깅 포트로 실행해주세요:\n"
                f"  macOS: open -a \"Google Chrome\" --args --remote-debugging-port=9222\n"
                f"  이미 크롬이 열려있으면 완전히 종료 후 위 명령 실행"
            )
            return []

        # 기존 컨텍스트의 새 탭 사용 (실제 크롬 쿠키·fingerprint 유지)
        context = browser.contexts[0]
        page = await context.new_page()

        logger.info(f"아디다스 Extra Sale 페이지 이동: {sale_url}")
        try:
            await page.goto(
                sale_url,
                wait_until="domcontentloaded",
                timeout=PAGE_LOAD_TIMEOUT,
            )
        except Exception as exc:
            logger.warning(f"goto 에러(계속 진행): {type(exc).__name__}: {exc}")

        # 페이지 최종 도착 상태 확인 (redirect 감지)
        final_url = page.url
        page_title = await page.title()
        logger.info(f"페이지 로드 완료 — URL: {final_url} | 제목: {page_title!r}")
        if expected_segment and expected_segment not in final_url:
            logger.warning(
                f"Extra Sale URL에서 다른 페이지로 이동됨 — 세일 종료 또는 차단 가능성 있음. "
                f"현재 URL: {final_url}"
            )

        # 상품 카드 셀렉터 자동 탐지 (wait_for_selector 내장)
        card_selector = await _find_card_selector(page)
        if not card_selector:
            logger.error("상품 카드 셀렉터를 찾을 수 없습니다. DOM 구조를 확인하세요.")
            await page.close()
            return []
        logger.info(f"카드 셀렉터 사용: {card_selector!r}")

        seen_codes: set[str] = set()
        page_num = 1
        while True:
            logger.info(f"페이지 {page_num} 수집 중 (url={page.url})")

            # 카드 렌더링 대기
            await asyncio.sleep(PAGE_SETTLE_SEC)

            products = await _extract_products(page, card_selector, seen_codes)
            all_products.extend(products)
            logger.info(f"페이지 {page_num}: {len(products)}개 수집 (누적: {len(all_products)}개)")

            # 다음 페이지 버튼 탐색 (버튼이 viewport에 들어오도록 스크롤 후 확인)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1.0)

            next_btn = page.locator(NEXT_BTN_SELECTOR).first
            try:
                is_visible = await next_btn.is_visible(timeout=3000)
            except Exception:
                is_visible = False

            if not is_visible:
                logger.info("마지막 페이지 — 크롤링 완료")
                break

            await next_btn.click()
            await asyncio.sleep(random.uniform(2.0, 3.0))
            page_num += 1

        await page.close()

    finally:
        await pw.stop()

    logger.info(f"아디다스 크롤링 완료 — 총 {len(all_products)}개")
    return all_products


# ---------------------------------------------------------------------------
# 단독 실행 진입점
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    async def _main():
        products = await crawl_adidas()
        if not products:
            print("수집된 상품이 없습니다.")
            return

        today = date.today().strftime("%Y%m%d")
        out_dir = Path(config.OUTPUT_DIR) / today
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "adidas_products.json"
        out_path.write_text(
            json.dumps([asdict(p) for p in products], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n총 {len(products)}개 저장 → {out_path}")

    asyncio.run(_main())
