# Requirements: resell-sniper

**Defined:** 2026-04-13
**Core Value:** 차익이 있는 상품을 빠짐없이 포착한다 — 크롤링 실패나 탐지 차단 없이 안정적으로 전체 파이프라인이 완주되어야 한다.

## v1 Requirements (Milestone 1 — 완료됨)

이미 검증된 요구사항. 참조용으로 유지.

### 크롤링 파이프라인

- ✓ **CRAWL-01**: 네이버 브랜드스토어·스마트스토어 병렬 크롤링 → `naver_products.json`
- ✓ **CRAWL-02**: 아디다스 Extra Sale 신발 크롤링 (Akamai CDP 우회) → `adidas_products.json`
- ✓ **CRAWL-03**: Kream 리셀 가격·거래량 조회 (검색 입력 + Enter 방식)
- ✓ **CRAWL-04**: 차익 비교 (거래량 ≥ 100, 가격차 ≥ 10,000원) → `arbitrage_results.json`
- ✓ **CRAWL-05**: 날짜별 상품 변경 추적 (`diff_output.py`)
- ✓ **CRAWL-06**: 전체·일별 모드 선택 실행 (`--mode crawl|kream|full`)

### 탐지 우회

- ✓ **ANTI-01**: Playwright-stealth + UA 풀 (12종) + sec-ch-ua Client Hints
- ✓ **ANTI-02**: 쿠키 영속성 (`output/session/kream_cookies.json`)
- ✓ **ANTI-03**: 차단 감지 시 지수 백오프 재시도 (30→60→120초)
- ✓ **ANTI-04**: Chrome CDP 연결 (아디다스 전용)

## v2 Requirements (Milestone 2 — 현재 목표)

### 새 크롤링 소스

- [x] **SRC-01**: 새 쇼핑몰 크롤러 1개 이상 추가 (무신사, 나이키 공홈 등 협의 후 결정)
- [ ] **SRC-02**: 새 소스 NaverProduct 포맷 통합 (자동 diff·비교 포함)

### 안정성 및 버그 수정

- [ ] **STBL-01**: 셀렉터 오류로 인한 크롤링 누락 수정
- [ ] **STBL-02**: Kream 차단 시 복구 로직 개선
- [ ] **STBL-03**: 아디다스 페이지 구조 변경 대응 (카드 셀렉터 자동 탐지 강화)
- [ ] **STBL-04**: 에러 발생 시 부분 결과 저장 (전체 실패 방지)

### 성능 개선

- [ ] **PERF-01**: 네이버 크롤링 속도 개선 (무한스크롤 최적화)
- [ ] **PERF-02**: Kream 동시성 최적화 (차단 없이 안전한 최대 동시성 탐색)
- [ ] **PERF-03**: 재실행 시 불필요한 중복 크롤링 최소화 (캐시 활용)

### 알림

- [ ] **NOTF-01**: 차익 결과를 텔레그램으로 전송
- [ ] **NOTF-02**: 크롤링 오류·차단 발생 시 알림

## Out of Scope

| Feature | Reason |
|---------|--------|
| 웹 UI / 대시보드 | 개인 도구 — CLI + JSON으로 충분 |
| 자동 구매 실행 | 탐색만 지원, 실제 거래는 수동 |
| 실시간 스트리밍 | 배치 실행으로 충분, 복잡도 불필요 |
| 다중 사용자 / 인증 | 개인 도구 |
| Docker/배포 자동화 | 로컬 실행 전용 |

## Traceability

로드맵 생성 후 업데이트 예정.

| Requirement | Phase | Status |
|-------------|-------|--------|
| SRC-01 | Phase 1 | Complete |
| SRC-02 | Phase 1 | Pending |
| STBL-01 | Phase 2 | Pending |
| STBL-02 | Phase 2 | Pending |
| STBL-03 | Phase 2 | Pending |
| STBL-04 | Phase 2 | Pending |
| PERF-01 | Phase 3 | Pending |
| PERF-02 | Phase 3 | Pending |
| PERF-03 | Phase 3 | Pending |
| NOTF-01 | Phase 4 | Pending |
| NOTF-02 | Phase 4 | Pending |

**Coverage:**
- v2 requirements: 11 total
- Mapped to phases: 11
- Unmapped: 0 ✓

---
*Requirements defined: 2026-04-13*
*Last updated: 2026-04-13 after GSD initialization*
