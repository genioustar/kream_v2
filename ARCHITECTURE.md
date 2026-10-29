# resell-sniper 아키텍처

## 개요
네이버 쇼핑몰(브랜드스토어·스마트스토어), 아디다스 공홈, 무신사, 나이키 공홈, special URL에서 상품을 수집하고, Kream에서 동일 상품의 리셀 가격과 거래량을 조회하여 차익 거래 기회를 자동으로 탐색하는 파이프라인.

## 실행 방법

```bash
python chrome.py              # Chrome CDP 실행 — adidas·nike·special 크롤링 전 필수
python main.py                # 전체 파이프라인 (기본: full)
python main.py --mode crawl   # 전체 크롤링 (Kream 제외)
python main.py --mode kream   # Kream 검색만 (오늘자 *_products.json 사용)
python -m adidas.crawler      # 아디다스 단독 크롤링
python -m musinsa.crawler     # 무신사 단독 크롤링
python -m nike.crawler        # 나이키 단독 크롤링
python -m special.crawler     # special 단독 크롤링
```

## 실행 흐름

```
main.py --mode full (기본)
  │
  ├── STEP 1: 전체 사이트 크롤링
  │     ├── naver/crawler.py   → output/YYYYMMDD/marketplace_products.json
  │     │     각 사이트별 병렬 크롤링 (config.SEARCH_URLS)
  │     ├── adidas/crawler.py  → output/YYYYMMDD/adidas_products.json
  │     │     Extra Sale 신발 전체 수집, Kids 카테고리 제외
  │     ├── musinsa/crawler.py → output/YYYYMMDD/musinsa_products.json
  │     │     브랜드 PLP 또는 아울렛 랭킹 API 수집, 품절/모델코드 미추출 상품 제외
  │     ├── nike/crawler.py    → output/YYYYMMDD/nike_products.json
  │     │     세일 신발 무한스크롤 수집, Kids/주니어 키워드 제외
  │     └── special/crawler.py → output/YYYYMMDD/special_products.json
  │           SPECIAL_SALE_URL JS 휴리스틱 자동 탐지 (미설정 시 스킵)
  │     * 오늘자 파일이 이미 있으면 스킵
  │
  ├── STEP 2~3: Kream 검색 + 차익 비교 (청크 단위 처리)
  │     ├── 모델명 소스 결정
  │     │     ├── 전체 크롤 날짜(1·10·20·30일) / kream 모드
  │     │     │     오늘자 *_products.json 전체에서 고유 model_name 추출
  │     │     └── 그 외 날짜
  │     │           diff 생성 후 모든 *_diff.json 의 added/modified 에서 model_name 추출
  │     ├── 200개 초과 시 200개씩 청크 분할 (청크 간 30분 대기)
  │     └── 각 청크마다:
  │           1. kream/crawler.py 로 Kream 검색 후 상세 모델번호 완전 일치 검증
  │              (페이지 풀 KREAM_MAX_CONCURRENCY개, 검색별 상위 5개 후보)
  │           2. kream/comparator.py 로 모델번호 재검증 + 차익 비교 (누적)
  │           3. output/YYYYMMDD/arbitrage_results.json 즉시 저장
  │
  └── STEP 4: diff_output.py (전체 크롤 날짜에만 실행)
        오늘자 *_products.json 전체 diff → output/diff/YYYYMMDD_vs_YYYYMMDD/
        그 외 날짜는 STEP 2 직전에 이미 생성됨
```

## 디렉토리 구조

