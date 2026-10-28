"""
naver/crawler.py
네이버 브랜드스토어 / 스마트스토어 상품 크롤러
"""
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional

import config
from common.browser import create_browser, new_stealth_page
from common.logger import get_logger
from common.model_names import MODEL_STRATEGIES, extract_model_names
from common.models import MarketplaceProduct
from naver.parser import clean_url, get_selectors, page_origin, parse_price

logger = get_logger("naver.crawler")

# 스크롤 관련 상수
SCROLL_PAUSE_MS = 1500       # 스크롤 후 대기 시간 (밀리초)
MAX_SCROLL_ATTEMPTS = 30     # 새 상품이 없을 때 최대 재시도 횟수
PAGE_LOAD_TIMEOUT = 30_000   # 페이지 로드 타임아웃 (밀리초)
INITIAL_PRODUCT_WAIT_MS = 15_000
MANUAL_PRODUCT_WAIT_MS = 30_000
NAVER_COOKIE_FILE = (
    Path(__file__).parent.parent
    / config.OUTPUT_DIR
    / "session"
    / "naver_cookies.json"
)
SKIP_REASON_SOLD_OUT = "품절"
SKIP_REASON_MODEL_MISSING = "모델명 없음"
SKIP_REASON_PARSE_ERROR = "파싱 오류"
SKIP_REASONS = (
    SKIP_REASON_SOLD_OUT,
    SKIP_REASON_MODEL_MISSING,
    SKIP_REASON_PARSE_ERROR,
)


async def _wait_for_product_list(
    page,
    item_selector: str,
    site_name: str,
    manual_input_lock: asyncio.Lock,
) -> None:
    """
    상품 목록이 자동으로 나타나지 않으면 사용자의 브라우저 조치를 기다린다.

    여러 사이트를 병렬로 열어도 입력 프롬프트는 한 번에 하나만 표시한다.
    사용자가 열린 Chrome에서 로그인/보안 확인을 마치고 Enter를 누르면
    목록을 다시 확인하며, ``q`` 입력 시 해당 사이트를 중단한다.
    """
    try:
        await page.wait_for_selector(
            item_selector,
            state="attached",
            timeout=INITIAL_PRODUCT_WAIT_MS,
        )
        return
    except Exception:
        logger.warning(
            f"[{site_name}] 상품 목록이 자동으로 표시되지 않음 — 사용자 입력 대기"
        )

    async with manual_input_lock:
        while await page.locator(item_selector).count() == 0:
            prompt = (
                f"\n[{site_name}] 열린 Chrome에서 로그인/보안 확인을 완료한 뒤 "
                "Enter를 누르세요. 이 사이트를 중단하려면 q 입력: "
            )
            try:
                answer = await asyncio.to_thread(input, prompt)
            except EOFError as exc:
                raise RuntimeError(
                    f"[{site_name}] 상품 목록 미표시 및 사용자 입력을 받을 수 없음"
                ) from exc

            if answer.strip().casefold() == "q":
                raise RuntimeError(f"[{site_name}] 사용자가 크롤링을 중단했습니다.")

            try:
                await page.wait_for_selector(
                    item_selector,
                    state="attached",
                    timeout=MANUAL_PRODUCT_WAIT_MS,
                )
            except Exception:
                logger.warning(
                    f"[{site_name}] 상품 목록이 아직 표시되지 않음 — 다시 입력 대기"
                )


async def _is_sold_out_product(item, store_type: str) -> bool:
    """
    smart_store 상품 카드의 품절 표시 여부를 판단합니다.

    스마트스토어 베스트/검색 카드에서는 이미지 위 오버레이가 "품절" 텍스트로,
    배송 영역이 "일시품절" 텍스트로 노출된다. 두 표시 중 하나라도 있으면
    Kream 조회 대상에서 제외하기 위해 수집하지 않는다.
    """
    if store_type != "smart_store":
        return False

    try:
        return await item.evaluate(
            """(el) => {
                const lines = (el.innerText || '')
                    .split(/\\n+/)
                    .map((text) => text.trim())
                    .filter(Boolean);

                if (lines.includes('일시품절')) {
                    return true;
                }

                return Array.from(el.querySelectorAll('*')).some((node) => {
                    const text = (node.innerText || node.textContent || '').trim();
                    return text === '품절';
                });
            }"""
        )
    except Exception as exc:
        logger.debug(f"품절 여부 확인 실패 (수집 계속): {exc}")
        return False


