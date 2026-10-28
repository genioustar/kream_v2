---
gsd_state_version: 1.0
milestone: v1.1
milestone_name: 확장 및 안정화
status: executing
last_updated: "2026-05-08T02:29:34.824Z"
progress:
  total_phases: 4
  completed_phases: 1
  total_plans: 2
  completed_plans: 2
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-13)

**Core value:** 차익이 있는 상품을 빠짐없이 포착한다
**Current milestone:** Milestone 2 — 확장 및 안정화
**Current focus:** Phase 01 — 나이키 공홈 크롤러 추가

## Current Phase

**Phase:** 1 — 나이키 공홈 크롤러 추가
**Status:** Executing Phase 01

다음 명령으로 실행 시작: `/gsd:execute-phase 1`

## Milestone Progress

| Phase | Title | Status |
|-------|-------|--------|
| 1 | 나이키 공홈 크롤러 추가 | Planned (2 plans ready) |
| 2 | 안정성 및 버그 수정 | Pending |
| 3 | 성능 개선 | Pending |
| 4 | 텔레그램 알림 | Pending |

## Phase 1 Plans

| Plan | Title | Wave | Status |
|------|-------|------|--------|
| 01-01 | nike/crawler.py 구현 | 1 | Complete (커밋: 50e3ece, 9145766) |
| 01-02 | main.py 통합 및 문서 업데이트 | 2 | Ready (depends on 01-01) |

## Decisions

- [kream] 검색 카드는 이름에 모델코드가 있을 때만 채택하고, 같은 href는 한 번만 남긴다.
  - SUPERSEDED: 카드 3장 이상이면 빈 결과, 1~2장이면 첫 카드 — 연관 상품이 3장 이상이면 코드가 있는 카드도 탈락했다.
- [naver] 상품 href의 기준 호스트는 그 카드를 연 페이지 origin (`page_origin`).
  - SUPERSEDED: `clean_url` 기본값 `https://www.naver.com` — 브랜드스토어·스마트스토어의 루트 상대 경로가 `www.naver.com`으로 저장됐다.
- [arbitrage] `price_diff`는 JSON에 정수로 남긴다. 천 단위 표시는 로그에서만 한다.
  - SUPERSEDED: 저장 직전에 `"12,000"` 문자열로 덮기 — 파일을 다시 읽으면 `ArbitrageResult.price_diff: int`와 어긋난다.
- [config] 쇼핑스토리 5002700579는 `SPECIAL_SALE_URL`만 소유한다.
  - SUPERSEDED: `SEARCH_URLS` adidas 항목과 `SPECIAL_SALE_URL`이 같은 id — 2026-10-24 네이버 카드 셀렉터는 타임아웃이고 special만 문서를 열었다.
- [pipeline] `NIKE_SALE_URL`·`ADIDAS_SALE_URL`이 없으면 `main`은 크롤러를 호출하지 않는다. 모듈은 단독 실행용으로 남긴다.
- [crawler-config] 크롤링 URL 단일 출처 = config.py — naver/adidas/nike 모두 `config.SEARCH_URLS` / `config.ADIDAS_SALE_URL` / `config.NIKE_SALE_URL` 만 참조. URL 미설정/빈 값이면 해당 크롤링은 자동 스킵(`[]` 반환). redirect 감지 키워드는 URL path 마지막 segment 에서 자동 추출 (하드코딩 제거)
- [chrome] DevTools 주소는 `chrome.resolve_cdp_url()` — 9222가 DevTools면 유지하고, 다른 프로세스가 점유 중이면 `chrome.py`가 9222–9232의 빈 포트에 Chrome을 띄워 `output/session/cdp_port`에 기록한다. 준비 실패 시 종료 코드 1.
  - SUPERSEDED: `connect_over_cdp("http://localhost:9222")` 고정 — 9222가 DevTools가 아닌 Chrome에 점유되면 연결이 404이고, `chrome.py`는 준비되지 않아도 종료 코드 0을 반환했다.
- [01-nike-crawler] 실제 Chrome CDP 연결 사용 (`connect_over_cdp`) — Kasada 탐지로 Playwright Chromium 차단됨. product_wall API 응답 인터셉트 방식 채택. 주소는 `chrome.resolve_cdp_url()`.
  - SUPERSEDED: 기존 "CDP 대신 create_browser+new_stealth_page 사용" 결정 — Akamai는 없지만 Kasada가 무한스크롤 JS를 비활성화해 21개만 수집되는 문제 발생
- [01-nike-crawler] 스타일코드(CN8490-002) 우선 모델명 추출 — Kream 검색 정확도 최대화. API 응답의 `productCode` 1순위, `_extract_model_name` 폴백
- [01-nike-crawler] 성인 전용 URL + 키워드 2차 필터 — Kids 완전 제외
- [Phase 01-nike-crawler] nike 블록은 아디다스 패턴 복제 — try/except 예외 격리, 빈 결과 시 파일 저장 생략

## Recent Activity

- 2026-04-28: 크롤링 URL 단일 출처(config) 통일 — adidas 하드코딩 제거, ADIDAS_SALE_URL 교정, naver/adidas/nike URL 부재 가드 추가, redirect 키워드 자동 추출
- 2026-04-22: 문서·코드 정합성 정리 — nike/crawler.py의 DOM 파싱 잔재 제거(함수 3개·상수 9개·_JS_EXTRACT), ARCHITECTURE.md nike 섹션 API 방식으로 재작성
- 2026-04-15: 나이키 Kasada 우회 — CDP + API 응답 인터셉트로 전환 (기존 stealth 방식 폐기)
- 2026-04-15: 01-01 플랜 완료 — nike/crawler.py 구현
- 2026-04-13: GSD 초기화 및 Phase 1 플랜 작성

---
*Last updated: 2026-04-28*
