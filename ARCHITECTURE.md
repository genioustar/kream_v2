# resell-sniper 아키텍처

## 개요
네이버 쇼핑몰(브랜드스토어·스마트스토어), 아디다스 공홈, 나이키 공홈에서 상품을 수집하고, Kream에서 동일 상품의 리셀 가격과 거래량을 조회하여 차익 거래 기회를 자동으로 탐색하는 파이프라인.

## 실행 방법

```bash
python chrome.py              # Chrome CDP 실행 — adidas·nike 크롤링 전 필수
python main.py                # 전체 파이프라인 (기본: full)
python main.py --mode crawl   # 전체 크롤링 (Kream 제외)
python main.py --mode kream   # Kream 검색만 (오늘자 *_products.json 사용)
python -m adidas.crawler      # 아디다스 단독 크롤링
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
  │           1. kream/crawler.py 로 Kream 검색 (페이지 풀 KREAM_MAX_CONCURRENCY개)
  │           2. kream/comparator.py 로 차익 비교 (누적)
  │           3. output/YYYYMMDD/arbitrage_results.json 즉시 저장
  │
  └── STEP 4: diff_output.py (전체 크롤 날짜에만 실행)
        오늘자 *_products.json 전체 diff → output/diff/YYYYMMDD_vs_YYYYMMDD/
        그 외 날짜는 STEP 2 직전에 이미 생성됨
```

## 디렉토리 구조

