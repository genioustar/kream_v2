# resell-sniper 아키텍처

## 개요
네이버 쇼핑몰(브랜드스토어·스마트스토어), 아디다스 공홈, 무신사, SSF SHOP, 나이키 공홈, special URL에서 상품을 수집하고, Kream에서 동일 상품의 리셀 가격과 거래량을 조회하여 차익 거래 기회를 자동으로 탐색하는 파이프라인.

## 실행 방법

```bash
python chrome.py              # 선택: ego lite Task Space 연결 점검
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
  │     │     각 사이트별 순차 크롤링 (config.SEARCH_URLS)
  │     ├── adidas/crawler.py  → output/YYYYMMDD/adidas_products.json
  │     │     설정한 세일 목록 전체 수집, Kids 카테고리 제외
  │     ├── musinsa/crawler.py → output/YYYYMMDD/musinsa_products.json
  │     │     브랜드 PLP 또는 아울렛 랭킹 API 수집, 품절/모델코드 미추출 상품 제외
  │     ├── ssfshop/crawler.py → output/YYYYMMDD/ssfshop_products.json
  │     │     ADIDAS KOREA 브랜드관 전체(의류·잡화·신발), currentPage 순회
  │     ├── nike/crawler.py    → output/YYYYMMDD/nike_products.json
  │     │     세일 신발 무한스크롤 수집, Kids/주니어 키워드 제외
  │     └── special/crawler.py → output/YYYYMMDD/special_products.json
  │           SPECIAL_SALE_URL JS 휴리스틱 자동 탐지 (빈 문자열이면 비활성, 스킵)
  │     * 오늘자 파일이 이미 있으면 스킵
  │
  ├── STEP 2~3: Kream 검색 + 차익 비교 (청크 단위 처리)
  │     ├── 모델명 소스 결정
  │     │     ├── 전체 크롤 날짜(1·10·20·30일)
  │     │     │     오늘자 *_products.json 전체에서 고유 model_name 추출
  │     │     └── 그 외 날짜 (kream 모드 포함)
  │     │           diff 생성 후 모든 *_diff.json 의 added/modified 에서 model_name 추출
  │     ├── 200개 초과 시 200개씩 청크 분할 (추가 휴식 없음)
  │     └── 각 청크마다:
  │           1. kream/crawler.py 로 Kream 검색 후 상세 모델번호 완전 일치 검증
  │              (단일 탭 재사용, 검색별 상위 5개 후보)
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
├── chrome.py             # ego lite Task Space 에서 webdriver 표시 점검
├── ego/
│   └── space_host.mjs    # Task Space 명령 루프
├── config.py             # 사이트 URL, 필터 상수, 전환 대기 설정
├── diff_output.py        # 날짜별 JSON 변경분 비교 도구
│
├── common/               # 공유 모듈
│   ├── browser.py        # create_browser() — ego lite Task Space 하나·작업 페이지 1개
│   ├── ego_session.py    # EgoClient, EgoPage — ego-browser 로컬 포트 명령
│   ├── pacing.py        # pause(), check_response(), CrawlBlockedError
│   ├── logger.py         # get_logger()
│   ├── model_names.py    # 사이트 전략별 모델명 추출·오탐 필터
│   └── models.py         # MarketplaceProduct, KreamProduct, ArbitrageResult, ItemDiff, FieldChange
│
├── adidas/               # 아디다스 공홈 크롤러
│   ├── __init__.py
│   └── crawler.py        # crawl_adidas() — 세일 목록, HTTP 진단·MarketplaceProduct 포맷 저장
│
├── musinsa/              # 무신사 브랜드 PLP·아울렛 랭킹 API 크롤러
│   ├── __init__.py
│   └── crawler.py        # crawl_musinsa() — 무신사 상품, MarketplaceProduct 포맷 저장
│
├── ssfshop/              # SSF SHOP 브랜드관 목록 크롤러
│   ├── __init__.py
│   └── crawler.py        # crawl_ssfshop() — ADIDAS KOREA 전체 상품, MarketplaceProduct 포맷 저장
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
│   ├── crawler.py        # crawl_marketplace() — 사이트별 순차 크롤링
│   ├── diagnostics.py    # 비로그인 접근 상태·요청 진단과 제한 시 중단
│   ├── public_search.py  # 공개 스토어 UI 검색·조건과 완전성 검증
│   ├── checkpoint.py     # 같은 날 완료된 스토어 결과 재사용
│   └── parser.py         # 네이버 HTML 파싱
│
├── kream/                # Kream 크롤러 + 가격 비교
│   ├── __init__.py
│   ├── crawler.py        # init_kream_page(), search_kream()
│   ├── parser.py         # Kream 셀렉터, 가격·거래량·상세 모델번호 파싱/비교
│   ├── comparator.py     # find_arbitrage()
│   └── history_marker.py # 직전 날짜 결과와 비교해 저장 이력·가격 변동 표시
│
├── output/               # 크롤링 결과 저장소
│   ├── YYYYMMDD/
│   │   ├── marketplace_products.json
│   │   ├── adidas_products.json
│   │   ├── musinsa_products.json
│   │   ├── ssfshop_products.json
│   │   ├── nike_products.json
│   │   ├── special_products.json
│   │   ├── arbitrage_results.json
│   │   ├── diagnostics/  # naver_<run_id>_<site>.json
│   │   ├── checkpoints/naver/ # 설정 해시별 완료 결과
│   │   └── validation/   # 독립 DOM/JSON 검증 산출물
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
│   ├── test_adidas_http.py       # HTTP 상태·재시도·진단·페이지 진행 회귀 테스트
│   ├── test_sequential_crawling.py # 순차 처리·휴식·접근 제한 중단 회귀 테스트
│   ├── test_browser.py           # 독립 Chrome 실행·단일 탭·실패 정리 테스트
│   ├── test_naver_diagnostics.py # 보호·로그인·HTTP 제한·개인정보 제외 회귀 테스트
│   ├── test_naver_checkpoint.py  # 날짜/설정별 캐시·실패 후 재개 회귀 테스트
│   ├── test_naver_collection.py  # 파싱 실패·최종 보호 감지 시 완료 저장 방지, 상품 URL origin 회귀(카드별·일괄 경로)
│   ├── test_naver_parser.py      # page_origin·clean_url(base 필수) 회귀 테스트
│   ├── test_naver_public_search.py # 공개 검색 UI·검색 조건 검증 회귀 테스트
│   ├── test_kream_model_match.py # Kream 상세 모델번호 검증·비교 회귀 테스트
│   ├── test_history_marker.py    # 직전 결과 비교 표시(new/same/changed) 회귀 테스트
│   ├── test_model_names.py       # 모델명 규칙·크롤러 변환 회귀 테스트
│   └── test_ssfshop_collection.py # SSF SHOP 목록 파싱·페이지 순회·실패 시 중단 회귀 테스트

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
- `KREAM_CHUNK_SIZE = 200` — 청크 분할 기준 (모델 수가 이를 초과하면 분할)
- STEP 2~3 모델명 소스: 전체 크롤 날짜(1·10·20·30일)는 `*_products.json` 전체, 그 외 날짜는 `*_diff.json` 전체
- 검색할 모델이 없으면 브라우저를 열지 않는다. diff에 변경이 없으면 전체 모델 검색으로 폴백하지 않는다.
- Kream 중단 시 완료된 모델의 차익 결과를 저장한 뒤 예외로 종료한다. 완료 모델이 하나도 없으면 기존 결과 파일을 덮어쓰지 않는다.
- 청크 처리: 모델 수 > 200이면 200개씩 분할, 각 청크 완료 후 `arbitrage_results.json` 저장, 추가 휴식 없이 다음 청크 진행
- 청크 루프 전 `load_previous_price_map(output_dir)`를 1회 호출해 직전 결과를 로드하고, 저장 시 각 항목에 `mark_result()`로 표시를 붙인다.

### common/browser.py
- `create_browser()` — 사용자의 ego lite 안에서 Task Space `resell-sniper` 를 연다. 별도 프로필과 디버깅 포트로 브라우저를 띄우지 않는다.
- `_CrawlerTabs.new_page()` — 그 Space 의 작업 페이지 1개만 연다. 기존 작업 페이지를 닫으면 다음 페이지를 연다.
- 연결이 끝나면 그 Task Space 만 닫는다. ego lite 앱과 사용자의 다른 Space 는 닫지 않는다.
- Space 는 기본 프로필을 쓴다. 쿠키는 그 프로필을 따르는 ego lite 로그인과 같다. 사용자의 다른 탭은 이 Space 로 옮기지 않는다.

### common/ego_session.py
- `EgoClient` — `ego-browser nodejs` 로 `ego/space_host.mjs` 를 실행한다. Python 이 연 로컬 포트로 명령과 응답을 주고받는다.
- `EgoPage` — 크롤러가 쓰던 페이지 호출을 Task Space 페이지로 전달한다. 키보드 입력, 보이는 요소 확인, `p:has-text()` 텍스트 읽기를 포함한다. 응답 이벤트는 Network 기록을 그대로 넘긴다. Nike 상품 목록 응답만 본문을 가져온다.

### common/pacing.py
- `pause(min_seconds, max_seconds, reason)` — 범위를 검사하고 지정된 구간의 대기 시간을 로그로 남긴 후 쉰다.
- `check_response(response)` — Kream 홈·상세 응답의 HTTP 403/429를 `CrawlBlockedError`로 전달한다. 네이버는 전용 진단 모듈로 처리한다.
- `CrawlBlockedError` — 후속 요청과 자동 재시도를 중단할 접근 제한 예외.

### chrome.py
- `check_connection()` — Task Space 에서 `about:blank` 를 열고 `navigator.webdriver === true` 가 아닌지 확인한 뒤 그 Space 를 닫는다.

### nike/crawler.py
- `crawl_nike()` — 나이키 세일 신발 페이지 크롤링. **독립 Chrome 창 실행** (`create_browser()`). URL 출처: `config.NIKE_SALE_URL` 직접 읽기. 빈 문자열이면 비활성, 즉시 [] 반환. redirect 감지 키워드는 URL path 마지막 segment에서 자동 추출.
- `_collect_via_api(page, target_count=100)` — 스크롤로 Nike JS가 `product_wall` API 호출을 유도하고 `page.on("response", ...)` 로 JSON 응답을 인터셉트해 수집. scrollY 변화 없음 NO_CHANGE_LIMIT(6)회 연속이면 종료
- `_parse_api_product(item, crawled_at)` — API 응답 항목을 `MarketplaceProduct` 로 변환. 필드: `copy.title`, `copy.subTitle`, `prices.currentPrice`, `pdpUrl.url`, `productCode`
- `_extract_model_name(card_text, href)` — `_parse_api_product` 내 폴백. 스타일코드(CN8490-002) 우선, href 세그먼트 2순위, 첫 줄 3순위
- `_should_exclude(card_text, subtitle)` — EXCLUDE_KEYWORDS("kids", "어린이", "유아", "주니어") 포함 여부 검사
- 저장 포맷: `MarketplaceProduct` (site_name="nike") → `output/YYYYMMDD/nike_products.json`

### special/crawler.py
- `crawl_special()` — `config.SPECIAL_SALE_URL` 을 직접 읽어 JS 휴리스틱으로 크롤링. **독립 Chrome 창 실행** (`create_browser()`). 빈 문자열이면 비활성, 즉시 [] 반환. Chrome 실행 실패 시 오류를 전달한다.
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

### ssfshop/crawler.py
- `crawl_ssfshop()` — `config.SSFSHOP_SALE_URL`을 직접 읽어 ADIDAS KOREA 브랜드관 목록을 수집한다. URL이 비어 있으면 즉시 `[]`. 경로의 첫 세그먼트가 브랜드관 slug다. 단독 실행 진입점은 없다.
- 순회: 쿼리의 `currentPage`는 무시하고 1페이지부터 덮어쓴다. 한 페이지의 상품 영역은 `<section id="goods_container">` 안의 `li.god-item`만이다. 컨테이너가 있고 카드가 0개면 종료한다. 컨테이너가 없으면 예외다. 페이지 요청 사이에만 `pause(2.5, 4.5, "SSF SHOP 다음 목록 페이지 요청 전 대기")`를 호출하고, 빈 페이지 뒤에는 쉬지 않는다. 안전 상한은 `MAX_PAGES = 150`.
- 실패: `HTTPError`는 `check_response`로 넘긴다. 403/429는 `CrawlBlockedError`, 그 외 HTTP 오류·타임아웃은 그대로 전파한다. 중간 페이지 실패, 컨테이너가 없는 페이지, 상한까지 카드가 남아 있으면 일부 결과를 반환하지 않고 예외를 올린다. 첫 페이지 컨테이너가 있고 카드가 0개면 `[]`.
- 가격: 카드 가격 칸의 `판매가` 숫자만 저장한다. 원가·할인율은 쓰지 않는다. 판매가에는 추가할인이 이미 반영되어 있고 `SITE_DISCOUNT_RATES["ssfshop"] = 0`이라 `sale_price`는 산출하지 않는다.
- 카드 변환: 상품명은 공백을 정리한 `<span class="name">`이다. 모델코드는 `suffix_single`로 뽑는다. 모델코드가 여러 개 나오면 이름 끝의 마지막 하나만 쓴다. 상세 URL은 `https://www.ssfshop.com/{slug}/{godNo}/good`(`view-godno`, 추적 쿼리 없음). `(url, model_name)`으로 중복을 제거한다. 이름·판매가·모델코드·godNo가 없으면 debug 로그 후 건너뛴다.
- 호출 경로: `_brand_slug`, `_page_url`, `_detail_url`, `_fetch_html`/`_fetch_html_async`, `_GoodsListParser`, `_open_container`, `_note_section_end`, `_raise_if_unclosed`, `_parse_cards`, `_normalize_text`, `_products_from_card`, `_sale_price`.
- 목록 파서는 `#goods_container`가 `section`으로 한 번 열리는 것만 컨테이너로 본다. 컨테이너 안에서만 태그 이름 스택으로 짝을 검사한다. 끝 태그가 스택 맨 위와 다르면 `RuntimeError`다. `HTML_VOID_ELEMENTS`의 void 요소는 스택에 넣지 않는다. 컨테이너 안 `<section>` 시작은 예외다. 컨테이너가 없거나 두 번 열리면 예외다. 문서의 section 깊이가 0 아래로 내려가면 짝 없는 `</section>`으로 예외다. 파싱이 끝났는데 컨테이너가 열린 채여도 예외이며 메시지에 마지막으로 본 godNo가 들어간다. 한 카드에서 이름 span 또는 가격 span 캡처가 두 번 열리면 예외다. 컨테이너가 있고 카드가 0개일 때만 정상 종료다. 컨테이너 밖의 section 시작·끝만 깊이로 세고, 그 외 컨테이너 밖 태그는 검사하지 않는다.
- 저장 포맷: `MarketplaceProduct` (site_name="ssfshop") → `output/YYYYMMDD/ssfshop_products.json`