```
resell-sniper/
├── AGENTS.md             # 에이전트 작업 규칙
├── ARCHITECTURE.md       # 현재 코드 기준 아키텍처 문서
├── CLAUDE.md             # Claude/Codex 작업 지침
├── README.md             # 사용자용 실행 안내
├── history.md            # 주요 변경 결정 기록
├── main.py               # 파이프라인 진입점 (--mode crawl|full|kream)
├── chrome.py             # Chrome CDP 디버깅 모드 실행 스크립트 (Windows/macOS 공용)
├── config.py             # 사이트 URL, 필터 상수, 동시성 설정
├── diff_output.py        # 날짜별 JSON 변경분 비교 도구
│
├── common/               # 공유 모듈
│   ├── browser.py        # create_browser(), new_stealth_page(), USER_AGENTS
│   ├── logger.py         # get_logger()
│   ├── model_names.py    # 사이트 전략별 모델명 추출·오탐 필터
│   └── models.py         # MarketplaceProduct, KreamProduct, ArbitrageResult, ItemDiff, FieldChange
│
├── adidas/               # 아디다스 공홈 크롤러
│   ├── __init__.py
│   └── crawler.py        # crawl_adidas() — Extra Sale 신발, MarketplaceProduct 포맷 저장
│
├── musinsa/              # 무신사 브랜드 PLP·아울렛 랭킹 API 크롤러
│   ├── __init__.py
│   └── crawler.py        # crawl_musinsa() — 무신사 상품, MarketplaceProduct 포맷 저장
│
├── nike/                 # 나이키 공홈 크롤러
│   ├── __init__.py
│   └── crawler.py        # crawl_nike() — 세일 신발 무한스크롤, MarketplaceProduct 포맷 저장
│
├── special/              # 범용 적응형 크롤러
│   ├── __init__.py
│   └── crawler.py        # crawl_special() — SPECIAL_SALE_URL JS 휴리스틱 자동 탐지
│
├── naver/                # 네이버 크롤러
│   ├── __init__.py
│   ├── crawler.py        # crawl_marketplace() — 사이트별 병렬 크롤링
│   └── parser.py         # 네이버 HTML 파싱
│
├── kream/                # Kream 크롤러 + 가격 비교
│   ├── __init__.py
│   ├── crawler.py        # init_kream_page(), search_kream()
│   ├── parser.py         # Kream 셀렉터, 가격·거래량·상세 모델번호 파싱/비교
│   └── comparator.py     # find_arbitrage()
│
├── output/               # 크롤링 결과 저장소
│   ├── YYYYMMDD/
│   │   ├── marketplace_products.json
│   │   ├── adidas_products.json
│   │   ├── musinsa_products.json
│   │   ├── nike_products.json
│   │   ├── special_products.json
│   │   └── arbitrage_results.json
│   ├── diff/
│   │   └── YYYYMMDD_vs_YYYYMMDD/
│   │       ├── marketplace_products_diff.json
│   │       └── adidas_products_diff.json
│   └── session/
│       ├── kream_cookies.json
│       └── naver_cookies.json
│
├── logs/                 # 로그 파일
├── plans/                # 작업 계획 산출물
├── tests/
│   ├── test_kream_model_match.py # Kream 상세 모델번호 검증·비교 회귀 테스트
│   └── test_model_names.py       # 모델명 규칙·크롤러 변환 회귀 테스트
├── requirements.txt
└── venv/
```

## 주요 모듈 설명

### main.py
- `--mode crawl` — STEP 1만 실행 (모든 사이트 크롤링 후 저장)
- `--mode full` — STEP 1~4 전체 실행 (기본값)
- `--mode kream` — STEP 2~3만 실행 (오늘자 `*_products.json` 로드 후 Kream 검색)
- `_load_all_products(output_dir)` — 오늘자 디렉토리의 모든 `*_products.json` 자동 감지·합산 로드
- `_load_products_json(path)` — 단일 `*_products.json` → `MarketplaceProduct` 리스트 복원
- `_apply_sale_price(products, source_key)` — STEP 1 각 크롤러 결과에 `config.SITE_DISCOUNT_RATES[source_key]` 할인율을 적용해 `sale_price` 부여. 0%면 sale_price=None 유지
- `_extract_models_from_diff(diff_path)` — diff 파일의 added/modified 항목에서 model_name 추출
- `_search_with_page_pool(model_name, page_pool)` — 페이지 풀 기반 Kream 검색
- `KREAM_CHUNK_SIZE = 200` — 청크 분할 기준 (모델 수가 이를 초과하면 분할)
- `KREAM_CHUNK_INTERVAL_SEC = 1800` — 청크 간 대기 시간 (30분)
- STEP 2~3 모델명 소스: 전체 크롤 날짜(1·10·20·30일)는 `*_products.json` 전체, 그 외 날짜는 `*_diff.json` 전체
- 청크 처리: 모델 수 > 200이면 200개씩 분할, 각 청크 완료 후 `arbitrage_results.json` 저장, 다음 청크 전 30분 대기

