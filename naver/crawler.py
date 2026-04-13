"""
naver/crawler.py
네이버 브랜드스토어 / 스마트스토어 상품 크롤러
"""
import asyncio
from datetime import datetime
from typing import Optional

import config
from common.browser import create_browser
from common.logger import get_logger
from common.models import NaverProduct
from naver.parser import get_selectors, parse_price, extract_model_names, clean_url

logger = get_logger("naver.crawler")

# 스크롤 관련 상수
SCROLL_PAUSE_MS = 1500       # 스크롤 후 대기 시간 (밀리초)
MAX_SCROLL_ATTEMPTS = 30     # 새 상품이 없을 때 최대 재시도 횟수
PAGE_LOAD_TIMEOUT = 30_000   # 페이지 로드 타임아웃 (밀리초)


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
    crawled_at: str,
) -> list[NaverProduct]:
    """
    단일 상품 카드 Locator에서 NaverProduct 목록을 추출합니다.
    모델명이 "/" 로 여러 개인 경우 복수 반환합니다.
    오류 발생 시 빈 리스트를 반환합니다.
    """
    try:
        # 상품명
        name_el = item.locator(selectors["name"]).first
        product_name = (await name_el.inner_text()).strip()
        if not product_name:
            return []

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
        product_url = clean_url(raw_url)

        # 모델명 목록 추출 (복수 가능)
        model_names = extract_model_names(product_name)

        return [
            NaverProduct(
                site_name=site_name,
                product_name=product_name,
                model_name=model_name,
                price=price,
                url=product_url,
                crawled_at=crawled_at,
            )
            for model_name in model_names
        ]

    except Exception as exc:
        logger.debug(f"상품 파싱 실패 (건너뜀): {exc}")
        return []


async def _crawl_site(context, site_info: dict) -> list[NaverProduct]:
    """
    단일 네이버 사이트(브랜드스토어 or 스마트스토어)를 크롤링합니다.

    Args:
        context: Playwright BrowserContext
        site_info: config.SEARCH_URLS의 단일 항목

    Returns:
        수집된 NaverProduct 목록

    Raises:
        RuntimeError: 페이지 로드 또는 상품 목록 추출에 완전히 실패한 경우
    """
    site_name: str = site_info["site_name"]
    url: str = site_info["url"]
    store_type: str = site_info["type"]

    logger.info(f"[{site_name}] 크롤링 시작: {url}")

    selectors = get_selectors(store_type)
    crawled_at = datetime.now().isoformat(timespec="seconds")

    page = await context.new_page()
    try:
        # 페이지 로드
        try:
            await page.goto(url, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT)
        except Exception as exc:
            raise RuntimeError(f"[{site_name}] 페이지 로드 실패: {exc}") from exc

        # 초기 상품 렌더링 대기
        try:
            await page.wait_for_selector(selectors["item"], timeout=15_000)
        except Exception as exc:
            raise RuntimeError(f"[{site_name}] 상품 목록 셀렉터 없음 ({selectors['item']}): {exc}") from exc

        # 무한스크롤 처리
        total_count = await _scroll_to_bottom(page, selectors["item"])
        logger.info(f"[{site_name}] 스크롤 완료 - 총 상품 카드: {total_count}개")

        if total_count == 0:
            raise RuntimeError(f"[{site_name}] 수집된 상품 카드가 없습니다.")

        # 각 상품 카드 파싱
        items = page.locator(selectors["item"])
        products: list[NaverProduct] = []
        skip_count = 0

        for i in range(total_count):
            item = items.nth(i)
            extracted = await _extract_product(item, selectors, site_name, crawled_at)
            if extracted:
                products.extend(extracted)
            else:
                skip_count += 1

        logger.info(
            f"[{site_name}] 파싱 완료 - 성공: {len(products)}개, 건너뜀: {skip_count}개"
        )
        return products

    finally:
        await page.close()


async def crawl_naver() -> list[NaverProduct]:
    """
    config.SEARCH_URLS에 정의된 모든 네이버 사이트를 병렬로 크롤링하여 상품을 수집합니다.

    Returns:
        수집된 NaverProduct 전체 목록

    Raises:
        RuntimeError: 모든 사이트 크롤링이 실패한 경우
    """
    async with create_browser(headless=True) as context:
        tasks = [_crawl_site(context, site_info) for site_info in config.SEARCH_URLS]
        gather_results = await asyncio.gather(*tasks, return_exceptions=True)

    all_products: list[NaverProduct] = []
    failed_sites: list[str] = []
    for site_info, result in zip(config.SEARCH_URLS, gather_results):
        if isinstance(result, Exception):
            logger.error(f"사이트 크롤링 실패 [{site_info['site_name']}]: {result}")
            failed_sites.append(site_info["site_name"])
        else:
            all_products.extend(result)

    if failed_sites:
        logger.warning(f"실패한 사이트: {failed_sites}")

    if not all_products and len(failed_sites) == len(config.SEARCH_URLS):
        raise RuntimeError(f"모든 네이버 사이트 크롤링에 실패했습니다: {failed_sites}")

    logger.info(f"전체 수집 완료 - 총 {len(all_products)}개 상품")
    return all_products


# ---------------------------------------------------------------------------
# 단독 실행 진입점
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import json
    from pathlib import Path

    async def main():
        products = await crawl_naver()
        out_dir = Path(config.OUTPUT_DIR)
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / f"naver_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
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
