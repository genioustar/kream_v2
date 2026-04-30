# 변경 히스토리

## 2026-04-28 — 크롤링 URL 단일 출처(config.py) 통일

- `adidas/crawler.py` 의 `ADIDAS_EXTRA_SALE_URL` 하드코딩 상수 제거 → `config.ADIDAS_SALE_URL` 사용으로 통일 (`nike` 와 동일 패턴)
- `config.ADIDAS_SALE_URL` 값을 죽은 outlet URL 에서 실사용 URL(`extra_sale-shoes?sort=price-low-to-high`) 로 교정
- naver/adidas/nike 크롤러에 URL 부재 가드 추가: 변수 미설정 또는 빈 값이면 안내 로그 후 즉시 `[]` 반환 (해당 크롤링만 스킵, 파이프라인 계속)
- `naver/crawler.py` 항목 단위 가드: `SEARCH_URLS` 항목별 `url` 누락 시 그 사이트만 스킵
- redirect 감지 하드코딩 키워드(`"extra_sale"`, `"clearance-shoes"`) 제거 → URL path 마지막 segment 자동 추출 방식으로 교체
- `ARCHITECTURE.md` 의 모듈 설명·config 표 갱신, `ADIDAS_SALE_URL` 항목 추가

## 2026-04-22 — 문서·코드 정합성 정리 (하네스 엔지니어링 룰 도입)

### nike/crawler.py 죽은 코드 제거 (473→311줄)
- API 인터셉트 전환 후 남아있던 DOM 파싱 잔재 일괄 삭제
- 제거 함수: `_find_card_selector()`, `_extract_all_products()`, `_parse_price()`
- 제거 상수: `CARD_SELECTORS`, `TITLE_SELECTOR`, `SUBTITLE_SELECTOR`, `PRICE_SELECTOR`, `LINK_SELECTOR`, `SELECTOR_TIMEOUT_MS`, `DIAG_BODY_TEXT_LEN`, `SCROLL_BUFFER_RATIO`, `API_ATTRIBUTE_IDS`, `API_PAGE_SIZE`, `_JS_EXTRACT`
- 현재 실행 체인: `crawl_nike()` → `_collect_via_api()` → `_parse_api_product()` (+ `_should_exclude`, `_extract_model_name`)

### ARCHITECTURE.md — nike 섹션 재작성
- "create_browser + new_stealth_page 조합 사용 (CDP 불필요)" 오기를 제거, CDP 필수 방식으로 정정
- 존재하지 않거나 호출되지 않던 함수 기술 삭제, `_collect_via_api`·`_parse_api_product` 기술 추가

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

## 2026-04-15 — Phase 1 나이키 공홈 크롤러 추가

- Phase 1 (나이키 공홈 크롤러 추가) 완료: `nike/crawler.py` 신규 작성, `config.NIKE_SALE_URL` 상수 추가, `main.py` STEP 1 에 나이키 크롤링 블록 추가, `Makefile` 에 `make nike` 타겟 추가. 무한스크롤 + JS 일괄 파싱 + Kids 키워드 필터로 세일 신발을 수집하며 기존 diff·Kream 차익 비교 파이프라인에 자동 통합됨.

---

## 2026-04-13 — 아디다스 크롤러 완성

### chrome-debug.sh / Makefile 추가
- Chrome 신규 보안 요구사항: `--remote-debugging-port` 사용 시 `--user-data-dir` 필수. 없으면 포트 바인딩 없이 조용히 무시됨.
- `chrome-debug.sh`: 이미 실행 중이면 스킵, 미실행이면 시작 후 최대 10초 대기
- `Makefile`: `make chrome / adidas / crawl / kream / full` 단축 명령

### adidas/crawler.py 수정
- **상품명 추출 교체**: `innerText` → URL pathname 디코딩 방식  
  `/케이타키-알파-슬라이드/JR1153.html` → `케이타키 알파 슬라이드`  
  (innerText는 "위시리스트 담기" 등 UI 텍스트에 오염됨)
- **카드 셀렉터**: `article[class*="product"]` 우선 사용  
  (`[class*="product-card"]`는 731개 과매칭으로 제외)
- **페이지네이션**: 다음 버튼 셀렉터 `data-auto-id` → `data-testid="pagination-next-button"`  
  결과: 10페이지 × 48개, Kids 제외 → 389개 수집

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

## 2026-04-12 — 아디다스 크롤러 신설 & 파이프라인 확장

### adidas/crawler.py (신규)
- Akamai Bot Manager 우회: Playwright 자체 브라우저는 fingerprint 탐지로 차단 → `connect_over_cdp(CDP_URL)` 방식 채택
- Extra Sale 신발 크롤링, Kids 카테고리 자동 제외, 상품코드 기준 중복 제거

### main.py
- `--mode {crawl|full|kream}` CLI 인자 추가
- `_load_all_products(output_dir)`: 오늘자 모든 `*_products.json` 자동 감지·합산 (신규 소스 자동 포함)
- `FULL_CRAWL_DAYS = {1, 10, 20, 30}`: 해당 날짜는 전체 모델명, 그 외 날짜는 diff 기반 모델명으로 Kream 검색

### diff_output.py
- `DEFAULT_PRODUCTS_KEY = ("model_name", "site_name")`: `*_products.json` 미등록 파일 자동 처리 (신규 소스 별도 등록 불필요)
- `compute_diff()`: removed 항목 저장 제외, added/modified만 저장

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
