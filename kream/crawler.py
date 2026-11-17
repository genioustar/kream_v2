"""
kream/crawler.py
모델명으로 Kream을 검색하여 KreamProduct 목록을 반환하는 비동기 크롤러.

설계 원칙:
- 메인 페이지는 init_kream_page() 에서 1회만 방문 (검색마다 재방문 금지)
- 페이지를 재사용하여 검색 (create/close 반복 금지)
- 스크롤·마우스 휠·랜덤 대기로 사람처럼 행동
- 차단 또는 검색 실패 시 후속 검색 중단 (자동 재시도 없음)
"""
import asyncio
import logging
import random
import time
from urllib.parse import parse_qs, urlsplit
from common.models import KreamProduct
from common.pacing import CrawlBlockedError, check_response
from kream.parser import (
    KREAM_PRODUCT_CARD_SELECTOR,
    KREAM_SELECTORS,
    model_numbers_match,
    parse_kream_model_number,
    parse_kream_price,
    parse_trade_count,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
PAGE_LOAD_TIMEOUT  = 60_000      # 페이지 로드 타임아웃 (ms)
SELECTOR_TIMEOUT   = 15_000      # 셀렉터 대기 타임아웃 (ms)
MAX_RESULTS        = 5           # 검색 결과에서 최대 처리할 상품 수
KREAM_BASE_URL     = "https://kream.co.kr"

# 검색 입력창 폴백 셀렉터 순서 (앞에서부터 시도)
# 실제 DOM 확인 결과: class="input_search", placeholder="브랜드, 상품, 프로필, 태그 등"
SEARCH_INPUT_SELECTORS = [
    "input.input_search",            # 실제 클래스명 (가장 신뢰)
    "input[placeholder*='브랜드']",  # 실제 placeholder 기반
    "header input[type='text']",
    "input[placeholder*='검색']",
]

# 검색 버튼 폴백 셀렉터 순서. 실측 클릭은 전부 button.btn_search 였다.
SEARCH_BUTTON_SELECTORS = [
    "button.btn_search",
    "button.header-search-button",
]

# ego Locator.is_visible(timeout)은 timeout(ms) 동안 실제로 폴링한다.
# 입력창이 이미 보이는지 보는 확인은 기다리지 않고 셀렉터마다 즉시 1회만 판정한다.
SEARCH_INPUT_ALREADY_VISIBLE_TIMEOUT_MS = 0
# 버튼이 보이는지, 클릭 뒤 오버레이 입력창이 열리는지는 기다린다.
SEARCH_BUTTON_VISIBLE_TIMEOUT_MS = 2_000
SEARCH_INPUT_AFTER_CLICK_TIMEOUT_MS = 3_000

# Enter 뒤 사람처럼 보이는 간격을 유지하는 최소 대기(초).
RENDER_MIN_WAIT_MIN_SEC = 1.5
RENDER_MIN_WAIT_MAX_SEC = 2.5
# 최소 대기 다음, 이번 검색 결과가 렌더링됐는지 폴링하는 상한(초).
# 기존 고정 대기 상한(7초)과 비슷하다.
RENDER_MAX_WAIT_SEC = 7.0
RENDER_POLL_INTERVAL_SEC = 0.2


# ---------------------------------------------------------------------------
# 사람처럼 행동하는 헬퍼
# ---------------------------------------------------------------------------

def _monotonic() -> float:
    """렌더링 폴링 시각. 테스트는 이 이름만 갈아끼운다."""
    return time.monotonic()


async def _poll_sleep(seconds: float) -> None:
    """렌더링 폴링 간격. 테스트는 이 이름만 갈아끼운다."""
    await asyncio.sleep(seconds)


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
            if not await btn.is_visible(timeout=SEARCH_BUTTON_VISIBLE_TIMEOUT_MS):
                continue
            await _human_wait(0.3, 0.7)
            await btn.click()
            logger.debug("검색 버튼 클릭 완료: %r", selector)
            return True
        except Exception:
            continue
    return False


async def _find_visible_input(page, timeout_ms: int):
    """
    SEARCH_INPUT_SELECTORS 순서로 보이는 입력창을 찾아 반환한다.
    찾지 못하면 None 반환.

    timeout_ms 가 0이면 셀렉터마다 즉시 1회만 본다. ego 어댑터는
    timeout 이 0보다 크면 그 시간 동안 폴링한다.
    """
    for selector in SEARCH_INPUT_SELECTORS:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=timeout_ms):
                logger.debug("검색 입력창 발견: %r", selector)
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
    # 입력창이 이미 보이는 경우 (검색 결과 페이지) — 버튼 클릭 불필요.
    # 상세 페이지에는 입력창이 없으므로 여기서 기다리지 않는다.
    locator = await _find_visible_input(page, timeout_ms=SEARCH_INPUT_ALREADY_VISIBLE_TIMEOUT_MS)
    if locator:
        logger.debug("검색 입력창 이미 활성화 — 버튼 클릭 생략")
        return locator

    # 입력창이 없는 경우 (홈 등) — 검색 버튼 클릭으로 활성화
    if not await _click_search_button(page):
        raise RuntimeError(f"검색 버튼 클릭 실패 (url={page.url})")

    await _human_wait(0.3, 0.7)

    locator = await _find_visible_input(page, timeout_ms=SEARCH_INPUT_AFTER_CLICK_TIMEOUT_MS)
    if locator:
        return locator

    raise RuntimeError(f"검색 입력창을 찾을 수 없습니다 (시도: {SEARCH_INPUT_SELECTORS})")


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

