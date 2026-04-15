"""
nike/crawler.py
나이키 코리아(nike.com/kr) 세일 신발 페이지 크롤러.

playwright-stealth 를 적용한 일반 브라우저(create_browser)로 접근한다.
아디다스와 달리 Chrome CDP 가 필요 없으며, common/browser.py 의
create_browser + new_stealth_page 조합만으로 동작한다.

상품 로딩 방식: 무한스크롤 (페이지네이션 없음)
Kids 카테고리: 성인 전용 URL(config.NIKE_SALE_URL) 을 사용해 1차 제외 +
              카드 텍스트 키워드("Kids", "어린이") 로 2차 제외
"""
import asyncio
import json
import re
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from playwright.async_api import Page

import config
from common.browser import create_browser, new_stealth_page
from common.logger import get_logger
from common.models import NaverProduct

logger = get_logger("nike.crawler")

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
SITE_NAME               = "nike"
PAGE_LOAD_TIMEOUT_MS    = 60_000
SELECTOR_TIMEOUT_MS     = 15_000
PAGE_SETTLE_SEC         = 3.0
SCROLL_PAUSE_MS         = 2_000
NETWORK_IDLE_TIMEOUT_MS = 3_000
MAX_SCROLL_ATTEMPTS     = 40
NO_CHANGE_LIMIT         = 3       # N회 연속 카드 수 변화 없으면 스크롤 종료
DIAG_BODY_TEXT_LEN      = 500     # 셀렉터 실패 시 진단용 본문 출력 길이

# 상품 카드 셀렉터 후보 (앞에서부터 시도) - RESEARCH.md 의 셀렉터 안정성 권고 반영
CARD_SELECTORS: list[str] = [
    '.product-card',
    'div[class*="product-card"]',
    '[data-testid*="product-card"]',
]

TITLE_SELECTOR    = '.product-card__title'
SUBTITLE_SELECTOR = '.product-card__subtitle'
PRICE_SELECTOR    = '.product-price'
LINK_SELECTOR     = '.product-card__link-overlay'

# 나이키 스타일코드 패턴 (예: CN8490-002, FQ8143-100)
STYLE_CODE_RE = re.compile(r'\b[A-Z]{2}\d{4}-\d{3}\b')

# Kids/비신발 제외 키워드 (카드 innerText 기준, 대소문자 구분 없음)
EXCLUDE_KEYWORDS: tuple[str, ...] = ("kids", "어린이", "유아", "주니어")

# ---------------------------------------------------------------------------
# JS 추출 함수 (page.evaluate 에 전달, 셀렉터 상수를 인자로 받음)
# ---------------------------------------------------------------------------
_JS_EXTRACT = """(args) => {
    const { cardSelector, titleSel, subtitleSel, priceSel, linkSel } = args;
    const cards = Array.from(document.querySelectorAll(cardSelector));
    return cards.map(card => {
        const titleEl    = card.querySelector(titleSel);
        const subtitleEl = card.querySelector(subtitleSel);
        const priceEl    = card.querySelector(priceSel);
        const linkEl     = card.querySelector(linkSel) || card.querySelector('a[href]');

        return {
            href:     linkEl ? linkEl.href : null,
            name:     titleEl ? titleEl.innerText.trim() : null,
            subtitle: subtitleEl ? subtitleEl.innerText.trim() : null,
            priceStr: priceEl ? priceEl.innerText.trim() : null,
            cardText: (card.innerText || '').trim(),
        };
    });
}"""


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

async def _find_card_selector(page: Page) -> str | None:
    """
    CARD_SELECTORS 순서로 시도하여 실제 DOM에 존재하는 셀렉터를 반환한다.
    각 셀렉터는 wait_for_selector 로 렌더링 완료까지 대기한다.
    모든 셀렉터 실패 시 진단 정보를 WARNING 로그에 출력한다.
    """
    for sel in CARD_SELECTORS:
        try:
            await page.wait_for_selector(sel, timeout=SELECTOR_TIMEOUT_MS)
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
        body_text = await page.evaluate(
            f"() => document.body?.innerText?.slice(0, {DIAG_BODY_TEXT_LEN}) || ''"
        )
        logger.warning(
            f"셀렉터 전부 실패 — 현재 URL: {current_url} | 제목: {title!r} | "
            f"본문 앞 {DIAG_BODY_TEXT_LEN}자: {body_text!r}"
        )
    except Exception as diag_exc:
        logger.warning(f"셀렉터 실패 진단 중 오류: {diag_exc}")

    return None