async def _scroll_to_bottom(page, item_selector: str) -> int:
    """
    페이지를 끝까지 스크롤하며 새 상품이 더 이상 로드되지 않으면 중단합니다.

    Returns:
        마지막으로 확인된 상품 카드 수
    """
    prev_count = 0
    no_change_streak = 0

    for attempt in range(MAX_SCROLL_ATTEMPTS):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(SCROLL_PAUSE_MS)
        # 스크롤 후 동적 콘텐츠 로드 대기 (타임아웃 시 무시)
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass

        current_count = await page.locator(item_selector).count()
        logger.debug(f"스크롤 {attempt + 1}회 - 상품 수: {current_count}")

        if current_count == prev_count:
            no_change_streak += 1
            if no_change_streak >= 3:
                logger.debug("3회 연속 변화 없음 → 스크롤 종료")
                break
        else:
            no_change_streak = 0

        prev_count = current_count

    return prev_count


async def _extract_product(
    item,
    selectors: dict[str, str],
    site_name: str,
    store_type: str,
    model_strategy: str,
    crawled_at: str,
    page_url: str,
) -> tuple[list[MarketplaceProduct], str | None]:
    """
    단일 상품 카드 Locator에서 상품 목록과 건너뜀 사유를 추출합니다.
    사이트의 model_strategy에 따라 괄호/슬래시/후행 코드를 해석하며,
    대체 모델이 여러 개인 경우 복수 반환합니다.
    성공 시 건너뜀 사유는 None이고, 실패 시 품절/모델명 없음/파싱 오류 중
    하나를 반환합니다.
    """
    try:
        if await _is_sold_out_product(item, store_type):
            logger.debug(f"[{site_name}] 품절 상품 제외")
            return [], SKIP_REASON_SOLD_OUT

        # 상품명
        name_el = item.locator(selectors["name"]).first
        product_name = (await name_el.inner_text()).strip()
        if not product_name:
            raise ValueError("상품명 없음")

        # 가격 - fallback 셀렉터 리스트를 순서대로 시도
        price_candidates = selectors["price"]
        if isinstance(price_candidates, str):
            price_candidates = [price_candidates]
        price: Optional[int] = None
        for price_selector in price_candidates:
            try:
                price_el = item.locator(price_selector).first
                price_str = (await price_el.inner_text(timeout=2000)).strip()
                if price_str:
                    price = parse_price(price_str)
                    break
            except Exception:
                continue
        if price is None:
            raise ValueError("가격 셀렉터 fallback 모두 실패")

        # URL
        url_el = item.locator(selectors["url"]).first
        raw_url = await url_el.get_attribute("href")
        product_url = clean_url(raw_url, page_origin(page_url))

        # 모델명 목록 추출 (복수 가능)
        model_names = extract_model_names(product_name, strategy=model_strategy)
        if not model_names:
            logger.debug(f"[{site_name}] 모델명 없음 (건너뜀): {product_name}")
            return [], SKIP_REASON_MODEL_MISSING

        return (
            [
                MarketplaceProduct(
                    site_name=site_name,
                    product_name=product_name,
                    model_name=model_name,
                    price=price,
                    url=product_url,
                    crawled_at=crawled_at,
                )
                for model_name in model_names
            ],
            None,
        )

    except Exception as exc:
        logger.debug(f"상품 파싱 실패 (건너뜀): {exc}")
        return [], SKIP_REASON_PARSE_ERROR


async def _crawl_site(
    context,
    site_info: dict,
    manual_input_lock: asyncio.Lock,
) -> list[MarketplaceProduct]:
    """
    단일 네이버 사이트(브랜드스토어 or 스마트스토어)를 크롤링합니다.

    Args:
        context: Playwright BrowserContext
        site_info: config.SEARCH_URLS의 단일 항목
        manual_input_lock: 사용자 조치 프롬프트를 직렬화하는 잠금

    Returns:
        수집된 MarketplaceProduct 목록

    Raises:
        RuntimeError: 페이지 로드 또는 상품 목록 추출에 완전히 실패한 경우
    """
    site_name: str = site_info["site_name"]
    url: str = site_info["url"]
    store_type: str = site_info["type"]
    model_strategy: str = site_info.get("model_strategy", "auto")
    if model_strategy not in MODEL_STRATEGIES:
        raise ValueError(
            f"[{site_name}] 지원하지 않는 model_strategy: {model_strategy!r}"
        )

    logger.info(
        f"[{site_name}] 크롤링 시작: {url} "
        f"(model_strategy={model_strategy})"
    )

    selectors = get_selectors(store_type)
    crawled_at = datetime.now().isoformat(timespec="seconds")

    page = await new_stealth_page(context)
    try:
        # 페이지 로드
        try:
            await page.goto(url, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT)
        except Exception as exc:
            logger.warning(f"[{site_name}] 페이지 로드 미완료 — 목록 확인 계속: {exc}")

        # 초기 상품 렌더링 대기. 보안 확인/로그인이 필요하면 사용자 입력을 기다린다.
        await _wait_for_product_list(
            page,
            selectors["item"],
            site_name,
            manual_input_lock,
        )

        # 무한스크롤 처리
        total_count = await _scroll_to_bottom(page, selectors["item"])
        logger.info(f"[{site_name}] 스크롤 완료 - 총 상품 카드: {total_count}개")

        if total_count == 0:
            raise RuntimeError(f"[{site_name}] 수집된 상품 카드가 없습니다.")

        # 각 상품 카드 파싱
        items = page.locator(selectors["item"])
        products: list[MarketplaceProduct] = []
        skip_counts = {reason: 0 for reason in SKIP_REASONS}

        for i in range(total_count):
            item = items.nth(i)
            extracted, skip_reason = await _extract_product(
                item,
                selectors,
                site_name,
                store_type,
                model_strategy,
                crawled_at,
                page.url,
            )
            if extracted:
                products.extend(extracted)
            elif skip_reason is not None:
                skip_counts[skip_reason] += 1

        skip_count = sum(skip_counts.values())
        skip_details = ", ".join(
            f"{reason}: {skip_counts[reason]}개"
            for reason in SKIP_REASONS
        )
        logger.info(
            f"[{site_name}] 파싱 완료 - 성공: {len(products)}개, "
            f"건너뜀: {skip_count}개 ({skip_details})"
        )
        return products

    finally:
        await page.close()


