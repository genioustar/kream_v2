"""
kream/crawler.py
모델명으로 Kream을 검색하여 KreamProduct 목록을 반환하는 비동기 크롤러.

설계 원칙:
- 메인 페이지는 init_kream_page() 에서 1회만 방문 (검색마다 재방문 금지)
- 페이지를 재사용하여 검색 (create/close 반복 금지)
- 스크롤·마우스 휠·랜덤 대기로 사람처럼 행동
- 차단 감지 시 지수 백오프로 최대 MAX_RETRIES회 재시도
"""
import asyncio
import random
import time
from common.browser import new_stealth_page
from common.logger import get_logger
from common.models import KreamProduct
from kream.parser import KREAM_SELECTORS, parse_kream_price, parse_trade_count

logger = get_logger("kream.crawler")

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
PAGE_LOAD_TIMEOUT  = 60_000      # 페이지 로드 타임아웃 (ms)
SELECTOR_TIMEOUT   = 15_000      # 셀렉터 대기 타임아웃 (ms)
DELAY_MIN_SEC      = 8.0         # 검색 간 최소 딜레이 (초)
DELAY_MAX_SEC      = 15.0        # 검색 간 최대 딜레이 (초)
MAX_RESULTS        = 5           # 검색 결과에서 최대 처리할 상품 수
MAX_RETRIES        = 3           # 차단 시 최대 재시도 횟수
RETRY_BACKOFF_BASE = 30          # 재시도 대기 기본 초 (30 → 60 → 120)
KREAM_BASE_URL     = "https://kream.co.kr"

# 검색 입력창 폴백 셀렉터 순서 (앞에서부터 시도)
# 실제 DOM 확인 결과: class="input_search", placeholder="브랜드, 상품, 프로필, 태그 등"
SEARCH_INPUT_SELECTORS = [
    "input.input_search",            # 실제 클래스명 (가장 신뢰)
    "input[placeholder*='브랜드']",  # 실제 placeholder 기반
    "header input[type='text']",
    "input[placeholder*='검색']",
]

# 검색 버튼 폴백 셀렉터 순서
# 홈: #wrap > ... > button (정확한 경로)
# 공통: class 기반 (검색결과 페이지에서도 동작)
SEARCH_BUTTON_SELECTORS = [
    "#wrap > div.header-wrapper > div > div > div > div > div > div > div.header_main > div > div.right > div > button",
    "button.btn_search",
    "button.header-search-button",
]


# ---------------------------------------------------------------------------
# 사람처럼 행동하는 헬퍼
# ---------------------------------------------------------------------------

async def _human_wait(min_s: float = 1.0, max_s: float = 3.0) -> None:
    """랜덤 대기로 사람처럼 행동한다."""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def _human_type(page, text: str) -> None:
    """검색어를 사람처럼 한 글자씩 타이핑한다."""
    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep(random.uniform(0.08, 0.22))


async def _click_search_button(page) -> bool:
    """
    헤더 검색 버튼을 클릭한다.
    SEARCH_BUTTON_SELECTORS 순서로 시도하여 첫 번째 보이는 버튼을 클릭한다.

    Returns:
        True: 클릭 성공
        False: 모든 셀렉터 실패
    """
    for selector in SEARCH_BUTTON_SELECTORS:
        try:
            btn = page.locator(selector).first
            if not await btn.is_visible(timeout=2000):
                continue
            await _human_wait(0.3, 0.7)
            await btn.click()
            logger.debug(f"검색 버튼 클릭 완료: {selector!r}")
            return True
        except Exception:
            continue
    return False


async def _find_visible_input(page, timeout_ms: int = 2000):
    """
    SEARCH_INPUT_SELECTORS 순서로 보이는 입력창을 찾아 반환한다.
    찾지 못하면 None 반환.
    """
    for selector in SEARCH_INPUT_SELECTORS:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=timeout_ms):
                logger.debug(f"검색 입력창 발견: {selector!r}")
                return locator
        except Exception:
            continue
    return None


