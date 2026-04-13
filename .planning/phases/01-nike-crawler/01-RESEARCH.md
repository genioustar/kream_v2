# Phase 1: 나이키 공홈 크롤러 추가 - Research

**Researched:** 2026-04-13
**Domain:** nike.com/kr 세일 신발 크롤링 — 봇 탐지 우회, DOM 파싱, NaverProduct 파이프라인 통합
**Confidence:** MEDIUM

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SRC-01 | 새 쇼핑몰 크롤러 1개 이상 추가 (나이키 공홈) | nike.com/kr clearance-shoes URL 확인, 셀렉터·스크롤 전략 문서화 |
| SRC-02 | 새 소스 NaverProduct 포맷 통합 (자동 diff·비교 포함) | main.py 통합 패턴 확인, DEFAULT_PRODUCTS_KEY 자동 적용 확인 |
</phase_requirements>

---

## Summary

나이키 코리아 세일 신발 페이지(`nike.com/kr/w/clearance-shoes-3yaepzy7ok`)는 **무한스크롤** 방식으로 상품을 로드한다. Kids 전용 페이지는 별도 URL(`/kids-sale-shoes-3yaepzv4dhzy7ok`)로 분리되어 있어, 성인 세일 URL만 크롤링하면 Kids 상품이 포함되지 않는다. 단, 성인 세일 페이지에도 일부 Kids 관련 상품이 혼입될 수 있으므로 카드 텍스트 기반 필터(아디다스 패턴 참조)를 보조 수단으로 추가하는 것이 안전하다.

나이키는 **Akamai Bot Manager + Kasada** 이중 보호를 사용하지만, 공개 카탈로그(세일 상품 목록)에 대해서는 기본 브라우저 자동화가 실질적으로 허용된다는 실무 보고가 있다. 아디다스와 달리 CDP 연결이 필수는 아니며, 기존 `common/browser.py`의 `create_browser(headless=True)` + `new_stealth_page()` 조합(playwright-stealth 2.0.3)으로 접근 가능할 가능성이 높다. 단, 성공 여부는 실제 실행으로 검증해야 한다.

**모델명(스타일코드)** 추출은 아디다스와 달리 상품 상세 URL(`/kr/t/{한글명}/{코드}`)에 독립적인 스타일코드 세그먼트가 없다. 대신 상품 카드의 `data-testid="product-description-style-color"` 요소 또는 상품 URL 마지막 경로 세그먼트(`/t/air-max-90/CN8490-002`처럼 전역 사이트 기준)에서 추출해야 한다. 그러나 한국어 URL에서는 마지막 세그먼트가 `oVLJrCLn` 형식의 내부 ID일 수 있어 검증이 필요하다.

**Primary recommendation:** `nike/crawler.py`를 신규 생성하고 `crawl_nike() → list[NaverProduct]` 로 구현. `main.py` STEP 1에 3줄(import, 호출, 저장) 추가하면 나머지 파이프라인(Kream 검색·diff·차익 비교)은 자동 통합된다.

---

## Project Constraints (from CLAUDE.md)

- 항상 한국어로 응답, 주석·커밋 메시지도 한국어
- 함수 단일 책임 원칙, 변수명 영어, 매직 넘버 상수 추출
- `any` 타입 금지, 에러를 `console.log`만으로 처리 금지
- 요청하지 않은 파일 수정 금지 (단, `main.py`와 `config.py`는 통합을 위해 수정 범위에 포함)
- 소스 추가·수정 시 `ARCHITECTURE.md` 업데이트 필수
- 타입 `any` 사용 금지 — `dict[str, ...]` 등 명시적 타입 사용

---

## Standard Stack

### Core (기존 프로젝트 스택 — 변경 없음)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| playwright (async) | 1.58.0 | 브라우저 자동화, 무한스크롤 처리 | 기존 코드베이스 전체가 사용 |
| playwright-stealth | 2.0.3 | 봇 탐지 우회 (webdriver 시그니처 제거) | 기존 `new_stealth_page()` 패턴 |
| Python | 3.14 | 런타임 | 프로젝트 고정 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| asyncio | stdlib | 비동기 크롤링 | 기존 패턴과 동일 |
| dataclasses | stdlib | NaverProduct 직렬화 | 기존 모델 재사용 |
| common/browser.py | 내부 | create_browser() + new_stealth_page() | Kream·Naver 패턴 그대로 사용 |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| playwright-stealth (일반 브라우저) | CDP 연결 (아디다스 방식) | 나이키는 공개 카탈로그에서 stealth만으로 접근 가능 가능성 높음. CDP는 크롬 사전 실행 필요 → 복잡도 증가. stealth 차단 시 CDP로 fallback 검토 |
| 무한스크롤 전략 | API 직접 호출 | nike.com은 `api.nike.com/cic/browse/v2` API 존재하나, 한국어 사이트 파라미터 검증 안됨. 브라우저 방식이 더 안정적 |