### adidas/crawler.py
- `crawl_adidas()` — `config.ADIDAS_SALE_URL` 을 직접 읽어 세일 목록을 독립 Chrome에서 수집한다. 빈 문자열이면 비활성(`[]`), 접근·렌더링·페이지 진행 실패는 예외로 구분하며 부분 수집 결과를 반환하지 않는다. Kids와 중복 모델코드를 제외한다.
- `_navigate(page, sale_url, diagnostics)` — 문서 응답 상태를 확인한다. 문서 429만 최대 2회 재시도하며 `_retry_delay(retry_after, attempt)`로 Retry-After 초/HTTP-date 또는 5·10초 대기를 적용한다. 서버 지정 대기가 `MAX_RETRY_WAIT_SEC=60`을 초과하면 일찍 재시도하지 않고 중단한다. `MAX_RATE_LIMIT_RETRIES=2`.
- `_raise_http_error(status, url, retry_after)` — 상태별 안내를 포함한 예외. 403과 재시도 불가/소진 429는 `CrawlBlockedError`로 파이프라인에도 전파한다.
- `_ResponseDiagnostics` — `on_response()`로 문서 상태와 document/XHR/fetch HTTP 오류를 기록한다. `check()`는 메인 문서 및 adidas.co.kr 하위 호스트의 `/api/` 상품·PLP·검색 경로 실패를 전달한다. 실측한 개별 상품 보조 경로 `/plp-app/api/product/{code}`와 장바구니·분석 요청은 진단 전용이며 목록 DOM으로 수집한다. `save(error)`는 실패 시 `output/YYYYMMDD/diagnostics/adidas_HHMMSS_ffffff.json`과 화면 PNG를 남긴다. URL·상태·Retry-After·본문 일부만 저장하며 쿠키/인증 헤더는 저장하지 않는다. 닫힌 페이지에서도 가능한 JSON 진단을 유지한다.
- `_find_card_selector(page, diagnostics)` — `CARD_SELECTORS`를 공통 15초 한도에서 탐지한다. HTTP 오류를 중간 확인하며 카드가 없으면 수집 실패를 보고한다.
- `_extract_products(page, card_selector, seen_codes)` — DOM 카드의 상품명·코드·세일가·Kids 여부를 추출한다. 상품명은 링크 경로를 디코딩하고 하이픈을 공백으로 바꾼다.
- 다음 버튼이 없거나 비활성화면 종료한다. 필터 전 상품 링크 목록으로 동일 페이지 반복과 빈 페이지를 감지한다. 설정 URL과 다른 경로로 리다이렉트되면 실패로 처리한다.
- 저장 포맷: `MarketplaceProduct` → `output/YYYYMMDD/adidas_products.json`. 공통 브라우저·개인 로그인·프로필 운용은 변경하지 않는다.

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
- `page_origin(page_url)` — scheme과 netloc이 있으면 `scheme://netloc`을 반환하고, 없으면 `page_url`을 그대로 반환한다.
- `clean_url(raw_url, base)` — 상대 URL을 절대 URL로 변환한다. `base`는 필수 인자이며 기본값이 없다. 빈 값이면 None, `http`로 시작하면 그대로, `//`로 시작하면 `https:`를 붙이고, `/`로 시작하면 base 끝의 `/`를 뺀 뒤 경로를 붙인다. 그 외는 그대로 반환한다.

