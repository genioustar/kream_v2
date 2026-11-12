"""
naver/crawler.py
네이버 브랜드스토어 / 스마트스토어 상품 크롤러
"""
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

import config
from common.browser import create_browser
from common.model_names import MODEL_STRATEGIES, extract_model_names
from common.models import MarketplaceProduct
from common.pacing import CrawlBlockedError, pause
from naver.checkpoint import load_checkpoint, save_checkpoint
from naver.diagnostics import NaverAccessError, NaverCollectionError, NaverDiagnostics, safe_url
from naver.parser import clean_url, get_selectors, page_origin, parse_price
from naver.public_search import navigate_public_search

logger = logging.getLogger(__name__)

# 스크롤 관련 상수
SCROLL_PAUSE_MS = 3500       # 스크롤 후 대기 시간 (밀리초)
MAX_SCROLL_ATTEMPTS = 30     # 새 상품이 없을 때 최대 재시도 횟수
PAGE_LOAD_TIMEOUT = 30_000   # 페이지 로드 타임아웃 (밀리초)
INITIAL_PRODUCT_WAIT_MS = 15_000
MANUAL_PRODUCT_WAIT_MS = 30_000
SKIP_REASON_SOLD_OUT = "품절"
SKIP_REASON_MODEL_MISSING = "모델명 없음"
SKIP_REASON_PARSE_ERROR = "파싱 오류"
SKIP_REASONS = (
    SKIP_REASON_SOLD_OUT,
    SKIP_REASON_MODEL_MISSING,
    SKIP_REASON_PARSE_ERROR,
)


async def _wait_for_operator(page, item_selector: str, site_name: str, diagnostics: NaverDiagnostics) -> None:
    """로그인·보안 확인 화면에서 프로세스를 종료하지 않고 사용자 입력을 기다린다."""
    logger.warning("[%s] 로그인/보안 확인 화면 — 종료하지 않고 입력을 기다립니다", site_name)
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
        diagnostics.pause_reason = None
        try:
            await page.wait_for_selector(
                item_selector, state="attached", timeout=MANUAL_PRODUCT_WAIT_MS,
            )
        except Exception:
            logger.warning("[%s] 상품 목록이 아직 표시되지 않음 — 다시 입력 대기", site_name)
        try:
            await diagnostics.check("product_list")
        except CrawlBlockedError:
            raise
    diagnostics.pause_reason = None


async def _wait_for_product_list(
    page,
    item_selector: str,
    site_name: str,
    diagnostics: NaverDiagnostics,
) -> None:
    """상품 목록을 기다린다. 로그인·보안 확인 화면이면 사용자가 끝낼 때까지 유지한다."""
    try:
        await diagnostics.run(
            page.wait_for_selector(item_selector, state="attached", timeout=INITIAL_PRODUCT_WAIT_MS),
            "product_list",
        )
    except CrawlBlockedError:
        raise
    except Exception:
        request_failed = (
            diagnostics.data["navigation_exception_class"]
            or diagnostics.data["collection_request_failure_counts"]
            or diagnostics.data["collection_http_error_counts"]
        )
        if request_failed and not diagnostics.pause_reason:
            logger.warning("[%s] 상품 목록 미표시 [network_error] — 후속 스토어 중단", site_name)
            raise NaverCollectionError("network_error", diagnostics.report_path) from None
    if await page.locator(item_selector).count() > 0:
        diagnostics.pause_reason = None
        return
    if diagnostics.pause_reason:
        await _wait_for_operator(page, item_selector, site_name, diagnostics)
        return
    logger.warning("[%s] 상품 목록 미표시 [product_list_timeout] — 후속 스토어 중단", site_name)
    raise NaverCollectionError("product_list_timeout", diagnostics.report_path)


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
        logger.debug("품절 여부 확인 실패 (수집 계속): %s", type(exc).__name__)
        return False


