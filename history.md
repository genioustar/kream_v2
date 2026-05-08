# 변경 히스토리

## 2026-05-08 — 비교 맥락 식별자 naver → marketplace 일괄 변경

- `common/models.py`: `NaverProduct` → `MarketplaceProduct` (클래스명), `ArbitrageResult` 필드 `naver_site/price/url` → `marketplace_site/price/url`
- `kream/comparator.py`: 파라미터·루프 변수·ArbitrageResult 키워드 인자 전체 marketplace 로 통일
- `main.py`: import, 변수명(`naver_output/products`→`marketplace_output/products`), 파일명(`marketplace_products.json`), `_apply_sale_price` 두 번째 인자(`"marketplace"`)
- `config.py`: `SITE_DISCOUNT_RATES["naver"]` → `["marketplace"]`
- `diff_output.py`: `KEY_CONFIG` 키 `naver_products.json` → `marketplace_products.json`
- `kream/crawler.py`, `adidas/crawler.py`, `nike/crawler.py`, `special/crawler.py`: `NaverProduct` → `MarketplaceProduct`
- `special/crawler.py`: `from naver.parser` → `from marketplace.parser`, `_to_naver_product` → `_to_marketplace_product`
- `marketplace/` 디렉토리(구 `naver/`)는 실제 네이버 크롤러이므로 내부 로직 유지

## 2026-05-07 — sale_price 도입 — 사이트별 수동 할인율 적용 후 Kream 차익 비교에 effective price 사용

- `config.py`: `SITE_DISCOUNT_RATES` 딕셔너리 추가 (naver·adidas·nike·special 각 0%). 0이면 sale_price 미산출.
- `common/models.py`: `NaverProduct`에 `sale_price: Optional[int] = None` 필드 추가. default=None으로 과거 JSON 호환 보장.
- `main.py`: `_apply_sale_price(products, source_key)` 헬퍼 추가. STEP 1 각 크롤러 호출 직후 저장 직전 호출. naver 9개 스토어 모두 단일 "naver" 할인율 적용.
- `kream/comparator.py`: `_effective_price(p)` 헬퍼 추가. `find_arbitrage`에서 naver.price 대신 effective price 사용. ArbitrageResult.naver_price·price_diff 모두 effective price 기준으로 산출.

## 2026-05-04 — SPECIAL_SALE_URL 범용 크롤러 추가 및 Kream 검색 연동 수정

- `special/crawler.py` 신규: `config.SPECIAL_SALE_URL`에 지정된 임의 쇼핑 페이지를 CDP로 크롤링. JS 휴리스틱(li → a → leaf 조상 3단계 전략)으로 상품 카드 자동 탐지, scrollY 변화 없음 5회 연속 시 종료.
- `config.py`: `SPECIAL_SALE_URL = ""` 추가. 빈 값이면 크롤링 스킵.
- `main.py`: STEP 1에 special 크롤링 블록 추가 (`special_products.json` 저장).
- `diff_output.py` 버그 수정: `diff_date_pair`가 새 날짜에만 존재하는 `*_products.json`을 건너뛰어 Kream 검색에서 누락되는 문제 → 신규 파일의 전체 항목을 `added`로 처리해 diff 파일 생성하도록 수정.

## 2026-04-22 — 문서·코드 정합성 정리 (하네스 엔지니어링 룰 도입)

### .planning/STATE.md — Decisions 갱신
- "CDP 대신 create_browser+new_stealth_page 사용" 결정을 SUPERSEDED 처리하고 CDP 채택 결정으로 대체

### CLAUDE.md — 하네스 엔지니어링 룰 추가
- 경로 교체 시 잔재 즉시 제거, grep 0회면 삭제, 문서/코드 1:1 정합성, SUPERSEDED 포맷, 세션 종료 전 체크리스트 명문화

### CLAUDE.md — 크롤러 검증 시 Playwright MCP 활용 규칙 추가
- Evaluator 단계에서 Playwright MCP 로 대상 사이트 DOM 과 크롤링 JSON 을 교차 검증하는 절차 도입
- Firecrawl·Fetch 정적 HTML MCP 사용 금지 (SPA·Kasada·Akamai 우회 불가)
- 검증은 CDP 포트 9222 세션을 크롤러와 순차 공유, 수정된 크롤러만 대상으로 수행
- 판정 기준: PASS(모든 항목 충족) / FLAG(상품수 미달이지만 샘플 일치) / FAIL(샘플 불일치·DOM 접근 실패)
- 설치 명령: `claude mcp add playwright npx @playwright/mcp@latest`