async def _is_blocked_page(page) -> bool:
    """Kream 차단 문구가 있는 페이지만 차단으로 본다.

    본문을 읽지 못하거나 길이가 짧은 경우는 이동 중 화면일 수 있어
    전체 검색을 끊지 않는다. 호출한 쪽에서 모델번호 확인을 이어간다.
    """
    try:
        content = await page.content()
    except Exception as exc:
        logger.warning(
            "Kream 페이지 본문을 읽지 못했습니다: url=%s error=%s",
            getattr(page, "url", ""),
            type(exc).__name__,
        )
        return False
    content_lower = content.lower()
    blocked_texts = ["잠시 후 다시 시도", "잠시후 다시 시도", "too many requests", "rate limit"]
    for text in blocked_texts:
        if text.lower() in content_lower:
            return True
    if len(content) < 500:
        logger.warning(
            "Kream 페이지 본문이 짧아 모델 확인으로 넘깁니다: url=%s len=%s",
            getattr(page, "url", ""),
            len(content),
        )
    return False


def _record_kream_response(page, response) -> None:
    """검색 SPA의 first-party 문서/API 접근 제한을 기억한다."""
    host = (urlsplit(response.url).hostname or "").lower()
    if (host == "kream.co.kr" or host.endswith(".kream.co.kr")) and response.request.resource_type in ("document", "xhr", "fetch"):
        if response.status in (403, 429):
            page._kream_blocked_status = response.status


def _raise_if_kream_limited(page) -> None:
    status = getattr(page, "_kream_blocked_status", None)
    if status in (403, 429):
        raise CrawlBlockedError(f"Kream 문서/API HTTP {status} — 후속 요청 중단")


async def _load_detail_model_number(page, product_url: str) -> str | None:
    """
    Kream 상품 상세 페이지로 이동하여 실제 모델번호를 읽는다.

    모델번호 DOM이 없거나 파싱할 수 없으면 오탐 저장을 막기 위해 None을 반환한다.
    """
    _raise_if_kream_limited(page)
    response = None
    try:
        response = await page.goto(
            product_url,
            wait_until="domcontentloaded",
            timeout=PAGE_LOAD_TIMEOUT,
        )
    except Exception as exc:
        # Kream은 일부 리소스 오류 때문에 navigation 예외가 나도 본문이 렌더링될 수 있다.
        logger.debug(
            "Kream 상세 페이지 goto 에러(모델번호 확인 계속): url=%s, error=%s", product_url, type(exc).__name__
        )

    check_response(response)
    _raise_if_kream_limited(page)
    if await _is_blocked_page(page):
        raise CrawlBlockedError("Kream 상세 페이지 접근 제한/비정상 응답 — 후속 요청 중단")
    try:
        await page.wait_for_selector(
            KREAM_SELECTORS["detail_model_number"],
            state="attached",
            timeout=SELECTOR_TIMEOUT,
        )
        texts = await page.locator(
            KREAM_SELECTORS["detail_model_number"]
        ).all_text_contents()
    except Exception as exc:
        logger.warning(
            "Kream 상세 모델번호 확인 실패: url=%s, error=%s", product_url, type(exc).__name__
        )
        return None

    for text in texts:
        model_number = parse_kream_model_number(text)
        if model_number:
            return model_number

    logger.warning("Kream 상세 모델번호 파싱 실패: url=%s", product_url)
    return None