async def crawl_marketplace() -> list[MarketplaceProduct]:
    """
    config.SEARCH_URLS에 정의된 모든 네이버 사이트를 병렬로 크롤링하여 상품을 수집합니다.

    Returns:
        수집된 MarketplaceProduct 전체 목록.
        config.SEARCH_URLS 가 비어있거나 모든 항목의 url 이 누락이면 [] 반환.

    Raises:
        RuntimeError: 유효한 사이트가 있었으나 모두 크롤링에 실패한 경우
    """
    search_urls = getattr(config, "SEARCH_URLS", None) or []
    if not search_urls:
        logger.info("config.SEARCH_URLS 미설정/빈 목록 — marketplace 크롤링 건너뜀")
        return []

    # 항목별 url 누락은 해당 사이트만 스킵, 나머지는 진행
    valid_sites: list[dict] = []
    for site_info in search_urls:
        url = (site_info.get("url") or "").strip()
        if not url:
            logger.warning(
                f"[{site_info.get('site_name', '?')}] url 누락 — 해당 사이트 건너뜀"
            )
            continue
        model_strategy = site_info.get("model_strategy", "auto")
        if model_strategy not in MODEL_STRATEGIES:
            raise ValueError(
                f"[{site_info.get('site_name', '?')}] 지원하지 않는 "
                f"model_strategy: {model_strategy!r}"
            )
        valid_sites.append(site_info)

    if not valid_sites:
        logger.info("config.SEARCH_URLS 의 모든 항목에 url 이 없음 — marketplace 크롤링 건너뜀")
        return []

    manual_input_lock = asyncio.Lock()
    async with create_browser(
        headless=False,
        channel="chrome",
        cookie_file=NAVER_COOKIE_FILE,
        use_native_fingerprint=True,
    ) as context:
        tasks = [
            _crawl_site(context, site_info, manual_input_lock)
            for site_info in valid_sites
        ]
        gather_results = await asyncio.gather(*tasks, return_exceptions=True)

    all_products: list[MarketplaceProduct] = []
    failed_sites: list[str] = []
    for site_info, result in zip(valid_sites, gather_results):
        if isinstance(result, Exception):
            logger.error(f"사이트 크롤링 실패 [{site_info['site_name']}]: {result}")
            failed_sites.append(site_info["site_name"])
        else:
            all_products.extend(result)

    if failed_sites:
        logger.warning(f"실패한 사이트: {failed_sites}")

    if not all_products and len(failed_sites) == len(valid_sites):
        raise RuntimeError(f"모든 marketplace 사이트 크롤링에 실패했습니다: {failed_sites}")

    logger.info(f"전체 수집 완료 - 총 {len(all_products)}개 상품")
    return all_products


# ---------------------------------------------------------------------------
# 단독 실행 진입점
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import json

    async def main():
        products = await crawl_marketplace()
        out_dir = Path(config.OUTPUT_DIR)
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / f"marketplace_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        data = [
            {
                "site_name": p.site_name,
                "product_name": p.product_name,
                "model_name": p.model_name,
                "price": p.price,
                "url": p.url,
                "crawled_at": p.crawled_at,
            }
            for p in products
        ]
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"저장 완료: {out_path} ({len(products)}개)")

    asyncio.run(main())