### common/browser.py
- `USER_AGENTS` — 12종의 Chrome 130~136 호환 UA 풀 (Mac/Windows/Linux)
- `_get_client_hints(ua)` — UA에서 Chrome 버전·플랫폼을 추출해 `sec-ch-ua`, `sec-ch-ua-mobile`, `sec-ch-ua-platform` 헤더 생성
- `_load_cookies(context, cookie_file)` / `_save_cookies(context, cookie_file)` — 호출 소스가 지정한 파일에 쿠키 영속화
- `create_browser(headless, channel, cookie_file, use_native_fingerprint)` — 기본은 UA 1회 선택·Client Hints 주입·Kream 쿠키 재사용. 소스별 브라우저 채널·쿠키 파일을 선택하고 실제 브라우저 fingerprint를 그대로 쓸 수 있다.
- `new_stealth_page(context)` — playwright-stealth를 적용하고 `navigator.webdriver`만 가린다. 플러그인 목록과 `window.chrome`은 브라우저 값 그대로 둔다.

### nike/crawler.py
- `crawl_nike()` — 나이키 세일 신발 페이지 크롤링. **실제 Chrome CDP 연결 필수** (`connect_over_cdp("http://127.0.0.1:9222")`). Kasada 탐지로 Playwright Chromium은 차단됨. URL 출처: `config.NIKE_SALE_URL` (미설정/빈 값이면 즉시 [] 반환). redirect 감지 키워드는 URL path 마지막 segment에서 자동 추출.
- `_collect_via_api(page, target_count=100)` — 스크롤로 Nike JS가 `product_wall` API 호출을 유도하고 `page.on("response", ...)` 로 JSON 응답을 인터셉트해 수집. scrollY 변화 없음 NO_CHANGE_LIMIT(6)회 연속이면 종료
- `_parse_api_product(item, crawled_at)` — API 응답 항목을 `MarketplaceProduct` 로 변환. 필드: `copy.title`, `copy.subTitle`, `prices.currentPrice`, `pdpUrl.url`, `productCode`
- `_extract_model_name(card_text, href)` — `_parse_api_product` 내 폴백. 스타일코드(CN8490-002) 우선, href 세그먼트 2순위, 첫 줄 3순위
- `_should_exclude(card_text, subtitle)` — EXCLUDE_KEYWORDS("kids", "어린이", "유아", "주니어") 포함 여부 검사
- 저장 포맷: `MarketplaceProduct` (site_name="nike") → `output/YYYYMMDD/nike_products.json`

### special/crawler.py
- `crawl_special()` — `config.SPECIAL_SALE_URL` 페이지를 JS 휴리스틱으로 크롤링. **실제 Chrome CDP 연결 필수** (`connect_over_cdp("http://127.0.0.1:9222")`). 미설정/빈 값이면 즉시 [] 반환. CDP 연결 실패 시 [] 반환 + error 로그.
- `SPECIAL_MODEL_STRATEGY`가 지원 목록에 없으면 브라우저 연결 전에 `ValueError`로 설정 오류를 알린다.
- `_JS_EXTRACT` — 3단계 휴리스틱으로 상품 카드 자동 탐지하는 JS 문자열
  - 전략 1: `<li>` 요소 중 img + a[href] + 가격패턴 모두 포함한 것
  - 전략 2: `<a[href]>` 요소 자체에 img + 가격패턴 포함한 것
  - 전략 3: 가격 텍스트 leaf 요소에서 위로 올라가며 형제 2개 이상인 조상 탐색
- `_to_marketplace_products(card, crawled_at, model_strategy)` — `SPECIAL_MODEL_STRATEGY`로 상품명 모델 후보를 모두 변환. 상품명에서 못 찾은 경우 영문이 포함된 URL code만 단일 후보로 폴백.
- 스크롤: `SCROLL_STEP_PX=800`, `SCROLL_STEP_DELAY_MS=1500`, scrollY 변화 없음 `NO_CHANGE_LIMIT(5)`회 연속 시 종료. `(URL, model_name)` 키로 중복 제거.
- 저장 포맷: `MarketplaceProduct` (site_name="special") → `output/YYYYMMDD/special_products.json`

