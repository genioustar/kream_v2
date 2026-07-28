import re
from typing import Optional


# ---------------------------------------------------------------------------
# CSS 셀렉터
# ---------------------------------------------------------------------------

# brand.naver.com (브랜드스토어)
# - 상품 카드: .product_list li  또는  ul.product_list > li
# - 상품명:    .product_info .product_name  또는  strong.product_name
# - 가격:      .product_info .price .num  또는  em.num
# - URL:       a.product_name_link  또는  a[href*="/products/"]

# smartstore.naver.com (스마트스토어 검색결과)
# - 상품 카드: ul.bd_lst_ul > li  또는  .thumbnail_product
# - 상품명:    .tit_inner  또는  .goods_nm
# - 가격:      .price_num  또는  strong.price
# - URL:       a.goods_lst_thumb  또는  a[href*="/products/"]

SELECTORS: dict[str, dict] = {
    "brand_store": {
        # 개별 상품 카드 - href="/products/" 링크를 포함한 li
        "item": "li:has(a[href*='/products/'])",
        # 상품명 - strong 태그 (두 스토어 공통 구조)
        "name": "strong",
        # 가격 셀렉터 fallback 후보 리스트 (앞에서부터 순서대로 시도)
        "price": [
            "span.zIK_uvWc6D",
            "[class*='price'] em",
            "[class*='price'] strong",
            "[class*='num']",
            "em[class]",
        ],
        # 상품 링크
        "url": "a[href*='/products/']",
    },
    "smart_store": {
        # 스마트스토어도 동일한 구조 사용
        "item": "li:has(a[href*='/products/'])",
        # 상품명
        "name": "strong",
        # 가격 셀렉터 fallback 후보 리스트 (앞에서부터 순서대로 시도)
        "price": [
            "span.zIK_uvWc6D",
            "[class*='price'] em",
            "[class*='price'] strong",
            "[class*='num']",
            "em[class]",
        ],
        # 상품 링크
        "url": "a[href*='/products/']",
    },
}


def get_selectors(store_type: str) -> dict[str, str]:
    """스토어 타입에 맞는 CSS 셀렉터 딕셔너리를 반환합니다."""
    if store_type not in SELECTORS:
        raise ValueError(f"알 수 없는 스토어 타입: {store_type!r}. 허용값: {list(SELECTORS)}")
    return SELECTORS[store_type]


# ---------------------------------------------------------------------------
# 가격 파싱
# ---------------------------------------------------------------------------

def parse_price(price_str: str) -> int:
    """
    가격 문자열에서 숫자만 추출하여 int로 반환합니다.

    예) "123,456원" -> 123456
        "123,456" -> 123456
        "₩ 123,456" -> 123456
    """
    digits = re.sub(r"[^\d]", "", price_str)
    if not digits:
        raise ValueError(f"가격을 파싱할 수 없습니다: {price_str!r}")
    return int(digits)


# ---------------------------------------------------------------------------
# 편의 함수
# ---------------------------------------------------------------------------

def clean_url(raw_url: Optional[str], base: str = "https://www.naver.com") -> Optional[str]:
    """상대 URL을 절대 URL로 변환합니다."""
    if not raw_url:
        return None
    if raw_url.startswith("http"):
        return raw_url
    if raw_url.startswith("//"):
        return "https:" + raw_url
    if raw_url.startswith("/"):
        return base.rstrip("/") + raw_url
    return raw_url
