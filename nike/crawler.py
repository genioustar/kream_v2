"""
nike/crawler.py
나이키 코리아(nike.com/kr) 세일 신발 페이지 크롤러.

Kasada 봇 탐지 우회를 위해 실제 Chrome에 CDP로 연결한다.
Playwright 자체 Chromium은 Kasada fingerprint 탐지에 걸려 무한스크롤 JS가 비활성화된다.

사전 준비:
  make chrome 으로 Chrome CDP를 먼저 실행해야 한다.

상품 수집 방식:
  스크롤로 Nike JS가 product_wall API를 호출하게 유도 → 응답 인터셉트 → JSON 파싱
Kids 카테고리: 성인 전용 URL(config.NIKE_SALE_URL) 로 1차 제외 +
              title/subTitle 키워드("Kids", "어린이") 로 2차 제외
"""
import asyncio
import json
import re
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Page, async_playwright

import config
from common.logger import get_logger
from common.models import NaverProduct

logger = get_logger("nike.crawler")

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
CDP_URL                 = "http://127.0.0.1:9222"  # make chrome으로 먼저 실행
SITE_NAME               = "nike"
PAGE_LOAD_TIMEOUT_MS    = 60_000
PAGE_SETTLE_SEC         = 3.0
SCROLL_STEP_PX          = 200     # 한 번에 스크롤할 픽셀 (천천히 내리기)
SCROLL_STEP_DELAY_MS    = 1_200   # 스크롤 후 새 상품 로드 대기
NETWORK_IDLE_TIMEOUT_MS = 3_000
MAX_SCROLL_ATTEMPTS     = 120     # 400px × 120 = 48,000px 최대 스크롤
NO_CHANGE_LIMIT         = 6       # N회 연속 새 상품 없으면 스크롤 종료
TARGET_PRODUCT_COUNT    = 100     # 목표 수집 상품 수

# 나이키 스타일코드 패턴 (예: CN8490-002, FQ8143-100)
STYLE_CODE_RE = re.compile(r'\b[A-Z]{2}\d{4}-\d{3}\b')

# Kids/비신발 제외 키워드 (카드 innerText 기준, 대소문자 구분 없음)
EXCLUDE_KEYWORDS: tuple[str, ...] = ("kids", "어린이", "유아", "주니어")

# Nike product_wall API (응답 인터셉트로 상품 데이터 수집)
API_BASE_URL = (
    "https://api.nike.com/discover/product_wall/v1"
    "/marketplace/KR/language/ko"
    "/consumerChannelId/d9a5bc42-4b9c-4976-858a-f159cf99c647"
)


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

async def _collect_via_api(
    page: Page,
    target_count: int = TARGET_PRODUCT_COUNT,
) -> list[NaverProduct]:
    """
    Nike product_wall API 응답을 인터셉트하며 스크롤로 상품을 수집한다.

    page.evaluate(fetch(...)) 직접 호출은 Kasada가 Nike JS에 자동 삽입하는
    보안 토큰 헤더를 포함하지 않아 API가 차단된다.
    대신 스크롤로 Nike 자체 JS가 API를 호출하게 하고 응답을 인터셉트한다.

    종료 조건:
    - 목표 수집 수 달성
    - scrollY 변화 없음 NO_CHANGE_LIMIT회 연속 (페이지 끝)
    """
    seen: dict[str, NaverProduct] = {}
    crawled_at = datetime.now().isoformat(timespec="seconds")
    log_first = True

    async def on_api_response(response):
        nonlocal log_first
        if API_BASE_URL not in response.url:
            return
        try:
            data = await response.json()
            if log_first:
                logger.info(f"[DIAG] API 첫 응답 샘플: {json.dumps(data, ensure_ascii=False)[:400]}")
                log_first = False
            groupings = data.get("productGroupings") or []
            products_list = [p for g in groupings for p in (g.get("products") or [])]
            before = len(seen)
            for item in products_list:
                p = _parse_api_product(item, crawled_at)
                if p:
                    key = p.url or p.model_name
                    if key and key not in seen:
                        seen[key] = p
                        logger.info(f"수집: {p.product_name!r} | {p.model_name} | {p.price:,}원")
            logger.info(f"API 배치 {len(products_list)}개, 신규 {len(seen) - before}개, 누적 {len(seen)}개")
        except Exception as exc:
            logger.debug(f"API 응답 파싱 오류: {exc}")

    page.on("response", on_api_response)

    scroll_stall_streak = 0
    for attempt in range(MAX_SCROLL_ATTEMPTS):
        if len(seen) >= target_count:
            logger.info(f"목표 달성 ({len(seen)} >= {target_count}) → 종료")
            break

        prev_y: int = await page.evaluate("() => window.scrollY")
        await page.evaluate(f"window.scrollBy(0, {SCROLL_STEP_PX})")
        await page.wait_for_timeout(SCROLL_STEP_DELAY_MS)
        curr_y: int = await page.evaluate("() => window.scrollY")
        logger.info(f"스크롤 {attempt + 1}회 — scrollY: {prev_y}→{curr_y}, 누적: {len(seen)}개")

        if curr_y == prev_y:
            scroll_stall_streak += 1
            if scroll_stall_streak >= NO_CHANGE_LIMIT:
                logger.info(f"페이지 끝 도달 → 종료")
                break
        else:
            scroll_stall_streak = 0

    products = list(seen.values())
    logger.info(f"수집 완료 — 총 {len(products)}개")
    return products


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