### musinsa/crawler.py
- `crawl_musinsa()` — `config.MUSINSA_SALE_URL` 형식에 따라 브랜드 PLP 또는 main 랭킹 크롤러로 분기. URL 미설정/빈 값이면 즉시 [] 반환.
- `_extract_brand_slug(page_url)` — `/brand/{brand}/...` URL에서 브랜드 slug 추출.
- `_is_ranking_url(page_url)` — `/main/.../ranking` URL 판별.
- `_build_referer(page_url, brand)` — 캠페인 URL을 API Referer용 `/brand/{brand}/products?...` 형태로 정규화.
- `_build_first_api_url(page_url, brand, page_size)` — 사용자 URL의 query(`gf`, `sortCode` 등)를 보존하고 `brand`, `page=1`, `size=100`, `caller=FLAGSHIP`을 붙여 첫 API URL 생성.
- `_build_ranking_api_url(page_url)` — 랭킹 페이지 query를 `client.musinsa.com/api/home/web/v5/pans/ranking` URL로 변환.
- `_fetch_json(url, referer)` / `_fetch_json_async(url, referer)` — stdlib urllib 요청을 `asyncio.to_thread`로 실행.
- `_resolve_price(item)` — DOM 카드 가격과 일치하는 `finalPrice` 우선, 없으면 `couponPrice`, `price` 순으로 폴백.
- `_build_marketplace_products(...)` — 두 API의 공통 필드를 `slash_first` 모델 전략으로 변환하며 품절·모델코드 미추출 상품 제외.
- `_to_marketplace_products(item, crawled_at)` / `_to_ranking_marketplace_products(item, crawled_at)` — 브랜드 PLP/랭킹 API 항목을 공통 변환기로 전달.
- `_extract_ranking_items(data)` — 랭킹 API `MULTICOLUMN` 모듈의 `PRODUCT_COLUMN` 항목 평탄화.
- `_crawl_brand_products(sale_url, crawled_at)` — 브랜드 PLP `pagination.nextPageUrl`의 `hmacId`를 따라 전체 페이지 수집.
- `_crawl_ranking_products(sale_url, crawled_at)` — 랭킹 API `link.next`를 따라 최대 20페이지 수집.
- 브랜드 페이지네이션: 1페이지 응답의 `pagination.nextPageUrl`을 그대로 따라간다. 2페이지 이후 API는 `hmacId`가 필요하므로 page 번호를 직접 조립하지 않는다.
- 저장 포맷: `MarketplaceProduct` (site_name="musinsa") → `output/YYYYMMDD/musinsa_products.json`

### adidas/crawler.py
- `crawl_adidas()` — Extra Sale 신발 페이지 전체 크롤링. **실제 Chrome CDP 연결 필수** (`connect_over_cdp("http://127.0.0.1:9222")`). Kids 카테고리 자동 제외. 페이지네이션 처리. URL 출처: `config.ADIDAS_SALE_URL` (미설정/빈 값이면 즉시 [] 반환). redirect 감지 키워드는 URL path 마지막 segment에서 자동 추출.
- `_find_card_selector(page)` — CARD_SELECTORS 후보 목록에서 실제 DOM에 존재하는 셀렉터 자동 탐지. 전부 실패 시 URL·제목·본문 앞 500자를 WARNING 로그로 출력
- `_extract_products(page, card_selector)` — JS evaluate로 카드 일괄 파싱 (상품명·코드·세일가·Kids 여부)
- 상품명 추출: `href` URL 경로에서 `decodeURIComponent` → `-` 공백 치환 방식 (innerText 파싱 대비 UI 텍스트 오염 없음)
- 네비게이션 후 최종 URL·제목 INFO 로그. Extra Sale URL 이탈 시 WARNING (세일 종료/차단 감지)
- 저장 포맷: `MarketplaceProduct` (site_name="adidas") → `output/YYYYMMDD/adidas_products.json`