### naver/crawler.py
- `_is_sold_out_product(item, store_type)` — `smart_store` 카드에서 `품절` 오버레이 또는 `일시품절` 배송 문구를 감지하면 수집 대상에서 제외한다. 제외된 상품은 `marketplace_products.json`에 저장되지 않아 Kream 조회 대상에서도 빠진다.
- 네이버는 `create_browser()`로 독립 Chrome 창을 열고 사이트마다 작업 탭을 순차 생성한다. 기존 Chrome 로그인 상태는 공유하지 않는다.
- `_wait_for_product_list(page, item_selector, site_name, diagnostics)` — 상품 목록을 15초간 기다린다. 로그인·보안 확인 화면이면 종료하지 않고 Enter를 기다린 뒤 30초간 목록을 다시 확인한다. `q`는 그 스토어를 중단한다. 네트워크 오류와 목록 타임아웃은 후속 스토어를 중단한다.
- `_scroll_to_bottom(page, item_selector, diagnostics)` — 매 스크롤과 대기 중 접근 상태를 감시한다. 스크롤 간 3.5초 대기, 최대 30회, 카드 수가 3회 연속 같으면 종료한다.
- `_extract_product(item, selectors, site_name, store_type, model_strategy, crawled_at, origin)` — 단일 상품 카드 파싱. 성공 시 상품 목록과 `None`을, 건너뛸 때는 빈 목록과 `품절`·`모델명 없음`·`파싱 오류` 중 하나를 반환한다. 사이트 전략으로 모델명을 복수 변환한다. 상품 링크는 `clean_url(href, origin)`이다.
- `_product_from_card(card, site_name, model_strategy, crawled_at, origin)` — 일괄로 읽은 카드 dict를 같은 규칙으로 상품으로 변환한다. 상품 링크는 `clean_url(href, origin)`이다.
- `_collect_products(page, selectors, site_name, store_type, model_strategy, crawled_at, total_count, diagnostics)` — 카드 전체를 한 번에 읽고, 실패하면 카드별로 다시 읽는다. 두 경로 모두 수집 시작 시점의 `page_origin(page.url)`을 `origin`으로 넘긴다.
- `_crawl_site(context, site_info, run_id=None)` — 기본 `direct`는 `domcontentloaded`로 이동하며, `navigation="store_search_ui"`는 공개 홈의 검색 UI를 사용한다. 목록·스크롤·파싱·최종 반환까지 진단으로 감시한다. 설정 오류를 검증하고 성공/실패 보고서와 진단 예외를 보존한다. 품절·모델명 없음은 정상 제외이며 실제 파싱 오류가 있으면 완료 캐시를 만들지 않도록 실패 처리한다.
- `crawl_marketplace()` — config.SEARCH_URLS를 단일 작업 탭으로 순차 처리한다. 같은 날 완료 캐시를 우선 읽고 모두 있으면 브라우저 실행도 생략한다. 실제 네트워크 수집 사이에만 1~3초 쉬며 정상 종료한 스토어만 캐시에 저장한다. 수집 실패·접근 제한 시 후속 스토어를 열지 않는다. 빈 설정은 [] 반환, URL 누락 항목은 건너뛴다.

