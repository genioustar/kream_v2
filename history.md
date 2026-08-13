# 변경 히스토리

## 2026-08-05 — Kream 검색 결과 상세 모델번호 검증

- [Kream 모델 일치] 검색 카드의 모델을 검색어로 간주하지 않고 각 후보 상세 페이지의 `모델번호`를 읽어 정규화된 전체 문자열이 일치할 때만 Kream 상품과 차익 결과에 포함한다. 상세 모델번호 누락·파싱 실패·불일치는 fail-closed로 제외하며 comparator에서도 저장 전 다시 검증한다.
  - SUPERSEDED: 검색 결과가 1~2개면 첫 번째 상품을 정확 매치로 간주하고 3개 이상이면 전부 제외 — Kream 유사 검색이 `IG5749` 검색에 실제 `IG5349` 상품을 첫 번째로 노출할 수 있어, 상위 5개 후보의 상세 모델번호 완전 일치 검증으로 교체했다.

## 2026-07-29 — 네이버 상품 건너뜀 사유 로그 세분화

- [네이버 크롤링 로그] 사이트별 파싱 완료 로그의 `건너뜀`을 `품절`·`모델명 없음`·`파싱 오류`로 나눠 집계한다. 기존 총 건너뜀 수도 유지해 수집 제외와 실제 파싱 실패를 즉시 구분할 수 있게 했다.

## 2026-07-28 — 사이트별 모델명 추출 전략 통합

- [모델명 추출] `common/model_names.py`를 공통 추출기로 추가하고 괄호·슬래시·후행·본문 코드, 숫자-only 코드, 공백 복합 SKU, 복수 대체 모델을 사이트별 전략(`bracket_first`, `slash_first`, `suffix_single`, `suffix_grouped`, `auto`)에 따라 처리한다. 시즌·사이즈·수량·용량 표기는 공통 오탐 필터로 제외한다.
  - SUPERSEDED: 무신사 모델명을 대문자+숫자 스타일코드의 마지막 값만으로 추출 — 실제 아울렛 상품의 괄호 숫자 코드, 슬래시 코드, 후행 코드, 목록용 보조번호 표기를 누락해 공통 `slash_first` 전략으로 교체했다.
- [네이버·special 연동] `config.SEARCH_URLS[*].model_strategy`와 `SPECIAL_MODEL_STRATEGY`를 추가하고, 네이버 및 special 크롤러가 추출된 복수 모델을 각각 `MarketplaceProduct`로 저장하도록 통일했다.
- [네이버 수동 복구] 네이버는 실제 Chrome 채널·전용 `naver_cookies.json`을 사용한다. 상품 목록이 15초 내 나타나지 않으면 브라우저에서 로그인·보안 확인을 마친 뒤 Enter를 누를 때까지 사이트별로 대기하고, `q` 입력 시 해당 사이트만 중단한다.
- [무신사 URL] `/brand/{slug}` PLP와 현재 설정된 `/main/.../ranking`을 모두 지원하며, 랭킹 API의 `link.next`를 따라 수집한다.
  - SUPERSEDED: 무신사 크롤러가 브랜드 PLP URL만 지원 — 실제 설정 URL이 아울렛 랭킹으로 바뀌어 URL 형식별 API 분기로 확장했다.
- [검증] 실제 스토어 표기에서 수집한 괄호·마지막 슬래시·복합 SKU·숫자 대체 코드와 `230~250MM`·`2way` 오탐, 수동 입력 재개 경로를 `tests/test_model_names.py`에 고정했다.

## 2026-06-24 — smart_store 품절 상품 Kream 조회 제외

- `naver/crawler.py`: `smart_store` 상품 카드에서 `품절` 오버레이 또는 `일시품절` 배송 문구를 감지하면 수집하지 않도록 `_is_sold_out_product()` 추가.
- 품절 상품은 `marketplace_products.json`에 저장되지 않으므로 Kream 검색 대상에서도 자동 제외된다. `brand_store` 타입은 기존 동작을 유지한다.

## 2026-06-20 — 무신사 브랜드 PLP API 크롤러 추가

- `musinsa/crawler.py` 신규: `config.MUSINSA_SALE_URL`에서 brand slug와 query를 추출해 `api2/dp/v2/plp/goods`를 호출하고, `pagination.nextPageUrl`의 `hmacId`를 따라 전체 페이지를 수집한다.
- 무신사 가격은 DOM 카드에 표시되는 `finalPrice`를 우선 사용하고, 모델명은 대문자+숫자 스타일코드 패턴의 마지막 값을 우선 추출한다. 품절 상품과 모델코드 미추출 상품은 제외한다.
- `config.py`: 무신사 아디다스 무진장 캠페인 URL과 `SITE_DISCOUNT_RATES["musinsa"] = 0` 추가.
- `main.py`: STEP 1에 무신사 크롤링 블록 추가 (`musinsa_products.json` 저장). diff/Kream 검색은 기존 `*_products.json` 자동 로드 경로를 재사용한다.

## 2026-05-08 — 비교 맥락 식별자 naver → marketplace 일괄 변경

- `common/models.py`: `NaverProduct` → `MarketplaceProduct` (클래스명), `ArbitrageResult` 필드 `naver_site/price/url` → `marketplace_site/price/url`
- `kream/comparator.py`: 파라미터·루프 변수·ArbitrageResult 키워드 인자 전체 marketplace 로 통일
- `main.py`: import, 변수명(`naver_output/products`→`marketplace_output/products`), 파일명(`marketplace_products.json`), `_apply_sale_price` 두 번째 인자(`"marketplace"`)
- `config.py`: `SITE_DISCOUNT_RATES["naver"]` → `["marketplace"]`
- `diff_output.py`: `KEY_CONFIG` 키 `naver_products.json` → `marketplace_products.json`
- `kream/crawler.py`, `adidas/crawler.py`, `nike/crawler.py`, `special/crawler.py`: `NaverProduct` → `MarketplaceProduct`
- `special/crawler.py`: 공통 상품 타입을 `MarketplaceProduct`로 통일하고, 모델명 추출은 현재 `common.model_names`를 사용한다.
- `naver/` 디렉터리는 실제 네이버 크롤러이므로 유지하고 공통 데이터 타입·필드명만 `marketplace` 문맥으로 변경했다.

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
- SUPERSEDED (2026-08-05): 검색 결과 선택을 거래량 최다에서 첫 번째 상품(검색 결과 맨 왼쪽)으로 변경 — 유사 검색 오탐 때문에 상세 모델번호 완전 일치 검증으로 교체했다.
- 검색 간 딜레이 8~15초, 차단 시 지수 백오프 (30→60→120초)
  - SUPERSEDED (2026-04-13): 재시도마다 새 stealth 페이지 생성 — 페이지 풀 정합성을 위해 같은 페이지를 Kream 홈으로 재이동하도록 교체했다.

### common/browser.py
- `USER_AGENTS`: Chrome 130~136 기반 12종 (Mac 4, Windows 5, Linux 3)
- `_get_client_hints()`: UA와 정확히 일치하는 `sec-ch-ua` 헤더 자동 생성 (불일치 → 봇 신호 방지)
- 쿠키 영속화: `output/session/kream_cookies.json`

### naver/parser.py
- `price` 셀렉터를 fallback 후보 리스트로 변경 (DOM 구조 변경 시 자동 전환)