async def _find_verified_kream_product(
    model_name: str,
    cards_data: list[dict],
    page,
) -> KreamProduct | None:
    """검색 후보 상세를 순서대로 확인해 모델번호가 정확히 맞는 첫 상품을 반환한다."""
    for card in cards_data[:MAX_RESULTS]:
        try:
            if not card["name"] or not card["priceStr"] or not card["href"]:
                logger.debug("[%s] 상품명/가격/URL 없음 — 건너뜀", model_name)
                continue

            actual_model_number = await _load_detail_model_number(page, card["href"])
            if actual_model_number is None:
                logger.warning(
                    "[%s] 실제 모델번호를 확인할 수 없음 — url=%s 건너뜀", model_name, card['href']
                )
                continue

            if not model_numbers_match(model_name, actual_model_number):
                logger.info(
                    "[%s] Kream 실제 모델번호 불일치 — actual=%r, url=%s 건너뜀", model_name, actual_model_number, card['href']
                )
                continue

            kream_price = parse_kream_price(card["priceStr"])
            trade_count = (
                parse_trade_count(card["tradeStr"])
                if card["tradeStr"]
                else 0
            )
            product = KreamProduct(
                model_name=actual_model_number,
                kream_name=card["name"],
                kream_price=kream_price,
                trade_count=trade_count,
                kream_url=card["href"],
            )
            logger.info(
                "[%s] 모델번호 검증 완료: actual=%r, %r, 가격=%s원, 거래량=%s", model_name, actual_model_number, card['name'], format(kream_price, ","), trade_count
            )
            return product

        except CrawlBlockedError:
            raise
        except Exception as exc:
            logger.warning("[%s] 후보 검증 실패: %s — 건너뜀", model_name, exc)

    logger.info(
        "[%s] 상위 %s개 후보 중 실제 모델번호가 일치하는 상품 없음", model_name, min(len(cards_data), MAX_RESULTS)
    )
    return None


# ---------------------------------------------------------------------------
# 페이지 초기화 (메인 페이지 1회 방문)
# ---------------------------------------------------------------------------