### naver/diagnostics.py
- `safe_url(url)` — 기록할 URL에서 쿼리·fragment·사용자 인증정보를 제외한다.
- `classify_access(...)` — 정확한 보호조치/로그인 URL 또는 화면의 강한 신호와 인증 쿠키 존재 여부로 접근 상태를 분류한다. 일반 로그인 링크는 신호로 사용하지 않는다.
- `NaverAccessError` / `NaverCollectionError` — 진단 코드와 보고서 경로를 포함한 접근 제한/수집 실패 예외. 계정 보호는 `CrawlBlockedError` 계열로 자동 재시도하지 않는다.
- `NaverDiagnostics` — 네이버 document/XHR/fetch의 요청·HTTP 상태·실패 수와 안전한 이벤트를 수집한다. 주 문서와 알려진 상품 목록 API의 403/429, 계정 보호는 중단 신호다. 로그인·보안 확인은 `pause_reason`만 남기고 수집을 유지한다. 인증 쿠키는 기록만 한다. 광고·개인화 보조 응답은 진단만 남긴다. `attach()`로 감시 시작, `check()`/`run()`으로 접근 상태를 점검하고 중단 신호면 작업을 취소, `finish()`로 결과 저장, `close()`로 리스너 정리.
- 인증 쿠키는 존재 여부만 기록한다. 응답 본문·헤더·쿠키 값·입력값·페이지 제목·HTML·스크린샷은 저장하지 않는다. 진단은 `output/YYYYMMDD/diagnostics/naver_<run_id>_<site>.json`에 저장하며 저장 실패가 원래 제한 오류를 덮어쓰지 않는다. 내부 서버 판정 사유를 확정하는 기능은 아니다.