async def _open_search_and_get_input(page):
    """
    검색 입력창 Locator를 반환한다.
    - 검색 결과 페이지처럼 입력창이 이미 보이면 바로 반환 (버튼 클릭 생략)
    - 입력창이 없으면 검색 버튼을 클릭해 활성화 후 반환

    Returns:
        검색 입력창 Locator

    Raises:
        RuntimeError: 버튼 클릭 실패 또는 입력창을 찾지 못한 경우
    """
    # 입력창이 이미 보이는 경우 (검색 결과 페이지) — 버튼 클릭 불필요
    locator = await _find_visible_input(page, timeout_ms=1000)
    if locator:
        logger.debug("검색 입력창 이미 활성화 — 버튼 클릭 생략")
        return locator

    # 입력창이 없는 경우 (홈 등) — 검색 버튼 클릭으로 활성화
    if not await _click_search_button(page):
        raise RuntimeError(f"검색 버튼 클릭 실패 (url={page.url})")

    await _human_wait(0.3, 0.7)

    locator = await _find_visible_input(page, timeout_ms=3000)
    if locator:
        return locator

    raise RuntimeError(f"검색 입력창을 찾을 수 없습니다 (시도: {SEARCH_INPUT_SELECTORS})")


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

async def _is_blocked_page(page) -> bool:
    """Kream 차단/에러 페이지 여부를 감지한다."""
    try:
        content = await page.content()
        if len(content) < 500:
            return True
        blocked_texts = ["잠시 후 다시 시도", "잠시후 다시 시도", "too many requests", "rate limit"]
        content_lower = content.lower()
        for text in blocked_texts:
            if text.lower() in content_lower:
                return True
        return False
    except Exception:
        return True


# ---------------------------------------------------------------------------
# 페이지 초기화 (메인 페이지 1회 방문)
# ---------------------------------------------------------------------------

