# kream_v2

네이버 스토어·아디다스·무신사·나이키·SSF SHOP·special URL의 상품을 수집하고, Kream 시세와 비교하여 차익 거래 기회를 탐색하는 파이프라인.

## 요구사항

- Python 3.11+
- ego lite (네이버·Kream·아디다스·나이키·special 별도 창 실행용)

## 설치

### Windows

```powershell
# 1. 가상환경 생성
python -m venv .venv
.venv\Scripts\activate

# 2. 의존 패키지 설치
pip install -r requirements.txt

```

### macOS

```bash
# 1. 가상환경 생성
python3 -m venv .venv
source .venv/bin/activate

# 2. 의존 패키지 설치
pip install -r requirements.txt

```

## ego lite 실행 설정

ego lite와 Python 의존성(`pip install -r requirements.txt`)만 필요합니다. `ego-browser` 명령이 있어야 합니다.
`python main.py`는 새 브라우저를 띄우지 않습니다. 설치된 ego lite 안에 `resell-sniper` Task Space 를 열어 그 안에서만 수집합니다. 사용자의 다른 탭은 그대로 둡니다. Space 는 ego lite 기본 프로필의 로그인을 사용합니다.
네이버 로그인·보안 확인 화면이 나오면 그 Space 에서 확인한 뒤 터미널에서 Enter를 누릅니다. `q`를 입력하면 그 스토어만 중단합니다. 계정 보호와 접근 제한은 진단을 저장하고 종료합니다. 수집이 끝나면 그 Task Space 만 닫고 ego lite 앱은 닫지 않습니다.
`python chrome.py`는 같은 Space 에서 `navigator.webdriver` 표시를 확인한 뒤 Space 를 닫습니다.

## 순차 실행과 휴식

크롤러 작업 탭은 한 번에 1개만 사용합니다. 네이버는 스토어 탭을 닫은 후 1~3초 쉬고 다음 스토어를 열며, Kream은 같은 탭에서 모델을 하나씩 검색합니다. Kream 검색·상세 이동 전 추가 휴식, 정기 휴식과 청크 간 30분 휴식은 없습니다. 렌더링·입력 대기는 유지합니다. 네이버 전환 시간은 `config.py`에서 조정할 수 있습니다.

네이버 접근/수집 실패, Kream 차단 신호·검색 실패 또는 문서/API의 403/429 감지 시 후속 요청을 중단합니다. Kream은 완료된 모델의 차익 결과를 저장한 뒤 종료합니다. 변경 상품이 없는 diff 실행은 검색을 생략합니다. 여러 파이프라인 프로세스를 동시에 실행하지 마세요.

## 실행

```bash
# 전체 파이프라인 (크롤링 + Kream 가격 비교)
python main.py

# 아디다스 단독 크롤링
python -m adidas.crawler

# 나이키 단독 크롤링
python -m nike.crawler

# 무신사 단독 크롤링
python -m musinsa.crawler

# special 단독 크롤링
python -m special.crawler

# 전체 크롤링만 (Kream 검색 없음)
python main.py --mode crawl

# Kream 검색만 (오늘자 *_products.json이 이미 있어야 함)
python main.py --mode kream
```

### 실행 모드 요약

| 모드     | 설명                                                                      | Chrome 필요 |
| -------- | ------------------------------------------------------------------------- | :-------------: |
| `full`   | 설정된 전체 소스 크롤링 → Kream 가격 비교 → 차익 결과 저장               | 조건부          |
| `crawl`  | 사이트 크롤링만 수행, `output/YYYYMMDD/*_products.json` 저장              | 조건부          |
| `kream`  | 오늘자 `*_products.json`을 읽어 Kream 검색·비교만 수행                    | O               |
| `adidas` | 아디다스 단독 크롤링                                                      | O               |
| `musinsa` | 무신사 단독 크롤링                                                       | X               |
| `nike`   | 나이키 단독 크롤링                                                        | O               |
| `special` | special 단독 크롤링                                                      | O               |

`full`·`crawl`에서 소스 URL이 비어 있으면 해당 크롤러는 건너뜁니다.

### 플랫폼별 차이

| 항목               | Windows                         | macOS                          |
| ------------------ | ------------------------------- | ------------------------------ |
| 가상환경 활성화    | `.venv\Scripts\activate`        | `source .venv/bin/activate`    |
| Python 경로        | `.venv/Scripts/python`          | `.venv/bin/python`             |

## 출력

결과는 `output/YYYYMMDD/` 디렉터리에 저장됩니다.

| 파일                     | 내용                                           |
| ------------------------ | ---------------------------------------------- |
| `marketplace_products.json` | 네이버 스토어 수집 상품                     |
| `diagnostics/naver_*.json` | 네이버 스토어별 성공·실패 관측 기록 |
| `checkpoints/naver/*.json` | 같은 날 완료한 네이버 스토어 결과 (재실행 시 재사용) |
| `adidas_products.json`   | 아디다스 Extra Sale 수집 상품                  |
| `musinsa_products.json`  | 무신사 브랜드 또는 아울렛 랭킹 수집 상품       |
| `ssfshop_products.json`  | SSF SHOP ADIDAS KOREA 브랜드관 전체 상품 (의류·잡화·신발) |
| `nike_products.json`     | 나이키 세일 수집 상품                          |
| `special_products.json`  | special URL 수집 상품                           |
| `arbitrage_results.json` | 차익 거래 가능 상품 목록 (가격 차이 기준 정렬) |

## 설정

`config.py`에서 주요 값을 변경할 수 있습니다.

