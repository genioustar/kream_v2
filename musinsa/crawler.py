"""
musinsa/crawler.py
무신사 브랜드 PLP / 아울렛 랭킹 API 크롤러.

무신사 브랜드/캠페인 페이지는 Next.js 화면 렌더링 후
api2/dp/v2/plp/goods API로 상품을 페이지 단위 로드한다.
첫 응답의 pagination.nextPageUrl에는 다음 페이지 접근에 필요한 hmacId가 포함되므로,
page 번호를 직접 조립하지 않고 nextPageUrl을 그대로 따라간다.

아울렛 랭킹 페이지는 client.musinsa.com의 ranking API를 사용하며,
응답의 link.next를 따라 다음 순위를 수집한다.
"""
import asyncio
import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import config
from common.logger import get_logger
from common.model_names import extract_model_names
from common.models import MarketplaceProduct

logger = get_logger("musinsa.crawler")

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
SITE_NAME            = "musinsa"
BRAND_API_URL        = "https://api.musinsa.com/api2/dp/v2/plp/goods"
RANKING_API_URL      = "https://client.musinsa.com/api/home/web/v5/pans/ranking"
DEFAULT_PAGE_SIZE    = 100
MAX_RANKING_PAGES    = 20
REQUEST_TIMEOUT_SEC  = 20
REQUEST_DELAY_SEC    = 0.2
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
    """무신사 API JSON을 동기 HTTP 요청으로 가져온다."""
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
    with urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as response:
        return json.loads(response.read().decode("utf-8"))


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
        logger.debug(f"품절 상품 제외: {name!r}")
        return []

    model_names = extract_model_names(name, strategy="slash_first")

    if not name or not url or price is None:
        logger.debug(
            f"필수 정보 누락 — 건너뜀: "
            f"name={name!r}, url={url!r}, price={price!r}"
        )
        return []

    if not model_names:
        logger.debug(f"모델코드 추출 실패 — 건너뜀: {name!r}")
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
) -> list[MarketplaceProduct]:
    """브랜드/캠페인 URL의 PLP API 페이지를 끝까지 수집한다."""
    brand = _extract_brand_slug(sale_url)
    if not brand:
        logger.warning(f"MUSINSA_SALE_URL에서 브랜드 slug를 찾을 수 없습니다: {sale_url}")
        return []

    referer = _build_referer(sale_url, brand)
    next_url = _build_first_api_url(sale_url, brand, DEFAULT_PAGE_SIZE)

    logger.info(f"무신사 브랜드 상품 크롤링 시작: brand={brand}, url={sale_url}")

    products: list[MarketplaceProduct] = []
    seen_keys: set[tuple[str, str]] = set()
    page_num = 0

    while next_url:
        page_num += 1
        try:
            data = await _fetch_json_async(next_url, referer)
        except Exception as exc:
            logger.warning(f"무신사 API 요청 실패 (page={page_num}): {exc}")
            break

        payload = data.get("data") or {}
        items = payload.get("list") or []
        pagination = payload.get("pagination") or {}

        added = 0
        for item in items:
            for product in _to_marketplace_products(item, crawled_at):
                key = (product.url or "", product.model_name)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                products.append(product)
                added += 1

        logger.info(
            f"무신사 page={pagination.get('page', page_num)} "
            f"수집 {added}개 / 누적 {len(products)}개 "
            f"(API 항목 {len(items)}개, total={pagination.get('totalCount')})"
        )

        if not pagination.get("hasNext"):
            break

        next_url = pagination.get("nextPageUrl")
        if next_url:
            await asyncio.sleep(REQUEST_DELAY_SEC)

    logger.info(f"무신사 브랜드 크롤링 완료 — 총 {len(products)}개")
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

    logger.info(f"무신사 랭킹 상품 크롤링 시작: url={sale_url}")

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
            logger.warning(f"무신사 랭킹 API 요청 실패 (page={page_num}): {exc}")
            break

        items = _extract_ranking_items(data)
        if not items:
            logger.info(f"무신사 랭킹 page={page_num} API 항목 없음 → 종료")
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
            f"무신사 랭킹 page={page_num} 수집 {added}개 / "
            f"누적 {len(products)}개 (API 항목 {len(items)}개)"
        )

        next_url = (data.get("link") or {}).get("next")
        if next_url:
            await asyncio.sleep(REQUEST_DELAY_SEC)

    if page_num >= MAX_RANKING_PAGES and next_url:
        logger.warning(
            f"무신사 랭킹 최대 페이지({MAX_RANKING_PAGES}) 도달 — 수집 종료"
        )

    logger.info(f"무신사 랭킹 크롤링 완료 — 총 {len(products)}개")
    return products


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

async def crawl_musinsa() -> list[MarketplaceProduct]:
    """
    config.MUSINSA_SALE_URL의 브랜드 PLP 또는 main 랭킹 상품을 크롤링한다.

    Returns:
        site_name='musinsa' 인 MarketplaceProduct 리스트.
        config.MUSINSA_SALE_URL 미설정/빈 값이면 즉시 [] 반환한다.
    """
    sale_url = getattr(config, "MUSINSA_SALE_URL", "") or ""
    if not sale_url.strip():
        logger.info("config.MUSINSA_SALE_URL 미설정 — 무신사 크롤링 건너뜀")
        return []

    crawled_at = datetime.now().isoformat(timespec="seconds")
    if _is_ranking_url(sale_url):
        return await _crawl_ranking_products(sale_url, crawled_at)
    if _extract_brand_slug(sale_url):
        return await _crawl_brand_products(sale_url, crawled_at)

    logger.warning(
        "지원하지 않는 MUSINSA_SALE_URL 형식입니다. "
        f"/brand/{{slug}} 또는 /main/.../ranking URL을 사용하세요: {sale_url}"
    )
    return []


# ---------------------------------------------------------------------------
# 단독 실행 진입점
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    async def _main():
        products = await crawl_musinsa()
        if not products:
            print("수집된 상품이 없습니다.")
            return

        today = date.today().strftime("%Y%m%d")
        out_dir = Path(config.OUTPUT_DIR) / today
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "musinsa_products.json"
        out_path.write_text(
            json.dumps([asdict(p) for p in products], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n총 {len(products)}개 저장 → {out_path}")

    asyncio.run(_main())
