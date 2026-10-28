"""
kream/parser.py
Kream 검색 결과 페이지 및 상세 페이지 파싱 유틸.

DOM 조작 없이 순수하게 문자열만 처리하는 함수 모음.
CSS 셀렉터 상수도 이 파일에서 중앙 관리한다.
"""
import re
import unicodedata
from html.parser import HTMLParser


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
    # 상세 페이지: "모델번호 {값}" 형식의 문단 (반응형 DOM 중복 가능)
    "detail_model_number": "p:has-text('모델번호')",
}


# ---------------------------------------------------------------------------
# 모델번호 파싱·비교
# ---------------------------------------------------------------------------

def parse_kream_model_number(text: str) -> str | None:
    """Kream 상세 정보의 ``모델번호 {값}`` 문단에서 모델번호를 추출한다."""
    match = re.fullmatch(r"\s*모델번호\s*[:：]?\s*(.*?)\s*", text, flags=re.DOTALL)
    if not match:
        return None
    model_number = match.group(1).strip()
    return model_number or None


class _KreamDetailModelNumberHTMLParser(HTMLParser):
    """상세 HTML의 문단 텍스트 중 모델번호 값을 찾는 내부 파서."""

    def __init__(self) -> None:
        super().__init__()
        self._in_paragraph = False
        self._text_parts: list[str] = []
        self.model_number: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.model_number is None and tag == "p":
            self._in_paragraph = True
            self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_paragraph:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "p" or not self._in_paragraph:
            return

        self._in_paragraph = False
        self.model_number = parse_kream_model_number("".join(self._text_parts))
        self._text_parts = []


def parse_kream_model_number_html(detail_html: str) -> str | None:
    """Playwright가 받은 Kream 상세 HTML에서 실제 모델번호를 추출한다."""
    parser = _KreamDetailModelNumberHTMLParser()
    parser.feed(detail_html)
    return parser.model_number


def normalize_model_number(model_number: str) -> str:
    """표기 차이 비교용으로 모델번호의 폭·대소문자·구분자를 정규화한다."""
    normalized = unicodedata.normalize("NFKC", model_number).casefold()
    return re.sub(r"[\s\-_.]+", "", normalized)


def model_numbers_match(expected: str, actual: str) -> bool:
    """두 모델번호가 정규화 후 전체 문자열 기준으로 같은지 반환한다."""
    normalized_expected = normalize_model_number(expected)
    normalized_actual = normalize_model_number(actual)
    return bool(normalized_expected and normalized_actual) and (
        normalized_expected == normalized_actual
    )


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