| 항목                     | 기본값                     | 설명                                      |
| ------------------------ | -------------------------- | ----------------------------------------- |
| `SEARCH_URLS`            | 네이버 스토어 8개          | URL·스토어 타입·모델명 추출 전략          |
| `SEARCH_URLS[*].navigation` | `direct` (루티크: `store_search_ui`) | 검색 URL 직접 이동 대신 공개 스토어 검색 UI 사용 |
| `MIN_TRADE_COUNT`        | `100`                      | Kream 최소 거래량 필터                    |
| `MIN_PRICE_DIFF`         | `5000`                     | 최소 차익 기준 (원)                       |
| `SITE_DELAY_MIN_SEC` / `SITE_DELAY_MAX_SEC` | `1` / `3` | 스토어 전환 대기(초) |




| `OUTPUT_DIR`             | `output`                   | 결과 저장 디렉터리                        |
| `NIKE_SALE_URL`          | 미설정                     | 나이키 세일 URL                           |
| `ADIDAS_SALE_URL`        | 미설정                     | 아디다스 세일 URL                         |
| `MUSINSA_SALE_URL`       | 무신사 아울렛 신발 랭킹    | `/brand/{slug}` 또는 `/main/.../ranking` |
| `SSFSHOP_SALE_URL`       | ADIDAS KOREA 브랜드관      | 의류·잡화·신발 전체. 빈 문자열이면 크롤링 생략 |
| `SPECIAL_SALE_URL`       | 미설정                     | 범용 크롤링 대상 URL                      |
| `SPECIAL_MODEL_STRATEGY` | `auto`                     | special 상품명 모델 추출 전략             |

### 사이트별 모델명 추출 전략

`SEARCH_URLS` 각 항목의 `model_strategy`만 바꾸면 사이트 표기에 맞춰 추출 우선순위를 조정할 수 있습니다.

| 전략 | 적합한 상품명 예시 |
| --- | --- |
| `bracket_first` | `상품명 (HF0383-419)`, `상품명 (ABC123/DEF456)` |
| `slash_first` | `상품명 / IF6562` |
| `suffix_single` | `상품명 WFS2020RMA-BLK` |
| `suffix_grouped` | `상품명 A21873 0BKBW 900` |
| `auto` | 괄호·슬래시·후행 표기가 섞인 사이트 |

공통 추출기는 5자리 이상 숫자 코드와 상품명 중간의 영문·숫자 코드도 인식하며, 시즌·사이즈·수량·용량 표기는 제외합니다.

### 네이버 수집 실패 진단

루티크는 검색 URL을 직접 열면 로그인으로 이동하지만, 공개 스토어 홈의 검색창에서는 비로그인 검색이 가능합니다. `navigation="store_search_ui"`는 홈에서 검색창을 열고 기존 검색어를 입력한 다음 검색·페이지당 개수를 UI로 설정합니다. 현재 `디젤`, 정확도순, 이미지형, 첫 페이지, 80개 조건을 유지합니다. 최종 검색 URL·검색 결과 제목·총수·카드 수를 확인하여 홈 상품이나 일부 결과를 성공으로 저장하지 않습니다. 검색 결과가 선택한 페이지 크기를 넘으면 미완료로 처리합니다.

실행 명령은 그대로 `python main.py --mode crawl`입니다. 네이버는 상품 목록을 최대 15초 기다립니다. 로그인·보안 확인 화면이면 종료하지 않고, 열린 Chrome에서 확인한 뒤 Enter를 누르면 30초간 목록을 다시 확인합니다. 계정 보호와 접근 제한은 해당 작업과 후속 스토어 수집을 중단합니다. 터미널의 진단 코드와 JSON 경로를 확인하세요.

진단 파일에는 실행 식별자·시각·단계, 요청/최종 URL의 호스트와 경로, 상품 카드 수, 네이버 요청·응답 통계와 관측 근거를 기록합니다. URL 쿼리·fragment·인증정보, 쿠키 값, HTML·응답 본문·페이지 제목·계정 화면 스크린샷은 저장하지 않습니다. 기록은 브라우저에서 관측한 상태이며 네이버 내부의 계정 보호 판정 사유를 확정하지 않습니다.

| 진단 코드 | 의미와 대응 |
| --- | --- |
| `account_protection` | 계정 보호 화면 관측. 평소 브라우저에서 본인확인 및 로그인 기록 확인 |
| `login_required` | 로그인 페이지 관측. 종료하지 않고 Chrome에서 로그인한 뒤 Enter로 재개 |
| `security_challenge` | 보안 확인 화면 관측. 종료하지 않고 확인 뒤 Enter로 재개 |
| `authenticated_session` | 수집 창에서 네이버 인증 쿠키가 관측됨. 기록만 하고 수집은 계속 |
| `http_403` / `http_429` | 관련 문서·상품 목록 API의 접근 제한. 후속 요청 없이 종료 |
| `product_list_timeout` | 대기 후에도 상품 카드 없음. 다른 제한 신호가 없다면 셀렉터·렌더링·빈 목록 여부 점검 |
| `network_error` | 페이지·상품 요청 실패. 네트워크 상태와 보고서의 예외 종류 확인 |
| `collection_error` | 기타 수집 실패. 보고서의 단계·예외 종류 확인 |

정상 완료한 스토어는 `checkpoints/naver/`에 별도로 저장됩니다. 같은 날 같은 URL·스토어 타입·모델 추출 전략·접속 방식으로 다시 실행하면 해당 스토어를 재방문하지 않습니다. 파싱 오류를 포함한 실패한 스토어는 완료 캐시를 만들지 않으며, 모든 스토어가 완료되기 전에는 최종 `marketplace_products.json`을 만들지 않습니다. 캐시는 매일 분리되며 할인율은 최종 저장 단계에서 적용됩니다.