async def _scroll_to_bottom(page: Page, card_selector: str) -> int:
    """
    페이지를 끝까지 스크롤하며 새 상품이 더 이상 로드되지 않으면 중단한다.
    naver/crawler.py 의 _scroll_to_bottom 패턴을 따른다.

    Returns:
        마지막으로 확인된 상품 카드 수
    """
    prev_count = 0
    no_change_streak = 0

    for attempt in range(MAX_SCROLL_ATTEMPTS):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(SCROLL_PAUSE_MS)
        try:
            await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)
        except Exception:
            pass

        current_count = await page.locator(card_selector).count()
        logger.debug(f"스크롤 {attempt + 1}회 — 카드 수: {current_count}")

        if current_count == prev_count:
            no_change_streak += 1
            if no_change_streak >= NO_CHANGE_LIMIT:
                logger.debug(f"{NO_CHANGE_LIMIT}회 연속 변화 없음 → 스크롤 종료")
                break
        else:
            no_change_streak = 0

        prev_count = current_count

    logger.info(f"스크롤 완료 — 최종 카드 수: {prev_count}개")
    return prev_count


def _parse_price(price_str: str | None) -> int | None:
    """
    가격 문자열에서 숫자만 추출하여 int로 반환한다.
    실패 시 None을 반환한다.

    예: '159,000원' → 159000, '169,000' → 169000, None → None
    """
    if not price_str:
        return None
    match = re.search(r'([\d,]+)', price_str)
    if not match:
        logger.debug(f"가격 파싱 실패: {price_str!r}")
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        logger.debug(f"가격 변환 실패: {price_str!r}")
        return None


def _extract_model_name(card_text: str, href: str | None) -> str | None:
    """
    상품 카드 텍스트와 URL에서 모델명(Kream 검색 키)을 추출한다.

    우선순위:
    1. card_text 에서 스타일코드 패턴 매칭 (예: CN8490-002)
    2. href 마지막 path 세그먼트에서 스타일코드 패턴 매칭
    3. card_text 의 첫 줄을 fallback으로 사용

    Returns:
        모델명 문자열, 추출 불가 시 None
    """
    # 1순위: card_text 에서 스타일코드 검색
    match = STYLE_CODE_RE.search(card_text)
    if match:
        return match.group()

    # 2순위: href 마지막 path 세그먼트에서 스타일코드 검색
    if href:
        try:
            segment = href.rstrip('/').split('/')[-1]
            code_match = STYLE_CODE_RE.match(segment)
            if code_match:
                return code_match.group()
        except Exception:
            pass

    # 3순위: card_text 첫 줄 fallback
    if card_text:
        first_line = card_text.splitlines()[0].strip()
        if first_line:
            return first_line

    return None


def _should_exclude(card_text: str, subtitle: str | None) -> bool:
    """
    Kids/비신발 키워드가 포함된 카드인지 판단한다.
    성인 전용 URL(1차 제외) 이후 추가적인 2차 키워드 필터링.

    Returns:
        True면 제외 대상
    """
    combined = (card_text + " " + (subtitle or "")).lower()
    return any(keyword in combined for keyword in EXCLUDE_KEYWORDS)


