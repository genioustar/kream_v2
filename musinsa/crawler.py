"""
musinsa/crawler.py
무신사 브랜드 PLP / 아울렛 랭킹 API 크롤러.

무신사 브랜드/캠페인 페이지는 Next.js 화면 렌더링 후
api2/dp/v2/plp/goods API로 상품을 페이지 단위 로드한다.
첫 응답의 pagination.nextPageUrl에는 다음 페이지 접근에 필요한 hmacId가 포함되므로,
page 번호를 직접 조립하지 않고 nextPageUrl을 그대로 따라간다.
hasNext가 참인데 nextPageUrl이 비어 있으면 RuntimeError로 중단한다.
다음 주소가 이미 방문한 주소이거나, hasNext가 참인 채 MAX_BRAND_PAGES에
도달하면 RuntimeError로 중단하고 부분 결과를 반환하지 않는다.

아울렛 랭킹 페이지는 client.musinsa.com의 ranking API를 사용하며,
응답의 link.next를 따라 다음 순위를 수집한다.

URL 경로가 /campaign/ 으로 시작하면 Kream 브랜드 할인 모드다. 캠페인 페이지에는 전체 상품 목록이 없어
config.MUSINSA_KREAM_BRANDS 의 브랜드 PLP 를 sortCode=DISCOUNT_RATE, category=config.MUSINSA_KREAM_CATEGORY(신발)로 받고, 할인율이
config.MUSINSA_MIN_DISCOUNT_RATE 미만인 항목이 나오면 그 브랜드 수집을 멈춘다.
"""
import asyncio
import json
import logging
from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import config
from common.model_names import extract_model_names
from common.models import MarketplaceProduct
from common.pacing import check_response

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
SITE_NAME            = "musinsa"
BRAND_API_URL        = "https://api.musinsa.com/api2/dp/v2/plp/goods"
RANKING_API_URL      = "https://client.musinsa.com/api/home/web/v5/pans/ranking"
DEFAULT_PAGE_SIZE    = 100
MAX_RANKING_PAGES    = 20
# 페이지당 100개이므로 최대 1만 개. 현재 브랜드관 규모 대비 충분한 안전 상한.
MAX_BRAND_PAGES      = 100
CAMPAIGN_PATH_PREFIX = "/campaign/"
# PLP API sortCode 값. saleRate 가 큰 상품부터 내려온다.
DISCOUNT_SORT_CODE   = "DISCOUNT_RATE"
REQUEST_TIMEOUT_SEC  = 20
REQUEST_DELAY_SEC    = 3.0
USER_AGENT           = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _extract_brand_slug(page_url: str) -> str | None:
    """
    /brand/{brand}/... 형태 URL에서 brand slug를 추출한다.

    예:
      https://www.musinsa.com/brand/adidas/campaign/... -> adidas
      https://www.musinsa.com/brand/adidas/products?... -> adidas
    """
    path_parts = [p for p in urlparse(page_url).path.split("/") if p]
    try:
        brand_index = path_parts.index("brand")
    except ValueError:
        return None

    if brand_index + 1 >= len(path_parts):
        return None
    return path_parts[brand_index + 1]


def _is_ranking_url(page_url: str) -> bool:
    """무신사 main 랭킹 URL인지 판별한다."""
    path = urlparse(page_url).path.rstrip("/")
    return path.endswith("/ranking") and "/main/" in path


def _is_campaign_url(page_url: str) -> bool:
    """무신사 캠페인 URL(/campaign/...)인지 판별한다."""
    return urlparse(page_url).path.startswith(CAMPAIGN_PATH_PREFIX)


def _build_discount_sorted_brand_url(brand: str) -> str:
    """브랜드 상품 목록을 할인율 내림차순·신발 카테고리로 보는 페이지 URL을 만든다."""
    query = urlencode({
        "sortCode": DISCOUNT_SORT_CODE,
        "category": config.MUSINSA_KREAM_CATEGORY,
    })
    return f"https://www.musinsa.com/brand/{brand}/products?{query}"


def _build_referer(page_url: str, brand: str) -> str:
    """캠페인 URL을 API 호출 referer로 쓰는 브랜드 상품 URL 형태로 정규화한다."""
    parsed = urlparse(page_url)
    query = f"?{parsed.query}" if parsed.query else ""
    return f"https://www.musinsa.com/brand/{brand}/products{query}"