---

## 2026-04-15 — 나이키 크롤러 Kasada 우회 및 API 인터셉트 방식 전환

### 문제 진단
- `nike/crawler.py` 가 21개 상품만 수집하는 문제 발생
- 원인: Nike가 Kasada 봇 탐지를 사용하며, Playwright 자체 Chromium을 감지해 무한스크롤 JS를 비활성화 — 초기 SSR 21개만 서빙
- 증거: 크롤러 실행 시 scrollY가 0→4569px까지 정상 이동하지만 API 호출 전혀 없음

### 해결 방법
- **Playwright Chromium → 실제 Chrome CDP 전환**: `create_browser()` 제거, `async_playwright().connect_over_cdp("http://localhost:9222")` 채택 (아디다스와 동일 방식)
- **DOM 파싱 → API 응답 인터셉트**: `page.on("response", ...)` 로 `api.nike.com/discover/product_wall` 응답을 인터셉트해 JSON 직접 파싱
- **실제 API 응답 구조 확인**: `productGroupings[*].products` (기존 가정 `productWall.products` 오류)
- **올바른 필드명 적용**: `copy.title`, `copy.subTitle`, `prices.currentPrice`, `pdpUrl.url`, `productCode`
- **Makefile 수정**: `make nike` 타겟이 `make chrome`에 의존하도록 추가

---

## 2026-04-13 — Kream 검색 버튼 폴백 + 새 창 방지

### kream/crawler.py
- `_click_search_button()`: 단일 셀렉터(`KREAM_SELECTORS["search_button"]`) → `SEARCH_BUTTON_SELECTORS` 폴백 리스트 순서 시도 (긴 CSS 경로 매칭 실패 시 fallback)
- `search_kream()` 재시도 전략 변경: `init_kream_page(context)` 로 새 창 생성 → 기존 페이지를 Kream 홈으로 재이동 (`page.goto(KREAM_BASE_URL)`)
  - 새 창 무한 생성 문제 해결
  - 원본 페이지가 항상 pool로 복귀되어 pool 관리 정합성 확보

---

## 2026-04-13 — kream 모드 diff 적용

### main.py
- `kream` 모드에서도 diff가 있으면 diff 기반 모델명 사용 (기존: 항상 전체 모델명 사용)
- `is_full_crawl_day or mode == "kream"` 조건에서 `mode == "kream"` 제거
- diff 없으면 기존과 동일하게 전체 모델명으로 폴백

---

## 2026-04-11 — Kream 두 번째 검색 실패 수정

### kream/crawler.py
- `_open_search_and_get_input()`: 검색 결과 페이지에서는 입력창이 이미 열려있어 버튼 클릭 시 닫혀버림  
  → 입력창 존재 여부 먼저 확인 후 없을 때만 버튼 클릭

---

## 2026-04-09 — Kream SPA 라우팅 전환 & 탐지 우회 강화

### kream/crawler.py
- Kream은 Vue SPA: `page.goto(search_url)` 시 HTML 39 bytes로 미렌더링  
  → `page.evaluate("window.location.href = url")`로 SPA 내부 라우팅 트리거
- 검색 결과 선택: 거래량 최다 → **첫 번째 상품**(검색 결과 맨 왼쪽)
- 검색 간 딜레이 8~15초, 차단 시 새 stealth 페이지 생성 + 지수 백오프 (30→60→120초)

### common/browser.py
- `USER_AGENTS`: Chrome 130~136 기반 12종 (Mac 4, Windows 5, Linux 3)
- `_get_client_hints()`: UA와 정확히 일치하는 `sec-ch-ua` 헤더 자동 생성 (불일치 → 봇 신호 방지)
- 쿠키 영속화: `output/session/kream_cookies.json`

### naver/parser.py
- `price` 셀렉터를 fallback 후보 리스트로 변경 (DOM 구조 변경 시 자동 전환)