---

## Architecture Patterns

### 신규 모듈 위치
```
resell-sniper/
├── nike/
│   └── crawler.py      # crawl_nike() — NaverProduct 포맷 저장
├── main.py             # STEP 1에 nike 크롤링 3줄 추가
└── config.py           # NIKE_SALE_URL 상수 추가
```

### Pattern 1: 아디다스 패턴 그대로 적용 (무한스크롤 버전)
**What:** `create_browser()` 없이 `async_playwright()` 직접 사용 (아디다스는 CDP 전용). 대신 `common/browser.py`의 `create_browser()` + `new_stealth_page()` 조합 사용 (Kream·Naver 패턴).
**When to use:** 나이키는 CDP 필요 없음 → `create_browser(headless=True)` 사용

```python
# nike/crawler.py 뼈대 — Source: 기존 naver/crawler.py 패턴 응용
import asyncio
from datetime import datetime
from playwright.async_api import Page
from common.browser import create_browser, new_stealth_page
from common.models import NaverProduct
from common.logger import get_logger

NIKE_SALE_URL = "https://www.nike.com/kr/w/clearance-shoes-3yaepzy7ok"
SITE_NAME = "nike"
PAGE_SETTLE_SEC = 3.0
MAX_SCROLL_ATTEMPTS = 40

async def crawl_nike() -> list[NaverProduct]:
    async with create_browser(headless=True) as context:
        page = await new_stealth_page(context)
        await page.goto(NIKE_SALE_URL, wait_until="domcontentloaded", timeout=60_000)
        await _scroll_to_bottom(page)
        return await _extract_all_products(page)
```

### Pattern 2: 무한스크롤 처리 (naver/crawler.py 패턴 참조)
**What:** 스크롤 후 상품 수 비교 — 3회 연속 변화 없으면 종료
**When to use:** 나이키 세일 페이지는 무한스크롤 확인됨

```python
# Source: naver/crawler.py _scroll_to_bottom 패턴 응용
async def _scroll_to_bottom(page: Page) -> None:
    prev_count = 0
    no_change_streak = 0
    for _ in range(MAX_SCROLL_ATTEMPTS):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(2000)
        current_count = await page.locator(PRODUCT_CARD_SELECTOR).count()
        if current_count == prev_count:
            no_change_streak += 1
            if no_change_streak >= 3:
                break
        else:
            no_change_streak = 0
        prev_count = current_count
```

### Pattern 3: JS evaluate 일괄 파싱 (adidas/crawler.py 패턴)
**What:** `page.evaluate()` JS 함수로 카드 전체를 한 번에 파싱
**When to use:** 상품 수가 많을 때 Locator 루프 대비 성능 우수

```python
# Source: adidas/crawler.py _JS_EXTRACT 패턴 응용
_JS_EXTRACT_NIKE = """() => {
    const cards = Array.from(document.querySelectorAll('.product-card'));
    return cards.map(card => {
        const anchor = card.querySelector('.product-card__link-overlay');
        const href = anchor ? anchor.href : null;
        const title = card.querySelector('.product-card__title');
        const name = title ? title.innerText.trim() : null;
        const priceEl = card.querySelector('.product-price');
        const priceStr = priceEl ? priceEl.innerText.trim() : null;
        const subtitle = card.querySelector('.product-card__subtitle');
        const category = subtitle ? subtitle.innerText.trim() : null;
        return { href, name, priceStr, category };
    });
}"""
```

