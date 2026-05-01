# kream_v2

네이버 스토어·아디다스·나이키 세일 상품을 수집하고, Kream 시세와 비교하여 차익 거래 기회를 탐색하는 파이프라인.

## 요구사항

- Python 3.11+
- Google Chrome (아디다스·나이키 크롤링 — CDP 연결용)
- GNU Make (Windows: Git for Windows에 포함)

## 설치

### Windows

```powershell
# 1. 가상환경 생성
python -m venv .venv
.venv\Scripts\activate

# 2. 의존 패키지 설치
pip install -r requirements.txt

# 3. Playwright 브라우저 바이너리 설치 (네이버·Kream 전용)
playwright install chromium
```

### macOS

```bash
# 1. 가상환경 생성
python3 -m venv .venv
source .venv/bin/activate

# 2. 의존 패키지 설치
pip install -r requirements.txt

# 3. Playwright 브라우저 바이너리 설치
playwright install chromium
```

## Chrome CDP 실행 (아디다스·나이키 전용)

아디다스·나이키 크롤러는 Akamai/Kasada 봇 탐지 우회를 위해 **실제 Chrome에 CDP로 연결**합니다.
Playwright 자체 Chromium은 fingerprint 탐지에 걸려 차단되므로 사용할 수 없습니다.

크롤링 전 Chrome을 CDP 디버깅 모드로 먼저 실행합니다:

```bash
make chrome
```

| 플랫폼  | Chrome 경로                                                        | 데이터 디렉토리    |
| ------- | ------------------------------------------------------------------ | ------------------ |
| Windows | `C:/Program Files/Google/Chrome/Application/chrome.exe`           | `C:/Temp/chrome-cdp` |
| macOS   | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`    | `/tmp/chrome-cdp`  |

> Chrome 설치 경로가 다른 경우 `Makefile`의 `CHROME_BIN` 값을 수정하세요.

## 실행

```bash
# [필수] 아디다스·나이키 크롤링 전 Chrome CDP 먼저 실행
make chrome

# 전체 파이프라인 (크롤링 + Kream 가격 비교)
make full

# 아디다스 단독 크롤링
make adidas

# 나이키 단독 크롤링
make nike

# 전체 크롤링만 (Kream 검색 없음)
make crawl

# Kream 검색만 (오늘자 *_products.json이 이미 있어야 함)
make kream
```

### 실행 모드 요약

| 모드     | 설명                                                                      | Chrome CDP 필요 |
| -------- | ------------------------------------------------------------------------- | :-------------: |
| `full`   | 네이버·아디다스·나이키 크롤링 → Kream 가격 비교 → 차익 결과 저장         | O               |
| `crawl`  | 사이트 크롤링만 수행, `output/YYYYMMDD/*_products.json` 저장              | O               |
| `kream`  | 오늘자 `*_products.json`을 읽어 Kream 검색·비교만 수행                    | X               |
| `adidas` | 아디다스 단독 크롤링                                                      | O               |
| `nike`   | 나이키 단독 크롤링                                                        | O               |

### 플랫폼별 차이

| 항목               | Windows                         | macOS                          |
| ------------------ | ------------------------------- | ------------------------------ |
| 가상환경 활성화    | `.venv\Scripts\activate`        | `source .venv/bin/activate`    |
| Python 경로        | `.venv/Scripts/python`          | `.venv/bin/python`             |
| Chrome 기본 경로   | `C:/Program Files/Google/...`   | `/Applications/Google Chrome…` |
| Make 사용 가능     | Git for Windows 설치 시 가능    | 기본 포함                      |

## 출력

결과는 `output/YYYYMMDD/` 디렉터리에 저장됩니다.

| 파일                     | 내용                                           |
| ------------------------ | ---------------------------------------------- |
| `naver_products.json`    | 네이버 스토어 수집 상품                        |
| `adidas_products.json`   | 아디다스 Extra Sale 수집 상품                  |
| `nike_products.json`     | 나이키 세일 수집 상품                          |
| `arbitrage_results.json` | 차익 거래 가능 상품 목록 (가격 차이 기준 정렬) |

## 설정

`config.py`에서 주요 값을 변경할 수 있습니다.

| 항목                    | 기본값                          | 설명                           |
| ----------------------- | ------------------------------- | ------------------------------ |
| `MIN_TRADE_COUNT`       | `100`                           | Kream 최소 거래량 필터         |
| `MIN_PRICE_DIFF`        | `10000`                         | 최소 차익 기준 (원)            |
| `KREAM_MAX_CONCURRENCY` | `2`                             | Kream 동시 검색 탭 수          |
| `OUTPUT_DIR`            | `output`                        | 결과 저장 디렉터리             |
| `NIKE_SALE_URL`         | nike.com/kr/w/clearance-shoes   | 나이키 세일 신발 URL           |
| `ADIDAS_SALE_URL`       | adidas.co.kr/extra_sale-shoes   | 아디다스 Extra Sale URL        |
