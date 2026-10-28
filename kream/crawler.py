"""
kream/crawler.py
모델명으로 Kream을 검색하여 KreamProduct 목록을 반환하는 비동기 크롤러.

설계 원칙:
- 페이지를 재사용하여 검색 (create/close 반복 금지)
- 검색은 결과 문서 URL로 이동한다
- 차단 감지 시 지수 백오프로 최대 MAX_RETRIES회 재시도
"""
import asyncio
import random
import time
from urllib.parse import quote
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
MAX_RETRIES        = 3           # 차단 시 최대 재시도 횟수
RETRY_BACKOFF_BASE = 30          # 재시도 대기 기본 초 (30 → 60 → 120)
KREAM_BASE_URL     = "https://kream.co.kr"


async def _human_wait(min_s: float = 1.0, max_s: float = 3.0) -> None:
    """랜덤 대기로 사람처럼 행동한다."""
    await asyncio.sleep(random.uniform(min_s, max_s))


def _card_matches_model(name: str | None, model_name: str) -> bool:
    if not name or not model_name:
        return False
    return model_name.casefold() in name.casefold()


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
    재사용 페이지로 Kream 검색 결과 문서를 연다.

    Returns:
        list[KreamProduct] : 정상 결과 (결과 없음이면 빈 리스트)
        None               : 차단이거나 검색 문서가 열리지 않음 (재시도 필요)
    """
    import re as _re
    import pathlib, datetime

    logger.info(f"[{model_name}] Kream 검색 시작")

    products: list[KreamProduct] = []

    t0 = time.monotonic()
    search_url = f"{KREAM_BASE_URL}/search?keyword={quote(model_name)}"
    try:
        await page.goto(search_url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
    except Exception as exc:
        logger.debug(f"[{model_name}] 검색 페이지 goto 에러(계속): {type(exc).__name__}: {exc}")
    if page.url.startswith("chrome-error://") or page.url.startswith("about:"):
        logger.warning(f"[{model_name}] 검색 페이지 로드 실패: {page.url}")
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

    matched = [card for card in cards_data if _card_matches_model(card.get("name"), model_name)]
    logger.info(
        f"[{model_name}] 검색 결과 {len(cards_data)}개, 모델코드 포함 {len(matched)}개"
    )
    if not matched:
        logger.info(f"[{model_name}] 모델코드가 이름에 있는 카드 없음")
        return []

    seen_hrefs: set[str] = set()
    for card in matched:
        try:
            href = card.get("href") or ""
            if not href or href in seen_hrefs:
                continue
            if not card.get("priceStr"):
                logger.debug(f"[{model_name}] 가격 없음 — 건너뜀")
                continue

            kream_price = parse_kream_price(card["priceStr"])
            trade_count = parse_trade_count(card["tradeStr"]) if card["tradeStr"] else 0

            product = KreamProduct(
                model_name=model_name,
                kream_name=card["name"],
                kream_price=kream_price,
                trade_count=trade_count,
                kream_url=href,
            )
            seen_hrefs.add(href)
            products.append(product)
            logger.info(
                f"[{model_name}] 수집: {card['name']!r}, "
                f"가격={kream_price:,}원, 거래량={trade_count}"
            )

        except Exception as exc:
            logger.warning(f"[{model_name}] 카드 파싱 실패: {exc} — 건너뜀")

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
        marketplace_json = Path(config.OUTPUT_DIR) / "marketplace_products.json"
        if not marketplace_json.exists():
            print(f"오류: {marketplace_json} 파일이 없습니다. 먼저 marketplace 크롤러를 실행하세요.")
            return

        data = json.loads(marketplace_json.read_text(encoding="utf-8"))
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