async def _scroll_to_bottom(page, item_selector: str, diagnostics: NaverDiagnostics) -> int:
    """
    페이지를 끝까지 스크롤하며 새 상품이 더 이상 로드되지 않으면 중단합니다.

    Returns:
        마지막으로 확인된 상품 카드 수
    """
    prev_count = 0
    no_change_streak = 0

    for attempt in range(MAX_SCROLL_ATTEMPTS):
        await diagnostics.run(page.evaluate("window.scrollTo(0, document.body.scrollHeight)"), "scroll")
        await diagnostics.run(page.wait_for_timeout(SCROLL_PAUSE_MS), "scroll")
        # 스크롤 후 동적 콘텐츠 로드 대기 (타임아웃 시 무시)
        try:
            await diagnostics.run(page.wait_for_load_state("networkidle", timeout=3000), "scroll")
        except CrawlBlockedError:
            raise
        except Exception:
            pass

        current_count = await page.locator(item_selector).count()
        logger.debug("스크롤 %s회 - 상품 수: %s", attempt + 1, current_count)

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
    origin: str,
) -> tuple[list[MarketplaceProduct], str | None]:
    """
    단일 상품 카드 Locator에서 상품 목록과 건너뜀 사유를 추출합니다.
    사이트의 model_strategy에 따라 괄호/슬래시/후행 코드를 해석하며,
    대체 모델이 여러 개인 경우 복수 반환합니다.
    성공 시 건너뜀 사유는 None이고, 실패 시 품절/모델명 없음/파싱 오류 중
    하나를 반환합니다.
    상품 URL의 base는 카드를 연 페이지의 origin입니다.
    """
    try:
        if await _is_sold_out_product(item, store_type):
            logger.debug("[%s] 품절 상품 제외", site_name)
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
        product_url = clean_url(raw_url, origin)

        # 모델명 목록 추출 (복수 가능)
        model_names = extract_model_names(product_name, strategy=model_strategy)
        if not model_names:
            logger.debug("[%s] 모델명 없음 (건너뜀): %s", site_name, product_name)
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
        logger.debug("상품 파싱 실패 (건너뜀): %s", type(exc).__name__)
        return [], SKIP_REASON_PARSE_ERROR


async def _read_cards(page, selectors: dict, store_type: str) -> list[dict]:
    """한 페이지의 카드 텍스트를 한 번의 조회로 읽는다."""
    prices = selectors["price"]
    if isinstance(prices, str):
        prices = [prices]
    return await page.evaluate(
        """(spec) => {
            const cards = [...document.querySelectorAll(spec.item)];
            return cards.map((el) => {
                const lines = (el.innerText || "").split(/\\n+/).map((text) => text.trim()).filter(Boolean);
                const soldOut = spec.smartStore && (
                    lines.includes("일시품절")
                    || [...el.querySelectorAll("*")].some((node) => ((node.innerText || node.textContent || "").trim() === "품절"))
                );
                return {
                    soldOut,
                    name: (el.querySelector(spec.name)?.innerText || "").trim(),
                    priceTexts: spec.prices.map((sel) => (el.querySelector(sel)?.innerText || "").trim()),
                    href: el.querySelector(spec.url)?.getAttribute("href") || "",
                };
            });
        }""",
        {
            "item": selectors["item"],
            "name": selectors["name"],
            "prices": prices,
            "url": selectors["url"],
            "smartStore": store_type == "smart_store",
        },
    )


def _product_from_card(
    card: dict, site_name: str, model_strategy: str, crawled_at: str, origin: str,
):
    if card.get("soldOut"):
        return [], SKIP_REASON_SOLD_OUT
    product_name = (card.get("name") or "").strip()
    if not product_name:
        return [], SKIP_REASON_PARSE_ERROR
    price = None
    for price_str in card.get("priceTexts") or []:
        if not price_str:
            continue
        try:
            price = parse_price(price_str)
            break
        except ValueError:
            continue
    if price is None:
        return [], SKIP_REASON_PARSE_ERROR
    model_names = extract_model_names(product_name, strategy=model_strategy)
    if not model_names:
        logger.debug("[%s] 모델명 없음 (건너뜀): %s", site_name, product_name)
        return [], SKIP_REASON_MODEL_MISSING
    return (
        [
            MarketplaceProduct(
                site_name=site_name,
                product_name=product_name,
                model_name=model_name,
                price=price,
                url=clean_url(card.get("href") or "", origin),
                crawled_at=crawled_at,
            )
            for model_name in model_names
        ],
        None,
    )