```
resell-sniper/
├── main.py               # 파이프라인 진입점 (--mode crawl|full|kream)
├── chrome.py             # Chrome CDP 실행. resolve_cdp_url()이 크롤러의 DevTools 주소를 정한다
├── config.py             # 사이트 URL, 필터 상수, 동시성 설정
├── diff_output.py        # 날짜별 JSON 변경분 비교 도구
│
├── common/               # 공유 모듈
│   ├── browser.py        # create_browser(), new_stealth_page(), USER_AGENTS
│   ├── logger.py         # get_logger()
│   └── models.py         # MarketplaceProduct, KreamProduct, ArbitrageResult, ItemDiff, FieldChange
│
├── adidas/               # 아디다스 공홈 크롤러
│   └── crawler.py        # crawl_adidas() — Extra Sale 신발, MarketplaceProduct 포맷 저장
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
│   ├── crawler.py        # crawl_marketplace() — 사이트별 병렬 크롤링
│   └── parser.py         # 네이버 HTML 파싱
│
├── kream/                # Kream 크롤러 + 가격 비교
│   ├── crawler.py        # init_kream_page(), search_kream()
│   ├── parser.py         # KREAM_SELECTORS, parse_kream_price(), parse_trade_count()
│   └── comparator.py     # find_arbitrage()
│
├── output/               # 크롤링 결과 저장소
│   ├── YYYYMMDD/
│   │   ├── marketplace_products.json
│   │   ├── adidas_products.json
│   │   ├── nike_products.json
│   │   ├── special_products.json
│   │   └── arbitrage_results.json
│   ├── diff/
│   │   └── YYYYMMDD_vs_YYYYMMDD/
│   │       ├── marketplace_products_diff.json
│   │       └── adidas_products_diff.json
│   └── session/
│       └── kream_cookies.json
│
├── logs/                 # 로그 파일
├── requirements.txt
└── .venv/
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
- 청크 처리: 모델 수 > 200이면 200개씩 분할, 각 청크 완료 후 `arbitrage_results.json` 저장, 다음 청크 전 30분 대기. `price_diff`는 정수로 저장하고 천 단위 표시는 로그에서만 한다.
- `NIKE_SALE_URL`·`ADIDAS_SALE_URL`이 없으면 해당 크롤러를 호출하지 않는다.

### common/browser.py
- `USER_AGENTS` — 12종의 Chrome UA 풀 (Mac/Windows/Linux, Chrome 130~136 최신 버전)
- `_get_client_hints(ua)` — UA에서 Chrome 버전·플랫폼을 추출해 `sec-ch-ua`, `sec-ch-ua-mobile`, `sec-ch-ua-platform` 헤더 생성
- `_load_cookies(context)` / `_save_cookies(context)` — `output/session/kream_cookies.json`에 쿠키 영속화
- `create_browser(headless)` — UA 1회 선택 후 컨텍스트 전체에 적용, `timezone_id="Asia/Seoul"` 설정, sec-ch-ua 헤더만 주입. `Sec-Fetch-*`와 `Accept-Encoding`은 Chromium 기본값에 맡긴다. 시작 시 쿠키 로드·종료 시 저장
- `new_stealth_page(context)` — playwright-stealth 적용 + webdriver 탐지 제거

### chrome.py
- `resolve_cdp_url()` — 크롤러가 붙을 DevTools 주소. 9222가 DevTools면 그 주소. 아니면 `output/session/cdp_port`에 기록된 포트.
- `main()` — 9222가 다른 프로세스에 점유돼 있으면 9222–9232 중 빈 포트로 Chrome을 띄운다. DevTools가 뜨면 포트를 기록하고 0으로 끝난다. 실패하면 1.

### nike/crawler.py
- `crawl_nike()` — 나이키 세일 신발 페이지 크롤링. **실제 Chrome CDP 연결 필수** (`connect_over_cdp(resolve_cdp_url())`). Kasada 탐지로 Playwright Chromium은 차단됨. URL 출처: `config.NIKE_SALE_URL` (미설정/빈 값이면 즉시 [] 반환). redirect 감지 키워드는 URL path 마지막 segment에서 자동 추출.
- `_collect_via_api(page, target_count=100)` — 스크롤로 Nike JS가 `product_wall` API 호출을 유도하고 `page.on("response", ...)` 로 JSON 응답을 인터셉트해 수집. scrollY 변화 없음 NO_CHANGE_LIMIT(6)회 연속이면 종료
- `_parse_api_product(item, crawled_at)` — API 응답 항목을 `MarketplaceProduct` 로 변환. 필드: `copy.title`, `copy.subTitle`, `prices.currentPrice`, `pdpUrl.url`, `productCode`
- `_extract_model_name(card_text, href)` — `_parse_api_product` 내 폴백. 스타일코드(CN8490-002) 우선, href 세그먼트 2순위, 첫 줄 3순위
- `_should_exclude(card_text, subtitle)` — EXCLUDE_KEYWORDS("kids", "어린이", "유아", "주니어") 포함 여부 검사
- 저장 포맷: `MarketplaceProduct` (site_name="nike") → `output/YYYYMMDD/nike_products.json`

### special/crawler.py
- `crawl_special()` — `config.SPECIAL_SALE_URL` 페이지를 JS 휴리스틱으로 크롤링. **실제 Chrome CDP 연결 필수** (`connect_over_cdp(resolve_cdp_url())`). 미설정/빈 값이면 즉시 [] 반환. CDP 연결 실패 시 [] 반환 + error 로그.
- `_JS_EXTRACT` — 3단계 휴리스틱으로 상품 카드 자동 탐지하는 JS 문자열
  - 전략 1: `<li>` 요소 중 img + a[href] + 가격패턴 모두 포함한 것
  - 전략 2: `<a[href]>` 요소 자체에 img + 가격패턴 포함한 것
  - 전략 3: 가격 텍스트 leaf 요소에서 위로 올라가며 형제 2개 이상인 조상 탐색
- `_to_marketplace_product(card, crawled_at)` — JS 추출 카드를 MarketplaceProduct로 변환. model_name은 `extract_model_names(name)` 첫 결과, 없으면 code 사용.
- 스크롤: `SCROLL_STEP_PX=800`, `SCROLL_STEP_DELAY_MS=1500`, scrollY 변화 없음 `NO_CHANGE_LIMIT(5)`회 연속 시 종료. seen_urls set으로 중복 제거.
- 저장 포맷: `MarketplaceProduct` (site_name="special") → `output/YYYYMMDD/special_products.json`

### adidas/crawler.py
- `crawl_adidas()` — Extra Sale 신발 페이지 전체 크롤링. **실제 Chrome CDP 연결 필수** (`connect_over_cdp(resolve_cdp_url())`). Kids 카테고리 자동 제외. 페이지네이션 처리. URL 출처: `config.ADIDAS_SALE_URL` (미설정/빈 값이면 즉시 [] 반환). redirect 감지 키워드는 URL path 마지막 segment에서 자동 추출.
- `_find_card_selector(page)` — CARD_SELECTORS 후보 목록에서 실제 DOM에 존재하는 셀렉터 자동 탐지. 전부 실패 시 URL·제목·본문 앞 500자를 WARNING 로그로 출력
- `_extract_products(page, card_selector)` — JS evaluate로 카드 일괄 파싱 (상품명·코드·세일가·Kids 여부)
- 상품명 추출: `href` URL 경로에서 `decodeURIComponent` → `-` 공백 치환 방식 (innerText 파싱 대비 UI 텍스트 오염 없음)
- 네비게이션 후 최종 URL·제목 INFO 로그. Extra Sale URL 이탈 시 WARNING (세일 종료/차단 감지)
- 저장 포맷: `MarketplaceProduct` (site_name="adidas") → `output/YYYYMMDD/adidas_products.json`

### naver/parser.py
- `SELECTORS` — 스토어 타입별 CSS 셀렉터 딕셔너리. `price` 키는 fallback 후보 리스트(list[str])로 관리
- `get_selectors(store_type)` — 스토어 타입에 맞는 셀렉터 딕셔너리 반환
- `parse_price(price_str)` — 가격 문자열에서 숫자 추출
- `extract_model_names(product_name)` — 상품명에서 모델명 목록 추출
- `page_origin(page_url)` — 카드를 연 페이지의 origin
- `clean_url(raw_url, base)` — 상대 URL을 `base` origin 기준 절대 URL로 변환. `base`는 호출자가 넘긴다.

### naver/crawler.py
- `_scroll_to_bottom(page, item_selector)` — 무한스크롤 처리
- `_extract_product(item, selectors, site_name, crawled_at, page_url)` — 단일 상품 카드 파싱. 상품 URL의 기준은 `page_url`의 origin.
- `_crawl_site(context, site_info)` — 단일 사이트 크롤링
- `crawl_marketplace()` — config.SEARCH_URLS 대상 병렬 크롤링. SEARCH_URLS 가 비어있거나 유효한 url 항목이 하나도 없으면 [] 반환. 항목별 url 누락 시 해당 사이트만 스킵하고 나머지는 진행.

### common/models.py
| 클래스 | 설명 |
|---|---|
| `MarketplaceProduct` | 공통 상품 포맷 (site_name, product_name, model_name, price, url, crawled_at, sale_price) — 네이버·아디다스 등 모든 소스 공유. sale_price는 SITE_DISCOUNT_RATES 할인 적용가, 미적용 시 None |
| `KreamProduct` | Kream 상품 (model_name, kream_name, kream_price, trade_count, kream_url) |
| `ArbitrageResult` | 차익 거래 결과 (양쪽 가격, 가격차, 거래량, URL, 체크 시각) |
| `ItemDiff` / `FieldChange` | diff_output.py 전용 변경 추적 모델 |

### kream/crawler.py
- `init_kream_page(context)` — stealth 페이지 생성 후 메인 페이지 1회 방문
- `search_kream(model_name, page)` — 차단 시 지수 백오프 재시도 후 홈으로 재이동
- `_search_kream_once(model_name, page)` — `https://kream.co.kr/search?keyword=` 문서로 이동한 뒤 결과 카드를 파싱. 헤더 검색창의 SPA 이동은 카드 DOM을 만들지 않는다.
- `_card_matches_model(name, model_name)` — 카드 이름에 검색한 모델코드가 있을 때만 채택. 같은 href는 한 번만 남긴다.
- 검색 간 딜레이: 8~15초 / 최대 재시도: 3회, 백오프: 30→60→120초

