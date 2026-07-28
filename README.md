# kream_v2

네이버 스토어·아디다스·무신사·나이키·special URL의 상품을 수집하고, Kream 시세와 비교하여 차익 거래 기회를 탐색하는 파이프라인.

## 요구사항

- Python 3.11+
- Google Chrome (네이버는 Playwright Chrome 채널, 아디다스·나이키·special은 CDP 연결용)

## 설치

### Windows

```powershell
# 1. 가상환경 생성
python -m venv .venv
.venv\Scripts\activate

# 2. 의존 패키지 설치
pip install -r requirements.txt

# 3. Playwright 브라우저 바이너리 설치 (Kream 전용)
playwright install chromium
```

### macOS

```bash
# 1. 가상환경 생성
python3 -m venv .venv
source .venv/bin/activate

# 2. 의존 패키지 설치
pip install -r requirements.txt

# 3. Playwright 브라우저 바이너리 설치 (Kream 전용)
playwright install chromium
```

## Chrome CDP 실행 (아디다스·나이키·special)

아디다스·나이키·special 크롤러는 탐지 우회와 세션 공유를 위해 **실제 Chrome에 CDP로 연결**합니다.
Playwright 자체 Chromium은 fingerprint 탐지에 걸려 차단되므로 사용할 수 없습니다.

크롤링 전 Chrome을 CDP 디버깅 모드로 먼저 실행합니다:

```bash
python chrome.py
```

| 플랫폼  | Chrome 경로                                                        | 데이터 디렉토리    |
| ------- | ------------------------------------------------------------------ | ------------------ |
| Windows | `C:/Program Files/Google/Chrome/Application/chrome.exe`           | `C:/Temp/chrome-cdp` |
| macOS   | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`    | `/tmp/chrome-cdp`  |

> Chrome 설치 경로가 다른 경우 `chrome.py`의 `CHROME_BIN` 값을 수정하세요.

## 실행

```bash
# [필수] 아디다스·나이키·special URL 사용 시 Chrome CDP 먼저 실행
python chrome.py

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

| 모드     | 설명                                                                      | Chrome CDP 필요 |
| -------- | ------------------------------------------------------------------------- | :-------------: |
| `full`   | 설정된 전체 소스 크롤링 → Kream 가격 비교 → 차익 결과 저장               | 조건부          |
| `crawl`  | 사이트 크롤링만 수행, `output/YYYYMMDD/*_products.json` 저장              | 조건부          |
| `kream`  | 오늘자 `*_products.json`을 읽어 Kream 검색·비교만 수행                    | X               |
| `adidas` | 아디다스 단독 크롤링                                                      | O               |
| `musinsa` | 무신사 단독 크롤링                                                       | X               |
| `nike`   | 나이키 단독 크롤링                                                        | O               |
| `special` | special 단독 크롤링                                                      | O               |

`full`·`crawl`에서 CDP가 필요한 소스 URL이 비어 있으면 해당 크롤러는 건너뜁니다.

### 플랫폼별 차이

| 항목               | Windows                         | macOS                          |
| ------------------ | ------------------------------- | ------------------------------ |
| 가상환경 활성화    | `.venv\Scripts\activate`        | `source .venv/bin/activate`    |
| Python 경로        | `.venv/Scripts/python`          | `.venv/bin/python`             |
| Chrome 기본 경로   | `C:/Program Files/Google/...`   | `/Applications/Google Chrome…` |

## 출력

결과는 `output/YYYYMMDD/` 디렉터리에 저장됩니다.

| 파일                     | 내용                                           |
| ------------------------ | ---------------------------------------------- |
| `marketplace_products.json` | 네이버 스토어 수집 상품                     |
| `adidas_products.json`   | 아디다스 Extra Sale 수집 상품                  |
| `musinsa_products.json`  | 무신사 브랜드 또는 아울렛 랭킹 수집 상품       |
| `nike_products.json`     | 나이키 세일 수집 상품                          |
| `special_products.json`  | special URL 수집 상품                           |
| `arbitrage_results.json` | 차익 거래 가능 상품 목록 (가격 차이 기준 정렬) |

## 설정

`config.py`에서 주요 값을 변경할 수 있습니다.

| 항목                     | 기본값                     | 설명                                      |
| ------------------------ | -------------------------- | ----------------------------------------- |
| `SEARCH_URLS`            | 네이버 스토어 8개          | URL·스토어 타입·모델명 추출 전략          |
| `MIN_TRADE_COUNT`        | `100`                      | Kream 최소 거래량 필터                    |
| `MIN_PRICE_DIFF`         | `5000`                     | 최소 차익 기준 (원)                       |
| `KREAM_MAX_CONCURRENCY`  | `2`                        | Kream 동시 검색 탭 수                     |
| `OUTPUT_DIR`             | `output`                   | 결과 저장 디렉터리                        |
| `NIKE_SALE_URL`          | 미설정                     | 나이키 세일 URL                           |
| `ADIDAS_SALE_URL`        | 미설정                     | 아디다스 세일 URL                         |
| `MUSINSA_SALE_URL`       | 무신사 아울렛 신발 랭킹    | `/brand/{slug}` 또는 `/main/.../ranking` |
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

네이버 상품 목록이 15초 내 나타나지 않으면 열린 Chrome에서 로그인 또는 보안 확인을 완료하라는 입력 프롬프트가 표시됩니다. 완료 후 Enter를 누르면 재개하며, `q`를 입력하면 해당 사이트만 중단합니다.