def _build_first_api_url(page_url: str, brand: str, page_size: int) -> str:
    """사용자 입력 URL의 query를 보존해 첫 PLP API URL을 만든다."""
    parsed = urlparse(page_url)
    query = parse_qs(parsed.query)
    params = {
        key: values[-1]
        for key, values in query.items()
        if values
    }
    params.update({
        "brand": brand,
        "page": "1",
        "size": str(page_size),
        "caller": "FLAGSHIP",
    })
    params.setdefault("gf", "A")
    return f"{BRAND_API_URL}?{urlencode(params)}"


def _build_ranking_api_url(page_url: str) -> str:
    """아울렛 랭킹 페이지 query를 ranking API URL로 변환한다."""
    parsed = urlparse(page_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    params = {
        key: values[-1]
        for key, values in query.items()
        if values
    }
    return f"{RANKING_API_URL}?{urlencode(params)}"


def _fetch_json(url: str, referer: str) -> dict:
    """
    무신사 API JSON을 동기 HTTP 요청으로 가져온다.

    HTTP 403/429는 CrawlBlockedError로 바꿔 전파하고, 다른 HTTP 오류는 그대로 전파한다.
    """
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
            "Origin": "https://www.musinsa.com",
            "Referer": referer,
        },
    )
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        check_response(exc)
        raise


async def _fetch_json_async(url: str, referer: str) -> dict:
    """blocking urllib 요청을 async 크롤러에서 안전하게 실행한다."""
    return await asyncio.to_thread(_fetch_json, url, referer)


def _resolve_price(item: dict) -> int | None:
    """
    무신사 카드에 표시되는 최종가를 반환한다.

    API의 finalPrice는 쿠폰가가 있는 경우 DOM 가격과 일치한다.
    없으면 price/couponPrice 순으로 폴백한다.
    """
    for key in ("finalPrice", "couponPrice", "price"):
        value = item.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


def _resolve_sale_rate(item: dict) -> int:
    """
    상품의 할인율(%)을 반환한다.

    saleRate 가 없거나 int 가 아니면 0 으로 본다. 할인 여부를 확인할 수 없는 상품이 기준을 통과하지 않게 한다.
    """
    value = item.get("saleRate")
    return value if isinstance(value, int) else 0


def _build_marketplace_products(
    *,
    name: str,
    url: str | None,
    price: int | None,
    is_sold_out: bool,
    crawled_at: str,
) -> list[MarketplaceProduct]:
    """API 종류와 무관한 무신사 상품 필드를 공통 포맷으로 변환한다."""
    name = name.strip()
    if is_sold_out:
        logger.debug("품절 상품 제외: %r", name)
        return []

    model_names = extract_model_names(name, strategy="slash_first")

    if not name or not url or price is None:
        logger.debug(
            "필수 정보 누락 — 건너뜀: name=%r, url=%r, price=%r", name, url, price
        )
        return []

    if not model_names:
        logger.debug("모델코드 추출 실패 — 건너뜀: %r", name)
        return []

    return [
        MarketplaceProduct(
            site_name=SITE_NAME,
            product_name=name,
            model_name=model_name,
            price=price,
            url=url,
            crawled_at=crawled_at,
        )
        for model_name in model_names
    ]


def _to_marketplace_products(item: dict, crawled_at: str) -> list[MarketplaceProduct]:
    """브랜드 PLP API 상품 항목을 MarketplaceProduct 목록으로 변환한다."""
    return _build_marketplace_products(
        name=item.get("goodsName") or "",
        url=item.get("goodsLinkUrl"),
        price=_resolve_price(item),
        is_sold_out=bool(item.get("isSoldOut")),
        crawled_at=crawled_at,
    )


def _to_ranking_marketplace_products(
    item: dict,
    crawled_at: str,
) -> list[MarketplaceProduct]:
    """랭킹 API PRODUCT_COLUMN 항목을 MarketplaceProduct 목록으로 변환한다."""
    info = item.get("info") or {}
    on_click = item.get("onClick") or {}
    price = info.get("finalPrice")
    return _build_marketplace_products(
        name=info.get("productName") or "",
        url=on_click.get("url"),
        price=price if isinstance(price, int) and price > 0 else None,
        is_sold_out=bool(info.get("isSoldOut")),
        crawled_at=crawled_at,
    )