async def _extract_all_products(page: Page, card_selector: str) -> list[NaverProduct]:
    """
    현재 페이지의 모든 상품 카드를 JS로 일괄 파싱하여 NaverProduct 목록을 반환한다.
    Kids 필터링, 필수 정보 누락 검사, 모델명 중복 제거를 수행한다.
    """
    crawled_at = datetime.now().isoformat(timespec="seconds")

    cards_data: list[dict] = await page.evaluate(_JS_EXTRACT, {
        "cardSelector": card_selector,
        "titleSel":     TITLE_SELECTOR,
        "subtitleSel":  SUBTITLE_SELECTOR,
        "priceSel":     PRICE_SELECTOR,
        "linkSel":      LINK_SELECTOR,
    })

    products: list[NaverProduct] = []
    seen: set[str] = set()

    for card in cards_data:
        card_text: str = card.get("cardText") or ""
        subtitle: str | None = card.get("subtitle")
        name: str | None = card.get("name")
        price_str: str | None = card.get("priceStr")
        href: str | None = card.get("href")

        # Kids/비신발 키워드 제외
        if _should_exclude(card_text, subtitle):
            logger.debug(f"Kids/비신발 키워드 제외: {name!r}")
            continue

        # 필수 정보 누락 검사
        if not name or not price_str or not href:
            logger.debug(f"필수 정보 누락 — 건너뜀: name={name!r}, priceStr={price_str!r}, href={href!r}")
            continue

        # 가격 파싱
        price = _parse_price(price_str)
        if price is None:
            logger.debug(f"가격 파싱 불가 — 건너뜀: {price_str!r}")
            continue

        # 모델명 추출
        mn = _extract_model_name(card_text, href)
        if mn is None:
            logger.debug(f"모델명 추출 불가 — 건너뜀: {name!r}")
            continue

        # 모델명 중복 방지
        if mn in seen:
            logger.debug(f"중복 모델명 — 건너뜀: {mn!r}")
            continue
        seen.add(mn)

        products.append(NaverProduct(
            site_name=SITE_NAME,
            product_name=name,
            model_name=mn,
            price=price,
            url=href,
            crawled_at=crawled_at,
        ))
        logger.info(f"수집: {name!r} | 모델={mn} | 가격={price:,}원")

    return products


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

async def crawl_nike() -> list[NaverProduct]:
    """
    나이키 코리아 세일 신발 페이지를 크롤링하여 NaverProduct 목록을 반환한다.

    create_browser + new_stealth_page 조합으로 봇 탐지를 우회한다.
    무한스크롤로 전체 상품을 로드 후 JS로 일괄 파싱한다.

    Returns:
        site_name='nike' 인 NaverProduct 리스트. 봇 차단 또는 빈 결과 시 [] 반환.
    """
    logger.info(f"나이키 세일 페이지 이동 중: {config.NIKE_SALE_URL}")

    async with create_browser(headless=True) as context:
        page = await new_stealth_page(context)
        try:
            # 페이지 이동
            try:
                await page.goto(
                    config.NIKE_SALE_URL,
                    wait_until="domcontentloaded",
                    timeout=PAGE_LOAD_TIMEOUT_MS,
                )
            except Exception as exc:
                logger.warning(f"goto 에러(계속 진행): {type(exc).__name__}: {exc}")

            # 최종 URL/제목 확인 (redirect 및 차단 감지)
            final_url = page.url
            page_title = await page.title()
            logger.info(f"페이지 로드 완료 — URL: {final_url} | 제목: {page_title!r}")

            if "clearance-shoes" not in final_url:
                logger.warning(
                    f"세일 URL에서 다른 페이지로 이동됨 — 리다이렉트 또는 차단 가능성. "
                    f"현재 URL: {final_url}"
                )

            # 초기 렌더링 대기
            await asyncio.sleep(PAGE_SETTLE_SEC)

            # 상품 카드 셀렉터 자동 탐지
            card_selector = await _find_card_selector(page)
            if not card_selector:
                logger.error("상품 카드 셀렉터를 찾을 수 없습니다. DOM 구조를 확인하세요.")
                return []

            # 무한스크롤로 전체 상품 로드
            await _scroll_to_bottom(page, card_selector)

            # 상품 파싱
            products = await _extract_all_products(page, card_selector)

            logger.info(f"나이키 크롤링 완료 — 총 {len(products)}개")
            return products

        finally:
            await page.close()


# ---------------------------------------------------------------------------
# 단독 실행 진입점
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    async def _main():
        products = await crawl_nike()
        if not products:
            print("수집된 상품이 없습니다.")
            return

        today = date.today().strftime("%Y%m%d")
        out_dir = Path(config.OUTPUT_DIR) / today
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "nike_products.json"
        out_path.write_text(
            json.dumps([asdict(p) for p in products], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n총 {len(products)}개 저장 → {out_path}")

    asyncio.run(_main())
