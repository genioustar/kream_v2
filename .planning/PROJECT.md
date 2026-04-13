# resell-sniper

## What This Is

네이버 쇼핑몰(브랜드스토어·스마트스토어) 및 아디다스 공홈에서 신발 상품을 수집하고, Kream에서 동일 상품의 리셀 가격과 거래량을 자동 조회해 차익 거래 기회를 탐색하는 크롤링 파이프라인이다. 개인 리셀 투자 의사결정을 위한 도구로, 매일 또는 주기적으로 실행해 결과를 JSON으로 저장한다.

## Core Value

**차익이 있는 상품을 빠짐없이 포착한다** — 크롤링 실패나 탐지 차단 없이 안정적으로 전체 파이프라인이 완주되어야 한다.

## Requirements

### Validated

- ✓ 네이버 브랜드스토어·스마트스토어 병렬 크롤링 → `naver_products.json` — milestone 1
- ✓ 아디다스 Extra Sale 신발 크롤링 (Akamai CDP 우회) → `adidas_products.json` — milestone 1
- ✓ Kream 리셀 가격·거래량 조회 (검색 입력 + Enter 방식) — milestone 1
- ✓ 차익 비교 (거래량 ≥ 100, 가격차 ≥ 10,000원) → `arbitrage_results.json` — milestone 1
- ✓ 날짜별 상품 변경 추적 (diff_output.py) — milestone 1
- ✓ 전체·일별 모드 선택 실행 (`--mode crawl|kream|full`) — milestone 1
- ✓ Playwright-stealth + UA 풀 + 쿠키 영속성 탐지 우회 — milestone 1
- ✓ 페이지 풀 기반 동시 Kream 검색 (KREAM_MAX_CONCURRENCY) — milestone 1

### Active

- [ ] 새 쇼핑몰 크롤러 추가 (예: 무신사, 나이키 공홈 등)
- [ ] 기존 크롤러 버그 수정 (셀렉터 오류, 탐지 차단 등)
- [ ] 성능 개선 (크롤링 속도, 동시성, 재시도 로직)
- [ ] 알림 기능 (차익 결과를 텔레그램 등으로 전송)

### Out of Scope

- 웹 UI / 대시보드 — 개인 도구이므로 CLI + JSON 출력으로 충분
- 자동 구매 실행 — 탐색(스카우팅)만 지원, 실제 거래는 수동
- 실시간 스트리밍 — 배치 실행 방식으로 충분

## Context

- **언어/런타임**: Python 3.14, Playwright (async), playwright-stealth
- **크롤링 대상**: 네이버 4개 사이트, 아디다스 공홈, Kream
- **탐지 우회**: Chrome CDP 연결 (아디다스 Akamai 우회), UA 풀 랜덤 선택, sec-ch-ua Client Hints, 쿠키 영속성
- **마일스톤 1 상태**: 전체 파이프라인 동작 완료. 간헐적 셀렉터 오류·차단 이슈 존재
- **실행 방식**: Makefile 단축 명령 (`make full`, `make kream` 등)

## Constraints

- **Tech Stack**: Python + Playwright 고정 — 기존 코드베이스와 호환성 유지
- **Chrome CDP**: 아디다스 크롤러는 실제 Chrome에 연결 필수 (`--user-data-dir` 포함)
- **Kream 동시성**: 과도한 동시 요청 시 차단 위험 — `KREAM_MAX_CONCURRENCY ≤ 3` 권장
- **개인 도구**: 배포·다중 사용자 고려 불필요

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Playwright-stealth + CDP 혼합 사용 | 일반 stealth는 Akamai 우회 불가 → 아디다스는 CDP, 나머지는 stealth | ✓ 동작 확인 |
| UA 풀에서 컨텍스트 생성 시 1회 고정 | 페이지마다 UA 변경 시 불일치 → 봇 신호 위험 | ✓ 동작 확인 |
| Kream 검색 딜레이 8~15초 | 빠른 검색 시 차단 발생 이력 | ✓ 안정화 |
| `DEFAULT_PRODUCTS_KEY = ("model_name", "site_name")` | 신규 소스 추가 시 diff 로직 자동 적용 | ✓ 확장성 확보 |
| NaverProduct 공통 포맷 | 모든 소스(네이버·아디다스 등)를 동일 구조로 통일 | ✓ 동작 확인 |

## Evolution

이 문서는 페이즈 전환 및 마일스톤 경계에서 업데이트된다.

**각 페이즈 전환 후 (`/gsd:transition`):**
1. 무효화된 요구사항 → Out of Scope로 이동 (이유 포함)
2. 검증된 요구사항 → Validated로 이동 (페이즈 참조 포함)
3. 새로 나타난 요구사항 → Active에 추가
4. 주요 결정사항 → Key Decisions에 추가
5. "What This Is" 여전히 정확한가 → 변경 시 업데이트

**각 마일스톤 후 (`/gsd:complete-milestone`):**
1. 전체 섹션 검토
2. Core Value 확인 — 여전히 올바른 우선순위인가?
3. Out of Scope 감사 — 이유가 여전히 유효한가?
4. Context를 현재 상태로 업데이트

---
*Last updated: 2026-04-13 after milestone 1 completion & GSD initialization*