### naver/checkpoint.py
- `CHECKPOINT_VERSION` — 완료 캐시 형식 버전. 현재 값은 2. 경로 해시(identity)와 저장 JSON의 `version`에 함께 기록한다. 상품 URL 생성 규칙처럼 저장 형식이 바뀌면 올려, 이전 형식의 같은 날 파일은 조회하지 않고 내용의 버전이 달라도 복원하지 않는다.
- `load_checkpoint(site_info)` — 날짜와 URL(검색 조건 포함)·스토어명·타입·모델 추출 전략·접속 방식·`CHECKPOINT_VERSION`의 해시가 일치하는 완료 결과만 복원한다. 버전이 다르거나 손상·불완전·다른 날짜 상품·스키마 오류는 재사용하지 않는다.
- `save_checkpoint(site_info, products)` — 정상 완료 결과를 `output/YYYYMMDD/checkpoints/naver/<hash>.json`에 `version=CHECKPOINT_VERSION`으로 원자적으로 저장한다. 최종 상품 파일과 분리하여 부분 결과가 Kream 입력으로 섞이지 않게 한다. 할인율은 기존 main.py 최종 저장 단계에서 적용한다.

### naver/public_search.py
- `navigate_public_search(page, url, diagnostics, timeout_ms)` — 공개 홈에서 검색창을 열고 설정 URL의 검색어와 페이지 크기를 UI로 적용한다. 정확도순·이미지형·첫 페이지와 지원 페이지 크기만 허용하고, 검색 결과 제목·최종 URL·총수·카드 수를 검증한다. 홈 문서가 로그인 페이지로 떨어지면 중단하지 않고 Enter를 기다린 뒤 홈 이동을 다시 한다. 비공개 API 호출은 하지 않는다.

