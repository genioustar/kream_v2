"""
kream/parser.py
Kream 검색 결과 페이지 및 상세 페이지 파싱 유틸.

DOM 조작 없이 순수하게 문자열만 처리하는 함수 모음.
CSS 셀렉터 상수도 이 파일에서 중앙 관리한다.
"""
import re


# ---------------------------------------------------------------------------
# CSS 셀렉터 상수
# ---------------------------------------------------------------------------
# Kream은 React SPA이므로 클래스명이 변경될 수 있다.
# 실제 동작 확인 후 필요 시 수정할 것.
#
# 검색 결과 페이지 (https://kream.co.kr/search?keyword=...)
#   - 상품 카드 컨테이너 : .search_result_item
#   - 카드 내 상품명     : .product_card .name
#   - 카드 내 즉시구매가 : .product_card .amount
#   - 카드 내 상세 링크  : .product_card a
#
# 상세 페이지 (https://kream.co.kr/products/...)
#   - 거래량             : .detail_wrap .trade_count
#   - 즉시구매가(재확인) : .buy_now_price .amount

KREAM_SELECTORS: dict[str, str] = {
    # 검색 결과 페이지: 카드 자체가 <a> 태그
    "search_results": "a.product_card[href*='/products/']",
}


# ---------------------------------------------------------------------------
# 가격 파싱
# ---------------------------------------------------------------------------

def parse_kream_price(price_str: str) -> int:
    """
    Kream 가격 문자열에서 정수를 추출한다.

    예)
        "215,000원"  -> 215000
        "₩215,000"  -> 215000
        "215000"     -> 215000

    Args:
        price_str: 원시 가격 문자열

    Returns:
        가격 (정수, 원 단위)

    Raises:
        ValueError: 숫자를 추출할 수 없는 경우
    """
    digits = re.sub(r"[^\d]", "", price_str)
    if not digits:
        raise ValueError(f"가격을 파싱할 수 없습니다: {price_str!r}")
    return int(digits)


# ---------------------------------------------------------------------------
# 거래량 파싱
# ---------------------------------------------------------------------------

def parse_trade_count(count_str: str) -> int:
    """
    Kream 거래량 문자열에서 정수를 추출한다.

    예)
        "· 거래 1.3만"  -> 13000
        "· 거래 71.6만" -> 716000
        "거래 342건"    -> 342
        "1,234개"       -> 1234
        "거래없음"      -> 0

    Args:
        count_str: 원시 거래량 문자열

    Returns:
        거래량 (정수). 숫자가 없으면 0.
    """
    m = re.search(r"([\d,]+(?:\.\d+)?)만", count_str)
    if m:
        return int(float(m.group(1).replace(",", "")) * 10000)
    digits = re.sub(r"[^\d]", "", count_str)
    if not digits:
        return 0
    return int(digits)