### common/model_names.py
- `MODEL_STRATEGIES` — `auto`, `bracket_first`, `slash_first`, `suffix_single`, `suffix_grouped` 허용 전략.
- `extract_model_names(product_name, strategy)` — 괄호·슬래시·후행·본문 후보를 전략 순서대로 평가하고 복수 모델명을 반환.
- 전략 선택: 괄호 중심 스토어는 `bracket_first`, 본문 마지막 `/ CODE`가 주류면 `slash_first`, 마지막 단일 코드는 `suffix_single`, 공백 복합 SKU는 `suffix_grouped`, 혼합 표기는 `auto`. 괄호 안 `/`는 복수 모델로 유지한다.
- 공통 오탐 필터: 시즌·연도(`26SS`, `2026 FW`, `2026-27`), 수량·팩·설명(`3P`, `2way`), 용량·사이즈(`11L`, `230~250MM`), 성별·등급(`D`, `GS`), 독립 영문 색상명 제외.
- 문맥 허용: 5자리 이상 숫자-only 코드, 숫자 선두/하이픈 코드, 괄호 안 영문-only 하이픈 코드, `suffix_grouped`의 공백 복합 SKU 및 영문-only 중간 세그먼트.
- 대문자/소문자는 판정 시에만 정규화하고 반환값은 원문을 보존한다.

### naver/parser.py
- `SELECTORS` — 스토어 타입별 CSS 셀렉터 딕셔너리. `price` 키는 fallback 후보 리스트(list[str])로 관리
- `get_selectors(store_type)` — 스토어 타입에 맞는 셀렉터 딕셔너리 반환
- `parse_price(price_str)` — 가격 문자열에서 숫자 추출
- `page_origin(page_url)` — 카드를 연 페이지의 origin
- `clean_url(raw_url, base)` — 상대 URL을 `base` origin 기준 절대 URL로 변환. `base`는 호출자가 넘긴다.

### naver/crawler.py
- `_is_sold_out_product(item, store_type)` — `smart_store` 카드에서 `품절` 오버레이 또는 `일시품절` 배송 문구를 감지하면 수집 대상에서 제외한다. 제외된 상품은 `marketplace_products.json`에 저장되지 않아 Kream 조회 대상에서도 빠진다.
- 네이버는 실제 Chrome 채널을 headful로 열고, `output/session/naver_cookies.json`을 Kream 쿠키와 분리해 재사용한다. 브라우저 고유 UA·Client Hints는 덮어쓰지 않는다.
- 페이지 생성은 `common.browser.new_stealth_page()`를 사용한다.
- `_is_naver_login_gate(url)` — 호스트가 `nid.naver.com`이면 로그인 화면이다.
- `_reopen_store(page, site_name, target_url)` — 로그인 화면에서 요청한 스토어 주소로 다시 이동한다.
- `_wait_for_product_list(page, item_selector, site_name, manual_input_lock, target_url, login_ready)` — 상품 목록이 15초 내 나타나지 않으면 열린 Chrome에서 로그인·보안 확인을 마친 사용자의 Enter 입력을 기다린다. 로그인 화면이면 상품 카드를 기다리지 않고, Enter 후 `target_url`로 다시 이동한다. 스토어 문서가 보이면 `output/session/naver_cookies.json`에 세션을 바로 저장하고 `login_ready`를 켠다. 같은 실행의 다른 로그인 탭은 그 신호로 추가 입력 없이 각자 주소로 다시 연다. 병렬 프롬프트는 잠금으로 직렬화하고 `q` 입력 시 해당 사이트를 중단한다.
- `_scroll_to_bottom(page, item_selector)` — 무한스크롤 처리
- `_extract_product(item, selectors, site_name, store_type, model_strategy, crawled_at, page_url)` — 단일 상품 카드 파싱. 상품 URL의 기준은 `page_url`의 origin이다. 성공 시 상품 목록과 `None`을, 건너뛸 때는 빈 목록과 `품절`·`모델명 없음`·`파싱 오류` 중 하나를 반환한다. 사이트 전략으로 모델명을 복수 변환한다.
- `_crawl_site(context, site_info, manual_input_lock)` — 단일 사이트 크롤링. `site_info.model_strategy`를 추출기에 전달하며 미설정 시 `auto`; 지원하지 않는 값은 크롤링 전에 해당 사이트 설정 오류로 처리한다. 완료 로그는 총 건너뜀 수와 품절·모델명 없음·파싱 오류별 건수를 함께 출력한다.
- `crawl_marketplace()` — config.SEARCH_URLS 대상 병렬 크롤링. SEARCH_URLS 가 비어있거나 유효한 url 항목이 하나도 없으면 [] 반환. 항목별 url 누락 시 해당 사이트만 스킵하고 나머지는 진행.