### common/models.py
| 클래스 | 설명 |
|---|---|
| `MarketplaceProduct` | 공통 상품 포맷 (site_name, product_name, model_name, price, url, crawled_at, sale_price) — 네이버·아디다스·무신사 등 모든 소스 공유. sale_price는 SITE_DISCOUNT_RATES 할인 적용가, 미적용 시 None |
| `KreamProduct` | 상세 페이지 모델번호 검증을 통과한 Kream 상품 (실제 model_name, kream_name, kream_price, trade_count, kream_url) |
| `ArbitrageResult` | 차익 거래 결과 (양쪽 가격, 가격차, 거래량, URL, 체크 시각) |
| `ItemDiff` / `FieldChange` | diff_output.py 전용 변경 추적 모델 |

### kream/crawler.py
- `init_kream_page(context)` — 기존 Chrome 세션에 새 탭 생성 후 메인 페이지 1회 방문
- `search_kream(model_name, page)` — 추가 휴식 없이 한 번만 시도한다. 차단/입력 실패/카드 대기 타임아웃은 후속 요청을 중단한다.
- `iter_kream_results(model_names, context)` — 탭 하나를 생성해 모든 모델 검색에 재사용한다. 추가 정기 휴식 없이 완료한 `(모델명, 결과)`를 순차 반환한다.
- `_record_kream_response(page, response)` / `_raise_if_kream_limited(page)` — Kream 도메인의 문서·XHR·fetch HTTP 403/429를 기억해 후속 검색·상세 요청 전에 중단한다. 타사 리소스 오류는 제외한다.
- `_open_search_and_get_input(page)` — 입력창이 이미 보이면 바로 반환(검색 결과 페이지), 없으면 헤더 검색 버튼 클릭 후 반환
- `_search_kream_once(model_name, page)` — 검색 입력 → 타이핑 → Enter → 결과 파싱
- `_load_detail_model_number(page, product_url)` — Playwright 세션으로 후보 상세 URL을 열고 서버 응답 HTML의 `모델번호` 문단을 우선 파싱한 뒤, 없으면 반응형 중복 요소 중 첫 hidden 노드에 막히지 않도록 `attached` DOM 전체 텍스트로 폴백한다. 두 경로 모두 실패하면 `None`을 반환해 fail-closed 처리
- `_find_verified_kream_product(model_name, cards_data, page)` — 검색 결과 상위 `MAX_RESULTS=5`개 상세를 순서대로 확인하고 실제 모델번호가 검색어와 완전 일치하는 첫 상품만 반환
- 검색 결과 수로 정확도를 추정하지 않는다. 상세 모델번호 검증을 통과하지 못한 후보는 KreamProduct와 차익 결과에 저장하지 않는다.
- 상세 이동 전 추가 휴식과 자동 재시도 없음. 차단은 HTTP 403/429와 차단 문구로만 판단한다. 본문을 읽지 못하거나 짧은 화면은 그 상품의 모델번호 확인으로 넘긴다.

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