async def init_kream_page(context):
    """
    stealth 페이지를 생성하고 메인 페이지를 1회 방문하여 세션·쿠키를 초기화한다.
    반환된 페이지는 이후 검색에서 재사용한다 (메인 페이지 재방문 없음).

    Raises:
        RuntimeError: 페이지 로드 후 chrome 에러 페이지에 있는 경우 (서버 접근 불가)
    """
    page = await new_stealth_page(context)
    logger.info("Kream 메인 페이지 초기화 시작")

    try:
        await page.goto(KREAM_BASE_URL, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
    except Exception as exc:
        # kream은 일부 리소스를 4xx로 응답해 Playwright가 navigation 실패로 처리하지만
        # 실제 HTML은 정상 로드되므로 무시하고 계속 진행
        logger.debug(f"Kream 메인 페이지 goto 에러(무시): {type(exc).__name__}")

    # chrome 에러 페이지 여부 확인 (500 빈 응답 시 chrome-error://chromewebdata/ 로 이동)
    current_url = page.url
    if current_url.startswith("chrome-error://") or current_url.startswith("about:"):
        raise RuntimeError(
            f"Kream 페이지 로드 실패 — chrome 에러 페이지로 이동됨: {current_url}\n"
            f"서버가 응답하지 않거나 봇 차단이 발생했을 수 있습니다."
        )

    logger.info(f"Kream 메인 페이지 로드 완료 (url={current_url})")
    await _human_wait(1.5, 3.0)

    return page


# ---------------------------------------------------------------------------
# 내부 구현 (단일 시도, 페이지 재사용)
# ---------------------------------------------------------------------------

async def _search_kream_once(model_name: str, page) -> list[KreamProduct] | None:
    """
    재사용 페이지로 Kream을 단 1회 검색한다.
    검색창을 클릭하고 키보드로 타이핑한 뒤 Enter를 눌러 사람처럼 검색한다.

    Returns:
        list[KreamProduct] : 정상 결과 (결과 없음이면 빈 리스트)
        None               : 차단/검색창 탐지 실패 (재시도 필요)
    """
    import re as _re
    import pathlib, datetime

    logger.info(f"[{model_name}] Kream 검색 시작")

    products: list[KreamProduct] = []

    # 헤더가 보이도록 맨 위로 스크롤 (검색결과 페이지에서 헤더가 숨겨질 수 있음)
    t0 = time.monotonic()
    try:
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass
    await _human_wait(0.5, 1.0)

    # 검색 버튼 클릭 → 입력창 활성화 → 이전 검색어 지우고 새 키워드 입력 → 엔터
    try:
        search_input = await _open_search_and_get_input(page)
        await _human_wait(0.3, 0.7)
        await search_input.click(click_count=3)  # 입력창 포커스 + 기존 텍스트 전체 선택
        await _human_wait(0.1, 0.3)
        await _human_type(page, model_name)      # 선택된 텍스트 대체하며 새 검색어 입력
        await _human_wait(0.3, 0.8)
        await page.keyboard.press("Enter")
        logger.debug(f"[{model_name}] 검색창 입력 완료 → Enter 전송")
    except RuntimeError as exc:
        logger.warning(f"[{model_name}] 검색 입력 실패: {exc}")
        return None

    # SPA 렌더링 완료 대기
    await _human_wait(4.0, 7.0)
    try:
        _title = await page.title()
    except Exception:
        _title = "N/A"
    logger.debug(
        f"[{model_name}] 렌더링 대기 완료 ({time.monotonic()-t0:.1f}s) "
        f"url={page.url} title={_title!r}"
    )

    # 차단 페이지 감지
    if await _is_blocked_page(page):
        # 디버그용 스크린샷 저장
        try:
            dump_dir = pathlib.Path(__file__).parent.parent / "output" / "debug"
            dump_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = _re.sub(r"[^\w\-]", "_", model_name)[:30]
            shot_path = dump_dir / f"blocked_{ts}_{safe_name}.png"
            await page.screenshot(path=str(shot_path), full_page=True)
            logger.warning(
                f"[{model_name}] 차단 페이지 감지 "
                f"url={page.url} — {shot_path.name}"
            )
        except Exception:
            logger.warning(f"[{model_name}] 차단 페이지 감지")
        return None

    # 검색 결과 없음 감지
    no_result = await page.locator("text=검색 결과가 없습니다").count()
    if no_result > 0:
        logger.info(f"[{model_name}] 검색 결과 없음")
        return []

    # 검색 결과 카드 대기
    t0 = time.monotonic()
    try:
        await page.wait_for_selector(
            KREAM_SELECTORS["search_results"], timeout=SELECTOR_TIMEOUT
        )
        logger.debug(f"[{model_name}] 카드 셀렉터 감지 완료 ({time.monotonic()-t0:.1f}s)")
    except Exception as exc:
        try:
            page_title = await page.title()
            body_len = len(await page.content())
        except Exception:
            page_title, body_len = "N/A", -1
        logger.warning(
            f"[{model_name}] 카드 셀렉터 타임아웃 ({time.monotonic()-t0:.1f}s) "
            f"title={page_title!r} body_len={body_len} error={type(exc).__name__}"
        )
        return []

    # 카드 데이터를 JS로 한 번에 추출
    cards_data: list[dict] = await page.evaluate("""() => {
        const cards = document.querySelectorAll('a.product_card[href*="/products/"]');
        return Array.from(cards).map(card => {
            const ps = Array.from(card.querySelectorAll('p')).map(p => p.textContent.trim());

            const priceStr = ps.find(t => /\\d[\\d,]+원/.test(t)) || null;
            const tradeStr = ps.find(t => /거래\\s+\\d+/.test(t)) || null;
            const nameStr = ps.find(t =>
                t.length > 10 &&
                !/%/.test(t) &&
                !/[\\d,]+원/.test(t) &&
                !/관심/.test(t) &&
                !/거래/.test(t) &&
                !/리뷰/.test(t)
            ) || null;

            return { href: card.href, name: nameStr, priceStr, tradeStr };
        });
    }""")

    total_found = len(cards_data)
    logger.info(f"[{model_name}] 검색 결과 {total_found}개 발견")

    if total_found >= 3:
        logger.info(f"[{model_name}] 결과 {total_found}개 — 정확한 매칭 없음, 건너뜀")
        return []

    if total_found == 0:
        logger.warning(f"[{model_name}] 처리할 카드 없음")
        return []

    for card in cards_data:
        try:
            if not card["name"] or not card["priceStr"]:
                logger.debug(f"[{model_name}] 상품명/가격 없음 — 건너뜀")
                continue

            kream_price = parse_kream_price(card["priceStr"])
            trade_count = parse_trade_count(card["tradeStr"]) if card["tradeStr"] else 0

            product = KreamProduct(
                model_name=model_name,
                kream_name=card["name"],
                kream_price=kream_price,
                trade_count=trade_count,
                kream_url=card["href"],
            )
            products.append(product)
            logger.info(
                f"[{model_name}] 수집: {card['name']!r}, "
                f"가격={kream_price:,}원, 거래량={trade_count}"
            )

        except Exception as exc:
            logger.warning(f"[{model_name}] 카드 파싱 실패: {exc} — 건너뜀")

    # 첫 번째 상품(검색 결과 맨 왼쪽)만 반환
    if products:
        first = products[0]
        products = [first]
        logger.info(f"[{model_name}] 첫 번째 상품 선택: {first.kream_name!r}, 가격={first.kream_price:,}원, 거래량={first.trade_count:,}")

    return products


# ---------------------------------------------------------------------------
# 공개 API (재시도 래퍼)
# ---------------------------------------------------------------------------

async def search_kream(model_name: str, page) -> list[KreamProduct]:
    """
    재사용 페이지로 Kream을 검색. 차단 시 지수 백오프로 최대 MAX_RETRIES회 재시도.
    재시도 시 새 창을 생성하지 않고 기존 페이지를 Kream 홈으로 재이동하여 세션을 초기화한다.

    Args:
        model_name: 검색할 모델명
        page:       init_kream_page()로 초기화된 Playwright Page (재사용)
    """
    for attempt in range(MAX_RETRIES + 1):
        result = await _search_kream_once(model_name, page)
        if result is not None:
            return result
        if attempt < MAX_RETRIES:
            wait_time = RETRY_BACKOFF_BASE * (2 ** attempt)  # 30, 60, 120초
            logger.warning(
                f"[{model_name}] 차단 감지 — {wait_time}초 후 재시도 "
                f"({attempt + 1}/{MAX_RETRIES})"
            )
            await asyncio.sleep(wait_time)
            # 새 창 대신 기존 페이지를 홈으로 재이동하여 세션 초기화
            try:
                logger.info(f"[{model_name}] 차단 우회 — Kream 홈으로 재이동")
                await page.goto(
                    KREAM_BASE_URL,
                    wait_until="domcontentloaded",
                    timeout=PAGE_LOAD_TIMEOUT,
                )
                await _human_wait(2.0, 4.0)
            except Exception as exc:
                logger.warning(f"[{model_name}] 홈 재이동 실패: {exc}")
        else:
            logger.error(f"[{model_name}] 최대 재시도 횟수 초과 — 건너뜀")
    return []


# ---------------------------------------------------------------------------
# 단독 실행 진입점 (디버깅용)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import json
    from pathlib import Path

    import config
    from common.browser import create_browser

    async def _main():
        naver_json = Path(config.OUTPUT_DIR) / "naver_products.json"
        if not naver_json.exists():
            print(f"오류: {naver_json} 파일이 없습니다. 먼저 naver 크롤러를 실행하세요.")
            return

        data = json.loads(naver_json.read_text(encoding="utf-8"))
        seen: set[str] = set()
        unique_models: list[str] = []
        for item in data:
            mn = item.get("model_name", "").strip()
            if mn and mn not in seen:
                seen.add(mn)
                unique_models.append(mn)

        print(f"검색할 모델명: {len(unique_models)}개")

        async with create_browser(headless=False) as context:
            page = await init_kream_page(context)
            for mn in unique_models:
                results = await search_kream(mn, page)
                for p in results:
                    print(
                        f"  [{p.model_name}] {p.kream_name} | "
                        f"가격={p.kream_price:,}원 | 거래량={p.trade_count}"
                    )
                await _human_wait(DELAY_MIN_SEC, DELAY_MAX_SEC)

    asyncio.run(_main())