### Pattern 4: main.py 통합 — 3줄 추가
```python
# main.py STEP 1 — 아디다스 블록 다음에 추가
from nike.crawler import crawl_nike

nike_output = output_dir / "nike_products.json"
if nike_output.exists():
    logger.info(f"오늘자 파일 존재 — 나이키 크롤링 스킵: {nike_output}")
else:
    logger.info("나이키 세일 신발 크롤링 시작")
    try:
        nike_products = await crawl_nike()
        if nike_products:
            _save_json(nike_products, nike_output)
            logger.info(f"나이키 상품 {len(nike_products)}개 저장 → {nike_output}")
        else:
            logger.warning("나이키 수집 결과 없음 — 파일 저장 생략")
    except Exception as exc:
        logger.warning(f"나이키 크롤링 실패 (파이프라인은 계속): {exc}")
```

### Anti-Patterns to Avoid
- **CDP 연결 불필요하게 사용:** 나이키는 공개 카탈로그에서 stealth 브라우저로 접근 가능 — CDP는 Akamai가 playwright 브라우저 자체를 차단할 때만 필요
- **아디다스와 달리 페이지네이션 없음:** 나이키는 무한스크롤 — `NEXT_BTN_SELECTOR` 방식 불가
- **UA 컨텍스트별 변경 금지:** `common/browser.py`에서 이미 처리됨. 크롤러에서 재설정하지 않음

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| 봇 탐지 우회 | 직접 헤더/fingerprint 조작 | `common/browser.py`의 `create_browser()` + `new_stealth_page()` | UA 풀·Client Hints·stealth 이미 통합됨 |
| 무한스크롤 종료 감지 | 타임아웃 기반 sleep | naver/crawler.py `_scroll_to_bottom` 패턴 | "N회 연속 변화 없음" 패턴이 더 안정적 |
| NaverProduct 직렬화 | 별도 JSON 포맷 | `dataclasses.asdict()` + `_save_json()` (main.py 기존 함수) | 파이프라인 자동 통합은 `NaverProduct` 포맷 필수 |
| Kids 필터링 | 별도 페이지 요청 | URL 단계에서 성인 전용 URL 사용 (`clearance-shoes-3yaepzy7ok`) | Kids URL은 `kids-sale-shoes-3yaepzv4dhzy7ok`로 분리됨 |

**Key insight:** 아디다스 크롤러와 달리 나이키는 CDP가 필요 없으므로, 기존 Naver 크롤러처럼 `create_browser()` context manager를 그대로 사용할 수 있다. 추가 인프라 없이 동작 가능.

---

## Nike.com/kr 도메인 분석 (핵심 발견)

### 세일 신발 URL
| 목적 | URL |
|------|-----|
| 성인 세일 신발 (Kids 제외됨) | `https://www.nike.com/kr/w/clearance-shoes-3yaepzy7ok` |
| Kids 세일 신발 (별도 URL) | `https://www.nike.com/kr/w/kids-sale-shoes-3yaepzv4dhzy7ok` |
| 상품 상세 페이지 패턴 (전역) | `https://www.nike.com/t/{product-name}/{STYLE-CODE}` (예: `/t/air-max-90/CN8490-002`) |
| 상품 상세 페이지 패턴 (한국어) | `https://www.nike.com/kr/t/{한글-상품명}/{내부ID}` (예: `/kr/t/덩크-로우-레트로-남성-신발-oVLJrCLn`) |

**중요 발견:** 한국어 상품 URL의 마지막 세그먼트(`oVLJrCLn`)는 스타일코드(CN8490-002 형식)가 아닌 내부 ID일 가능성이 있다. Kream 검색에 사용할 모델명은 `data-testid="product-description-style-color"` 요소에서 추출하거나, 상품 카드 텍스트에서 정규식으로 추출해야 한다. **실제 DOM 확인 후 전략 결정 필요.**

### 상품 카드 셀렉터 (MEDIUM confidence — 다중 출처 검증)
| 요소 | CSS 셀렉터 | 출처 신뢰도 |
|------|-----------|-----------|
| 상품 카드 컨테이너 | `.product-card` | MEDIUM (scrapingant 튜토리얼, crypter70 GitHub) |
| 상품명 | `.product-card__title` | MEDIUM (scrapingant 튜토리얼) |
| 카테고리/서브타이틀 | `.product-card__subtitle` | MEDIUM |
| 가격 | `.product-price` | MEDIUM |
| 상품 링크 | `.product-card__link-overlay` | MEDIUM |
| 스타일 코드 (상세 페이지) | `[data-testid="product-description-style-color"]` | LOW (trickster.dev 단일 출처) |