### kream/history_marker.py
- `find_previous_result_path(output_dir)` — 오늘 폴더(`output_dir.name`) 이전의 `YYYYMMDD` 폴더 중 `arbitrage_results.json`이 실제 존재하는 가장 최신 파일 경로. 없으면 `None`
- `load_previous_price_map(output_dir)` — 직전 결과를 `{(model_name, marketplace_site): {marketplace_price, ...}}`로 변환. 같은 키의 여러 가격은 집합에 모두 보관. 파일 없음은 정상(빈 맵), JSON 오류·최상위 list 아님은 warning 후 빈 맵(전부 신규), 손상 레코드(dict 아님·키 누락·model_name/marketplace_site 가 문자열 아님·가격이 None·bool·쉼표 문자열·비정수 float·Infinity/NaN)는 해당 건만 건너뛰고 건수를 warning 1회로 남김
- `select_closest_previous_price(previous_prices, today_price)` — 이전 가격 집합에서 오늘 가격과 차이가 가장 작은 값(동률이면 더 작은 가격)
- `mark_result(result, previous_prices)` — 결과 dict 한 건에 아래 필드를 추가한다. `kream_price`는 비교하지 않는다.
  - `previously_saved` (bool) — 이전 파일에 같은 키가 있으면 true
  - `price_status` — `new`(키 없음) | `same`(오늘 가격이 이전 가격 집합에 포함) | `changed`(미포함)
  - `previous_marketplace_price` (int) — `changed`일 때만 추가, 가장 가까운 이전 가격

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
| `SITE_DELAY_MIN_SEC` / `SITE_DELAY_MAX_SEC` | 1 / 3 | 네이버 스토어 전환 대기(초) |