async def _collect_products(
    page,
    selectors: dict,
    site_name: str,
    store_type: str,
    model_strategy: str,
    crawled_at: str,
    total_count: int,
    diagnostics: NaverDiagnostics,
):
    """카드 전체를 한 번에 읽고, 실패하면 카드별로 다시 읽는다."""
    origin = page_origin(page.url)
    skip_counts = {reason: 0 for reason in SKIP_REASONS}
    diagnostics.data["skip_counts"] = skip_counts
    products: list[MarketplaceProduct] = []
    try:
        raw_cards = await diagnostics.run(_read_cards(page, selectors, store_type), "parse")
    except Exception as exc:
        logger.warning("[%s] 일괄 파싱 실패, 카드별 조회로 진행: %s", site_name, exc)
        raw_cards = None
    if raw_cards is not None:
        for card in raw_cards[:total_count]:
            extracted, skip_reason = _product_from_card(
                card, site_name, model_strategy, crawled_at, origin,
            )
            if extracted:
                products.extend(extracted)
            elif skip_reason is not None:
                skip_counts[skip_reason] += 1
        diagnostics.data["product_count"] = len(products)
        return products, skip_counts

    items = page.locator(selectors["item"])
    for i in range(total_count):
        extracted, skip_reason = await diagnostics.run(
            _extract_product(
                items.nth(i),
                selectors,
                site_name,
                store_type,
                model_strategy,
                crawled_at,
                origin,
            ),
            "parse",
        )
        if extracted:
            products.extend(extracted)
        elif skip_reason is not None:
            skip_counts[skip_reason] += 1
        diagnostics.data["product_count"] = len(products)
    return products, skip_counts