async def init_kream_page(context):
    """
    기존 Chrome 세션에 새 탭을 생성하고 메인 페이지를 1회 방문한다.
    반환된 페이지는 이후 검색에서 재사용한다 (메인 페이지 재방문 없음).

    Raises:
        RuntimeError: 페이지 로드 후 chrome 에러 페이지에 있는 경우 (서버 접근 불가)
    """
    page = await context.new_page()
    page._kream_blocked_status = None
    page.on("response", lambda response: _record_kream_response(page, response))
    logger.info("Kream 메인 페이지 초기화 시작")

    response = None
    try:
        response = await page.goto(KREAM_BASE_URL, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
    except Exception as exc:
        # kream은 일부 리소스를 4xx로 응답해 Playwright가 navigation 실패로 처리하지만
        # 실제 HTML은 정상 로드되므로 무시하고 계속 진행
        logger.debug("Kream 메인 페이지 goto 에러(무시): %s", type(exc).__name__)

    check_response(response)
    _raise_if_kream_limited(page)
    # chrome 에러 페이지 여부 확인 (500 빈 응답 시 chrome-error://chromewebdata/ 로 이동)
    current_url = page.url
    if current_url.startswith("chrome-error://") or current_url.startswith("about:"):
        raise RuntimeError(
            f"Kream 페이지 로드 실패 — chrome 에러 페이지로 이동됨: {current_url}\n"
            f"서버가 응답하지 않거나 봇 차단이 발생했을 수 있습니다."
        )

    logger.info("Kream 메인 페이지 로드 완료 (url=%s)", current_url)
    await _human_wait(1.5, 3.0)

    return page


# ---------------------------------------------------------------------------
# 내부 구현 (단일 시도, 페이지 재사용)
# ---------------------------------------------------------------------------

_CARD_HREFS_JS = (
    "(selector) => [...document.querySelectorAll(selector)].map((card) => card.href)"
)
_NO_RESULT_TEXT_SELECTOR = "text=검색 결과가 없습니다"


def _search_keyword(url: str | None) -> str | None:
    """검색 URL의 keyword 쿼리를 디코딩한다. + 와 %20 은 공백이다."""
    if not isinstance(url, str) or not url:
        return None
    values = parse_qs(urlsplit(url).query, keep_blank_values=True).get("keyword")
    if not values:
        return None
    return values[0]


async def _product_card_hrefs(page) -> list[str] | None:
    """현재 화면의 검색 카드 href 목록. 읽지 못하면 None."""
    try:
        hrefs = await page.evaluate(_CARD_HREFS_JS, KREAM_PRODUCT_CARD_SELECTOR)
    except Exception:
        return None
    if not isinstance(hrefs, list):
        return None
    return [href for href in hrefs if isinstance(href, str)]


async def _location_href(page) -> str | None:
    """실제 주소. page.url 은 명령(goto/click/fill/press) 응답 시점에만 갱신되는 캐시라 SPA 안에서 바뀐 주소를 반영하지 못한다."""
    try:
        href = await page.evaluate("location.href")
    except Exception:
        return None
    if not isinstance(href, str):
        return None
    return href


async def _search_results_rendered(
    page,
    model_name: str,
    previous_hrefs: list[str] | None,
    previous_no_result: bool,
) -> bool:
    """이번 검색의 실제 주소와 결과 화면이 맞춰졌으면 True.

    title 은 넣지 않는다. title 이 주소·카드보다 늦게 바뀌면 이미 그려진
    결과도 상한까지 기다리게 된다.
    """
    if _search_keyword(await _location_href(page)) != model_name:
        return False
    try:
        no_result = await page.locator(_NO_RESULT_TEXT_SELECTOR).count() > 0
    except Exception:
        no_result = False
    # 직전에도 결과 없음이면 문구가 남을 수 있다. 연속 결과 없음은 상한까지 기다린 뒤 기존 흐름으로 간다. 정확성은 유지되고 느릴 뿐이다.
    if no_result and not previous_no_result:
        return True
    if previous_hrefs is None:
        return False
    hrefs = await _product_card_hrefs(page)
    if not hrefs:
        return False
    # 연속된 비슷한 모델이 같은 카드면 상한까지 기다린 뒤 기존 흐름으로 간다. 정확성은 기존과 같고 느릴 뿐이다.
    if hrefs == previous_hrefs:
        return False
    return True


async def _wait_for_search_render(
    page,
    model_name: str,
    previous_hrefs: list[str] | None,
    previous_no_result: bool,
) -> bool:
    """Enter 뒤 최소 간격을 둔 다음, 상한 안에서 이번 검색 결과 렌더링을 폴링한다.

    Returns:
        True: 상한 안에 조건이 맞음
        False: 상한까지 조건이 맞지 않음. 호출한 쪽은 기존 판정으로 이어간다.
    """
    # 사람처럼 보이는 간격을 유지한다.
    await _human_wait(RENDER_MIN_WAIT_MIN_SEC, RENDER_MIN_WAIT_MAX_SEC)
    deadline = _monotonic() + RENDER_MAX_WAIT_SEC
    while True:
        if await _search_results_rendered(
            page, model_name, previous_hrefs, previous_no_result
        ):
            return True
        remaining = deadline - _monotonic()
        if remaining <= 0:
            return False
        await _poll_sleep(min(RENDER_POLL_INTERVAL_SEC, remaining))


async def _search_kream_once(model_name: str, page) -> list[KreamProduct] | None:
    """
    재사용 페이지로 Kream을 단 1회 검색한다.
    검색창을 클릭하고 키보드로 타이핑한 뒤 Enter를 눌러 사람처럼 검색한다.

    Returns:
        list[KreamProduct] : 정상 결과 (결과 없음이면 빈 리스트)
        None               : 차단/검색창 탐지 실패 (실행 중단)
    """
    import re as _re
    import pathlib, datetime

    logger.info("[%s] Kream 검색 시작", model_name)

    # 헤더가 보이도록 맨 위로 스크롤 (검색결과 페이지에서 헤더가 숨겨질 수 있음)
    t0 = _monotonic()
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
        previous_hrefs = await _product_card_hrefs(page)
        if previous_hrefs is None:
            logger.debug("[%s] 검색 전 카드 href 스냅샷 실패", model_name)
        try:
            previous_no_result = await page.locator(_NO_RESULT_TEXT_SELECTOR).count() > 0
        except Exception:
            previous_no_result = False
        await page.keyboard.press("Enter")
        logger.debug("[%s] 검색창 입력 완료 → Enter 전송", model_name)
    except RuntimeError as exc:
        logger.warning("[%s] 검색 입력 실패: %s", model_name, exc)
        return None

    # Enter 뒤 최소 간격을 두고, 상한 안에서 이번 검색 결과 렌더링을 본다.
    # 상한까지 안 맞으면 아래 차단·결과 없음·카드 대기를 그대로 탄다.
    rendered = await _wait_for_search_render(
        page, model_name, previous_hrefs, previous_no_result
    )
    _raise_if_kream_limited(page)
    try:
        _title = await page.title()
    except Exception:
        _title = "N/A"
    current_hrefs = await _product_card_hrefs(page)
    logger.debug(
        "[%s] 렌더링 대기 완료 (%.1fs) keyword=%s 스냅샷카드=%s 현재카드=%s 직전결과없음=%s title=%r 조건충족=%s",
        model_name,
        _monotonic() - t0,
        _search_keyword(await _location_href(page)),
        None if previous_hrefs is None else len(previous_hrefs),
        None if current_hrefs is None else len(current_hrefs),
        previous_no_result,
        _title,
        rendered,
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
                "[%s] 차단 페이지 감지 url=%s — %s", model_name, page.url, shot_path.name
            )
        except Exception:
            logger.warning("[%s] 차단 페이지 감지", model_name)
        return None

    # 검색 결과 없음 감지.
    # 연속 결과 없음이면 렌더 조건이 잔존 문구를 바로 믿지 않고, 최소 대기 뒤 7초 상한이 지난 다음에 이 문구를 본다.
    # 그 시점은 기존 4~7초 고정 대기보다 늦으므로 더 보수적이다.
    no_result = await page.locator(_NO_RESULT_TEXT_SELECTOR).count()
    if no_result > 0:
        logger.info("[%s] 검색 결과 없음", model_name)
        return []

    # 검색 결과 카드 대기
    t0 = _monotonic()
    try:
        await page.wait_for_selector(
            KREAM_SELECTORS["search_results"], timeout=SELECTOR_TIMEOUT
        )
        logger.debug("[%s] 카드 셀렉터 감지 완료 (%.1fs)", model_name, _monotonic() - t0)
    except Exception as exc:
        try:
            page_title = await page.title()
            body_len = len(await page.content())
        except Exception:
            page_title, body_len = "N/A", -1
        logger.warning(
            "[%s] 카드 셀렉터 타임아웃 (%.1fs) title=%r body_len=%s error=%s", model_name, _monotonic() - t0, page_title, body_len, type(exc).__name__
        )
        return None

    # 카드 데이터를 JS로 한 번에 추출
    cards_data: list[dict] = await page.evaluate("""(selector) => {
        const cards = document.querySelectorAll(selector);
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
    }""", KREAM_PRODUCT_CARD_SELECTOR)

    total_found = len(cards_data)
    logger.info("[%s] 검색 결과 %s개 발견", model_name, total_found)

    if total_found == 0:
        logger.warning("[%s] 처리할 카드 없음", model_name)
        return []

    product = await _find_verified_kream_product(model_name, cards_data, page)
    return [product] if product is not None else []


# ---------------------------------------------------------------------------
# 공개 API (순차 검색·중단)
# ---------------------------------------------------------------------------

async def search_kream(model_name: str, page) -> list[KreamProduct]:
    """추가 휴식 없이 한 번만 시도한다. 비정상 결과는 후속 요청을 중단한다."""
    _raise_if_kream_limited(page)
    result = await _search_kream_once(model_name, page)
    if result is None:
        raise CrawlBlockedError(f"[{model_name}] 차단 또는 검색 입력 실패 — 자동 재시도 없이 중단")
    return result


async def iter_kream_results(model_names: list[str], context):
    """탭 하나를 재사용해 추가 휴식 없이 순차 검색한다."""
    if not model_names:
        return
    page = await init_kream_page(context)
    for model_name in model_names:
        yield model_name, await search_kream(model_name, page)