def _extract_ranking_items(data: dict) -> list[dict]:
    """랭킹 API modules에서 PRODUCT_COLUMN 항목만 평탄화한다."""
    modules = (data.get("data") or {}).get("modules") or []
    return [
        item
        for module in modules
        if module.get("type") == "MULTICOLUMN"
        for item in (module.get("items") or [])
        if item.get("type") == "PRODUCT_COLUMN"
    ]


async def _crawl_brand_products(
    sale_url: str,
    crawled_at: str,
    min_discount_rate: int | None = None,
) -> list[MarketplaceProduct]:
    """
    브랜드/캠페인 URL의 PLP API 페이지를 끝까지 수집한다.

    min_discount_rate 를 주면 목록이 할인율 내림차순이라는 전제로, 할인율이 그 값 미만인
    항목이 나오는 순간 그 항목과 이후 항목을 버리고 다음 페이지를 요청하지 않는다.
    """
    brand = _extract_brand_slug(sale_url)
    if not brand:
        logger.warning("MUSINSA_SALE_URL에서 브랜드 slug를 찾을 수 없습니다: %s", sale_url)
        return []

    referer = _build_referer(sale_url, brand)
    next_url = _build_first_api_url(sale_url, brand, DEFAULT_PAGE_SIZE)

    logger.info("무신사 브랜드 상품 크롤링 시작: brand=%s, url=%s", brand, sale_url)

    products: list[MarketplaceProduct] = []
    seen_keys: set[tuple[str, str]] = set()
    seen_page_urls: set[str] = set()
    page_num = 0
    stopped_sale_rate: int | None = None
    stopped_at_first_item = False

    while next_url:
        seen_page_urls.add(next_url)
        page_num += 1
        try:
            data = await _fetch_json_async(next_url, referer)
        except Exception as exc:
            logger.error("무신사 API 요청 실패 (page=%s): %s", page_num, exc)
            raise

        payload = data.get("data") or {}
        items = payload.get("list") or []
        pagination = payload.get("pagination") or {}

        added = 0
        for item_index, item in enumerate(items):
            if min_discount_rate is not None:
                sale_rate = _resolve_sale_rate(item)
                if sale_rate < min_discount_rate:
                    stopped_sale_rate = sale_rate
                    stopped_at_first_item = page_num == 1 and item_index == 0
                    break
            for product in _to_marketplace_products(item, crawled_at):
                key = (product.url or "", product.model_name)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                products.append(product)
                added += 1

        logger.info(
            "무신사 page=%s 수집 %s개 / 누적 %s개 (API 항목 %s개, total=%s)", pagination.get('page', page_num), added, len(products), len(items), pagination.get('totalCount')
        )

        if stopped_sale_rate is not None or not pagination.get("hasNext"):
            break

        next_url = pagination.get("nextPageUrl")
        if next_url is None or next_url == "":
            raise RuntimeError(f"무신사 다음 페이지 주소가 없습니다 (page={page_num})")
        if next_url in seen_page_urls:
            raise RuntimeError(f"무신사 다음 페이지 주소가 반복됩니다 (page={page_num})")
        if page_num >= MAX_BRAND_PAGES:
            raise RuntimeError(
                f"무신사 브랜드 최대 페이지({MAX_BRAND_PAGES})에 도달했지만 다음 페이지가 있습니다 (page={page_num})"
            )
        await asyncio.sleep(REQUEST_DELAY_SEC)

    if stopped_at_first_item:
        logger.warning(
            "무신사 브랜드 첫 항목에서 바로 중단되어 0개 수집 — brand=%s, 첫 항목 saleRate=%s%%, 기준 %s%%. "
            "정렬이 적용되지 않았거나 할인 상품 없음",
            brand, stopped_sale_rate, min_discount_rate,
        )
    if stopped_sale_rate is None:
        logger.info("무신사 브랜드 크롤링 완료 — brand=%s, 총 %s개", brand, len(products))
    else:
        logger.info(
            "무신사 브랜드 크롤링 완료 — brand=%s, 총 %s개 (할인율 %s%% 에서 중단, 기준 %s%% 이상)",
            brand, len(products), stopped_sale_rate, min_discount_rate,
        )
    return products