### common/models.py
| 클래스 | 설명 |
|---|---|
| `MarketplaceProduct` | 공통 상품 포맷 (site_name, product_name, model_name, price, url, crawled_at, sale_price) — 네이버·아디다스·무신사 등 모든 소스 공유. sale_price는 SITE_DISCOUNT_RATES 할인 적용가, 미적용 시 None |
| `KreamProduct` | 상세 페이지 모델번호 검증을 통과한 Kream 상품 (실제 model_name, kream_name, kream_price, trade_count, kream_url) |
| `ArbitrageResult` | 차익 거래 결과 (양쪽 가격, 가격차, 거래량, URL, 체크 시각) |
| `ItemDiff` / `FieldChange` | diff_output.py 전용 변경 추적 모델 |

### kream/crawler.py
- `init_kream_page(context)` — stealth 페이지 생성 후 메인 페이지 1회 방문
- Kream은 설치된 Chrome 채널을 headful로 열고, UA와 Client Hints는 그 브라우저 값을 쓴다.
- `search_kream(model_name, page)` — 차단 시 지수 백오프 재시도. 새 페이지를 만들지 않고 같은 페이지를 Kream 홈으로 재이동
- `_open_search_and_get_input(page)` — 입력창이 이미 보이면 바로 반환(검색 결과 페이지), 없으면 헤더 검색 버튼 클릭 후 반환
- `_search_kream_once(model_name, page)` — 검색 입력 → 타이핑 → Enter → 결과 파싱
- `_load_detail_model_number(page, product_url)` — Playwright 세션으로 후보 상세 URL을 열고 서버 응답 HTML의 `모델번호` 문단을 우선 파싱한 뒤, 없으면 반응형 중복 요소 중 첫 hidden 노드에 막히지 않도록 `attached` DOM 전체 텍스트로 폴백한다. 두 경로 모두 실패하면 `None`을 반환해 fail-closed 처리
- `_find_verified_kream_product(model_name, cards_data, page)` — 검색 결과 상위 `MAX_RESULTS=5`개 상세를 순서대로 확인하고 실제 모델번호가 검색어와 완전 일치하는 첫 상품만 반환
- 검색 결과 수로 정확도를 추정하지 않는다. 상세 모델번호 검증을 통과하지 못한 후보는 KreamProduct와 차익 결과에 저장하지 않는다.
- 검색 간 딜레이: 8~15초 / 최대 재시도: 3회, 백오프: 30→60→120초

### kream/parser.py
- `KREAM_SELECTORS` — 검색 카드와 상세 `모델번호` 문단 셀렉터
- `parse_kream_model_number(text)` — `모델번호 {값}` 문단에서 실제 모델번호 추출
- `parse_kream_model_number_html(detail_html)` — Playwright가 받은 서버 렌더링 상세 HTML의 문단에서 실제 모델번호 추출
- `normalize_model_number(model_number)` — NFKC·대소문자·공백/구분자 표기를 비교용으로 정규화
- `model_numbers_match(expected, actual)` — 정규화된 두 모델번호의 전체 문자열 일치 여부 판정. 부분 문자열은 허용하지 않는다.
- `parse_kream_price()` / `parse_trade_count()` — 검색 카드 가격·거래량 문자열 파싱