### kream/comparator.py
- `_effective_price(p)` — MarketplaceProduct의 sale_price가 있으면 그것을, 없으면 price를 반환
- `_is_opportunity(marketplace_price, kream_price, trade_count)` — 거래량·가격차 필터 판정
- `find_arbitrage(marketplace_products, kream_products_map)` — 비교 기준: `_effective_price(marketplace)`. ArbitrageResult.marketplace_price = effective price, price_diff = kream_price - effective_price

### diff_output.py
- `KEY_CONFIG` — 파일별 복합 비교 키 (명시 등록 파일)
- `DEFAULT_PRODUCTS_KEY = ("model_name", "site_name")` — `*_products.json` 미등록 파일 자동 적용 (신규 소스 추가 시 별도 등록 불필요)
- `compute_diff()` — added / modified 항목만 저장 (removed 제외)
- `diff_date_pair()` — 두 날짜 디렉토리의 `*_products.json` 전체 비교

### config.py
| 상수 | 기본값 | 설명 |
|---|---|---|
| `SEARCH_URLS` | 8개 사이트 | 네이버 카드 셀렉터로 여는 스토어. 쇼핑스토리 5002700579는 여기 없다 |
| `MIN_TRADE_COUNT` | 100 | Kream 거래량 최솟값 |
| `MIN_PRICE_DIFF` | 7,000 | 차익 최솟값 (원) |
| `KREAM_MAX_CONCURRENCY` | 2 | 동시 Kream 검색 페이지 수 |
| `OUTPUT_DIR` | "output" | 결과 저장 루트 디렉토리 |
| `NIKE_SALE_URL` | 미설정 (주석) | 나이키 성인 세일 신발 URL. 없으면 `main`이 크롤러를 호출하지 않는다 |
| `ADIDAS_SALE_URL` | 미설정 (주석) | 아디다스 Extra Sale 신발 URL. 없으면 `main`이 크롤러를 호출하지 않는다 |
| `SPECIAL_SALE_URL` | 쇼핑스토리 5002700579 | 범용 적응형 크롤러 대상. 네이버 카드 셀렉터로 열리지 않는 페이지의 단일 소유 |
| `SITE_DISCOUNT_RATES` | 전 사이트 0% | 소스 크롤러별 수동 할인율 (%). 키: marketplace·adidas·nike·special. 0이면 sale_price 미산출 |

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
8. 차단 감지 시 새 stealth 페이지 생성 + 지수 백오프 재시도 (30→60→120초)
