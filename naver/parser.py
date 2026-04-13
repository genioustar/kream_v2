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
# 모델명 추출
# ---------------------------------------------------------------------------

def _is_model_like(s: str) -> bool:
    """괄호 안 내용이 모델명다운지 판단 (영문+숫자 포함, 한글 없음)."""
    s = s.strip()
    if not s:
        return False
    if re.search(r"[가-힣]", s):  # 한글 포함이면 모델명 아님
        return False
    return bool(re.search(r"[A-Za-z0-9]", s))


def _is_model_token(token: str) -> bool:
    """
    개별 토큰이 모델명 코드인지 판단합니다.

    조건:
    - 한글 없음
    - 숫자를 반드시 포함
    - 패턴: 대문자/숫자로 시작, 이후 영숫자 및 하이픈
    """
    if not token:
        return False
    if re.search(r"[가-힣]", token):
        return False
    if not re.search(r"[0-9]", token):
        return False
    return bool(re.match(r"^[A-Z0-9][A-Za-z0-9-]*$", token))


def _extract_model_from_tokens(product_name: str) -> Optional[str]:
    """
    상품명을 공백으로 분리하여 끝에서부터 모델코드 토큰을 찾습니다.

    마지막 한글 토큰 이후에 연속으로 등장하는 모델코드 토큰들을 수집하여
    공백으로 결합해 반환합니다. 찾지 못하면 None을 반환합니다.

    예시:
    - "뉴발란스 509 블랙 U509163"  → "U509163"
    - "나이키 W 에어 IB5824-001"   → "IB5824-001"
    - "드래곤 디퓨전 트리플 4종"    → None (숫자만 있는 '4종'은 한글 포함)
    """
    tokens = product_name.strip().split()
    if not tokens:
        return None

    # 끝에서부터 모델코드 토큰을 수집
    model_tokens: list[str] = []
    for token in reversed(tokens):
        if _is_model_token(token):
            model_tokens.append(token)
        else:
            break

    if not model_tokens:
        return None

    # 원래 순서로 복원
    model_tokens.reverse()
    return " ".join(model_tokens)


def _is_valid_model_name(model_name: str) -> bool:
    """
    유효한 모델명인지 검증한다.

    조건: 한글이 없고 영문자(A-Za-z) 또는 숫자(0-9)를 포함해야 한다.

    예) "DZ4524" → True, "IB5824-001" → True
        "OORIGINAL-BLACK" → True
        "12345" → True (숫자만도 허용)
        "디젤 시계" → False (한글 포함)
    """
    if re.search(r"[가-힣]", model_name):
        return False
    return bool(re.search(r"[A-Za-z0-9]", model_name))


def extract_model_names(product_name: str) -> list[str]:
    """
    상품명에서 모델명 목록을 추출합니다.

    규칙:
    1. () 안의 내용에서 모델명을 찾습니다.
    2. "/" 구분자가 있으면 분리하여 여러 모델로 등록합니다.
    3. () 여러 개인 경우 영문+숫자 패턴의 것을 우선 선택합니다.
    4. 괄호에서 모델명을 찾지 못한 경우, 상품명 끝부분 토큰에서 모델코드 패턴을 탐색합니다.
    5. 어디에서도 유효한 모델명(영문+숫자 조합)을 찾지 못하면 빈 리스트를 반환합니다.

    예시:
    - "나이키 에어맥스 (MR530CC)"                            → ["MR530CC"]
    - "양말 (SD1501WT3/SD1501BK3)"                           → ["SD1501WT3", "SD1501BK3"]
    - "나이키 삭스 (6팩) (SX6897-100)"                       → ["SX6897-100"]
    - "뉴발란스 509 블랙 U509163"                            → ["U509163"]
    - "나이키 W 에어 슈퍼플라이 메탈릭 실버 앤 블랙 IB5824-001" → ["IB5824-001"]
    - "드래곤 디퓨전 트리플 지갑 3종"                         → [] (유효 모델명 없음)
    """
    bracket_contents = re.findall(r"\(([^)]+)\)", product_name)

    if not bracket_contents:
        # 괄호 없음 -> 토큰 기반 추출 시도
        token_model = _extract_model_from_tokens(product_name)
        if token_model and _is_valid_model_name(token_model):
            return [token_model[:80]]
        return []

    # 사이즈 표기 제거 (예: "size245-285", "Size230", "SIZE 250~280")
    _size_re = re.compile(r"^size\s*[\d]", re.IGNORECASE)
    bracket_contents = [c for c in bracket_contents if not _size_re.match(c.strip())]

    # 모델명 후보만 필터링
    model_candidates = [c.strip() for c in bracket_contents if _is_model_like(c)]

    if not model_candidates:
        # 괄호는 있지만 모델명다운 것이 없음 -> 토큰 기반 추출 시도
        token_model = _extract_model_from_tokens(product_name)
        if token_model and _is_valid_model_name(token_model):
            return [token_model[:80]]
        return []

    # 슬래시 분리 처리
    result: list[str] = []
    for candidate in model_candidates:
        if "/" in candidate:
            result.extend(p.strip() for p in candidate.split("/") if p.strip())
        else:
            result.append(candidate)

    # 유효한 모델명만 유지 (영문+숫자 조합), 중복 제거, 최대 80자
    seen: set[str] = set()
    unique: list[str] = []
    for m in result:
        m = m[:80]
        if m not in seen and _is_valid_model_name(m):
            seen.add(m)
            unique.append(m)

    return unique


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