**셀렉터 안정성:** `product-card__*` 패턴은 의미론적 클래스명으로 비교적 안정적이나, CSS-in-JS 난독화 빌드 배포 시 변경 가능. 아디다스처럼 `_find_card_selector()` 패턴(후보 목록 순차 탐지)을 도입하는 것이 방어적.

### 봇 탐지 수준
| 항목 | 상세 |
|------|------|
| 보호 솔루션 | Akamai Bot Manager (공개 카탈로그) + Kasada (로그인/결제 영역) |
| 공개 카탈로그 접근 | 기본 브라우저 자동화로도 가능하다는 실무 보고 있음 (LOW confidence) |
| playwright-stealth 충분성 | 가능성 있음 — 단, 반드시 실제 실행으로 검증 필요 |
| CDP 필요 여부 | 아디다스(Akamai 강성 차단)와 달리 CDP 필수 아님. 차단 시 fallback |
| 탐지 방법 | TLS fingerprinting + HTTP/2 fingerprinting + IP reputation + 행동 분석 |

### 상품 로딩 방식
- **무한스크롤** 확인 (페이지네이션 없음)
- 초기 로드: 약 24개 상품
- 스크롤 시 추가 로드 (AJAX)
- 전체 세일 상품: 수백 개 예상

---

## Common Pitfalls

### Pitfall 1: 한국어 URL의 모델명 추출 실패
**What goes wrong:** `/kr/t/덩크-로우-레트로-남성-신발-oVLJrCLn`에서 URL 마지막 세그먼트를 스타일코드로 오해. `oVLJrCLn` 형식은 `CN8490-002` 형식의 스타일코드가 아님
**Why it happens:** 전역(US) URL 패턴과 한국어 URL 패턴이 다름
**How to avoid:** 상품 카드의 `data-testid="product-description-style-color"` 요소에서 텍스트 추출 우선. 없으면 상품명에서 정규식(`[A-Z]{2}\d{4}-\d{3}` 형식) 추출 시도
**Warning signs:** model_name이 `oVLJrCLn` 같은 8자리 알파뉴메릭으로 나오는 경우

### Pitfall 2: 봇 차단 시 무한 대기
**What goes wrong:** 나이키가 차단하면 페이지가 로드되지 않거나 빈 카드만 반환됨 — 예외 발생 없이 조용히 실패
**Why it happens:** 차단은 403이 아니라 빈 결과 또는 CAPTCHA 리다이렉트로 나타남
**How to avoid:** 카드 수가 0개이면 WARNING 로그 + 페이지 URL/제목 출력 (아디다스 `_find_card_selector` 진단 패턴 적용)
**Warning signs:** `page.url`이 세일 URL에서 다른 페이지로 이탈

### Pitfall 3: 무한스크롤 조기 종료
**What goes wrong:** 2초 대기가 부족해서 새 상품이 로드되기 전에 카운트 비교 → 조기 종료
**Why it happens:** 네트워크 지연, 렌더링 지연
**How to avoid:** 스크롤 후 `wait_for_timeout(2000)` + `networkidle` wait 시도 (3초 타임아웃으로 무시). naver/crawler.py 패턴 그대로 적용
**Warning signs:** 수집 상품 수가 지나치게 적음 (수십 개 수준)

### Pitfall 4: playwright-stealth 2.0 API 변경
**What goes wrong:** `stealth_async(page)` 임포트 시도 → `ImportError`
**Why it happens:** playwright-stealth 2.0.3에서는 `Stealth` 클래스 인스턴스의 `apply_stealth_async(page)` 메서드 사용
**How to avoid:** `common/browser.py`의 `new_stealth_page(context)` 함수 사용 — 내부적으로 `_stealth.apply_stealth_async(page)` 호출. 직접 임포트 금지
**Warning signs:** `from playwright_stealth import stealth_async` → `ImportError`

### Pitfall 5: main.py에서 나이키 크롤러가 아디다스와 달리 동기화 문제
**What goes wrong:** `crawl_nike()`가 내부에서 `create_browser()`를 쓸 경우, 아디다스(`crawl_adidas()`)가 이미 CDP 브라우저 인스턴스를 점유 → 충돌 없음 (독립 컨텍스트). 단, 메모리·포트 이중 사용 주의
**Why it happens:** 아디다스는 CDP(localhost:9222), 나이키는 별도 playwright 인스턴스
**How to avoid:** 나이키 크롤러를 아디다스와 순차 실행 (기존 main.py 구조 유지)