def _parse_api_product(item: dict, crawled_at: str) -> NaverProduct | None:
    """
    Nike product_wall API 응답의 상품 항목을 NaverProduct로 변환한다.

    실제 API 필드 구조:
    - copy.title       → 상품명
    - copy.subTitle    → 카테고리 (subTitle, 대문자 T)
    - prices.currentPrice → 현재 가격 (float, int 변환)
    - pdpUrl.url       → 상품 페이지 절대 URL
    - productCode      → 스타일코드 (예: 553558-093)
    """
    try:
        copy = item.get("copy") or {}
        title = copy.get("title")
        subtitle = copy.get("subTitle")  # 실제 필드명: subTitle

        prices = item.get("prices") or {}
        price_val = prices.get("currentPrice")

        pdp_url = item.get("pdpUrl") or {}
        url = pdp_url.get("url")  # 이미 절대 URL

        if _should_exclude((title or "") + " " + (subtitle or ""), subtitle):
            logger.debug(f"Kids 필터 제외: {title!r}")
            return None

        if not title or price_val is None or not url:
            logger.debug(f"필수 정보 누락: title={title!r}, price={price_val}, url={url!r}")
            return None

        model_name = (
            item.get("productCode")  # 553558-093 형태
            or _extract_model_name((title or "") + " " + (subtitle or ""), url)
            or title
        )

        return NaverProduct(
            site_name=SITE_NAME,
            product_name=title,
            model_name=model_name,
            price=int(price_val),
            url=url,
            crawled_at=crawled_at,
        )
    except Exception as exc:
        logger.debug(f"_parse_api_product 오류: {exc}")
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


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

async def crawl_nike() -> list[NaverProduct]:
    """
    나이키 코리아 세일 신발 페이지를 크롤링하여 NaverProduct 목록을 반환한다.

    Kasada 우회를 위해 실제 Chrome CDP에 연결한다 (make chrome 선행 필요).
    스크롤로 product_wall API 호출을 유도하고 응답을 인터셉트해 파싱한다.

    Returns:
        site_name='nike' 인 NaverProduct 리스트. 연결 실패 또는 빈 결과 시 [] 반환.
        config.NIKE_SALE_URL 미설정/빈 값이면 즉시 [] 반환 (크롤링 스킵).
    """
    sale_url = getattr(config, "NIKE_SALE_URL", "") or ""
    if not sale_url.strip():
        logger.info("config.NIKE_SALE_URL 미설정 — 나이키 크롤링 건너뜀")
        return []

    # redirect 감지용 키워드: URL path 의 마지막 segment
    # 예) /kr/w/clearance-shoes-3yaepzy7ok → "clearance-shoes-3yaepzy7ok"
    expected_segment = urlparse(sale_url).path.rstrip("/").rsplit("/", 1)[-1]

    logger.info(f"나이키 세일 페이지 이동 중: {sale_url}")

    pw = await async_playwright().start()
    try:
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
            logger.info(f"실제 Chrome CDP 연결 성공 ({CDP_URL})")
        except Exception as exc:
            logger.error(
                f"Chrome CDP 연결 실패: {exc}\n"
                f"먼저 'make chrome'을 실행해 Chrome CDP를 시작하세요."
            )
            return []

        context = browser.contexts[0]
        page = await context.new_page()

        try:
            try:
                await page.goto(
                    sale_url,
                    wait_until="domcontentloaded",
                    timeout=PAGE_LOAD_TIMEOUT_MS,
                )
            except Exception as exc:
                logger.warning(f"goto 에러(계속 진행): {type(exc).__name__}: {exc}")

            final_url = page.url
            page_title = await page.title()
            logger.info(f"페이지 로드 완료 — URL: {final_url} | 제목: {page_title!r}")

            if expected_segment and expected_segment not in final_url:
                logger.warning(f"세일 URL에서 다른 페이지로 이동됨. 현재 URL: {final_url}")

            await asyncio.sleep(PAGE_SETTLE_SEC)

            products = await _collect_via_api(page)
            logger.info(f"나이키 크롤링 완료 — 총 {len(products)}개")
            return products

        finally:
            await page.close()

    finally:
        await pw.stop()


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