### kream/comparator.py
- `_effective_price(p)` — MarketplaceProduct의 sale_price가 있으면 그것을, 없으면 price를 반환
- `_is_opportunity(marketplace_price, kream_price, trade_count)` — 거래량·가격차 필터 판정
- `find_arbitrage(marketplace_products, kream_products_map)` — 가격·거래량 필터 전에 마켓 상품 모델번호와 Kream 상세 모델번호를 다시 완전 일치 비교한다. 가격 기준은 `_effective_price(marketplace)`이며 `ArbitrageResult.marketplace_price = effective price`, `price_diff = kream_price - effective_price`

### diff_output.py
- `KEY_CONFIG` — 파일별 복합 비교 키 (명시 등록 파일)
- `DEFAULT_PRODUCTS_KEY = ("model_name", "site_name")` — `*_products.json` 미등록 파일 자동 적용 (신규 소스 추가 시 별도 등록 불필요)
- `compute_diff()` — added / modified 항목만 저장 (removed 제외)
- `diff_date_pair()` — 두 날짜 디렉토리의 `*_products.json` 전체 비교

### config.py
| 상수 | 기본값 | 설명 |
|---|---|---|
| `SEARCH_URLS` | 8개 사이트 | 네이버 크롤링 대상. 각 항목의 `model_strategy`로 사이트별 표기 규칙 선언 |
| `MIN_TRADE_COUNT` | 100 | Kream 거래량 최솟값 |
| `MIN_PRICE_DIFF` | 5,000 | 차익 최솟값 (원) |
| `KREAM_MAX_CONCURRENCY` | 2 | 동시 Kream 검색 페이지 수 |
| `OUTPUT_DIR` | "output" | 결과 저장 루트 디렉토리 |
| `NIKE_SALE_URL` | 미설정 (주석 예시만 존재) | 나이키 성인 세일 신발 URL (빈 값/미설정 시 나이키 크롤링 스킵) |
| `ADIDAS_SALE_URL` | 미설정 (주석 예시만 존재) | 아디다스 Extra Sale 신발 URL (빈 값/미설정 시 아디다스 크롤링 스킵) |
| `MUSINSA_SALE_URL` | musinsa.com/main/outlet/ranking?...categoryCode=107002 | 무신사 아울렛 신발 랭킹 URL. `/brand/{slug}` URL도 지원 |
| `SPECIAL_SALE_URL` | 미설정 (주석 예시만 존재) | 범용 적응형 크롤러 대상 URL (빈 값/미설정 시 special 크롤링 스킵) |
| `SPECIAL_MODEL_STRATEGY` | "auto" | special 상품명 모델 추출 전략 |
| `SITE_DISCOUNT_RATES` | marketplace 0%, adidas 30%, musinsa 0%, nike 0%, special 0% | 소스 크롤러별 수동 할인율 (%). 0이면 sale_price 미산출 |

## 새 크롤링 소스 추가 방법

1. `{소스명}/crawler.py` 생성 — `crawl_{소스명}()` 구현, `MarketplaceProduct` 포맷으로 반환
2. `main.py` STEP 1에 크롤링 호출 및 `{소스명}_products.json` 저장 추가
3. 나머지(Kream 검색 대상 포함·diff 생성·차익 비교)는 자동 처리됨

**참고:** Phase 1 에서 추가된 `nike/crawler.py` 가 이 패턴의 실제 사례이다.

## 탐지 우회 전략
1. Playwright-stealth 적용으로 자동화 시그니처 제거
2. UA 풀에서 랜덤 선택 — 컨텍스트 생성 시 1회 고정 (페이지마다 변경 시 불일치 → 봇 신호)
3. sec-ch-ua/sec-ch-ua-platform 헤더 — UA와 정확히 일치하는 Client Hints 자동 생성
4. timezone_id="Asia/Seoul" — 한국 사용자 시간대로 고정
5. 쿠키 영속성 — 세션 쿠키를 `output/session/kream_cookies.json`에 저장·재사용
6. 검색 방식: 검색창 클릭 + 키보드 타이핑 + Enter
7. 사람처럼 행동: 랜덤 딜레이, 마우스 이동, 스크롤
8. 차단 감지 시 같은 페이지를 Kream 홈으로 재이동 + 지수 백오프 재시도 (30→60→120초)