async def _crawl_site(
    context,
    site_info: dict,
    run_id: str | None = None,
) -> list[MarketplaceProduct]:
    """
    단일 네이버 사이트(브랜드스토어 or 스마트스토어)를 크롤링합니다.

    Args:
        context: Playwright BrowserContext
        site_info: config.SEARCH_URLS의 단일 항목
        run_id: 동일 실행의 진단 보고서를 연결하는 식별자

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
        "[%s] 크롤링 시작: %s (model_strategy=%s)", site_name, safe_url(url), model_strategy
    )

    selectors = get_selectors(store_type)
    crawled_at = datetime.now().isoformat(timespec="seconds")

    output_dir = Path(__file__).resolve().parent.parent / config.OUTPUT_DIR
    diagnostics = NaverDiagnostics(None, site_name, url, selectors["item"], output_dir, run_id)
    page = None
    try:
        page = await context.new_page()
        diagnostics.page = page
        diagnostics.attach()
        expected_count = None
        if site_info.get("navigation") == "store_search_ui":
            expected_count = await navigate_public_search(
                page, url, diagnostics, PAGE_LOAD_TIMEOUT,
            )
            if expected_count == 0:
                if diagnostics.data["observed_card_count"] != 0:
                    diagnostics.data["failure_detail"] = "incomplete_search_results"
                    raise NaverCollectionError("collection_error", diagnostics.report_path)
                await diagnostics.check("complete")
                diagnostics.finish("success")
                return []
        else:
            diagnostics.data["entry_mode"] = "direct"
            # 직접 접속 시에는 navigation 타임아웃 뒤에도 실제 목록을 확인한다.
            try:
                await diagnostics.run(
                    page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT), "navigation",
                )
            except CrawlBlockedError:
                raise
            except Exception as exc:
                diagnostics.data["navigation_exception_class"] = type(exc).__name__
                logger.warning("[%s] 페이지 로드 미완료 (%s) — 목록 확인 계속", site_name, type(exc).__name__)
            await diagnostics.check("navigation")

        # 개인 계정 로그인 없이 공개 상품만 수집한다.
        await _wait_for_product_list(
            page,
            selectors["item"],
            site_name,
            diagnostics,
        )

        # 무한스크롤 처리
        total_count = await _scroll_to_bottom(page, selectors["item"], diagnostics)
        logger.info("[%s] 스크롤 완료 - 총 상품 카드: %s개", site_name, total_count)
        if expected_count is not None:
            diagnostics.data["observed_card_count"] = total_count
            if total_count != expected_count:
                diagnostics.data["failure_detail"] = "incomplete_search_results"
                raise NaverCollectionError("collection_error", diagnostics.report_path)

        if total_count == 0:
            raise RuntimeError(f"[{site_name}] 수집된 상품 카드가 없습니다.")

        products, skip_counts = await _collect_products(
            page, selectors, site_name, store_type, model_strategy, crawled_at, total_count, diagnostics,
        )

        skip_count = sum(skip_counts.values())
        skip_details = ", ".join(
            f"{reason}: {skip_counts[reason]}개"
            for reason in SKIP_REASONS
        )
        logger.info(
            "[%s] 파싱 완료 - 성공: %s개, 건너뜀: %s개 (%s)", site_name, len(products), skip_count, skip_details
        )
        await diagnostics.check("complete")
        diagnostics.data["product_count"] = len(products)
        if skip_counts[SKIP_REASON_PARSE_ERROR]:
            diagnostics.data["failure_detail"] = "product_parse_error"
            raise NaverCollectionError("collection_error", diagnostics.report_path)
        diagnostics.finish("success")
        logger.info("[%s] 비로그인 수집 진단: %s", site_name, diagnostics.report_path)
        return products

    except NaverAccessError as exc:
        diagnostics.finish("blocked", exc.reason)
        raise
    except NaverCollectionError as exc:
        diagnostics.finish("failed", exc.reason)
        raise
    except Exception as exc:
        diagnostics.data["exception_class"] = type(exc).__name__
        diagnostics.data["exception_message"] = str(exc)[:500]
        logger.error("[%s] 수집 예외: %s", site_name, exc)
        diagnostics.finish("failed", "collection_error")
        raise NaverCollectionError("collection_error", diagnostics.report_path) from exc
    finally:
        diagnostics.close()
        if page is not None:
            try:
                await asyncio.wait_for(page.close(), timeout=5)
            except Exception as exc:
                logger.warning("[%s] 작업 탭 종료 실패 (%s)", site_name, type(exc).__name__)


async def crawl_marketplace() -> list[MarketplaceProduct]:
    """
    config.SEARCH_URLS의 네이버 사이트를 하나씩 열어 상품을 수집합니다.

    Returns:
        수집된 MarketplaceProduct 전체 목록.
        config.SEARCH_URLS 가 비어있거나 모든 항목의 url 이 누락이면 [] 반환.

    Raises:
        RuntimeError: 사이트 접근 제한 또는 수집 실패로 후속 사이트를 중단한 경우
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
                "[%s] url 누락 — 해당 사이트 건너뜀", site_info.get('site_name', '?')
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

    prepared = [(site, load_checkpoint(site)) for site in valid_sites]
    if all(products is not None for _, products in prepared):
        logger.info("모든 네이버 스토어의 오늘 완료 결과 재사용 — 브라우저 실행 생략")
        return [product for _, products in prepared for product in products]

    run_id = uuid4().hex
    all_products: list[MarketplaceProduct] = []
    async with create_browser() as context:
        attempted = False
        for site_info, cached in prepared:
            if cached is not None:
                logger.info("[%s] 오늘 완료된 상품 %s개 재사용", site_info['site_name'], len(cached))
                all_products.extend(cached)
                continue
            if attempted:
                await pause(config.SITE_DELAY_MIN_SEC, config.SITE_DELAY_MAX_SEC, "다음 스토어 이동 전 휴식")
            attempted = True
            try:
                products = await _crawl_site(context, site_info, run_id)
                save_checkpoint(site_info, products)
                all_products.extend(products)
            except (CrawlBlockedError, NaverCollectionError):
                raise
            except Exception as exc:
                # 목록 확인 실패 후 다른 스토어로 연속 접근하지 않는다.
                raise RuntimeError(f"[{site_info['site_name']}] 수집 실패 — 후속 스토어 중단") from exc

    logger.info("전체 수집 완료 - 총 %s개 상품", len(all_products))
    return all_products