---

## Code Examples

### 나이키 크롤러 전체 뼈대

```python
# nike/crawler.py
"""
nike/crawler.py
나이키 공홈 세일 신발 크롤러.
Playwright-stealth 브라우저로 무한스크롤을 처리하며 상품을 수집한다.
"""
import asyncio
import json
import random
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from playwright.async_api import Page

import config
from common.browser import create_browser, new_stealth_page
from common.logger import get_logger
from common.models import NaverProduct

logger = get_logger("nike.crawler")

SITE_NAME = "nike"
PAGE_LOAD_TIMEOUT = 60_000
SELECTOR_TIMEOUT = 15_000
PAGE_SETTLE_SEC = 3.0
MAX_SCROLL_ATTEMPTS = 40
NO_CHANGE_LIMIT = 3     # N회 연속 변화 없으면 스크롤 종료

CARD_SELECTORS = [
    '.product-card',
    '[data-testid*="product"]',
    'div[class*="product-card"]',
]
TITLE_SELECTOR = '.product-card__title'
PRICE_SELECTOR = '.product-price'
LINK_SELECTOR = '.product-card__link-overlay'
STYLE_CODE_TESTID = '[data-testid="product-description-style-color"]'

async def crawl_nike() -> list[NaverProduct]:
    """나이키 공홈 세일 신발 페이지를 크롤링하여 NaverProduct 목록을 반환한다."""
    async with create_browser(headless=True) as context:
        page = await new_stealth_page(context)
        await page.goto(config.NIKE_SALE_URL, wait_until="domcontentloaded",
                        timeout=PAGE_LOAD_TIMEOUT)
        await asyncio.sleep(PAGE_SETTLE_SEC)
        card_selector = await _find_card_selector(page)
        if not card_selector:
            logger.error("상품 카드 셀렉터를 찾을 수 없습니다.")
            return []
        await _scroll_to_bottom(page, card_selector)
        return await _extract_all_products(page, card_selector)
```

### 모델명 추출 전략 (스타일 코드)