async def _crawl_kream_brand_discount_products(
    crawled_at: str,
) -> list[MarketplaceProduct]:
    """
    MUSINSA_KREAM_BRANDS 를 순서대로 할인율 내림차순으로 수집한다.

    캠페인 페이지에는 전체 상품 목록이 없어 URL 을 직접 긁지 않는다.
    """
    brands = config.MUSINSA_KREAM_BRANDS
    min_discount_rate = config.MUSINSA_MIN_DISCOUNT_RATE
    if not brands:
        logger.warning("config.MUSINSA_KREAM_BRANDS 가 비어 있어 수집할 브랜드가 없습니다")
        return []

    logger.info(
        "무신사 Kream 브랜드 할인 모드 시작: brands=%s, 최소 할인율=%s%%",
        ", ".join(brands), min_discount_rate,
    )

    products: list[MarketplaceProduct] = []
    for index, brand in enumerate(brands):
        if index > 0:
            await asyncio.sleep(REQUEST_DELAY_SEC)
        products.extend(await _crawl_brand_products(
            _build_discount_sorted_brand_url(brand),
            crawled_at,
            min_discount_rate=min_discount_rate,
        ))

    logger.info("무신사 Kream 브랜드 할인 모드 완료 — 브랜드 %s개, 총 %s개", len(brands), len(products))
    return products


async def _crawl_ranking_products(
    sale_url: str,
    crawled_at: str,
) -> list[MarketplaceProduct]:
    """main 랭킹 URL의 API link.next를 따라 상품 순위를 수집한다."""
    next_url: str | None = _build_ranking_api_url(sale_url)
    seen_page_urls: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()
    products: list[MarketplaceProduct] = []
    page_num = 0

    logger.info("무신사 랭킹 상품 크롤링 시작: url=%s", sale_url)

    while (
        next_url
        and next_url not in seen_page_urls
        and page_num < MAX_RANKING_PAGES
    ):
        seen_page_urls.add(next_url)
        page_num += 1
        try:
            data = await _fetch_json_async(next_url, sale_url)
        except Exception as exc:
            logger.error("무신사 랭킹 API 요청 실패 (page=%s): %s", page_num, exc)
            raise

        items = _extract_ranking_items(data)
        if not items:
            logger.info("무신사 랭킹 page=%s API 항목 없음 → 종료", page_num)
            break

        added = 0
        for item in items:
            for product in _to_ranking_marketplace_products(item, crawled_at):
                key = (product.url or "", product.model_name)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                products.append(product)
                added += 1

        logger.info(
            "무신사 랭킹 page=%s 수집 %s개 / 누적 %s개 (API 항목 %s개)", page_num, added, len(products), len(items)
        )

        next_url = (data.get("link") or {}).get("next")
        if next_url:
            await asyncio.sleep(REQUEST_DELAY_SEC)

    if page_num >= MAX_RANKING_PAGES and next_url:
        logger.warning(
            "무신사 랭킹 최대 페이지(%s) 도달 — 수집 종료", MAX_RANKING_PAGES
        )

    logger.info("무신사 랭킹 크롤링 완료 — 총 %s개", len(products))
    return products


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

async def crawl_musinsa() -> list[MarketplaceProduct]:
    """
    config.MUSINSA_SALE_URL의 브랜드 PLP, main 랭킹, 캠페인(Kream 브랜드 할인) 상품을 크롤링한다.

    Returns:
        site_name='musinsa' 인 MarketplaceProduct 리스트.
        config.MUSINSA_SALE_URL 미설정/빈 값이면 즉시 [] 반환한다.

    Raises:
        수집 도중 실패하면 예외가 전파되며 부분 결과를 반환하지 않는다.
        HTTP 403/429는 CrawlBlockedError로 전파된다.
        브랜드 응답이 hasNext인데 nextPageUrl이 비어 있거나, 다음 주소가 반복되거나,
        hasNext인 채 MAX_BRAND_PAGES에 도달하면 RuntimeError로 전파된다. 부분 결과는 반환하지 않는다.
    """
    sale_url = getattr(config, "MUSINSA_SALE_URL", "") or ""
    if not sale_url.strip():
        logger.info("config.MUSINSA_SALE_URL 미설정 — 무신사 크롤링 건너뜀")
        return []

    crawled_at = datetime.now().isoformat(timespec="seconds")
    if _is_campaign_url(sale_url):
        return await _crawl_kream_brand_discount_products(crawled_at)
    if _is_ranking_url(sale_url):
        return await _crawl_ranking_products(sale_url, crawled_at)
    if _extract_brand_slug(sale_url):
        return await _crawl_brand_products(sale_url, crawled_at)

    logger.warning(
        "지원하지 않는 MUSINSA_SALE_URL 형식입니다. /brand/{slug}, /main/.../ranking, /campaign/... URL을 사용하세요: %s", sale_url
    )
    return []