| `OUTPUT_DIR` | "output" | 결과 저장 루트 디렉토리 |
| `NIKE_SALE_URL` | `""` | 나이키 성인 세일 신발 URL. 빈 문자열이면 비활성, 크롤러는 `config.NIKE_SALE_URL` 을 직접 읽음. 예시: `https://www.nike.com/kr/w/clearance-shoes-3yaepzy7ok` |
| `ADIDAS_SALE_URL` | `""` | 아디다스 세일 목록 URL. 빈 문자열이면 비활성, 크롤러는 `config.ADIDAS_SALE_URL` 을 직접 읽음. 예시: `https://www.adidas.co.kr/outlet?grid=true` (공식 아울렛 전체보기, grid=true 필수). 의류·신발·용품 포함, Kids 제외 |
| `MUSINSA_SALE_URL` | musinsa.com/main/outlet/ranking?...categoryCode=107002 | 무신사 아울렛 신발 랭킹 URL. `/brand/{slug}` URL도 지원 |
| `SSFSHOP_SALE_URL` | ssfshop.com/ADIDAS-KOREA/main?...brandShopNo=BDMA29AKU | SSF SHOP ADIDAS KOREA 브랜드관 전체(의류·잡화·신발). 빈 문자열이면 비활성, 크롤러는 `config.SSFSHOP_SALE_URL`을 직접 읽음. `currentPage`는 1부터 덮어씀 |
| `SPECIAL_SALE_URL` | `""` | 범용 적응형 크롤러 대상 URL. 빈 문자열이면 비활성, 크롤러는 `config.SPECIAL_SALE_URL` 을 직접 읽음. 예시: `https://brand.naver.com/adidas/shoppingstory/detail?id=5002700579` |
| `SPECIAL_MODEL_STRATEGY` | "auto" | special 상품명 모델 추출 전략 |
| `SITE_DISCOUNT_RATES` | marketplace 0%, adidas 30%, musinsa 0%, nike 0%, special 0%, ssfshop 0% | 소스 크롤러별 수동 할인율 (%). 0이면 sale_price 미산출 |

## 새 크롤링 소스 추가 방법

1. `{소스명}/crawler.py` 생성 — `crawl_{소스명}()` 구현, `MarketplaceProduct` 포맷으로 반환
2. `main.py` STEP 1에 크롤링 호출 및 `{소스명}_products.json` 저장 추가
3. 나머지(Kream 검색 대상 포함·diff 생성·차익 비교)는 자동 처리됨

**참고:** Phase 1 에서 추가된 `nike/crawler.py` 가 이 패턴의 실제 사례이다.

## 브라우저 세션 운용
- ego lite 와 `ego-browser` 가 준비된 뒤 `pip install -r requirements.txt`로 의존성을 설치한다. `python main.py`는 사용자의 ego lite 안에 Task Space `resell-sniper` 를 연다. `python chrome.py`는 그 Space 에서 webdriver 표시를 확인한 뒤 Space 를 닫는다.
- 네이버·Kream·나이키·아디다스·special은 공통 창 팩토리를 사용한다. 무신사는 HTTP API, SSF SHOP은 서버 렌더링 HTML GET을 사용한다.
- Chrome 확장·Node 의존성·연결 승인 없이 동작하며 기존 사용자 창은 건드리지 않는다. 네이버 로그인·보안 확인 화면에서는 열린 Chrome에서 확인한 뒤 Enter로 수집을 이어간다. 계정 보호와 HTTP 403/429는 종료한다.
- 네이버 스토어 전환 대기는 1~3초다. Kream 검색·상세·정기·청크 휴식은 없으며 기존 렌더링/입력 대기는 유지한다.
- 단일 프로세스 작업 탭은 최대 1개다. 여러 파이프라인을 동시에 실행하지 않는다. 차단 감지 시 후속 요청을 중단하며 차단 방지를 보장하지 않는다. 아디다스 최초 문서의 429에 한해 위의 제한된 재시도 정책을 적용한다.
- 검증: Python 단위 테스트, `python chrome.py`로 `navigator.webdriver` 점검. 실제 사이트 DOM/JSON 교차검증은 크롤러 종료 후 확장 없는 Playwright MCP로 수행한다.