```python
import re

# 나이키 스타일코드 패턴: 예) CN8490-002, FQ8143-100, DV9956-001
_STYLE_CODE_RE = re.compile(r'\b[A-Z]{2}\d{4}-\d{3}\b')

def _extract_style_code(href: str | None, card_text: str) -> str | None:
    """
    상품 URL 또는 카드 텍스트에서 나이키 스타일코드를 추출한다.
    스타일코드 패턴: 영문 2자 + 숫자 4자 + '-' + 숫자 3자 (예: CN8490-002)
    """
    # 1순위: 카드 텍스트에서 정규식 추출
    if card_text:
        m = _STYLE_CODE_RE.search(card_text)
        if m:
            return m.group()
    # 2순위: URL 마지막 세그먼트 시도 (전역 URL: /t/product-name/CN8490-002)
    if href:
        segment = href.rstrip('/').split('/')[-1]
        if _STYLE_CODE_RE.match(segment):
            return segment
    return None
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `stealth_async(page)` import | `Stealth().apply_stealth_async(page)` | playwright-stealth 2.0 | ImportError 발생 — `common/browser.py` 이미 처리됨 |
| 나이키 개별 페이지 순회 | 무한스크롤 + 카드 일괄 파싱 | 현재 | 상품 수백 개를 1개 페이지에서 수집 가능 |

---

## Open Questions

1. **한국어 URL의 스타일코드 추출 방법**
   - What we know: 전역 URL(`/t/product-name/CN8490-002`)에는 스타일코드가 포함됨. 한국어 URL 마지막 세그먼트는 `oVLJrCLn` 같은 내부 ID일 가능성
   - What's unclear: `data-testid="product-description-style-color"` 요소가 리스팅 페이지 카드에도 존재하는지, 상세 페이지에만 있는지
   - Recommendation: 크롤러 초기 구현 시 카드 텍스트 전체에서 `[A-Z]{2}\d{4}-\d{3}` 정규식으로 추출 시도. 실패 시 한국어 URL을 영문 URL로 리다이렉트하는지 확인

2. **playwright-stealth만으로 나이키 차단 우회 가능 여부**
   - What we know: 공개 카탈로그는 기본 자동화 허용 가능성 있음 (단일 출처, LOW confidence)
   - What's unclear: 나이키 한국 사이트가 전역과 동일한 보호 수준인지
   - Recommendation: headless=True로 먼저 시도. 빈 결과 또는 차단 감지 시 headless=False로 전환, 그래도 실패 시 CDP 방식 검토

3. **성인 세일 페이지에 Kids 상품 혼입 여부**
   - What we know: Kids 전용 URL이 별도로 존재 → 성인 URL에는 Kids가 없을 가능성이 높음
   - What's unclear: 성인 세일 URL에서 Kids 상품이 섞여 나오는지
   - Recommendation: 카드 텍스트에 "어린이" / "Boys" / "Girls" 포함 여부로 보조 필터 추가 (낮은 비용)

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|-------------|-----------|---------|---------|
| Python | 런타임 | ✓ | 3.14.3 | — |
| playwright | 브라우저 자동화 | ✓ | 1.58.0 | — |
| playwright-stealth | 봇 우회 | ✓ | 2.0.3 | — |
| Chrome CDP (localhost:9222) | 아디다스 전용 | 조건부 | — | 나이키는 CDP 불필요 |

**Missing dependencies with no fallback:** 없음 — 나이키 크롤러는 기존 스택만으로 구현 가능

---

## Validation Architecture

> nyquist_validation 설정 미확인 → 기본값(활성화)으로 간주

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | Notes |
|--------|----------|-----------|-------------------|-------|
| SRC-01 | `crawl_nike()`가 NaverProduct 리스트 반환 | unit (mock) | `PYTHONPATH=. python -m pytest nike/tests/ -x` | Wave 0 생성 필요 |
| SRC-02 | `nike_products.json`이 파이프라인에 자동 통합됨 | integration | `PYTHONPATH=. python main.py --mode crawl` 후 파일 존재 확인 | 실제 크롤링 필요 |

### Wave 0 Gaps
- [ ] `nike/tests/test_crawler.py` — `crawl_nike()` 목 테스트 (실제 HTTP 요청 없이)
- [ ] `nike/tests/test_model_extraction.py` — 스타일코드 추출 정규식 단위 테스트
- [ ] `nike/__init__.py` — 모듈 패키지 등록

*(기존 프로젝트에 pytest 인프라 없음 — Wave 0에서 `requirements.txt`에 pytest 추가 또는 단독 실행 스크립트로 대체)*

---

## Sources

### Primary (HIGH confidence)
- 기존 코드베이스 직접 분석 — `adidas/crawler.py`, `naver/crawler.py`, `common/browser.py`, `main.py`
- `ARCHITECTURE.md` — "새 크롤링 소스 추가 방법" 섹션

### Secondary (MEDIUM confidence)
- [ScrapingAnt - Web Scraping with Playwright Part 2](https://scrapingant.com/blog/web-scraping-playwright-python-part-2) — `.product-card` 셀렉터, 무한스크롤 패턴
- [crypter70/Nike-Scraper GitHub](https://github.com/crypter70/Nike-Scraper) — `.product-card__title`, `.product-price` 셀렉터
- Nike.com/kr 검색 결과 — 세일 URL `clearance-shoes-3yaepzy7ok`, Kids URL `kids-sale-shoes-3yaepzv4dhzy7ok` 확인
- [Trickster Dev - Scraping Nike.com](https://www.trickster.dev/post/scraping-product-data-from-nike/) — `__NEXT_DATA__` JSON 임베딩, 스타일코드 URL 패턴, 봇 보호 정보

### Tertiary (LOW confidence)
- [The Web Scraping Club - Scraping Nike with 5 open source tools](https://substack.thewebscraping.club/p/scraping-nike-with-open-source) — Akamai + Kasada 이중 보호, 공개 카탈로그 접근 가능성

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — 기존 코드베이스와 동일, 버전 확인됨
- Architecture patterns: HIGH — 기존 adidas/naver 패턴을 직접 분석하고 적용
- 나이키 셀렉터: MEDIUM — 다중 외부 출처, 실제 DOM 검증 필요
- 봇 탐지 우회: MEDIUM — 실무 보고 있으나 한국 사이트 검증 없음
- 스타일코드 추출: LOW — 한국어 URL 패턴과 스타일코드 연결 관계 미검증

**Research date:** 2026-04-13
**Valid until:** 2026-05-13 (Nike DOM 구조는 배포 시 변경 가능 — 30일 기준)
